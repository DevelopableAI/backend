"""
GCP Cloud Run deployment provider with Cloud SQL PostgreSQL provisioning.

Deployment flow
───────────────
1. Detect credentials via GOOGLE_APPLICATION_CREDENTIALS or application
   default credentials (~/.config/gcloud/application_default_credentials.json).
2. Provision Cloud SQL PostgreSQL 15 instance (db-f1-micro).
3. Apply Prisma schema to the remote database (npx prisma db push).
4. Push the image to Google Container Registry (gcr.io/<project>/<name>).
5. Deploy to Cloud Run via the google-cloud-run SDK.
6. Allow unauthenticated requests (IAM binding for allUsers).
7. Apply labels for resource tracking.
8. Return a deployment record.

Database (Cloud SQL)
────────────────────
- db-f1-micro tier (~$7/month), PostgreSQL 15, single zone.
- Public IP enabled with 0.0.0.0/0 authorized for Prisma migration.
- WARNING: restrict authorized networks for production workloads.

Label constraints
─────────────────
GCP labels must match [a-z][a-z0-9_-]* and be ≤63 chars.
We normalise tag keys/values to lowercase and replace invalid characters.
"""

import base64
import getpass
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .base import BaseProvider


_DEFAULT_REGION = "us-central1"
_CONTAINER_PORT = 3000
_CLOUDSQL_WAIT_TIMEOUT_S = 900   # 15 minutes
_CLOUDSQL_POLL_INTERVAL_S = 20

# GCP APIs that must be enabled before deployment can proceed.
_REQUIRED_APIS = [
    "sqladmin.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
]

# Artifact Registry repository used for all Developable images in a project.
_AR_REPO_ID = "developable"


