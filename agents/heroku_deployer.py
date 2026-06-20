import json
import os
import secrets as _secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.deployment_state import DeploymentState
from core.docker_client import DockerClient
from core.provider_deployer import ProviderDeployer


class HerokuDeployer(ProviderDeployer):
    """
    Deploys to Heroku using Terraform apply + Docker container release.

    Flow:
      1. Write terraform.auto.tfvars.json.
      2. terraform init + apply — creates heroku_app, heroku_addon (postgres), config_association.
      3. Parse terraform output for app_url and database_url.
      4. Apply Prisma schema to Heroku Postgres.
      5. Build Docker image locally.
      6. Docker login to registry.heroku.com, push image, release via Formation API.
    """

    def deploy(
        self,
        spec: dict[str, Any],
        provider: Any,
        creds: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> dict[str, Any]:
        tf_dir = self.out_dir / "terraform"
        api_key = creds.get("api_key", "")

        env_file_vars = self._read_env_file()
        jwt_secret = env_file_vars.get("JWT_SECRET") or _secrets.token_urlsafe(32)

        tfvars_path = tf_dir / "terraform.auto.tfvars.json"
        tfvars_path.write_text(json.dumps({
            "project_name": project_name,
            "jwt_secret": jwt_secret,
            "heroku_region": creds.get("heroku_region", "us"),
        }, indent=2))

        tf_env = {**os.environ, "TF_VAR_heroku_api_key": api_key}
        runner = self.tf_runner_factory(tf_dir, tf_env)

        runner.init_if_needed()
        print(
            f"\n  ┌─ Step 2/6 — Terraform apply: Heroku app + Postgres addon  (ETA: ~2 min)\n"
            f"  │  Monitor: https://dashboard.heroku.com/apps\n"
            f"  └─────────────────────────────────────────────────────────────────────────\n"
        )
        runner.apply()

        outputs = runner.output_json()
        app_url = outputs.get("app_url", f"https://{project_name}.herokuapp.com")
        db_url = outputs.get("database_url", "")

        if not db_url:
            print(
                "\nError: Terraform output 'database_url' is empty. "
                "Run 'terraform output -json' in the terraform/ directory to diagnose.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Heroku uses postgres://, Prisma 5 requires postgresql://
        if db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]

        print("\n  Applying Prisma schema to Heroku Postgres...")
        provider.apply_schema(db_url)

        local_tag = f"developable/{project_name}:latest"
        print(f"\n  ┌─ Step 4/6 — Build Docker image  (ETA: ~3 min) ─────────────────────")
        print(f"  └─ Image tag: {local_tag}")
        self.docker.build(local_tag)

        heroku_image = f"registry.heroku.com/{project_name}/web"
        print(f"\n  ┌─ Step 5/6 — Push image + release container  (ETA: ~1 min) ─────────")
        print(f"  │  Monitor: https://dashboard.heroku.com/apps/{project_name}/activity")
        print(f"  └─ Target:  {heroku_image}")
        provider._docker_login(api_key)

        print(f"\n  Pushing image to {heroku_image}...")
        subprocess.run(["docker", "tag", local_tag, heroku_image], check=True)
        provider._docker_push_with_retry(heroku_image)

        image_id = provider._get_registry_config_digest(api_key, project_name)
        print(f"  [Heroku] Config digest: {image_id}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/vnd.heroku+json; version=3",
            "Content-Type": "application/json",
        }
        provider._api_headers = headers
        provider._app_name_resolved = project_name

        print(f"\n  Releasing web dyno...")
        provider._release(api_key, project_name, image_id)
        provider._print_release_status(headers, project_name)

        endpoint = provider._get_app_domain(headers, project_name)
        print(f"\n  ✓ Deployed — endpoint: {endpoint}")
        provider.wait_for_ready(endpoint)

        return DeploymentState.make_record(
            provider="heroku",
            region=None,
            endpoint=endpoint,
            image_uri=heroku_image,
            resources=[{"type": "terraform_managed", "id": project_name, "arn": None}],
            tags=provider.build_tags(project_name, deployment_id, spec),
        )
