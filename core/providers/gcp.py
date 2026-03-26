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

        return {
            "credentials_file": str(Path(sa_path).expanduser()) if sa_path else None,
            "credentials_type": "service_account" if sa_path else "adc",
            "project_id": project_id,
            "region": region,
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

        image_uri = f"gcr.io/{project_id}/{project_name}:latest"

        print(f"  [GCP] Project: {project_id}  Region: {region}")

        gcp_creds = self._load_credentials(creds_info)
        self._gcp_creds = gcp_creds

        # Push to GCR
        print(f"  [GCP] Pushing image to {image_uri}...")
        self._push_to_gcr(creds_info, image_tag, image_uri)

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
                "url": service_url,
                "project": project_id,
                "region": region,
            },
            {
                "type": "gcr_image",
                "id": image_uri,
                "url": f"https://gcr.io/{project_id}/{project_name}",
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

      - name: Configure Docker for GCR
        run: gcloud auth configure-docker --quiet

      - name: Build and push image
        run: |
          docker build -t gcr.io/{project_id}/{project_name}:latest .
          docker push gcr.io/{project_id}/{project_name}:latest

      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy {project_name} \\
            --image gcr.io/{project_id}/{project_name}:latest \\
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
        except Exception as exc:
            # 409 Conflict = instance already exists
            if "409" in str(exc) or "already exists" in str(exc).lower():
                print(f"  [GCP] Cloud SQL instance '{instance_name}' already exists — reusing.")
            else:
                print(f"\nCloud SQL instance creation failed: {exc}", file=sys.stderr)
                sys.exit(1)

    def _wait_for_cloud_sql(
        self, sqladmin: Any, project_id: str, instance_name: str
    ) -> str:
        """Poll until the instance is RUNNABLE. Returns public IP."""
        deadline = time.time() + _CLOUDSQL_WAIT_TIMEOUT_S
        while time.time() < deadline:
            try:
                inst = sqladmin.instances().get(
                    project=project_id, instance=instance_name
                ).execute()
                state = inst.get("state", "")
                if state == "RUNNABLE":
                    for addr in inst.get("ipAddresses", []):
                        if addr.get("type") == "PRIMARY":
                            return addr["ipAddress"]
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
            sqladmin.databases().insert(
                project=project_id,
                instance=instance_name,
                body={"name": db_name},
            ).execute()
        except Exception as exc:
            if "already exists" in str(exc).lower() or "409" in str(exc):
                return
            print(f"  [GCP] Warning: could not create database '{db_name}': {exc}")

    def _create_db_user(
        self,
        sqladmin: Any,
        project_id: str,
        instance_name: str,
        username: str,
        password: str,
    ) -> None:
        try:
            sqladmin.users().insert(
                project=project_id,
                instance=instance_name,
                body={"name": username, "password": password},
            ).execute()
        except Exception as exc:
            if "already exists" in str(exc).lower() or "409" in str(exc):
                # Update password for existing user
                try:
                    sqladmin.users().update(
                        project=project_id,
                        instance=instance_name,
                        name=username,
                        body={"password": password},
                    ).execute()
                except Exception:
                    pass
            else:
                print(f"  [GCP] Warning: could not create DB user '{username}': {exc}")

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

    def _push_to_gcr(
        self, creds_info: dict[str, Any], local_tag: str, image_uri: str
    ) -> None:
        if creds_info["credentials_file"]:
            key_json = Path(creds_info["credentials_file"]).read_text()
            result = subprocess.run(
                ["docker", "login", "-u", "_json_key", "--password-stdin", "https://gcr.io"],
                input=key_json.encode(),
                capture_output=True,
            )
            if result.returncode != 0:
                print(
                    f"\nDocker login to GCR failed:\n{result.stderr.decode()}",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            subprocess.run(
                ["gcloud", "auth", "configure-docker", "--quiet"],
                capture_output=True,
            )

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

        client = run_v2.ServicesClient(credentials=gcp_creds)
        parent = f"projects/{project_id}/locations/{region}"
        service_path = f"{parent}/services/{service_name}"

        env_list = [run_v2.EnvVar(name=k, value=v) for k, v in env_vars.items()]
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
            client.get_service(name=service_path)
            service.name = service_path
            op = client.update_service(service=service)
        except Exception:
            op = client.create_service(
                parent=parent, service=service, service_id=service_name
            )

        result = op.result(timeout=300)
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
        except Exception:
            print(
                "  [GCP] Warning: could not set unauthenticated IAM policy. "
                "Set it manually in Cloud Console if needed."
            )

    def _read_project_from_sa(self, sa_path: str) -> str:
        try:
            return json.loads(Path(sa_path).read_text()).get("project_id", "")
        except Exception:
            return ""

    def _normalise_labels(self, tags: dict[str, str]) -> dict[str, str]:
        """GCP label keys/values must be lowercase [a-z0-9_-]* ≤63 chars."""
        result: dict[str, str] = {}
        for k, v in tags.items():
            key = re.sub(r"[^a-z0-9_-]", "-", k.lower())[:63]
            val = re.sub(r"[^a-z0-9_-]", "-", v.lower())[:63]
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