class GCPProvider(BaseProvider):
    """Deploy to GCP Cloud Run with Cloud SQL PostgreSQL, using google-cloud-run SDK."""

    display_name = "GCP Cloud Run"

    def __init__(
        self,
        out_dir: Path,
        project_id: str | None = None,
        region: str | None = None,
    ) -> None:
        super().__init__(out_dir)
        self._project_id = project_id
        self._region = region or _DEFAULT_REGION
        self._gcp_creds: Any = None  # set in deploy() after configure()

    # ── Credential handling ────────────────────────────────────────────────────

    def detect_credentials(self) -> dict[str, Any] | None:
        """Try GOOGLE_APPLICATION_CREDENTIALS and application default credentials."""
        try:
            import google.auth
        except ImportError:
            return None

        project_id = (
            self._project_id
            or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
            or os.environ.get("GCLOUD_PROJECT", "")
        ).strip()

        sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if sa_path:
            if not Path(sa_path).exists():
                return None
            return {
                "credentials_file": sa_path,
                "credentials_type": "service_account",
                "project_id": project_id or self._read_project_from_sa(sa_path),
                "region": self._region,
                "credentials_b64": self._encode_sa_file(sa_path),
            }

        try:
            creds, detected_project = google.auth.default()
            pid = project_id or detected_project or ""
            if not pid:
                return None
            return {
                "credentials_file": None,
                "credentials_type": "adc",
                "project_id": pid,
                "region": self._region,
                "credentials_b64": "",
            }
        except Exception:
            return None

    def collect_credentials(self) -> dict[str, Any]:
        """Prompt for service account JSON path, project ID, and region."""
        print("\nGCP credentials not found.")
        print("Options:")
        print("  A) Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json")
        print("  B) Run: gcloud auth application-default login")
        print("  C) Enter a service account JSON file path below.\n")

        sa_path = input("  Path to service account JSON (or Enter to use ADC): ").strip()
        if sa_path and not Path(sa_path).expanduser().exists():
            print(f"Error: File not found: {sa_path}", file=sys.stderr)
            sys.exit(1)

        project_id = self._project_id or input("  GCP Project ID: ").strip()
        if not project_id:
            print("Error: GCP Project ID is required.", file=sys.stderr)
            sys.exit(1)

        if sa_path:
            project_id = project_id or self._read_project_from_sa(
                str(Path(sa_path).expanduser())
            )

        region = self._region or input(f"  Region [{_DEFAULT_REGION}]: ").strip() or _DEFAULT_REGION

        resolved_sa = str(Path(sa_path).expanduser()) if sa_path else None
        return {
            "credentials_file": resolved_sa,
            "credentials_type": "service_account" if sa_path else "adc",
            "project_id": project_id,
            "region": region,
            "credentials_b64": self._encode_sa_file(resolved_sa) if resolved_sa else "",
        }

    # ── Database provisioning ──────────────────────────────────────────────────

    def provision_database(
        self,
        spec: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Create a Cloud SQL PostgreSQL 15 instance (db-f1-micro).
        Returns (remote_database_url, resource_descriptors).
        """
        creds_info = self._credentials
        project_id: str = creds_info["project_id"]
        region: str = creds_info["region"]

        gcp_creds = self._load_credentials(creds_info)

        print("  [GCP] Checking required APIs are enabled...")
        self._ensure_apis_enabled(project_id, gcp_creds)

        tags = self.build_tags(project_name, deployment_id, spec)
        labels = self._normalise_labels(tags)

        try:
            from googleapiclient.discovery import build as gapi_build
        except ImportError:
            print(
                "\nError: google-api-python-client is not installed.\n"
                "Run: pip install google-api-python-client",
                file=sys.stderr,
            )
            sys.exit(1)

        sqladmin = gapi_build("sqladmin", "v1", credentials=gcp_creds)

        instance_name = f"{project_name}-db"
        db_name = project_name.replace("-", "_")
        db_password = secrets.token_urlsafe(16)

        print(f"  [GCP] Creating Cloud SQL instance '{instance_name}' (db-f1-micro, PostgreSQL 15)...")
        print(f"        This typically takes 5–10 minutes. Please wait...")

        self._ensure_cloud_sql_instance(sqladmin, project_id, instance_name, region, labels)

        print(f"  [GCP] Waiting for Cloud SQL instance to be ready...")
        public_ip = self._wait_for_cloud_sql(sqladmin, project_id, instance_name)

        print(f"  [GCP] Creating database '{db_name}' and user 'postgres'...")
        self._create_database(sqladmin, project_id, instance_name, db_name)
        self._create_db_user(sqladmin, project_id, instance_name, "postgres", db_password)

        db_url = f"postgresql://postgres:{db_password}@{public_ip}:5432/{db_name}"
        print(f"  [GCP] Cloud SQL instance public IP: {public_ip}")
        print(
            "  [GCP] Warning: Cloud SQL authorized networks set to 0.0.0.0/0.\n"
            "         Restrict to specific IPs for production workloads."
        )

        resources = [
            {
                "type": "cloud_sql_instance",
                "id": instance_name,
                "project": project_id,
                "region": region,
                "public_ip": public_ip,
            }
        ]
        return db_url, resources

    # ── Main deploy ────────────────────────────────────────────────────────────

    def deploy(
        self,
        spec: dict[str, Any],
        image_tag: str,
        env_vars: dict[str, str],
        deployment_id: str,
    ) -> dict[str, Any]:
        creds_info = self._credentials
        project_id: str = creds_info["project_id"]
        region: str = creds_info["region"]
        project_name = self.slug(spec)
        tags = self.build_tags(project_name, deployment_id, spec)
        labels = self._normalise_labels(tags)

        ar_host = f"{region}-docker.pkg.dev"
        image_uri = f"{ar_host}/{project_id}/{_AR_REPO_ID}/{project_name}:latest"

        print(f"  [GCP] Project: {project_id}  Region: {region}")

        gcp_creds = self._load_credentials(creds_info)
        self._gcp_creds = gcp_creds

        # Ensure the Artifact Registry repository exists
        print(f"  [GCP] Ensuring Artifact Registry repository '{_AR_REPO_ID}'...")
        self._ensure_artifact_registry_repo(gcp_creds, project_id, region)

        # Push to Artifact Registry
        print(f"  [GCP] Pushing image to {image_uri}...")
        self._push_to_registry(creds_info, image_tag, image_uri, ar_host)

        # Deploy to Cloud Run
        print(f"  [GCP] Deploying to Cloud Run service '{project_name}'...")
        service_url = self._deploy_cloud_run(
            gcp_creds, project_id, region, project_name, image_uri, env_vars, labels
        )

        # Allow unauthenticated access
        print(f"  [GCP] Setting IAM policy (allow unauthenticated)...")
        self._allow_unauthenticated(gcp_creds, project_id, region, project_name)

        resources = [
            {
                "type": "cloud_run_service",
                "id": project_name,
                "url": service_url or "pending",
                "project": project_id,
                "region": region,
            },
            {
                "type": "artifact_registry_image",
                "id": image_uri,
                "url": f"https://{region}-docker.pkg.dev/{project_id}/{_AR_REPO_ID}/{project_name}",
            },
        ]

        from core.deployment_state import DeploymentState
        return DeploymentState.make_record(
            provider="gcp",
            region=region,
            endpoint=service_url or "pending",
            image_uri=image_uri,
            resources=resources,
            tags=tags,
        )

    # ── CI/CD workflow generation ──────────────────────────────────────────────

    def generate_deploy_workflow(
        self,
        project_name: str,
        record: dict[str, Any],
    ) -> str:
        """Return a GitHub Actions deploy.yml for GCP Cloud Run."""
        creds_info = self._credentials
        project_id = creds_info.get("project_id", "YOUR_GCP_PROJECT_ID")
        region = creds_info.get("region", _DEFAULT_REGION)

        ar_host = f"{region}-docker.pkg.dev"
        image_uri = f"{ar_host}/{project_id}/{_AR_REPO_ID}/{project_name}:latest"

        return f"""\
name: Deploy to GCP Cloud Run

on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]

