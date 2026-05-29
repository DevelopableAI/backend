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
        "AWS_SESSION_TOKEN       — required when using temporary STS credentials",
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
        "AWS_SESSION_TOKEN": "session_token",
    },
    "gcp": {"GCP_CREDENTIALS": "credentials_b64"},
}

_OPTIONAL_PROVIDER_GITHUB_SECRETS: dict[str, set[str]] = {
    "aws": {"AWS_SESSION_TOKEN"},
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
        github_token: str = "",
        **kwargs: Any,
    ) -> None:
        self.out_dir = out_dir
        self.provider_name = provider
        self.tests_dir = tests_dir
        self.github_token = github_token
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

        record: dict[str, Any] | None = None
        try:
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
        finally:
            # ── 9. Push CI/CD deploy workflow ─────────────────────────────────
            # Always push deploy.yml even if deployment failed — the workflow
            # self-checks for secrets and will re-trigger when the user re-runs.
            # Skipped only when record is None (deployment failed before any
            # resources were created, so there is no endpoint/image to embed).
            if self._has_github_remote() and record is not None:
                try:
                    gitignore_changed = self._ensure_deployment_state_ignored()
                    if gitignore_changed:
                        self._push_gitignore_to_github()
                    workflow_yaml = provider.generate_deploy_workflow(project_name, record)
                    print(f"\n  Preparing GitHub Actions deploy workflow...")
                    secrets_ready = self._provision_github_secrets(provider_name, creds)
                    print(f"  Pushing CI/CD deploy workflow to GitHub...")
                    self._push_workflow_to_github(workflow_yaml)
                    if not secrets_ready:
                        repo = self._github_repo_fullname() or "<your-repo>"
                        print(
                            f"\n  deploy.yml pushed. The workflow will fail until the required\n"
                            f"  GitHub Actions secrets are set. Add them at:\n"
                            f"  https://github.com/{repo}/settings/secrets/actions"
                        )
                except Exception as exc:
                    print(f"\n  Warning: could not push deploy.yml: {exc}", file=sys.stderr)

        if record is None:
            sys.exit(1)

        # ── 8. Persist state ───────────────────────────────────────────────────
        state = DeploymentState(self.out_dir)
        state.initialise(
            project_name=project_name,
            schema_path=str(spec.get("schema_path", "")),
        )
        state.add(record)
        state.save()

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

    def _ensure_docker_running(self) -> None:
        """Block until Docker daemon is reachable, prompting the user if it isn't."""
        while True:
            probe = subprocess.run(
                ["docker", "info"],
                capture_output=True,
            )
            if probe.returncode == 0:
                return
            print(
                "\n  Docker is not running.\n"
                "  Please start Docker Desktop (or your Docker daemon) and press Enter to retry...",
                flush=True,
            )
            try:
                input()
            except EOFError:
                # Non-interactive environment — poll silently every 10 s.
                time.sleep(10)

    def _wait_for_user_action(self, message: str, steps: list[str]) -> None:
        """Print an actionable error with numbered resolution steps, then block until Enter."""
        print(f"\n  ⚠  {message}", flush=True)
        for i, step in enumerate(steps, 1):
            print(f"  {i}. {step}")
        print("\n  Press Enter once resolved to retry...", flush=True)
        try:
            input()
        except EOFError:
            time.sleep(10)

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
        self._ensure_docker_running()
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
    ) -> bool:
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
            return True
        optional_secrets = _OPTIONAL_PROVIDER_GITHUB_SECRETS.get(provider_name, set())

        token = self._github_token()
        repo = self._github_repo_fullname()

        if not token or not repo:
            self._print_secrets_instructions(provider_name, creds)
            return False

        try:
            from nacl import encoding, public as nacl_public
        except ImportError:
            print(
                "  PyNaCl not installed — cannot auto-set GitHub secrets.\n"
                "  Run: pip install PyNaCl"
            )
            self._print_secrets_instructions(provider_name, creds)
            return False

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
            return False

        key_data = key_resp.json()
        pub_key_bytes = base64.b64decode(key_data["key"])
        key_id = key_data["key_id"]

        set_ok: list[str] = []
        failed: list[str] = []

        for secret_name, cred_key in secret_map.items():
            value = creds.get(cred_key, "")
            if not value:
                if secret_name in optional_secrets:
                    continue
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

        if failed:
            print(f"\n  Could not auto-set: {', '.join(failed)}")
            self._print_secrets_instructions(provider_name, creds)
            return False

        if set_ok:
            print("  Secrets are ready — the deploy workflow can now be pushed safely.")

        return True

    def _github_token(self) -> str | None:
        """
        Resolve a GitHub token for the Secrets API.

        Tries in order:
        1. Token passed explicitly at construction (from main.py / skill).
        2. GITHUB_TOKEN environment variable.
        3. Token embedded in the git remote URL
           (https://<token>@github.com/owner/repo.git).
        """
        if self.github_token:
            return self.github_token

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
        """
        Fallback: print secret names when the GitHub API could not set them
        automatically (e.g. token lacks the 'secrets' write permission).
        """
        secret_map = _PROVIDER_GITHUB_SECRETS.get(provider_name, {})
        optional = _OPTIONAL_PROVIDER_GITHUB_SECRETS.get(provider_name, set())
        if not secret_map:
            return

        repo = self._github_repo_fullname() or "<your-repo>"
        settings_url = f"https://github.com/{repo}/settings/secrets/actions"

        print(
            "\n  ─────────────────────────────────────────────────────────────────\n"
            "  Could not auto-set GitHub Actions secrets (token may lack\n"
            "  'secrets' write permission — needs full 'repo' scope).\n"
            f"  Add them manually at: {settings_url}\n"
            "  ─────────────────────────────────────────────────────────────────"
        )
        for secret_name, cred_key in secret_map.items():
            suffix = "  (optional)" if secret_name in optional else ""
            print(f"    {secret_name}{suffix}")
        print("  ─────────────────────────────────────────────────────────────────")

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
        Hybrid boto3 + terraform import AWS deployment.

        Flow:
          1. boto3 provisions all 15 resources with terraform-matching names (~8-10 min,
             RDS runs async while other resources are created in parallel).
          2. Build Docker image locally.
          3. Push image to ECR (needed before import so tfvars has the real image tag).
          4. terraform import × 15 resources, then reconciliation apply — state becomes
             fully up-to-date so terraform plan/apply/destroy all work correctly.
          5. Run Prisma migration as a one-off ECS task inside the VPC.
             (ECS rolling deploy was already triggered by the reconciliation apply.)
        """
        region = creds.get("region", "us-east-1")

        print(
            f"\n  ┌─ Step 3/7 — Provision AWS infrastructure via boto3 ──────────────────\n"
            f"  │  ETA: ~10–15 min  (RDS takes 8–10 min; other resources ~2 min)\n"
            f"  │  Monitor RDS:  https://console.aws.amazon.com/rds/home?region={region}#databases:\n"
            f"  │  Monitor ECS:  https://console.aws.amazon.com/ecs/home?region={region}#/clusters\n"
            f"  └─────────────────────────────────────────────────────────────────────────"
        )
        pr = self._boto3_provision_aws(creds, project_name)

        local_tag = f"developable/{project_name}:latest"
        image_uri = f"{pr['ecr_url']}:latest"
        print(f"\n  ┌─ Step 4/7 — Build Docker image  (ETA: ~3 min) ─────────────────────")
        print(f"  └─ Image tag: {local_tag}")
        self._docker_build(local_tag)

        print(f"\n  ┌─ Step 5/7 — Push image to ECR  (ETA: ~1 min) ──────────────────────")
        print(f"  └─ Target:    {image_uri}")
        self._ecr_push(local_tag, image_uri, region, creds)

        print(
            f"\n  ┌─ Step 6/7 — Terraform import + reconciliation apply  (ETA: ~3 min) ──\n"
            f"  │  Imports 15 resources into state, then apply makes state fully current.\n"
            f"  └─────────────────────────────────────────────────────────────────────────"
        )
        self._terraform_import_aws(creds, project_name, pr)

        print(
            f"\n  ┌─ Step 7/7 — Prisma migration via ECS task  (ETA: ~2 min) ───────────\n"
            f"  │  Monitor: https://console.aws.amazon.com/ecs/home?region={region}"
            f"#/clusters/{project_name}/tasks\n"
            f"  └─────────────────────────────────────────────────────────────────────────"
        )
        self._run_ecs_migration(project_name, pr["db_url"], region, creds)

        endpoint = f"http://{pr['alb_dns']}"
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

    def _boto3_provision_aws(self, creds: dict[str, Any], project_name: str) -> dict[str, Any]:
        """
        Create all 15 Terraform-managed AWS resources via boto3 with terraform-matching names.

        RDS creation is kicked off first; all other resources (IAM, ALB, ECS) are created
        while RDS provisions in the background. All creates are idempotent.

        Returns a dict of IDs/ARNs needed for terraform import and the deployment record.
        """
        import boto3
        import json as _json
        import secrets as _secrets
        from botocore.exceptions import ClientError

        region = creds["region"]
        session = boto3.Session(
            aws_access_key_id=creds.get("access_key"),
            aws_secret_access_key=creds.get("secret_key"),
            aws_session_token=creds.get("session_token"),
            region_name=region,
        )
        ec2   = session.client("ec2")
        ecr   = session.client("ecr")
        ecs   = session.client("ecs")
        iam   = session.client("iam")
        rds   = session.client("rds")
        elb   = session.client("elbv2")
        logs  = session.client("logs")
        sts   = session.client("sts")

        # ── Validate credentials — prompt and retry on auth errors ───────────
        while True:
            try:
                account_id = sts.get_caller_identity()["Account"]
                break
            except ClientError as e:
                if e.response["Error"]["Code"] in (
                    "InvalidClientTokenId", "ExpiredTokenException",
                    "AuthFailure", "UnauthorizedOperation",
                ):
                    self._wait_for_user_action(
                        "AWS credentials are invalid or expired.",
                        [
                            "Export fresh credentials in this terminal:",
                            "  export AWS_ACCESS_KEY_ID=<key>",
                            "  export AWS_SECRET_ACCESS_KEY=<secret>",
                            "  export AWS_SESSION_TOKEN=<token>  # if using STS/assumed role",
                            "Verify with: aws sts get-caller-identity",
                        ],
                    )
                    session = boto3.Session(
                        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
                        region_name=region,
                    )
                    sts = session.client("sts")
                else:
                    raise
        ecr_url = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{project_name}"

        # Default VPC + subnets (matches data sources in main.tf.j2)
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
        if not vpcs["Vpcs"]:
            self._wait_for_user_action(
                "No default VPC found in this AWS region.",
                [
                    f"Create one with: aws ec2 create-default-vpc --region {region}",
                    "Or create it in the VPC console: https://console.aws.amazon.com/vpc/",
                    "Then press Enter to retry",
                ],
            )
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
            if not vpcs["Vpcs"]:
                print(f"\nError: still no default VPC in {region}.", file=sys.stderr)
                sys.exit(1)
        vpc_id = vpcs["Vpcs"][0]["VpcId"]
        subnet_ids = [s["SubnetId"] for s in ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )["Subnets"]]

        # ── Security groups ────────────────────────────────────────────────────
        print(f"  [boto3] Ensuring security groups...")
        alb_sg_id = self._ensure_sg_boto3(ec2, vpc_id, f"{project_name}-alb-sg",
            "Managed by Terraform",
            [{"proto": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"}])
        ecs_sg_id = self._ensure_sg_boto3(ec2, vpc_id, f"{project_name}-ecs-sg",
            "Managed by Terraform",
            [{"proto": "tcp", "from_port": 3000, "to_port": 3000, "src_sg": alb_sg_id}])
        rds_sg_id = self._ensure_sg_boto3(ec2, vpc_id, f"{project_name}-rds-sg",
            "Managed by Terraform",
            [{"proto": "tcp", "from_port": 5432, "to_port": 5432, "src_sg": ecs_sg_id}])

        # ── ECR ────────────────────────────────────────────────────────────────
        print(f"  [boto3] Ensuring ECR repository '{project_name}'...")
        try:
            ecr.create_repository(repositoryName=project_name,
                imageScanningConfiguration={"scanOnPush": True})
        except ClientError as e:
            if e.response["Error"]["Code"] != "RepositoryAlreadyExistsException":
                raise

        # ── RDS — kick off now, wait later ────────────────────────────────────
        env_vars = self._read_env_file()
        db_password = env_vars.get("DB_PASSWORD") or _secrets.token_urlsafe(24)
        jwt_secret  = env_vars.get("JWT_SECRET")  or _secrets.token_urlsafe(32)
        db_name = project_name.replace("-", "_")
        subnet_group = f"{project_name}-db-subnet-group"

        try:
            rds.create_db_subnet_group(
                DBSubnetGroupName=subnet_group,
                DBSubnetGroupDescription=f"Developable DB subnet group for {project_name}",
                SubnetIds=subnet_ids,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "DBSubnetGroupAlreadyExists":
                raise

        print(f"  [boto3] Kicking off RDS provisioning (5-10 min) — continuing with other resources...")
        try:
            rds.create_db_instance(
                DBInstanceIdentifier=project_name,
                DBInstanceClass="db.t3.micro",
                Engine="postgres",
                EngineVersion="15",
                MasterUsername="postgres",
                MasterUserPassword=db_password,
                DBName=db_name,
                VpcSecurityGroupIds=[rds_sg_id],
                DBSubnetGroupName=subnet_group,
                PubliclyAccessible=False,
                MultiAZ=False,
                StorageType="gp2",
                AllocatedStorage=20,
                BackupRetentionPeriod=1,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "DBInstanceAlreadyExists":
                raise
            print(f"  [boto3] RDS '{project_name}' already exists — reusing.")

        # ── IAM role ──────────────────────────────────────────────────────────
        role_name = f"{project_name}-ecs-execution"
        policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
        print(f"  [boto3] Ensuring IAM role '{role_name}'...")
        assume = _json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow",
                "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                "Action": "sts:AssumeRole"}],
        })
        try:
            role_arn = iam.create_role(RoleName=role_name,
                AssumeRolePolicyDocument=assume,
                Description=f"ECS execution role for {project_name}")["Role"]["Arn"]
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
            time.sleep(10)  # IAM propagation
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
            else:
                raise

        # ── ALB + target group + listener ─────────────────────────────────────
        print(f"  [boto3] Ensuring ALB '{project_name}'...")
        alb_arn, alb_dns = self._ensure_alb_boto3(elb, project_name, subnet_ids, alb_sg_id)
        tg_arn          = self._ensure_target_group_boto3(elb, project_name, vpc_id)
        try:
            listener_arn = self._ensure_alb_listener_boto3(elb, alb_arn, tg_arn)
        except ClientError as _tg_err:
            if _tg_err.response["Error"]["Code"] != "TargetGroupAssociationLimitException":
                raise
            # Stale association from a previous partial run — AWS hasn't fully released
            # the TG from the old ALB yet. Poll-delete it (ResourceInUse clears ~60s after
            # the old ALB is gone), then recreate and retry.
            print(f"  [boto3] Stale TG association — recycling target group (may take ~60s)...", flush=True)
            _tg_deadline = time.time() + 300
            while time.time() < _tg_deadline:
                try:
                    elb.delete_target_group(TargetGroupArn=tg_arn)
                    break
                except ClientError as _de:
                    if _de.response["Error"]["Code"] != "ResourceInUse":
                        raise
                    time.sleep(10)
            else:
                raise RuntimeError(f"Stale target group {tg_arn} could not be deleted within 5 minutes")
            _gone_deadline = time.time() + 60
            while time.time() < _gone_deadline:
                try:
                    elb.describe_target_groups(TargetGroupArns=[tg_arn])
                    time.sleep(5)
                except ClientError as _ge:
                    if _ge.response["Error"]["Code"] == "TargetGroupNotFound":
                        break
                    raise
            tg_arn = self._ensure_target_group_boto3(elb, project_name, vpc_id)
            listener_arn = self._ensure_alb_listener_boto3(elb, alb_arn, tg_arn)

        # ── CloudWatch log group ───────────────────────────────────────────────
        log_group = f"/ecs/{project_name}"
        try:
            logs.create_log_group(logGroupName=log_group)
            logs.put_retention_policy(logGroupName=log_group, retentionInDays=7)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise

        # ── ECS cluster ───────────────────────────────────────────────────────
        print(f"  [boto3] Ensuring ECS cluster '{project_name}'...")
        ecs.create_cluster(clusterName=project_name)

        # ── Wait for RDS ──────────────────────────────────────────────────────
        print(f"  [boto3] Waiting for RDS to become available...", flush=True)
        deadline = time.time() + 900
        rds_endpoint = None
        while True:
            deadline = time.time() + 900
            while time.time() < deadline:
                try:
                    resp = rds.describe_db_instances(DBInstanceIdentifier=project_name)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "DBInstanceNotFound":
                        time.sleep(15)
                        continue
                    raise
                inst   = resp["DBInstances"][0]
                status = inst["DBInstanceStatus"]
                if status == "available":
                    rds_endpoint = inst["Endpoint"]["Address"]
                    print(f"  [boto3] ✓ RDS available — {rds_endpoint}")
                    break
                print(f"  [boto3] RDS status: {status}...", end="\r", flush=True)
                time.sleep(15)

            if rds_endpoint:
                break
            self._wait_for_user_action(
                f"RDS instance '{project_name}' did not become available within 15 minutes.",
                [
                    f"Check the RDS console: https://console.aws.amazon.com/rds/",
                    f"Look for instance: {project_name}  (region: {region})",
                    "If status is 'failed' or 'incompatible-parameters': delete the instance and press Enter to retry provisioning",
                    "If status is still 'creating': press Enter to wait another 15 minutes",
                ],
            )

        # ── Task definition (placeholder image — reconciliation apply updates it) ──
        db_url = f"postgresql://postgres:{db_password}@{rds_endpoint}:5432/{db_name}"
        print(f"  [boto3] Registering placeholder task definition '{project_name}'...")
        td_resp = ecs.register_task_definition(
            family=project_name,
            networkMode="awsvpc",
            requiresCompatibilities=["FARGATE"],
            cpu="256",
            memory="512",
            executionRoleArn=role_arn,
            containerDefinitions=[{
                "name": project_name,
                "image": f"{ecr_url}:placeholder",
                "portMappings": [{"containerPort": 3000, "protocol": "tcp"}],
                "environment": [
                    {"name": "NODE_ENV",     "value": "production"},
                    {"name": "PORT",         "value": "3000"},
                    {"name": "DATABASE_URL", "value": db_url},
                ],
                "essential": True,
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group":         log_group,
                        "awslogs-region":        region,
                        "awslogs-stream-prefix": "ecs",
                    },
                },
            }],
        )
        td_arn      = td_resp["taskDefinition"]["taskDefinitionArn"]
        td_revision = td_resp["taskDefinition"]["revision"]

        # ── ECS service (desired_count=0 — real image pushed after this method) ──
        print(f"  [boto3] Ensuring ECS service '{project_name}'...")
        try:
            ecs.create_service(
                cluster=project_name,
                serviceName=project_name,
                taskDefinition=td_arn,
                desiredCount=0,
                launchType="FARGATE",
                networkConfiguration={"awsvpcConfiguration": {
                    "subnets": subnet_ids,
                    "securityGroups": [ecs_sg_id],
                    "assignPublicIp": "ENABLED",
                }},
                loadBalancers=[{
                    "targetGroupArn": tg_arn,
                    "containerName": project_name,
                    "containerPort": 3000,
                }],
            )
        except ClientError as e:
            if e.response["Error"]["Code"] not in (
                "ServiceAlreadyExistsException", "ServiceNotActiveException"
            ):
                raise
            print(f"  [boto3] ECS service '{project_name}' already exists.")

        return {
            "alb_sg_id":      alb_sg_id,
            "ecs_sg_id":      ecs_sg_id,
            "rds_sg_id":      rds_sg_id,
            "ecr_url":        ecr_url,
            "rds_endpoint":   rds_endpoint,
            "db_password":    db_password,
            "jwt_secret":     jwt_secret,
            "db_url":         db_url,
            "role_name":      role_name,
            "role_arn":       role_arn,
            "policy_arn":     policy_arn,
            "alb_arn":        alb_arn,
            "alb_dns":        alb_dns,
            "tg_arn":         tg_arn,
            "listener_arn":   listener_arn,
            "log_group":      log_group,
            "td_arn":         td_arn,
            "td_revision":    td_revision,
            "subnet_group":   subnet_group,
            "subnet_ids":     subnet_ids,
            "vpc_id":         vpc_id,
        }

    def _ensure_sg_boto3(
        self,
        ec2: Any,
        vpc_id: str,
        name: str,
        description: str,
        rules: list[dict[str, Any]],
    ) -> str:
        """Create (or return existing) security group and ensure ingress rules are present."""
        from botocore.exceptions import ClientError
        try:
            sg_id = ec2.create_security_group(
                GroupName=name, Description=description, VpcId=vpc_id
            )["GroupId"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidGroup.Duplicate":
                raise
            sg_id = ec2.describe_security_groups(Filters=[
                {"Name": "group-name", "Values": [name]},
                {"Name": "vpc-id",     "Values": [vpc_id]},
            ])["SecurityGroups"][0]["GroupId"]

        for rule in rules:
            perm: dict[str, Any] = {
                "IpProtocol": rule["proto"],
                "FromPort":   rule["from_port"],
                "ToPort":     rule["to_port"],
            }
            if "cidr" in rule:
                perm["IpRanges"] = [{"CidrIp": rule["cidr"]}]
            else:
                perm["UserIdGroupPairs"] = [{"GroupId": rule["src_sg"]}]
            try:
                ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[perm])
            except ClientError as e:
                if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                    raise
        return sg_id

    def _ensure_alb_boto3(
        self, elb: Any, name: str, subnet_ids: list[str], sg_id: str
    ) -> tuple[str, str]:
        """Create (or return existing) Application Load Balancer. Returns (arn, dns_name)."""
        from botocore.exceptions import ClientError
        try:
            lbs = elb.describe_load_balancers(Names=[name])["LoadBalancers"]
            return lbs[0]["LoadBalancerArn"], lbs[0]["DNSName"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "LoadBalancerNotFound":
                raise
        lb = elb.create_load_balancer(
            Name=name,
            Subnets=subnet_ids,
            SecurityGroups=[sg_id],
            Scheme="internet-facing",
            Type="application",
            IpAddressType="ipv4",
        )["LoadBalancers"][0]
        return lb["LoadBalancerArn"], lb["DNSName"]

    def _ensure_target_group_boto3(self, elb: Any, name: str, vpc_id: str) -> str:
        """Create (or return existing) target group for port 3000. Returns ARN."""
        from botocore.exceptions import ClientError
        try:
            resp = elb.describe_target_groups(Names=[name])
            if resp["TargetGroups"]:
                return resp["TargetGroups"][0]["TargetGroupArn"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "TargetGroupNotFound":
                raise
        tg = elb.create_target_group(
            Name=name,
            Protocol="HTTP",
            Port=3000,
            VpcId=vpc_id,
            TargetType="ip",
            HealthCheckProtocol="HTTP",
            HealthCheckPath="/health",
            HealthyThresholdCount=2,
            UnhealthyThresholdCount=3,
            HealthCheckIntervalSeconds=30,
        )["TargetGroups"][0]
        return tg["TargetGroupArn"]

    def _ensure_alb_listener_boto3(self, elb: Any, alb_arn: str, tg_arn: str) -> str:
        """Create (or return existing) HTTP:80 listener forwarding to target group. Returns ARN."""
        from botocore.exceptions import ClientError
        existing = elb.describe_listeners(LoadBalancerArn=alb_arn)["Listeners"]
        for l in existing:
            if l["Port"] == 80:
                return l["ListenerArn"]
        return elb.create_listener(
            LoadBalancerArn=alb_arn,
            Protocol="HTTP",
            Port=80,
            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
        )["Listeners"][0]["ListenerArn"]

    def _terraform_import_aws(
        self,
        creds: dict[str, Any],
        project_name: str,
        pr: dict[str, Any],
    ) -> None:
        """
        Import all 15 boto3-created resources into Terraform state, then run a
        reconciliation apply so the state is completely up-to-date.

        After this method returns:
        - terraform plan  → "No changes"
        - terraform apply → safely modifies individual resources
        - terraform destroy → cleanly destroys all 15 resources
        """
        import json as _json

        tf_dir = self.out_dir / "terraform"
        region = creds["region"]

        # tfvars with the real image tag — reconciliation apply updates the task def
        tfvars_path = tf_dir / "terraform.auto.tfvars.json"
        tfvars_path.write_text(_json.dumps({
            "project_name":  project_name,
            "aws_region":    region,
            "db_password":   pr["db_password"],
            "jwt_secret":    pr["jwt_secret"],
            "ecr_image_tag": "latest",
        }, indent=2))

        tf_env = {
            **os.environ,
            "AWS_ACCESS_KEY_ID":     creds.get("access_key", ""),
            "AWS_SECRET_ACCESS_KEY": creds.get("secret_key", ""),
            "AWS_DEFAULT_REGION":    region,
        }
        if creds.get("session_token"):
            tf_env["AWS_SESSION_TOKEN"] = creds["session_token"]

        if not (tf_dir / ".terraform" / "providers").exists():
            print("\n  [Terraform] Initializing backend...")
            self._tf_run(tf_dir, ["terraform", "init", "-input=false"], env=tf_env)
        else:
            print("\n  [Terraform] Backend already initialized — skipping init.")

        imports = [
            ("aws_security_group.alb",                    pr["alb_sg_id"]),
            ("aws_security_group.ecs",                    pr["ecs_sg_id"]),
            ("aws_security_group.rds",                    pr["rds_sg_id"]),
            ("aws_ecr_repository.main",                   project_name),
            ("aws_db_subnet_group.main",                  pr["subnet_group"]),
            ("aws_db_instance.main",                      project_name),
            ("aws_iam_role.ecs_execution",                pr["role_name"]),
            ("aws_iam_role_policy_attachment.ecs_execution",
             f"{pr['role_name']}/{pr['policy_arn']}"),
            ("aws_lb.main",                               pr["alb_arn"]),
            ("aws_lb_target_group.main",                  pr["tg_arn"]),
            ("aws_lb_listener.http",                      pr["listener_arn"]),
            ("aws_cloudwatch_log_group.main",             pr["log_group"]),
            ("aws_ecs_cluster.main",                      project_name),
            ("aws_ecs_task_definition.main",
             f"{project_name}:{pr['td_revision']}"),
            ("aws_ecs_service.main",                      f"{project_name}/{project_name}"),
        ]

        print(f"\n  [Terraform] Importing {len(imports)} resources into state...")
        ok = 0
        for addr, res_id in imports:
            r = subprocess.run(
                ["terraform", "import", "-input=false", addr, res_id],
                cwd=tf_dir, env=tf_env, capture_output=True, text=True,
            )
            if r.returncode == 0 or "already managed" in (r.stderr + r.stdout).lower():
                ok += 1
            else:
                print(f"  [Terraform] Warning: could not import {addr}: {r.stderr.strip()[:120]}")
        print(f"  [Terraform] {ok}/{len(imports)} resources in state.")

        # Reconciliation apply — makes state completely up-to-date:
        # creates new task def revision with real image, sets desired_count=1
        print(f"\n  [Terraform] Reconciliation apply (updates task def + starts ECS deploy)...")
        self._tf_run(tf_dir, ["terraform", "apply", "-auto-approve", "-input=false"], env=tf_env)
        print(f"  [Terraform] ✓ State is fully up-to-date — terraform destroy/apply/plan will work.")

    def _tf_run(self, tf_dir: Path, cmd: list[str], env: dict | None = None) -> None:
        """Run a Terraform command. Auto-recovers from stale locks; prompts on user-fixable errors."""
        result = subprocess.run(cmd, cwd=tf_dir, env=env, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout, end="")
        if result.returncode == 0:
            return

        combined = result.stdout + result.stderr

        # ── Auto-recover stale DynamoDB/GCS lock ──────────────────────────────
        lock_match = re.search(r'ID:\s+([0-9a-f-]{36})', combined)
        if lock_match and "state lock" in combined.lower():
            lock_id = lock_match.group(1)
            print(f"\n  [Terraform] Stale lock detected (ID: {lock_id}) — auto-unlocking...")
            unlock = subprocess.run(
                ["terraform", "force-unlock", "-force", lock_id],
                cwd=tf_dir, env=env, capture_output=True, text=True,
            )
            if unlock.returncode == 0:
                print("  Lock cleared — retrying command...")
                retry = subprocess.run(cmd, cwd=tf_dir, env=env, capture_output=True, text=True)
                if retry.stdout:
                    print(retry.stdout, end="")
                if retry.returncode == 0:
                    return
                result = retry
                combined = result.stdout + result.stderr

        # Derive project name from tf_dir for actionable messages (tf_dir = <out_dir>/terraform).
        project = tf_dir.parent.name

        # ── Stale S3/GCS state checksum conflict ──────────────────────────────
        if any(p in combined for p in (
            "checksum", "digest mismatch",
            "state data in S3 does not have",
            "Error refreshing state",
        )):
            self._wait_for_user_action(
                "Terraform found a stale state file — the S3/GCS state does not match the DynamoDB lock table.",
                [
                    f"Open DynamoDB console → table '{project}-tf-lock'",
                    f"  Delete the item whose LockID ends with '/terraform.tfstate'",
                    f"Open S3 console → bucket '{project}-tf-state'",
                    f"  Delete 'terraform.tfstate' (contains stale data from a prior run)",
                    "Press Enter — Terraform will initialize with a fresh empty state",
                ],
            )
            retry = subprocess.run(cmd, cwd=tf_dir, env=env, capture_output=True, text=True)
            if retry.stdout:
                print(retry.stdout, end="")
            if retry.returncode == 0:
                return
            result = retry

        # ── State backend bucket missing ──────────────────────────────────────
        elif any(p in combined.lower() for p in (
            "nosuchbucket", "no such bucket", "bucketnotfound",
        )):
            self._wait_for_user_action(
                "Terraform state backend bucket not found — it may have been deleted.",
                [
                    "Re-run the deployment from the beginning",
                    "  The bootstrap step will recreate the S3 bucket and DynamoDB table",
                ],
            )
            retry = subprocess.run(cmd, cwd=tf_dir, env=env, capture_output=True, text=True)
            if retry.stdout:
                print(retry.stdout, end="")
            if retry.returncode == 0:
                return
            result = retry

        # ── Backend credentials rejected ──────────────────────────────────────
        elif any(p in combined for p in (
            "AccessDenied",
            "Error: error configuring S3 Backend",
            "googleapi: Error 403",
        )):
            self._wait_for_user_action(
                "Terraform backend access denied — credentials may be expired or lack S3/GCS permissions.",
                [
                    "For AWS: export fresh credentials:",
                    "  export AWS_ACCESS_KEY_ID=<key>",
                    "  export AWS_SECRET_ACCESS_KEY=<secret>",
                    "  export AWS_SESSION_TOKEN=<token>  # if using STS",
                    "For GCP: re-authenticate:",
                    "  gcloud auth application-default login",
                ],
            )
            retry = subprocess.run(cmd, cwd=tf_dir, env=env, capture_output=True, text=True)
            if retry.stdout:
                print(retry.stdout, end="")
            if retry.returncode == 0:
                return
            result = retry

        # ── GCP API not enabled ───────────────────────────────────────────────
        elif any(p in combined for p in (
            "has not been used in project",
            "API has not been enabled",
            "SERVICE_DISABLED",
        )):
            self._wait_for_user_action(
                "A required GCP API is not enabled for this project.",
                [
                    "Open GCP console → APIs & Services → Library",
                    "Enable the API named in the error above",
                    "Common APIs needed: Cloud Run API, Cloud SQL Admin API, Artifact Registry API",
                ],
            )
            retry = subprocess.run(cmd, cwd=tf_dir, env=env, capture_output=True, text=True)
            if retry.stdout:
                print(retry.stdout, end="")
            if retry.returncode == 0:
                return
            result = retry

        print(f"\nTerraform command failed: {' '.join(cmd)}\n{result.stderr}", file=sys.stderr)
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

    def _run_ecs_migration(
        self, project_name: str, db_url: str, region: str, creds: dict[str, Any]
    ) -> None:
        """
        Run `prisma db push` as a one-off ECS Fargate task inside the VPC.

        RDS is in a private subnet — port 5432 is only reachable from within
        the VPC security group. We reuse the existing task definition (which
        already has DATABASE_URL set) and override the container command.
        The network config is read from the ECS service so we land in the same
        subnets and security group that have RDS access.
        """
        import boto3

        session = boto3.Session(
            aws_access_key_id=creds.get("access_key"),
            aws_secret_access_key=creds.get("secret_key"),
            aws_session_token=creds.get("session_token"),
            region_name=region,
        )
        ecs = session.client("ecs")

        # Borrow network config from the service — same subnets + security group.
        svc_resp = ecs.describe_services(cluster=project_name, services=[project_name])
        network_config = svc_resp["services"][0]["networkConfiguration"]

        print("\n  Running Prisma migration as ECS task inside VPC...")
        run_resp = ecs.run_task(
            cluster=project_name,
            taskDefinition=project_name,
            launchType="FARGATE",
            networkConfiguration=network_config,
            overrides={
                "containerOverrides": [{
                    "name": project_name,
                    "command": [
                        "sh", "-c",
                        "npx prisma db push --accept-data-loss",
                    ],
                    "environment": [{"name": "DATABASE_URL", "value": db_url}],
                }]
            },
        )

        failures = run_resp.get("failures", [])
        if not run_resp.get("tasks"):
            print(
                f"\n  Warning: ECS migration task did not start: {failures}\n"
                "  Apply the schema manually:\n"
                f"    DATABASE_URL='{db_url}' npx prisma db push --accept-data-loss",
                file=sys.stderr,
            )
            return

        task_arn = run_resp["tasks"][0]["taskArn"]
        short_id = task_arn.split("/")[-1]
        print(f"  Migration task started: {short_id} — waiting for completion...")

        waiter = ecs.get_waiter("tasks_stopped")
        waiter.wait(
            cluster=project_name,
            tasks=[task_arn],
            WaiterConfig={"Delay": 10, "MaxAttempts": 60},   # up to 10 minutes
        )

        result = ecs.describe_tasks(cluster=project_name, tasks=[task_arn])
        container = result["tasks"][0]["containers"][0]
        exit_code = container.get("exitCode")
        if exit_code == 0:
            print("  ✓ Prisma migration completed successfully.")
        else:
            reason = container.get("reason", "unknown error")
            print(
                f"\n  Warning: Prisma migration task exited with code {exit_code}: {reason}\n"
                "  Apply the schema manually:\n"
                f"    DATABASE_URL='{db_url}' npx prisma db push --accept-data-loss",
                file=sys.stderr,
            )

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
        Hybrid Python SDK + terraform import GCP deployment.

        Flow:
          1. _gcloud_provision_gcp(): enable APIs, kick off Cloud SQL (project_name as
             instance name — matches var.project_name in Terraform), create Artifact
             Registry in parallel (~30s), wait for Cloud SQL, create db + user.
          2. Apply Prisma schema to Cloud SQL public IP.
          3. Build Docker image locally.
          4. Push image to Artifact Registry.
          5. Deploy Cloud Run service via provider._deploy_cloud_run() SDK call.
          6. Set IAM via provider._allow_unauthenticated().
          7. Write tfvars with real db_password, jwt_secret, image_tag="latest".
          8. _terraform_import_gcp(): terraform init + import 10 resources +
             reconciliation apply — gives Terraform full lifecycle ownership.
        """
        import json
        import secrets as _secrets

        tf_dir = self.out_dir / "terraform"
        project_id = creds.get("project_id", "")
        region = creds.get("region", "us-central1")

        env_file_vars = self._read_env_file()
        db_password = env_file_vars.get("DB_PASSWORD") or _secrets.token_urlsafe(24)
        jwt_secret  = env_file_vars.get("JWT_SECRET")  or _secrets.token_urlsafe(32)

        db_name   = project_name.replace("-", "_")
        ar_host   = f"{region}-docker.pkg.dev"
        image_uri = f"{ar_host}/{project_id}/developable/{project_name}:latest"

        # ── Step 1: provision Cloud SQL + Artifact Registry via Python SDK ────
        print(
            f"\n  ┌─ Step 3/8 — Provision GCP infrastructure via Python SDK ────────────\n"
            f"  │  ETA: ~8–10 min  (Cloud SQL 8–10 min; Artifact Registry ~30s in parallel)\n"
            f"  │  Monitor Cloud SQL:  https://console.cloud.google.com/sql/instances?project={project_id}\n"
            f"  │  Monitor AR:         https://console.cloud.google.com/artifacts?project={project_id}\n"
            f"  └────────────────────────────────────────────────────────────────────────"
        )
        cloud_sql_ip = self._gcloud_provision_gcp(
            provider, creds, project_name, db_password, project_id, region
        )

        # ── Step 2: apply Prisma schema ────────────────────────────────────────
        db_url = f"postgresql://postgres:{db_password}@{cloud_sql_ip}:5432/{db_name}"
        self._wait_for_db_tcp(cloud_sql_ip, 5432)
        print("\n  [Step 4/8] Applying Prisma schema to Cloud SQL...")
        provider.apply_schema(db_url)

        # ── Step 3: build Docker image ─────────────────────────────────────────
        local_tag = f"developable/{project_name}:latest"
        print(f"\n  ┌─ Step 5/8 — Build Docker image  (ETA: ~3 min) ─────────────────────")
        print(f"  └─ Image tag: {local_tag}")
        self._docker_build(local_tag)

        # ── Step 4: push to Artifact Registry ─────────────────────────────────
        print(f"\n  ┌─ Step 6/8 — Push image to Artifact Registry  (ETA: ~1 min) ────────")
        print(f"  └─ Target:    {image_uri}")
        provider._push_to_registry(creds, local_tag, image_uri, ar_host)

        # ── Step 5: deploy Cloud Run via SDK ──────────────────────────────────
        print(
            f"\n  ┌─ Step 7/8 — Deploy Cloud Run service  (ETA: ~1 min) ───────────────\n"
            f"  │  Monitor: https://console.cloud.google.com/run?project={project_id}\n"
            f"  └────────────────────────────────────────────────────────────────────────"
        )
        gcp_creds = provider._load_credentials(creds)
        env_vars = {
            "NODE_ENV":     "production",
            "PORT":         "3000",
            "DATABASE_URL": db_url,
            "JWT_SECRET":   jwt_secret,
        }
        labels = provider._normalise_labels(
            provider.build_tags(project_name, deployment_id, spec)
        )
        cloud_run_url = provider._deploy_cloud_run(
            gcp_creds, project_id, region, project_name, image_uri, env_vars, labels
        )

        # ── Step 6: allow unauthenticated access ──────────────────────────────
        print(f"\n  [GCP] Setting IAM policy (allow unauthenticated)...")
        provider._allow_unauthenticated(gcp_creds, project_id, region, project_name)

        # ── Step 7: write tfvars ───────────────────────────────────────────────
        tfvars_path = tf_dir / "terraform.auto.tfvars.json"
        tfvars_path.write_text(json.dumps({
            "project_name": project_name,
            "gcp_project":  project_id,
            "gcp_region":   region,
            "db_password":  db_password,
            "jwt_secret":   jwt_secret,
            "image_tag":    "latest",
        }, indent=2))

        # ── Step 8: terraform import + reconciliation apply ────────────────────
        print(
            f"\n  ┌─ Step 8/8 — Terraform import + reconciliation apply  (ETA: ~2 min) ──\n"
            f"  │  Imports 10 resources into state so terraform destroy/apply/plan work.\n"
            f"  └────────────────────────────────────────────────────────────────────────"
        )
        tf_env = {**os.environ, "GOOGLE_CLOUD_PROJECT": project_id}
        if creds.get("credentials_file"):
            tf_env["GOOGLE_APPLICATION_CREDENTIALS"] = creds["credentials_file"]
        self._terraform_import_gcp(creds, project_name, project_id, region, db_name, tf_env)

        endpoint = cloud_run_url or f"https://{project_name}.run.app"
        print(f"\n  ✓ Deployed — endpoint: {endpoint}")

        from core.deployment_state import DeploymentState
        return DeploymentState.make_record(
            provider="gcp",
            region=region,
            endpoint=endpoint,
            image_uri=image_uri,
            resources=[{"type": "terraform_managed", "id": project_name, "arn": None}],
            tags=provider.build_tags(project_name, deployment_id, spec),
        )

    def _gcloud_provision_gcp(
        self,
        provider: Any,
        creds: dict[str, Any],
        project_name: str,
        db_password: str,
        project_id: str,
        region: str,
    ) -> str:
        """
        Provision Cloud SQL and Artifact Registry via the GCP Python SDK.

        Cloud SQL is kicked off immediately (8-10 min) while Artifact Registry
        is created in parallel (~30s). Returns the Cloud SQL public IP.

        Cloud SQL instance name is project_name — this matches var.project_name
        in the Terraform template so terraform import succeeds. Do NOT use
        f"{project_name}-db" (that is the legacy provision_database() naming).
        """
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
        sqladmin   = gapi_build("sqladmin", "v1", credentials=gcp_creds)
        db_name    = project_name.replace("-", "_")
        labels     = provider._normalise_labels(
            provider.build_tags(project_name, "gcp-provision", {})
        )

        print(f"  [GCP] Enabling required APIs...")
        provider._ensure_apis_enabled(project_id, gcp_creds)

        # Kick off Cloud SQL; instance name = project_name (must match Terraform)
        print(
            f"  [GCP] Kicking off Cloud SQL provisioning (8–10 min) "
            f"— creating Artifact Registry in parallel..."
        )
        provider._ensure_cloud_sql_instance(sqladmin, project_id, project_name, region, labels)

        # Create Artifact Registry while Cloud SQL provisions (~30s)
        print(f"  [GCP] Creating Artifact Registry repository 'developable'...")
        provider._ensure_artifact_registry_repo(gcp_creds, project_id, region)

        # Wait for Cloud SQL to reach RUNNABLE state
        print(f"  [GCP] Waiting for Cloud SQL to become available...", flush=True)
        public_ip = provider._wait_for_cloud_sql(sqladmin, project_id, project_name)
        print(f"  [GCP] ✓ Cloud SQL available — {public_ip}")

        print(f"  [GCP] Creating database '{db_name}' and setting postgres password...")
        provider._create_database(sqladmin, project_id, project_name, db_name)
        provider._create_db_user(sqladmin, project_id, project_name, "postgres", db_password)

        return public_ip

    def _terraform_import_gcp(
        self,
        creds: dict[str, Any],
        project_name: str,
        project_id: str,
        region: str,
        db_name: str,
        tf_env: dict[str, str],
    ) -> None:
        """
        Import all 10 SDK-provisioned GCP resources into Terraform state, then
        run a reconciliation apply so state is completely up-to-date.

        After this method returns:
        - terraform plan  → "No changes"
        - terraform apply → safely modifies individual resources
        - terraform destroy → cleanly destroys all 10 resources
        """
        tf_dir = self.out_dir / "terraform"

        if not (tf_dir / ".terraform" / "providers").exists():
            print("\n  [Terraform] Initializing backend...")
            self._tf_run(tf_dir, ["terraform", "init", "-input=false"], env=tf_env)
        else:
            print("\n  [Terraform] Backend already initialized — skipping init.")

        imports = [
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
        ]

        print(f"\n  [Terraform] Importing {len(imports)} resources into state...")
        ok = 0
        for addr, res_id in imports:
            r = subprocess.run(
                ["terraform", "import", "-input=false", addr, res_id],
                cwd=tf_dir, env=tf_env, capture_output=True, text=True,
            )
            if r.returncode == 0 or "already managed" in (r.stderr + r.stdout).lower():
                ok += 1
            else:
                print(f"  [Terraform] Warning: could not import {addr}: {r.stderr.strip()[:120]}")
        print(f"  [Terraform] {ok}/{len(imports)} resources in state.")

        print(f"\n  [Terraform] Reconciliation apply (confirms state matches deployed resources)...")
        self._tf_run(tf_dir, ["terraform", "apply", "-auto-approve", "-input=false"], env=tf_env)
        print(f"  [Terraform] ✓ State is fully up-to-date — terraform destroy/apply/plan will work.")

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

        # Heroku API key passed via env var — never written to a file.
        tf_env = {**os.environ, "TF_VAR_heroku_api_key": api_key}

        if not (tf_dir / ".terraform" / "providers").exists():
            print("\n  [Terraform] Initializing (local state)...")
            self._tf_run(tf_dir, ["terraform", "init", "-input=false"], env=tf_env)
        else:
            print("\n  [Terraform] Backend already initialized — skipping init.")

        print(
            f"\n  ┌─ Step 2/6 — Terraform apply: Heroku app + Postgres addon  (ETA: ~2 min)\n"
            f"  │  Monitor: https://dashboard.heroku.com/apps\n"
            f"  └─────────────────────────────────────────────────────────────────────────\n"
        )
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
        print(f"\n  ┌─ Step 4/6 — Build Docker image  (ETA: ~3 min) ─────────────────────")
        print(f"  └─ Image tag: {local_tag}")
        self._docker_build(local_tag)

        heroku_image = f"registry.heroku.com/{project_name}/web"
        print(f"\n  ┌─ Step 5/6 — Push image + release container  (ETA: ~1 min) ─────────")
        print(f"  │  Monitor: https://dashboard.heroku.com/apps/{project_name}/activity")
        print(f"  └─ Target:  {heroku_image}")
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
