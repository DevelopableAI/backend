"""
Deployment Agent.

The Deployment Agent is the fourth component of the Backend Engineer. It takes
a fully-generated Express API project (output of Developer + optionally Tester
and VersionControl) and deploys it — along with a managed database — to a cloud
provider of the user's choice.

Responsibilities
────────────────
1. Present an interactive provider menu (or accept --deploy-to via CLI).
2. Detect existing credentials; prompt for missing ones.
3. Ensure the Dockerfile exists in the output directory.
4. Provision a managed PostgreSQL database on the cloud provider.
5. Apply the Prisma schema to the remote database (npx prisma db push).
6. Build a Docker image from the output directory.
7. Inject the remote DATABASE_URL and deploy the container.
8. Record the deployment result in <out_dir>/.developable/state.json.
9. If a GitHub remote is configured, push a provider-specific deploy.yml
   GitHub Actions workflow that re-deploys after CI passes on main.
10. Run the generated test suite once against the live remote endpoint
    (no test file modifications — URL is passed as a CLI argument).

Zero LLM cost
─────────────
This agent makes no Anthropic API calls. All operations are pure Python SDK /
subprocess calls against the cloud provider APIs.

Usage (from main.py)
────────────────────
    deployer = Deployment(
        out_dir=out_dir,
        provider="aws",
        tests_dir=Path("./output/tests"),
        aws_region="us-east-1",
    )
    record = deployer.deploy(spec, api_plan)
    print(record["endpoint"])
"""

import base64
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from core.deployment_state import DeploymentState
from core.gitignore import (
    DEFAULT_GITIGNORE_CONTENT,
    ensure_required_gitignore_patterns,
)
from core.providers import PROVIDER_MAP, get_provider


_SECRETS_INSTRUCTIONS: dict[str, list[str]] = {
    "aws": [
        "AWS_ACCESS_KEY_ID       — your AWS access key",
        "AWS_SECRET_ACCESS_KEY   — your AWS secret key (keep this secret!)",
    ],
    "heroku": [
        "HEROKU_API_KEY          — your Heroku API token",
    ],
    "gcp": [
        "GCP_CREDENTIALS         — base64-encoded service account JSON key",
        "  (encode with: base64 -w0 service-account.json)",
    ],
}

# Maps provider slug → {GitHub secret name: key in credentials dict}
_PROVIDER_GITHUB_SECRETS: dict[str, dict[str, str]] = {
    "heroku": {"HEROKU_API_KEY": "api_key"},
    "aws": {
        "AWS_ACCESS_KEY_ID": "access_key",
        "AWS_SECRET_ACCESS_KEY": "secret_key",
    },
    "gcp": {"GCP_CREDENTIALS": "credentials_b64"},
}