jobs:
  deploy:
    if: ${{{{ github.event.workflow_run.conclusion == 'success' }}}}
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Authenticate to Google Cloud
        uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{{{ secrets.GCP_CREDENTIALS }}}}

      - name: Set up Cloud SDK
        uses: google-github-actions/setup-gcloud@v2

      - name: Configure Docker for Artifact Registry
        run: gcloud auth configure-docker {ar_host} --quiet

      - name: Build and push image
        run: |
          docker build -t {image_uri} .
          docker push {image_uri}

      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy {project_name} \\
            --image {image_uri} \\
            --region {region} \\
            --platform managed \\
            --quiet
"""

    # ── Private helpers: Cloud SQL ─────────────────────────────────────────────

    def _ensure_cloud_sql_instance(
        self,
        sqladmin: Any,
        project_id: str,
        instance_name: str,
        region: str,
        labels: dict[str, str],
    ) -> None:
        """Create a Cloud SQL PostgreSQL 15 instance if it doesn't exist."""
        try:
            from googleapiclient.errors import HttpError as GApiHttpError
        except ImportError:
            GApiHttpError = Exception  # type: ignore[misc,assignment]

        try:
            sqladmin.instances().insert(
                project=project_id,
                body={
                    "name": instance_name,
                    "databaseVersion": "POSTGRES_15",
                    "region": region,
                    "settings": {
                        "tier": "db-f1-micro",
                        "ipConfiguration": {
                            "ipv4Enabled": True,
                            "authorizedNetworks": [
                                {"value": "0.0.0.0/0", "name": "allow-all-migration"}
                            ],
                        },
                        "userLabels": labels,
                    },
                },
            ).execute()
        except GApiHttpError as exc:
            if exc.resp.status == 409:
                print(f"  [GCP] Cloud SQL instance '{instance_name}' already exists — reusing.")
            else:
                hint = self._api_not_enabled_hint(exc, project_id)
                if hint:
                    print(f"\n  [GCP] {hint}", file=sys.stderr)
                else:
                    print(f"\nCloud SQL instance creation failed: {exc}", file=sys.stderr)
                sys.exit(1)
        except Exception as exc:
            print(f"\nCloud SQL instance creation failed: {exc}", file=sys.stderr)
            sys.exit(1)

    def _wait_for_cloud_sql(
        self, sqladmin: Any, project_id: str, instance_name: str
    ) -> str:
        """Poll until the instance is RUNNABLE. Returns public IP."""
        _TERMINAL_STATES = {"FAILED", "SUSPENDED", "MAINTENANCE"}
        deadline = time.time() + _CLOUDSQL_WAIT_TIMEOUT_S
        while time.time() < deadline:
            try:
                inst = sqladmin.instances().get(
                    project=project_id, instance=instance_name
                ).execute()
                state = inst.get("state", "")
                if state in _TERMINAL_STATES:
                    print(
                        f"\n  [GCP] Cloud SQL instance entered terminal state '{state}'.\n"
                        "  Check your GCP quota, billing, and region availability.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                if state == "RUNNABLE":
                    for addr in inst.get("ipAddresses", []):
                        if addr.get("type") == "PRIMARY":
                            return addr["ipAddress"]
                    # RUNNABLE but no PRIMARY IP yet — keep polling
                    print(
                        f"  [GCP] Cloud SQL RUNNABLE but no public IP yet — waiting...",
                        end="\r",
                        flush=True,
                    )
                else:
                    print(f"  [GCP] Cloud SQL state: {state} — waiting...", end="\r", flush=True)
            except Exception:
                pass
            time.sleep(_CLOUDSQL_POLL_INTERVAL_S)
        print(
            f"\n  [GCP] Cloud SQL instance did not become RUNNABLE within "
            f"{_CLOUDSQL_WAIT_TIMEOUT_S}s.",
            file=sys.stderr,
        )
        sys.exit(1)

    def _create_database(
        self, sqladmin: Any, project_id: str, instance_name: str, db_name: str
    ) -> None:
        try:
            from googleapiclient.errors import HttpError as GApiHttpError
        except ImportError:
            GApiHttpError = Exception  # type: ignore[misc,assignment]

        try:
            sqladmin.databases().insert(
                project=project_id,
                instance=instance_name,
                body={"name": db_name},
            ).execute()
        except GApiHttpError as exc:
            if exc.resp.status == 409:
                return
            print(f"  [GCP] Warning: could not create database '{db_name}': {exc}")
        except Exception as exc:
            print(f"  [GCP] Warning: could not create database '{db_name}': {exc}")

    def _create_db_user(
        self,
        sqladmin: Any,
        project_id: str,
        instance_name: str,
        username: str,
        password: str,
    ) -> None:
        """
        Set the password for a Cloud SQL database user.

        Cloud SQL for PostgreSQL always pre-creates the `postgres` superuser with
        no password, so `users.insert` for that user always returns 409. We skip
        straight to `users.update` and retry up to 3 times to handle transient
        failures. A short sleep after success gives Cloud SQL time to propagate
        the credential change before Prisma attempts to connect.
        """
        try:
            from googleapiclient.errors import HttpError as GApiHttpError
        except ImportError:
            GApiHttpError = Exception  # type: ignore[misc,assignment]

        _MAX_ATTEMPTS = 3
        _RETRY_SLEEP_S = 8

        # Always try update first — the built-in postgres user already exists.
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                sqladmin.users().update(
                    project=project_id,
                    instance=instance_name,
                    name=username,
                    body={"name": username, "password": password},
                ).execute()
                print(f"  [GCP] Password set for database user '{username}'.")
                # Allow Cloud SQL time to propagate the credential change.
                time.sleep(8)
                return
            except GApiHttpError as exc:
                if exc.resp.status == 404:
                    # User genuinely doesn't exist — fall through to insert below.
                    break
                print(
                    f"  [GCP] Password update attempt {attempt}/{_MAX_ATTEMPTS} failed "
                    f"(HTTP {exc.resp.status}): {exc}"
                )
            except Exception as exc:
                print(
                    f"  [GCP] Password update attempt {attempt}/{_MAX_ATTEMPTS} failed: {exc}"
                )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_SLEEP_S)

        # User did not exist — create it from scratch.
        try:
            sqladmin.users().insert(
                project=project_id,
                instance=instance_name,
                body={"name": username, "password": password},
            ).execute()
            print(f"  [GCP] Database user '{username}' created.")
            time.sleep(8)
        except GApiHttpError as exc:
            if exc.resp.status == 409:
                # Raced with another create — password was set by the earlier update loop.
                return
            print(
                f"\n  [GCP] Fatal: could not create DB user '{username}': {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as exc:
            print(
                f"\n  [GCP] Fatal: could not create DB user '{username}': {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

    # ── Private helpers: Cloud Run / GCR ──────────────────────────────────────

    def _load_credentials(self, creds_info: dict[str, Any]) -> Any:
        if creds_info["credentials_type"] == "service_account" and creds_info["credentials_file"]:
            from google.oauth2 import service_account
            return service_account.Credentials.from_service_account_file(
                creds_info["credentials_file"],
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        import google.auth
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return creds

    def _ensure_artifact_registry_repo(
        self, gcp_creds: Any, project_id: str, region: str
    ) -> None:
        """
        Create the Artifact Registry Docker repository and wait until it is ready.

        Checks for existence first (GET), so repeated runs are fast. Repository
        creation is a long-running operation — we poll until it is DONE before
        returning so the subsequent docker push never hits a 'not found' race.
        """
        try:
            from googleapiclient.discovery import build as gapi_build
            from googleapiclient.errors import HttpError as GApiHttpError
        except ImportError:
            return

        try:
            ar = gapi_build("artifactregistry", "v1", credentials=gcp_creds)
        except Exception as exc:
            print(f"  [GCP] Warning: could not initialise Artifact Registry client: {exc}")
            return

        repo_name = (
            f"projects/{project_id}/locations/{region}/repositories/{_AR_REPO_ID}"
        )

        # Fast path: repo already exists
        try:
            ar.projects().locations().repositories().get(name=repo_name).execute()
            print(f"  [GCP] Artifact Registry repository '{_AR_REPO_ID}' already exists.")
            return
        except GApiHttpError as exc:
            if exc.resp.status != 404:
                print(f"  [GCP] Warning: could not check Artifact Registry repo: {exc}")
                return
        except Exception as exc:
            print(f"  [GCP] Warning: could not check Artifact Registry repo: {exc}")
            return

        # Repo does not exist — create it and wait for the LRO to finish.
        print(f"  [GCP] Creating Artifact Registry repository '{_AR_REPO_ID}'...")
        try:
            op = ar.projects().locations().repositories().create(
                parent=f"projects/{project_id}/locations/{region}",
                repositoryId=_AR_REPO_ID,
                body={
                    "format": "DOCKER",
                    "description": "Developable deployment images",
                },
            ).execute()
        except GApiHttpError as exc:
            if exc.resp.status == 409:
                print(f"  [GCP] Artifact Registry repository '{_AR_REPO_ID}' already exists.")
                return
            elif exc.resp.status == 403:
                print(
                    f"\n  [GCP] Cannot create Artifact Registry repository — permission denied.\n"
                    "  Grant the service account the 'Artifact Registry Administrator' role, or\n"
                    "  create the repository manually and re-run:\n"
                    f"    gcloud artifacts repositories create {_AR_REPO_ID} \\\n"
                    f"      --repository-format=docker --location={region} \\\n"
                    f"      --project={project_id}",
                    file=sys.stderr,
                )
                sys.exit(1)
            else:
                print(
                    f"\n  [GCP] Artifact Registry repository creation failed: {exc}",
                    file=sys.stderr,
                )
                sys.exit(1)
        except Exception as exc:
            print(
                f"\n  [GCP] Artifact Registry repository creation failed: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Poll the long-running operation until DONE (typically <10s)
        op_name = op.get("name", "")
        if op_name:
            for _ in range(30):
                time.sleep(3)
                try:
                    status = (
                        ar.projects()
                        .locations()
                        .operations()
                        .get(name=op_name)
                        .execute()
                    )
                    if status.get("done"):
                        if "error" in status:
                            print(
                                f"\n  [GCP] Artifact Registry repo creation failed: "
                                f"{status['error']}",
                                file=sys.stderr,
                            )
                            sys.exit(1)
                        break
                except Exception:
                    pass
        print(f"  [GCP] Artifact Registry repository '{_AR_REPO_ID}' ready.")

    def _push_to_registry(
        self, creds_info: dict[str, Any], local_tag: str, image_uri: str, ar_host: str
    ) -> None:
        """Tag and push a local Docker image to Artifact Registry."""
        if creds_info["credentials_file"]:
            key_json = Path(creds_info["credentials_file"]).read_text()
            result = subprocess.run(
                ["docker", "login", "-u", "_json_key", "--password-stdin", f"https://{ar_host}"],
                input=key_json.encode(),
                capture_output=True,
            )
            if result.returncode != 0:
                print(
                    f"\n  [GCP] Docker login to Artifact Registry failed:\n"
                    f"  {result.stderr.decode('utf-8', errors='replace')}",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            gcloud_result = subprocess.run(
                ["gcloud", "auth", "configure-docker", ar_host, "--quiet"],
                capture_output=True,
            )
            if gcloud_result.returncode != 0:
                print(
                    f"\n  [GCP] gcloud auth configure-docker {ar_host} failed.\n"
                    "  Ensure gcloud is installed and you have run:\n"
                    "    gcloud auth application-default login\n"
                    f"  Error: {gcloud_result.stderr.decode('utf-8', errors='replace').strip()}",
                    file=sys.stderr,
                )
                sys.exit(1)

        self._run(["docker", "tag", local_tag, image_uri])
        self._run(["docker", "push", image_uri])

    def _deploy_cloud_run(
        self,
        gcp_creds: Any,
        project_id: str,
        region: str,
        service_name: str,
        image_uri: str,
        env_vars: dict[str, str],
        labels: dict[str, str],
    ) -> str | None:
        try:
            from google.cloud import run_v2
        except ImportError:
            print(
                "\nError: google-cloud-run is not installed.\n"
                "Run: pip install google-cloud-run",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            from google.api_core.exceptions import NotFound as GCPNotFound
        except ImportError:
            GCPNotFound = Exception  # type: ignore[misc,assignment]

        client = run_v2.ServicesClient(credentials=gcp_creds)
        parent = f"projects/{project_id}/locations/{region}"
        service_path = f"{parent}/services/{service_name}"

        # Cloud Run sets these automatically; passing them causes a 400.
        _RESERVED = {"PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION"}
        env_list = [
            run_v2.EnvVar(name=k, value=v)
            for k, v in env_vars.items()
            if k not in _RESERVED
        ]
        container = run_v2.Container(
            image=image_uri,
            ports=[run_v2.ContainerPort(container_port=_CONTAINER_PORT)],
            env=env_list,
            resources=run_v2.ResourceRequirements(
                limits={"cpu": "1", "memory": "512Mi"},
            ),
        )
        service = run_v2.Service(
            template=run_v2.RevisionTemplate(
                containers=[container],
                scaling=run_v2.RevisionScaling(min_instance_count=0, max_instance_count=3),
            ),
            labels=labels,
        )

        try:
            try:
                client.get_service(name=service_path)
                service.name = service_path
                op = client.update_service(service=service)
            except GCPNotFound:
                op = client.create_service(
                    parent=parent, service=service, service_id=service_name
                )
        except Exception as exc:
            msg = str(exc)
            if "SERVICE_DISABLED" in msg or "has not been used" in msg or "it is disabled" in msg:
                print(
                    f"\n  [GCP] Cloud Run API is not enabled in project '{project_id}'.\n"
                    f"  Enable it at: https://console.cloud.google.com/apis/library/"
                    f"run?project={project_id}\n"
                    "  Then re-run the deployment.",
                    file=sys.stderr,
                )
            else:
                print(f"\n  [GCP] Cloud Run service creation failed: {exc}", file=sys.stderr)
            sys.exit(1)

        try:
            result = op.result(timeout=300)
        except Exception as exc:
            print(
                f"\n  [GCP] Cloud Run deployment timed out or failed: {exc}\n"
                "  Check the Cloud Run console for service status.",
                file=sys.stderr,
            )
            sys.exit(1)
        return result.uri

    def _allow_unauthenticated(
        self, gcp_creds: Any, project_id: str, region: str, service_name: str
    ) -> None:
        try:
            from google.cloud import run_v2
            from google.iam.v1 import iam_policy_pb2, policy_pb2
        except ImportError:
            return

        client = run_v2.ServicesClient(credentials=gcp_creds)
        resource = f"projects/{project_id}/locations/{region}/services/{service_name}"
        try:
            policy = policy_pb2.Policy(
                bindings=[policy_pb2.Binding(role="roles/run.invoker", members=["allUsers"])]
            )
            client.set_iam_policy(
                request=iam_policy_pb2.SetIamPolicyRequest(resource=resource, policy=policy)
            )
        except Exception as exc:
            print(
                f"  [GCP] Warning: could not set unauthenticated IAM policy: {exc}\n"
                "  The service may require authentication. Set it manually:\n"
                f"  gcloud run services add-iam-policy-binding {service_name} \\\n"
                f"    --region={region} --member=allUsers --role=roles/run.invoker"
            )

    def _ensure_apis_enabled(self, project_id: str, gcp_creds: Any) -> None:
        """
        Enable required GCP APIs via the Service Usage API before deployment.

        If the service account lacks serviceusage.services.enable permission,
        falls back to printing console URLs for manual enablement and continues
        (individual API calls will fail with clearer messages if still disabled).
        """
        try:
            from googleapiclient.discovery import build as gapi_build
            from googleapiclient.errors import HttpError as GApiHttpError
        except ImportError:
            return

        try:
            svc = gapi_build("serviceusage", "v1", credentials=gcp_creds)
        except Exception:
            return

        # Check which APIs are not yet enabled
        disabled: list[str] = []
        for api in _REQUIRED_APIS:
            try:
                state = (
                    svc.services()
                    .get(name=f"projects/{project_id}/services/{api}")
                    .execute()
                    .get("state", "")
                )
                if state != "ENABLED":
                    disabled.append(api)
            except Exception:
                disabled.append(api)

        if not disabled:
            return

        print(f"  [GCP] Enabling required APIs: {', '.join(disabled)}...")
        try:
            op = (
                svc.services()
                .batchEnable(
                    parent=f"projects/{project_id}",
                    body={"serviceIds": disabled},
                )
                .execute()
            )
            # Poll until the enablement operation completes (usually <30s)
            op_name = op.get("name", "")
            if op_name:
                for _ in range(30):
                    time.sleep(3)
                    status = svc.operations().get(name=op_name).execute()
                    if status.get("done"):
                        break
            print("  [GCP] APIs enabled. Waiting a moment for propagation...")
            time.sleep(5)
        except GApiHttpError as exc:
            if exc.resp.status in (403, 401):
                print(
                    "\n  [GCP] Cannot auto-enable APIs — service account lacks "
                    "serviceusage.services.enable permission.\n"
                    "  Enable these APIs manually in the GCP Console, then re-run:\n"
                )
                for api in disabled:
                    name = api.split(".")[0]
                    print(
                        f"    https://console.cloud.google.com/apis/library/"
                        f"{name}?project={project_id}"
                    )
                print()
            # Non-fatal: let individual API calls produce their own errors

    def _api_not_enabled_hint(self, exc: Any, project_id: str) -> str | None:
        """
        If exc is a 403 accessNotConfigured HttpError, return a formatted hint
        string with the console enable URL. Returns None for other errors.
        """
        try:
            if exc.resp.status != 403:
                return None
            details = json.loads(exc.content.decode()).get("error", {}).get("errors", [])
            for d in details:
                if d.get("reason") == "accessNotConfigured":
                    # Extract API name from the extended help URL if present
                    help_url = d.get("extendedHelp", "")
                    api_match = re.search(r"apis/api/([^/]+)/", d.get("message", ""))
                    api_id = api_match.group(1) if api_match else ""
                    if api_id:
                        return (
                            f"  GCP API not enabled: {api_id}\n"
                            f"  Enable it at: https://console.cloud.google.com/apis/library/"
                            f"{api_id.split('.')[0]}?project={project_id}\n"
                            "  Then re-run the deployment."
                        )
                    return (
                        f"  GCP API not enabled. Enable it and retry.\n"
                        f"  {help_url}"
                    )
        except Exception:
            pass
        return None

    def _read_project_from_sa(self, sa_path: str) -> str:
        try:
            return json.loads(Path(sa_path).read_text()).get("project_id", "")
        except Exception:
            return ""

    def _encode_sa_file(self, sa_path: str) -> str:
        """Return the base64-encoded contents of a service account JSON file."""
        try:
            return base64.b64encode(Path(sa_path).read_bytes()).decode()
        except Exception:
            return ""

    def _normalise_labels(self, tags: dict[str, str]) -> dict[str, str]:
        """GCP label keys/values must be lowercase [a-z][a-z0-9_-]* ≤63 chars."""
        result: dict[str, str] = {}
        for k, v in tags.items():
            key = re.sub(r"[^a-z0-9_-]", "-", k.lower())[:63]
            val = re.sub(r"[^a-z0-9_-]", "-", v.lower())[:63]
            # Keys and values must start with a lowercase letter
            if key and not key[0].isalpha():
                key = "x" + key[:62]
            if val and not val[0].isalpha():
                val = "x" + val[:62]
            result[key] = val
        return result

    def _run(self, cmd: list[str]) -> None:
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            print(
                f"\nCommand failed: {' '.join(cmd)}\n{result.stderr.decode()}",
                file=sys.stderr,
            )
            sys.exit(1)
