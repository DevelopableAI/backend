import json
import os
import secrets as _secrets
import sys
from pathlib import Path
from typing import Any

from core.deployment_state import DeploymentState
from core.docker_client import DockerClient
from core.provider_deployer import ProviderDeployer


class GCPDeployer(ProviderDeployer):
    """
    Deploys to GCP using a hybrid Python SDK + Terraform import strategy.

    Flow:
      1. Enable GCP APIs, kick off Cloud SQL (~8-10 min), create Artifact Registry in parallel.
      2. Apply Prisma schema to Cloud SQL public IP.
      3. Build Docker image locally.
      4. Push image to Artifact Registry.
      5. Deploy Cloud Run service via SDK.
      6. Set IAM (allow unauthenticated).
      7. Write tfvars.
      8. terraform import × 10 resources + reconciliation apply.
    """

    def deploy(
        self,
        spec: dict[str, Any],
        provider: Any,
        creds: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> dict[str, Any]:
        import socket

        tf_dir = self.out_dir / "terraform"
        project_id = creds.get("project_id", "")
        region = creds.get("region", "us-central1")

        env_file_vars = self._read_env_file()
        db_password = env_file_vars.get("DB_PASSWORD") or _secrets.token_urlsafe(24)
        jwt_secret = env_file_vars.get("JWT_SECRET") or _secrets.token_urlsafe(32)

        db_name = project_name.replace("-", "_")
        ar_host = f"{region}-docker.pkg.dev"
        image_uri = f"{ar_host}/{project_id}/developable/{project_name}:latest"

        print(
            f"\n  ┌─ Step 3/8 — Provision GCP infrastructure via Python SDK ────────────\n"
            f"  │  ETA: ~8–10 min  (Cloud SQL 8–10 min; Artifact Registry ~30s in parallel)\n"
            f"  │  Monitor Cloud SQL:  https://console.cloud.google.com/sql/instances?project={project_id}\n"
            f"  │  Monitor AR:         https://console.cloud.google.com/artifacts?project={project_id}\n"
            f"  └────────────────────────────────────────────────────────────────────────"
        )
        cloud_sql_ip = self._provision(provider, creds, project_name, db_password, project_id, region)

        db_url = f"postgresql://postgres:{db_password}@{cloud_sql_ip}:5432/{db_name}"
        self._wait_for_db_tcp(cloud_sql_ip, 5432)
        print("\n  [Step 4/8] Applying Prisma schema to Cloud SQL...")
        provider.apply_schema(db_url)

        local_tag = f"developable/{project_name}:latest"
        print(f"\n  ┌─ Step 5/8 — Build Docker image  (ETA: ~3 min) ─────────────────────")
        print(f"  └─ Image tag: {local_tag}")
        self.docker.build(local_tag)

        print(f"\n  ┌─ Step 6/8 — Push image to Artifact Registry  (ETA: ~1 min) ────────")
        print(f"  └─ Target:    {image_uri}")
        provider._push_to_registry(creds, local_tag, image_uri, ar_host)

        print(
            f"\n  ┌─ Step 7/8 — Deploy Cloud Run service  (ETA: ~1 min) ───────────────\n"
            f"  │  Monitor: https://console.cloud.google.com/run?project={project_id}\n"
            f"  └────────────────────────────────────────────────────────────────────────"
        )
        gcp_creds = provider._load_credentials(creds)
        env_vars = {
            "NODE_ENV": "production", "PORT": "3000",
            "DATABASE_URL": db_url, "JWT_SECRET": jwt_secret,
        }
        labels = provider._normalise_labels(provider.build_tags(project_name, deployment_id, spec))
        cloud_run_url = provider._deploy_cloud_run(
            gcp_creds, project_id, region, project_name, image_uri, env_vars, labels
        )

        print(f"\n  [GCP] Setting IAM policy (allow unauthenticated)...")
        provider._allow_unauthenticated(gcp_creds, project_id, region, project_name)

        tfvars_path = tf_dir / "terraform.auto.tfvars.json"
        tfvars_path.write_text(json.dumps({
            "project_name": project_name, "gcp_project": project_id,
            "gcp_region": region, "db_password": db_password,
            "jwt_secret": jwt_secret, "image_tag": "latest",
        }, indent=2))

        endpoint = cloud_run_url or f"https://{project_name}.run.app"
        print(f"\n  ✓ Deployed — endpoint: {endpoint}")

        record = DeploymentState.make_record(
            provider="gcp", region=region, endpoint=endpoint, image_uri=image_uri,
            resources=[{"type": "terraform_managed", "id": project_name, "arn": None}],
            tags=provider.build_tags(project_name, deployment_id, spec),
        )

        print(
            f"\n  ┌─ Step 8/8 — Terraform import + reconciliation apply  (ETA: ~2 min) ──\n"
            f"  │  Imports 10 resources into state so terraform destroy/apply/plan work.\n"
            f"  └────────────────────────────────────────────────────────────────────────"
        )
        tf_env = {**os.environ, "GOOGLE_CLOUD_PROJECT": project_id}
        if creds.get("credentials_file"):
            tf_env["GOOGLE_APPLICATION_CREDENTIALS"] = creds["credentials_file"]
        elif creds.get("credentials_type") == "adc":
            tf_env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)

        try:
            self._terraform_import(creds, project_name, project_id, region, db_name, tf_env)
        except SystemExit:
            print(
                "\n  Warning: Step 8 (Terraform state reconciliation) failed — "
                "the app is live but terraform destroy/apply/plan won't cover all resources.",
                file=sys.stderr,
            )

        return record

    def _provision(
        self,
        provider: Any,
        creds: dict[str, Any],
        project_name: str,
        db_password: str,
        project_id: str,
        region: str,
    ) -> str:
        """Provision Cloud SQL and Artifact Registry via the GCP Python SDK. Returns Cloud SQL IP."""
        try:
            from googleapiclient.discovery import build as gapi_build
        except ImportError:
            print(
                "\nError: google-api-python-client is not installed.\n"
                "Run: pip install google-api-python-client",
                file=sys.stderr,
            )
            sys.exit(1)

        gcp_creds = provider._load_credentials(creds)
        sqladmin = gapi_build("sqladmin", "v1", credentials=gcp_creds)
        db_name = project_name.replace("-", "_")
        labels = provider._normalise_labels(provider.build_tags(project_name, "gcp-provision", {}))

        print(f"  [GCP] Enabling required APIs...")
        provider._ensure_apis_enabled(project_id, gcp_creds)

        print(
            f"  [GCP] Kicking off Cloud SQL provisioning (8–10 min) "
            f"— creating Artifact Registry in parallel..."
        )
        provider._ensure_cloud_sql_instance(sqladmin, project_id, project_name, region, labels)

        print(f"  [GCP] Creating Artifact Registry repository 'developable'...")
        provider._ensure_artifact_registry_repo(gcp_creds, project_id, region)

        print(f"  [GCP] Waiting for Cloud SQL to become available...", flush=True)
        public_ip = provider._wait_for_cloud_sql(sqladmin, project_id, project_name)
        print(f"  [GCP] ✓ Cloud SQL available — {public_ip}")

        print(f"  [GCP] Creating database '{db_name}' and setting postgres password...")
        provider._create_database(sqladmin, project_id, project_name, db_name)
        provider._create_db_user(sqladmin, project_id, project_name, "postgres", db_password)
        return public_ip

    def _terraform_import(
        self,
        creds: dict[str, Any],
        project_name: str,
        project_id: str,
        region: str,
        db_name: str,
        tf_env: dict[str, str],
    ) -> None:
        tf_dir = self.out_dir / "terraform"
        runner = self.tf_runner_factory(tf_dir, tf_env)
        runner.init_if_needed()
        runner.import_resources([
            ('google_project_service.apis["run.googleapis.com"]',
             f"{project_id}/run.googleapis.com"),
            ('google_project_service.apis["sqladmin.googleapis.com"]',
             f"{project_id}/sqladmin.googleapis.com"),
            ('google_project_service.apis["artifactregistry.googleapis.com"]',
             f"{project_id}/artifactregistry.googleapis.com"),
            ('google_project_service.apis["secretmanager.googleapis.com"]',
             f"{project_id}/secretmanager.googleapis.com"),
            ("google_artifact_registry_repository.main",
             f"projects/{project_id}/locations/{region}/repositories/developable"),
            ("google_sql_database_instance.main",
             f"projects/{project_id}/instances/{project_name}"),
            ("google_sql_database.main",
             f"projects/{project_id}/instances/{project_name}/databases/{db_name}"),
            ("google_sql_user.main",
             f"{project_id}/{project_name}/postgres"),
            ("google_cloud_run_v2_service.main",
             f"projects/{project_id}/locations/{region}/services/{project_name}"),
            ("google_cloud_run_v2_service_iam_member.public",
             f"projects/{project_id}/locations/{region}/services/{project_name} "
             f"roles/run.invoker allUsers"),
        ])
        runner.apply()

    @staticmethod
    def _wait_for_db_tcp(host: str, port: int, timeout_s: int = 300) -> None:
        import socket
        import time
        deadline = time.time() + timeout_s
        print(f"  Waiting for database at {host}:{port} to accept connections", end="", flush=True)
        while time.time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=5):
                    print(" ready.")
                    return
            except OSError:
                print(".", end="", flush=True)
                time.sleep(5)
        print(f"\n  Warning: database at {host}:{port} did not become reachable within {timeout_s}s.",
              file=sys.stderr)