class Deployment:
    """
    The Deployment Agent.

    Args:
        out_dir:   Path to the generated project directory.
        provider:  Cloud provider slug ("aws", "heroku", "gcp"). If None,
                   the user is prompted interactively.
        tests_dir: Directory containing the generated test suite. If None,
                   auto-detected from <out_dir>/tests/. Remote smoke tests
                   are skipped when no test suite is found.
        **kwargs:  Provider-specific configuration forwarded to the provider
                   constructor (aws_region, heroku_app, gcp_project, gcp_region).
    """

    def __init__(
        self,
        out_dir: Path,
        provider: str | None = None,
        tests_dir: Path | None = None,
        **kwargs: Any,
    ) -> None:
        self.out_dir = out_dir
        self.provider_name = provider
        self.tests_dir = tests_dir
        self.provider_kwargs = self._normalise_kwargs(kwargs)

    # ── Public API ─────────────────────────────────────────────────────────────

    def deploy(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> dict[str, Any]:
        """
        Run the full deployment pipeline.

        Order of operations:
          1. Select + configure cloud provider.
          2. Bootstrap Terraform state backend (S3+DynamoDB / GCS / no-op for Heroku).
          3. Ensure Dockerfile exists.
          4. Provision managed PostgreSQL database.
          5. Apply Prisma schema to remote database.
          6. Build Docker image.
          7. Deploy container (with remote DATABASE_URL injected).
          8. Persist deployment state.
          9. Push CI/CD deploy workflow to GitHub (if remote configured).
         10. Run remote smoke tests.

        Note: Terraform file generation (terraform/*.tf) happens before this method
        is called — in main.py between VersionControl.generate_infra() and
        VersionControl.publish(). This ensures terraform files are pushed to GitHub
        and validated by CI before deployment runs.

        Args:
            spec:     Parsed Prisma spec (from PrismaParser).
            api_plan: File plan returned by the Developer agent.

        Returns:
            A deployment record dict (also persisted to state.json).
        """
        # ── 1. Resolve + configure provider ───────────────────────────────────
        provider_name = self.provider_name or self._ask_provider()
        kwargs = self.provider_kwargs.get(provider_name, {})
        provider = get_provider(provider_name, self.out_dir, **kwargs)

        print(f"\n  Detecting {provider.display_name} credentials...")
        creds = provider.detect_credentials()
        if creds is None:
            creds = provider.collect_credentials()
        else:
            print(f"  Found existing credentials.")
        provider.configure(creds)

        project_name = provider.slug(spec)
        deployment_id = str(uuid.uuid4())

        # ── 2. Bootstrap Terraform state backend ───────────────────────────────
        # Terraform files were already generated before the GitHub push. Here we
        # create the actual remote state infrastructure (S3+DynamoDB for AWS,
        # GCS bucket for GCP) so `terraform init` can connect immediately.
        # Names match what TerraformPlanner._derive_backend_config() wrote into
        # backend.tf — no mismatch possible.
        from core.terraform_backend import TerraformBackend
        from core.terraform_planner import TerraformPlanner
        _planner = TerraformPlanner()
        _minimal_config = {
            "aws_region": creds.get("region", "us-east-1"),
            "gcp_project": creds.get("project", ""),
            "gcp_region": creds.get("region", "us-central1"),
        }
        _backend_cfg = _planner._derive_backend_config(provider_name, project_name, _minimal_config)
        _tf_bootstrap_config = {
            **creds,
            "state_bucket": _backend_cfg.get("bucket", ""),
            "dynamodb_table": _backend_cfg.get("dynamodb_table", ""),
            "aws_region": creds.get("region", "us-east-1"),
            "gcp_project": creds.get("project", ""),
            "gcp_region": creds.get("region", "us-central1"),
        }
        print(f"\n  Bootstrapping Terraform state backend...")
        _actual_bootstrap = TerraformBackend().bootstrap(provider_name, _tf_bootstrap_config, project_name)

        # If the S3 bucket fallback fired, the bucket name in backend.tf is wrong.
        # Re-render the file locally so `terraform init` connects to the right bucket.
        if provider_name == "aws":
            _derived_bucket = _backend_cfg.get("bucket", "")
            _actual_bucket = _actual_bootstrap.get("bucket", "")
            if _actual_bucket and _actual_bucket != _derived_bucket:
                self._rerender_terraform_backend(spec, provider_name, _actual_bootstrap)

        # ── 3. Ensure Dockerfile ───────────────────────────────────────────────
        self._ensure_dockerfile(spec)

        # ── 4. Provision + deploy via Terraform (all providers) ───────────────
        tf_dir = self.out_dir / "terraform"
        tf_available = tf_dir.is_dir() and (tf_dir / "main.tf").exists()

        if tf_available:
            if provider_name == "aws":
                record = self._terraform_deploy_aws(
                    spec, provider, creds, project_name, deployment_id
                )
            elif provider_name == "gcp":
                record = self._terraform_deploy_gcp(
                    spec, provider, creds, project_name, deployment_id
                )
            else:  # heroku
                record = self._terraform_deploy_heroku(
                    spec, provider, creds, project_name, deployment_id
                )
        else:
            # Fallback: no terraform files — use legacy boto3/SDK path.
            if provider_name == "heroku":
                env_vars = self._read_env_file()
                image_tag = f"developable/{project_name}:latest"
                print(f"\n  Building Docker image '{image_tag}'...")
                self._docker_build(image_tag)
                print(f"\n  Deploying container to {provider.display_name}...")
                record = provider.deploy(spec, image_tag, env_vars, deployment_id)
                print(f"\n  Provisioning Heroku Postgres database...")
                db_url, db_resources = provider.provision_database(spec, project_name, deployment_id)
                print(f"\n  Applying Prisma schema to remote database...")
                provider.apply_schema(db_url)
                record["resources"].extend(db_resources)
                provider.wait_for_ready(record["endpoint"])
            else:
                print(f"\n  Provisioning managed PostgreSQL database...")
                db_url, db_resources = provider.provision_database(spec, project_name, deployment_id)
                print(f"\n  Applying Prisma schema to remote database...")
                provider.apply_schema(db_url)
                image_tag = f"developable/{project_name}:latest"
                print(f"\n  Building Docker image '{image_tag}'...")
                self._docker_build(image_tag)
                env_vars = self._read_env_file()
                env_vars["DATABASE_URL"] = db_url
                print(f"\n  Deploying container to {provider.display_name}...")
                record = provider.deploy(spec, image_tag, env_vars, deployment_id)
                record["resources"].extend(db_resources)

        # ── 8. Persist state ───────────────────────────────────────────────────
        state = DeploymentState(self.out_dir)
        state.initialise(
            project_name=project_name,
            schema_path=str(spec.get("schema_path", "")),
        )
        state.add(record)
        state.save()

        gitignore_changed = self._ensure_deployment_state_ignored()

        # ── 9. Push CI/CD deploy workflow + set GitHub secrets ────────────────
        if self._has_github_remote():
            if gitignore_changed:
                self._push_gitignore_to_github()
            print(f"\n  Generating and pushing CI/CD deploy workflow to GitHub...")
            workflow_yaml = provider.generate_deploy_workflow(project_name, record)
            pushed = self._push_workflow_to_github(workflow_yaml)
            if pushed:
                self._provision_github_secrets(provider_name, creds)

        # ── 10. Remote smoke tests ─────────────────────────────────────────────
        self._run_remote_tests(record["endpoint"])

        return record

    # ── Private helpers ────────────────────────────────────────────────────────

    def _ask_provider(self) -> str:
        """Present an interactive numbered menu and return the chosen slug."""
        providers = list(PROVIDER_MAP.items())
        print("\nDeployment Agent — select a cloud provider:")
        for i, (slug, name) in enumerate(providers, 1):
            print(f"  [{i}] {slug:<10} — {name}")
        print()
        while True:
            choice = input("  Enter number or provider name: ").strip().lower()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(providers):
                    return providers[idx][0]
            if choice in PROVIDER_MAP:
                return choice
            print(
                f"  Invalid choice. Enter 1–{len(providers)} or "
                f"one of: {', '.join(PROVIDER_MAP)}"
            )

    def _ensure_dockerfile(self, spec: dict[str, Any]) -> None:
        """Generate infra files (Dockerfile, docker-compose, CI) if Dockerfile is absent."""
        if (self.out_dir / "Dockerfile").exists():
            return
        print("  Dockerfile not found — generating infrastructure files...")
        from core.vc_planner import VCPlanner
        from core.assembler import Assembler
        plan = VCPlanner().plan(spec)
        Assembler(out_dir=self.out_dir, use_llm=False).assemble(spec, plan)
        print(f"  Generated {len(plan['files'])} infrastructure file(s).")

    def _docker_build(self, image_tag: str) -> None:
        """Build the Docker image from the output directory, streaming output.

        Uses `docker buildx build` with:
        - --platform linux/amd64   all supported providers require AMD64
        - --provenance=false       forces Docker manifest v2 format; newer Docker
                                   Desktop defaults to OCI format which Heroku
                                   (and some other registries) reject with
                                   "error from registry: unsupported"
        - --load                   loads the built image into the local Docker daemon
                                   (required when buildx is used with --platform)
        """
        result = subprocess.run([
            "docker", "buildx", "build",
            "--platform", "linux/amd64",
            "--provenance=false",
            "--load",
            "-t", image_tag,
            str(self.out_dir),
        ])
        if result.returncode != 0:
            print(
                "\nDocker build failed. Ensure Docker is running and the "
                "Dockerfile in the output directory is valid.",
                file=sys.stderr,
            )
            sys.exit(1)

    def _read_env_file(self) -> dict[str, str]:
        """Parse <out_dir>/.env into a key-value dict."""
        env_file = self.out_dir / ".env"
        env_vars: dict[str, str] = {}
        if not env_file.exists():
            return env_vars
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip().strip('"').strip("'")
        return env_vars

    def _ensure_deployment_state_ignored(self) -> bool:
        """
        Ensure .developable and other required patterns are present in .gitignore.
        """
        gitignore_path = self.out_dir / ".gitignore"
        if not gitignore_path.exists():
            gitignore_path.write_text(DEFAULT_GITIGNORE_CONTENT)
            return True
        return ensure_required_gitignore_patterns(gitignore_path)

    def _has_github_remote(self) -> bool:
        """Return True if the output directory has a git remote named 'origin'."""
        if not (self.out_dir / ".git").exists():
            return False
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=self.out_dir,
            capture_output=True,
        )
        return result.returncode == 0

    def _push_gitignore_to_github(self) -> bool:
        """
        Commit and push the .gitignore update after deployment state is created.
        Returns True on success, False if commit or push fails.
        """
        def git(*args: str) -> bool:
            r = subprocess.run(
                ["git", *args], cwd=self.out_dir, capture_output=True, text=True
            )
            return r.returncode == 0

        if not git("add", ".gitignore"):
            return False
        if not git(
            "commit",
            "-m",
            "Ignore Developable deployment state\n\nGenerated by Developable Deployment Agent.",
        ):
            return False

        result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=self.out_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"\n  Warning: could not push .gitignore to GitHub.\n"
                f"  Push it manually: cd {self.out_dir} && git push origin main",
            )
            return False

        print("  .gitignore updated and pushed to GitHub")
        return True

    def _push_workflow_to_github(self, workflow_yaml: str) -> bool:
        """
        Write .github/workflows/deploy.yml, commit, and push to origin main.
        Returns True on success, False if push fails (non-fatal).
        """
        workflow_dir = self.out_dir / ".github" / "workflows"
        workflow_dir.mkdir(parents=True, exist_ok=True)
        workflow_file = workflow_dir / "deploy.yml"
        workflow_file.write_text(workflow_yaml)

        def git(*args: str) -> bool:
            r = subprocess.run(
                ["git", *args], cwd=self.out_dir, capture_output=True, text=True
            )
            return r.returncode == 0

        if not git("add", ".github/workflows/deploy.yml"):
            return False
        if not git("commit", "-m", "Add cloud deployment CI/CD workflow\n\nGenerated by Developable Deployment Agent."):
            return False

        result = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=self.out_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(
                f"\n  Warning: could not push deploy.yml to GitHub.\n"
                f"  Push it manually: cd {self.out_dir} && git push origin main",
            )
            return False

        print(f"  deploy.yml pushed to GitHub: .github/workflows/deploy.yml")
        return True

    def _provision_github_secrets(
        self, provider_name: str, creds: dict[str, Any]
    ) -> None:
        """
        Set the required GitHub Actions secrets for the deploy workflow.

        Attempts to auto-set secrets via the GitHub API (requires a GitHub token
        with repo scope and PyNaCl installed). Falls back to printing manual
        instructions if the token is unavailable or the API call fails.

        GitHub requires secrets to be encrypted client-side with the repo's
        public key (libsodium sealed-box) before transmission.
        """
        secret_map = _PROVIDER_GITHUB_SECRETS.get(provider_name, {})
        if not secret_map:
            return

        token = self._github_token()
        repo = self._github_repo_fullname()

        if not token or not repo:
            self._print_secrets_instructions(provider_name, creds)
            return

        try:
            from nacl import encoding, public as nacl_public
        except ImportError:
            print(
                "  PyNaCl not installed — cannot auto-set GitHub secrets.\n"
                "  Run: pip install PyNaCl"
            )
            self._print_secrets_instructions(provider_name, creds)
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # Fetch repo public key (required for encryption)
        key_resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
            headers=headers,
            timeout=15,
        )
        if not key_resp.ok:
            print(f"  Could not fetch repo public key ({key_resp.status_code}) — skipping auto-set.")
            self._print_secrets_instructions(provider_name, creds)
            return

        key_data = key_resp.json()
        pub_key_bytes = base64.b64decode(key_data["key"])
        key_id = key_data["key_id"]

        set_ok: list[str] = []
        failed: list[str] = []

        for secret_name, cred_key in secret_map.items():
            value = creds.get(cred_key, "")
            if not value:
                failed.append(secret_name)
                continue

            # Encrypt with repo's public key (libsodium sealed-box)
            box = nacl_public.SealedBox(nacl_public.PublicKey(pub_key_bytes))
            encrypted = base64.b64encode(box.encrypt(value.encode())).decode()

            resp = requests.put(
                f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}",
                headers=headers,
                json={"encrypted_value": encrypted, "key_id": key_id},
                timeout=15,
            )
            if resp.status_code in (201, 204):
                set_ok.append(secret_name)
            else:
                failed.append(secret_name)
                print(f"  Warning: could not set {secret_name} ({resp.status_code}): {resp.text}")

        if set_ok:
            print(f"\n  GitHub Actions secrets set automatically: {', '.join(set_ok)}")
            print("  The deploy workflow will trigger after the next successful CI run on main.")

        if failed:
            print(f"\n  Could not auto-set: {', '.join(failed)}")
            self._print_secrets_instructions(provider_name, creds)

    def _github_token(self) -> str | None:
        """
        Resolve a GitHub token for the Secrets API.

        Tries in order:
        1. GITHUB_TOKEN environment variable.
        2. Token embedded in the git remote URL
           (https://<token>@github.com/owner/repo.git).
        """
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            return token

        url = self._git_remote_url()
        if url:
            m = re.match(r"https://([^@]+)@github\.com/", url)
            if m:
                return m.group(1)

        return None

    def _github_repo_fullname(self) -> str | None:
        """
        Extract 'owner/repo' from the git remote URL.
        Handles both https://github.com/owner/repo.git and git@github.com:owner/repo.git.
        """
        url = self._git_remote_url()
        if not url:
            return None
        # HTTPS: https://[token@]github.com/owner/repo[.git]
        m = re.search(r"github\.com[/:]([^/]+/[^/.]+?)(?:\.git)?$", url)
        return m.group(1) if m else None

    def _git_remote_url(self) -> str | None:
        """Return the origin remote URL, or None if unavailable."""
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=self.out_dir,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _print_secrets_instructions(
        self, provider_name: str, creds: dict[str, Any]
    ) -> None:
        """Print instructions for setting the required GitHub Actions secrets."""
        secrets_needed = _SECRETS_INSTRUCTIONS.get(provider_name, [])
        if not secrets_needed:
            return
        print(
            "\n  ─────────────────────────────────────────────────────────────────\n"
            "  To enable automated deployments, add these secrets to your\n"
            "  GitHub repository (Settings → Secrets and variables → Actions):\n"
            "  ─────────────────────────────────────────────────────────────────"
        )
        for line in secrets_needed:
            print(f"    {line}")
        print(
            "  ─────────────────────────────────────────────────────────────────\n"
            "  Workflow file: .github/workflows/deploy.yml"
        )

    def _run_remote_tests(self, endpoint: str) -> None:
        """
        Run the generated test suite against the remote endpoint.
        No test file modifications — the URL is passed as a CLI argument.
        Auto-detects tests_dir from <out_dir>/tests/ if not explicitly provided.
        """
        tests_dir = self.tests_dir
        if tests_dir is None:
            candidate = self.out_dir / "tests"
            if candidate.is_dir() and (candidate / "run_all.py").exists():
                tests_dir = candidate

        if tests_dir is None or not (tests_dir / "run_all.py").exists():
            return

        if "pending" in endpoint:
            print(
                "\n  Skipping remote smoke tests: endpoint not yet available.\n"
                f"  Run manually: {sys.executable} {tests_dir}/run_all.py <endpoint>"
            )
            return

        print(f"\n  Running remote smoke tests against {endpoint}...")
        print(f"  (test files are NOT modified — URL passed as argument)\n")
        subprocess.run(
            [sys.executable, str(tests_dir / "run_all.py"), endpoint],
        )
        # Non-fatal: test failures are printed but do not halt the deployment pipeline.

    # ── Terraform-based AWS deployment ────────────────────────────────────────

    def _terraform_deploy_aws(
        self,
        spec: dict[str, Any],
        provider: Any,
        creds: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> dict[str, Any]:
        """
        Provision AWS infrastructure via `terraform apply` and deploy the container.

        Flow:
          1. Write terraform.auto.tfvars.json with db_password + jwt_secret.
          2. terraform init  (connects to S3 backend bootstrapped in step 2 of deploy()).
          3. terraform apply (first pass) — creates ECR, RDS, ECS, ALB, VPC, IAM.
             RDS takes ~5-10 minutes; ECS task starts failing (no image yet).
          4. Parse terraform output for rds_endpoint, ecr_repository_url, alb_dns_name.
          5. Apply Prisma schema to RDS via provider.apply_schema().
          6. Build Docker image locally.
          7. Push image to ECR.
          8. Force-new ECS deployment so the service picks up the pushed image.
        """
        import json
        import secrets as _secrets

        tf_dir = self.out_dir / "terraform"
        region = creds.get("region", "us-east-1")

        # Read secrets from .env if present, otherwise generate.
        env_file_vars = self._read_env_file()
        db_password = env_file_vars.get("DB_PASSWORD") or _secrets.token_urlsafe(24)
        jwt_secret = env_file_vars.get("JWT_SECRET") or _secrets.token_urlsafe(32)

        # Write sensitive tfvars file — never committed (gitignored below).
        tfvars_path = tf_dir / "terraform.auto.tfvars.json"
        tfvars_path.write_text(json.dumps({
            "project_name": project_name,
            "aws_region": region,
            "db_password": db_password,
            "jwt_secret": jwt_secret,
        }, indent=2))
        self._ensure_gitignored("terraform/*.tfvars.json")

        # Pass AWS credentials to Terraform via environment variables.
        tf_env = {
            **os.environ,
            "AWS_ACCESS_KEY_ID": creds.get("access_key", ""),
            "AWS_SECRET_ACCESS_KEY": creds.get("secret_key", ""),
            "AWS_DEFAULT_REGION": region,
        }
        if creds.get("session_token"):
            tf_env["AWS_SESSION_TOKEN"] = creds["session_token"]

        print("\n  [Terraform] Initializing backend...")
        self._tf_run(tf_dir, ["terraform", "init", "-input=false"], env=tf_env)

        print("\n  [Terraform] Provisioning infrastructure (ECR, RDS, ECS Fargate, ALB, VPC)...")
        print("  RDS provisioning takes 5-10 minutes — please wait.\n")
        self._tf_run(tf_dir, ["terraform", "apply", "-auto-approve", "-input=false"], env=tf_env)

        # Parse outputs.
        out = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=tf_dir, capture_output=True, text=True, env=tf_env,
        )
        if out.returncode != 0:
            print(f"Error reading Terraform outputs:\n{out.stderr}", file=sys.stderr)
            sys.exit(1)
        outputs = {k: v["value"] for k, v in json.loads(out.stdout).items()}

        rds_endpoint = outputs["rds_endpoint"]        # "host:5432"
        ecr_url = outputs["ecr_repository_url"]
        alb_dns = outputs["alb_dns_name"]

        db_name = project_name.replace("-", "_")
        db_url = f"postgresql://postgres:{db_password}@{rds_endpoint}/{db_name}"

        rds_host, _, rds_port = rds_endpoint.partition(":")
        self._wait_for_db_tcp(rds_host, int(rds_port or 5432))

        print("\n  Applying Prisma schema to remote database...")
        provider.apply_schema(db_url)

        local_tag = f"developable/{project_name}:latest"
        image_uri = f"{ecr_url}:latest"
        print(f"\n  Building Docker image '{local_tag}'...")
        self._docker_build(local_tag)

        print(f"\n  Pushing image to ECR...")
        self._ecr_push(local_tag, image_uri, region, creds)

        print(f"\n  Triggering ECS deployment with pushed image...")
        self._ecs_force_deploy(project_name, region, creds)

        endpoint = f"http://{alb_dns}"
        print(f"\n  ✓ Deployed — endpoint: {endpoint}")

        from core.deployment_state import DeploymentState
        return DeploymentState.make_record(
            provider="aws",
            region=region,
            endpoint=endpoint,
            image_uri=image_uri,
            resources=[{"type": "terraform_managed", "id": project_name, "arn": None}],
            tags=provider.build_tags(project_name, deployment_id, spec),
        )

    def _tf_run(self, tf_dir: Path, cmd: list[str], env: dict | None = None) -> None:
        """Run a Terraform command, streaming output. Exits on failure."""
        result = subprocess.run(cmd, cwd=tf_dir, env=env)
        if result.returncode != 0:
            print(f"\nTerraform command failed: {' '.join(cmd)}", file=sys.stderr)
            sys.exit(1)

    def _ecr_push(
        self, local_tag: str, image_uri: str, region: str, creds: dict[str, Any]
    ) -> None:
        """Docker-login to ECR via boto3 token, then tag and push the image."""
        import base64
        import boto3

        session = boto3.Session(
            aws_access_key_id=creds.get("access_key"),
            aws_secret_access_key=creds.get("secret_key"),
            aws_session_token=creds.get("session_token"),
            region_name=region,
        )
        ecr = session.client("ecr")
        auth = ecr.get_authorization_token()["authorizationData"][0]
        username, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
        registry = auth["proxyEndpoint"]

        subprocess.run(
            ["docker", "login", "--username", username, "--password-stdin", registry],
            input=password.encode(), capture_output=True, check=True,
        )
        subprocess.run(["docker", "tag", local_tag, image_uri], check=True)
        result = subprocess.run(["docker", "push", image_uri])
        if result.returncode != 0:
            print(f"Error pushing image to ECR.", file=sys.stderr)
            sys.exit(1)

    def _ecs_force_deploy(self, project_name: str, region: str, creds: dict[str, Any]) -> None:
        """Force ECS to start a new deployment so it pulls the just-pushed image."""
        import boto3

        session = boto3.Session(
            aws_access_key_id=creds.get("access_key"),
            aws_secret_access_key=creds.get("secret_key"),
            aws_session_token=creds.get("session_token"),
            region_name=region,
        )
        ecs = session.client("ecs")
        ecs.update_service(
            cluster=project_name,
            service=project_name,
            forceNewDeployment=True,
        )
        print(f"  ECS service '{project_name}' redeployment triggered.")

    def _wait_for_db_tcp(self, host: str, port: int, timeout_s: int = 300) -> None:
        """
        Poll TCP port until it accepts a connection, then return.

        RDS (and Cloud SQL) report "available" in Terraform before PostgreSQL
        is actually ready to accept connections — typically 30-90 seconds later.
        Polling here prevents the Prisma migration from failing consistently.
        """
        deadline = time.time() + timeout_s
        interval = 5
        print(f"  Waiting for database at {host}:{port} to accept connections", end="", flush=True)
        while time.time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=5):
                    print(" ready.")
                    return
            except OSError:
                print(".", end="", flush=True)
                time.sleep(interval)
        print(f"\n  Warning: database at {host}:{port} did not become reachable within {timeout_s}s.", file=sys.stderr)

    def _ensure_gitignored(self, pattern: str) -> None:
        """Append pattern to .gitignore if not already present."""
        gitignore = self.out_dir / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text()
            if pattern not in content:
                gitignore.write_text(content.rstrip() + f"\n{pattern}\n")
        else:
            gitignore.write_text(f"{pattern}\n")

    def _rerender_terraform_backend(
        self, spec: dict[str, Any], provider: str, backend_config: dict[str, Any]
    ) -> None:
        """Re-render terraform/backend.tf with the actual (fallback) bucket name."""
        from core.terraform_planner import TerraformPlanner
        from generators.template import TemplateGenerator

        planner = TerraformPlanner()
        project_name = planner._derive_project_name(spec)
        context = {
            "project_name": project_name,
            "spec": spec,
            "entities": spec["entities"],
            "provider_config": {},
            "backend_config": backend_config,
        }
        backend_tf = self.out_dir / "terraform" / "backend.tf"
        if backend_tf.exists():
            content = TemplateGenerator().render(f"terraform/{provider}/backend.tf.j2", context)
            backend_tf.write_text(content)
            print(f"    Updated terraform/backend.tf → bucket: {backend_config['bucket']}")

    # ── Terraform-based GCP deployment ────────────────────────────────────────

    def _terraform_deploy_gcp(
        self,
        spec: dict[str, Any],
        provider: Any,
        creds: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> dict[str, Any]:
        """
        Provision GCP infrastructure via `terraform apply` and deploy the container.

        Flow:
          1. Write terraform.auto.tfvars.json with db_password, jwt_secret, image_tag="placeholder".
          2. terraform init  (connects to GCS backend bootstrapped in step 2 of deploy()).
          3. terraform apply (first pass) — creates Artifact Registry, Cloud SQL, Cloud Run
             with a placeholder image. Cloud SQL takes 5-10 minutes.
          4. Parse terraform output for cloud_run_url, artifact_registry_url, cloud_sql_public_ip.
          5. Apply Prisma schema to Cloud SQL via provider.apply_schema().
          6. Build Docker image locally.
          7. Push image to Artifact Registry.
          8. terraform apply (second pass) with image_tag="latest" → forces new Cloud Run revision.
        """
        import json
        import secrets as _secrets

        tf_dir = self.out_dir / "terraform"
        project_id = creds.get("project_id", "")
        region = creds.get("region", "us-central1")

        env_file_vars = self._read_env_file()
        db_password = env_file_vars.get("DB_PASSWORD") or _secrets.token_urlsafe(24)
        jwt_secret = env_file_vars.get("JWT_SECRET") or _secrets.token_urlsafe(32)

        tfvars_path = tf_dir / "terraform.auto.tfvars.json"
        tfvars_path.write_text(json.dumps({
            "project_name": project_name,
            "gcp_project": project_id,
            "gcp_region": region,
            "db_password": db_password,
            "jwt_secret": jwt_secret,
            "image_tag": "placeholder",
        }, indent=2))
        self._ensure_gitignored("terraform/*.tfvars.json")

        tf_env = {**os.environ, "GOOGLE_CLOUD_PROJECT": project_id}
        if creds.get("credentials_file"):
            tf_env["GOOGLE_APPLICATION_CREDENTIALS"] = creds["credentials_file"]

        print("\n  [Terraform] Initializing backend...")
        self._tf_run(tf_dir, ["terraform", "init", "-input=false"], env=tf_env)

        print("\n  [Terraform] Provisioning infrastructure (Artifact Registry, Cloud SQL, Cloud Run)...")
        print("  Cloud SQL provisioning takes 5-10 minutes — please wait.\n")
        self._tf_run(tf_dir, ["terraform", "apply", "-auto-approve", "-input=false"], env=tf_env)

        out = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=tf_dir, capture_output=True, text=True, env=tf_env,
        )
        if out.returncode != 0:
            print(f"Error reading Terraform outputs:\n{out.stderr}", file=sys.stderr)
            sys.exit(1)
        outputs = {k: v["value"] for k, v in json.loads(out.stdout).items()}

        cloud_run_url = outputs["cloud_run_url"]
        ar_url = outputs["artifact_registry_url"]
        cloud_sql_ip = outputs["cloud_sql_public_ip"]

        db_name = project_name.replace("-", "_")
        db_url = f"postgresql://postgres:{db_password}@{cloud_sql_ip}:5432/{db_name}"

        self._wait_for_db_tcp(cloud_sql_ip, 5432)

        print("\n  Applying Prisma schema to Cloud SQL...")
        provider.apply_schema(db_url)

        local_tag = f"developable/{project_name}:latest"
        image_uri = f"{ar_url}:latest"
        ar_host = f"{region}-docker.pkg.dev"

        print(f"\n  Building Docker image '{local_tag}'...")
        self._docker_build(local_tag)

        print(f"\n  Pushing image to Artifact Registry...")
        provider._push_to_registry(creds, local_tag, image_uri, ar_host)

        # Second apply: update image_tag to "latest" so Cloud Run creates a new revision.
        tfvars_path.write_text(json.dumps({
            "project_name": project_name,
            "gcp_project": project_id,
            "gcp_region": region,
            "db_password": db_password,
            "jwt_secret": jwt_secret,
            "image_tag": "latest",
        }, indent=2))
        print("\n  [Terraform] Deploying new Cloud Run revision with pushed image...")
        self._tf_run(tf_dir, ["terraform", "apply", "-auto-approve", "-input=false"], env=tf_env)

        print(f"\n  ✓ Deployed — endpoint: {cloud_run_url}")

        from core.deployment_state import DeploymentState
        return DeploymentState.make_record(
            provider="gcp",
            region=region,
            endpoint=cloud_run_url,
            image_uri=image_uri,
            resources=[{"type": "terraform_managed", "id": project_name, "arn": None}],
            tags=provider.build_tags(project_name, deployment_id, spec),
        )

    # ── Terraform-based Heroku deployment ─────────────────────────────────────

    def _terraform_deploy_heroku(
        self,
        spec: dict[str, Any],
        provider: Any,
        creds: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> dict[str, Any]:
        """
        Provision Heroku infrastructure via `terraform apply` and deploy the container.

        Flow:
          1. Write terraform.auto.tfvars.json with jwt_secret (heroku_api_key via env var).
          2. terraform init  (local state backend — no remote state to bootstrap).
          3. terraform apply — creates heroku_app, heroku_addon (postgres), config_association.
          4. Parse terraform output for app_url and database_url (sensitive).
          5. Apply Prisma schema to Heroku Postgres via provider.apply_schema().
          6. Build Docker image locally.
          7. Docker login to registry.heroku.com, push image, release via Formation API.
        """
        import json
        import secrets as _secrets

        tf_dir = self.out_dir / "terraform"
        api_key = creds.get("api_key", "")

        env_file_vars = self._read_env_file()
        jwt_secret = env_file_vars.get("JWT_SECRET") or _secrets.token_urlsafe(32)

        tfvars_path = tf_dir / "terraform.auto.tfvars.json"
        tfvars_path.write_text(json.dumps({
            "project_name": project_name,
            "jwt_secret": jwt_secret,
        }, indent=2))
        self._ensure_gitignored("terraform/*.tfvars.json")

        # Heroku API key passed via env var — never written to a file.
        tf_env = {**os.environ, "TF_VAR_heroku_api_key": api_key}

        print("\n  [Terraform] Initializing (local state)...")
        self._tf_run(tf_dir, ["terraform", "init", "-input=false"], env=tf_env)

        print("\n  [Terraform] Provisioning Heroku app and Postgres addon...")
        print("  Postgres addon provisioning typically takes 1-2 minutes.\n")
        self._tf_run(tf_dir, ["terraform", "apply", "-auto-approve", "-input=false"], env=tf_env)

        # sensitive outputs are still present as actual values in -json output.
        out = subprocess.run(
            ["terraform", "output", "-json"],
            cwd=tf_dir, capture_output=True, text=True, env=tf_env,
        )
        if out.returncode != 0:
            print(f"Error reading Terraform outputs:\n{out.stderr}", file=sys.stderr)
            sys.exit(1)
        outputs = {k: v["value"] for k, v in json.loads(out.stdout).items()}

        app_url = outputs.get("app_url", f"https://{project_name}.herokuapp.com")
        db_url = outputs.get("database_url", "")

        # Normalize scheme: Heroku uses postgres://, Prisma 5 requires postgresql://
        if db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]

        print("\n  Applying Prisma schema to Heroku Postgres...")
        provider.apply_schema(db_url)

        local_tag = f"developable/{project_name}:latest"
        print(f"\n  Building Docker image '{local_tag}'...")
        self._docker_build(local_tag)

        heroku_image = f"registry.heroku.com/{project_name}/web"
        print(f"\n  Authenticating Docker to Heroku registry...")
        provider._docker_login(api_key)

        print(f"\n  Pushing image to {heroku_image}...")
        subprocess.run(["docker", "tag", local_tag, heroku_image], check=True)
        provider._docker_push_with_retry(heroku_image)

        # Heroku Formation API requires the image config digest (not manifest digest).
        manifest_result = subprocess.run(
            ["docker", "manifest", "inspect", heroku_image],
            capture_output=True, text=True,
        )
        if manifest_result.returncode != 0:
            print(f"\nFailed to inspect manifest: {manifest_result.stderr}", file=sys.stderr)
            sys.exit(1)
        manifest = json.loads(manifest_result.stdout)
        image_id = manifest.get("config", {}).get("digest", "")
        if not image_id:
            print("\nCould not read config digest from manifest.", file=sys.stderr)
            sys.exit(1)
        print(f"  [Heroku] Config digest: {image_id}")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/vnd.heroku+json; version=3",
            "Content-Type": "application/json",
        }
        # Set provider state so helper methods work without re-initialisation.
        provider._api_headers = headers
        provider._app_name_resolved = project_name

        print(f"\n  Releasing web dyno...")
        provider._release(headers, project_name, image_id)
        provider._print_release_status(headers, project_name)

        endpoint = provider._get_app_domain(headers, project_name)
        print(f"\n  ✓ Deployed — endpoint: {endpoint}")

        from core.deployment_state import DeploymentState
        return DeploymentState.make_record(
            provider="heroku",
            region=None,
            endpoint=endpoint,
            image_uri=heroku_image,
            resources=[{"type": "terraform_managed", "id": project_name, "arn": None}],
            tags=provider.build_tags(project_name, deployment_id, spec),
        )

    @staticmethod
    def _normalise_kwargs(kwargs: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Map flat CLI kwargs to per-provider constructor dicts."""
        return {
            "aws": {k: v for k, v in {
                "region": kwargs.get("aws_region"),
            }.items() if v is not None},
            "heroku": {k: v for k, v in {
                "app_name": kwargs.get("heroku_app"),
            }.items() if v is not None},
            "gcp": {k: v for k, v in {
                "project_id": kwargs.get("gcp_project"),
                "region": kwargs.get("gcp_region"),
            }.items() if v is not None},
        }
