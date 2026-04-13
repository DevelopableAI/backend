"""
GCP Cloud Run deployment provider.

Deployment flow
───────────────
1. Detect credentials via GOOGLE_APPLICATION_CREDENTIALS or application
   default credentials (~/.config/gcloud/application_default_credentials.json).
2. Push the image to Google Container Registry (gcr.io/<project>/<name>).
3. Deploy to Cloud Run via the google-cloud-run SDK.
4. Allow unauthenticated requests (IAM binding for allUsers).
5. Apply labels for resource tracking.
6. Return a deployment record.

Database note
─────────────
DATABASE_URL must be set in <out_dir>/.env before deploying. For managed
Postgres on GCP consider Cloud SQL; supply the connection string as
DATABASE_URL (or use the Unix socket path when running inside GCP).

Label constraints
─────────────────
GCP labels must match [a-z][a-z0-9_-]* and be ≤63 chars.
We normalise tag keys/values to lowercase and replace invalid characters.
"""

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import BaseProvider


_DEFAULT_REGION = "us-central1"
_CONTAINER_PORT = 3000


class GCPProvider(BaseProvider):
    """Deploy to GCP Cloud Run using the google-cloud-run Python SDK."""

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

    # ── Credential handling ────────────────────────────────────────────────────

    def detect_credentials(self) -> dict[str, Any] | None:
        """
        Try GOOGLE_APPLICATION_CREDENTIALS (service account JSON path) and
        application default credentials. Also reads GOOGLE_CLOUD_PROJECT.
        Returns None if either credentials or project ID are unavailable.
        """
        try:
            import google.auth
            from google.auth.exceptions import DefaultCredentialsError
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

        # Try application default credentials
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

        project_id = (
            self._project_id
            or input("  GCP Project ID: ").strip()
        )
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

        # 1. Load credentials object
        gcp_creds = self._load_credentials(creds_info)

        # 2. Push image to GCR
        print(f"  [GCP] Pushing image to {image_uri}...")
        self._push_to_gcr(creds_info, image_tag, image_uri)

        # 3. Deploy to Cloud Run
        print(f"  [GCP] Deploying to Cloud Run service '{project_name}'...")
        service_url = self._deploy_cloud_run(
            gcp_creds, project_id, region, project_name, image_uri, env_vars, labels
        )

        # 4. Allow unauthenticated access
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

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load_credentials(self, creds_info: dict[str, Any]) -> Any:
        """Return a google.auth credentials object."""
        if creds_info["credentials_type"] == "service_account" and creds_info["credentials_file"]:
            from google.oauth2 import service_account
            return service_account.Credentials.from_service_account_file(
                creds_info["credentials_file"],
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        # Application default credentials
        import google.auth
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return creds

    def _push_to_gcr(
        self, creds_info: dict[str, Any], local_tag: str, image_uri: str
    ) -> None:
        """Authenticate Docker to GCR and push the image."""
        if creds_info["credentials_file"]:
            # Use service account JSON key for docker login
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
            # Use gcloud to configure docker (assumes gcloud CLI is available)
            result = subprocess.run(
                ["gcloud", "auth", "configure-docker", "--quiet"],
                capture_output=True,
            )
            # Non-fatal if gcloud is absent — docker push may still work if already configured

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
        """
        Create or replace the Cloud Run service.
        Returns the service URL or None on error.
        """
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

        env_list = [
            run_v2.EnvVar(name=k, value=v) for k, v in env_vars.items()
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
            # Try to get existing service first
            existing = client.get_service(name=service_path)
            # Update existing service
            service.name = service_path
            op = client.update_service(service=service)
        except Exception:
            # Create new service
            op = client.create_service(
                parent=parent,
                service=service,
                service_id=service_name,
            )

        result = op.result(timeout=300)
        return result.uri

    def _allow_unauthenticated(
        self, gcp_creds: Any, project_id: str, region: str, service_name: str
    ) -> None:
        """Set IAM policy to allow unauthenticated invocations."""
        try:
            from google.cloud import run_v2
            from google.iam.v1 import iam_policy_pb2, policy_pb2
        except ImportError:
            return  # Non-fatal — user can set this manually

        client = run_v2.ServicesClient(credentials=gcp_creds)
        resource = f"projects/{project_id}/locations/{region}/services/{service_name}"
        try:
            policy = policy_pb2.Policy(
                bindings=[
                    policy_pb2.Binding(
                        role="roles/run.invoker",
                        members=["allUsers"],
                    )
                ]
            )
            client.set_iam_policy(
                request=iam_policy_pb2.SetIamPolicyRequest(
                    resource=resource,
                    policy=policy,
                )
            )
        except Exception:
            print(
                "  [GCP] Warning: could not set unauthenticated IAM policy. "
                "Set it manually in Cloud Console if needed."
            )

    def _read_project_from_sa(self, sa_path: str) -> str:
        """Extract project_id from a service account JSON file."""
        try:
            data = json.loads(Path(sa_path).read_text())
            return data.get("project_id", "")
        except Exception:
            return ""

    def _normalise_labels(self, tags: dict[str, str]) -> dict[str, str]:
        """
        GCP label keys/values must match [a-z0-9_-]* and be ≤63 chars.
        Lowercase everything and replace invalid chars with '-'.
        """
        import re
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
