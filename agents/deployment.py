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

import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from core.deployment_state import DeploymentState
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
          2. Ensure Dockerfile exists.
          3. Provision managed PostgreSQL database.
          4. Apply Prisma schema to remote database.
          5. Build Docker image.
          6. Deploy container (with remote DATABASE_URL injected).
          7. Persist deployment state.
          8. Push CI/CD deploy workflow to GitHub (if remote configured).
          9. Run remote smoke tests.

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

        # ── 2. Ensure Dockerfile ───────────────────────────────────────────────
        self._ensure_dockerfile(spec)

        # ── 3. Provision database ──────────────────────────────────────────────
        # For Heroku: the app must exist before we can add the addon, so we
        # allow providers to set up prerequisites in provision_database()
        # themselves (Heroku creates the app in deploy(), so we call deploy()
        # first and provision_database() second for Heroku).
        if provider_name == "heroku":
            # Heroku needs the app to exist before addon provisioning.
            # Build + push + release first, then add the addon.
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

            # Heroku sets DATABASE_URL in config vars automatically after addon provisioning.
            # The app restarts on config var changes, so no extra release needed.
            record["resources"].extend(db_resources)

        else:
            # AWS / GCP: provision DB first, then deploy container.
            print(f"\n  Provisioning managed PostgreSQL database...")
            db_url, db_resources = provider.provision_database(spec, project_name, deployment_id)

            print(f"\n  Applying Prisma schema to remote database...")
            provider.apply_schema(db_url)

            # Build image
            image_tag = f"developable/{project_name}:latest"
            print(f"\n  Building Docker image '{image_tag}'...")
            self._docker_build(image_tag)

            # Override DATABASE_URL with the remote DB URL before deploying
            env_vars = self._read_env_file()
            env_vars["DATABASE_URL"] = db_url

            print(f"\n  Deploying container to {provider.display_name}...")
            record = provider.deploy(spec, image_tag, env_vars, deployment_id)
            record["resources"].extend(db_resources)

        # ── 7. Persist state ───────────────────────────────────────────────────
        state = DeploymentState(self.out_dir)
        state.initialise(
            project_name=project_name,
            schema_path=str(spec.get("schema_path", "")),
        )
        state.add(record)
        state.save()

        # ── 8. Push CI/CD deploy workflow ──────────────────────────────────────
        if self._has_github_remote():
            print(f"\n  Generating and pushing CI/CD deploy workflow to GitHub...")
            workflow_yaml = provider.generate_deploy_workflow(project_name, record)
            pushed = self._push_workflow_to_github(workflow_yaml)
            if pushed:
                self._print_secrets_instructions(provider_name, creds)

        # ── 9. Remote smoke tests ──────────────────────────────────────────────
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
        """Build the Docker image from the output directory, streaming output."""
        result = subprocess.run(
            ["docker", "build", "-t", image_tag, str(self.out_dir)],
        )
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
                f"  Run manually: python {tests_dir}/run_all.py <endpoint>"
            )
            return

        print(f"\n  Running remote smoke tests against {endpoint}...")
        print(f"  (test files are NOT modified — URL passed as argument)\n")
        subprocess.run(
            ["python", str(tests_dir / "run_all.py"), endpoint],
        )
        # Non-fatal: test failures are printed but do not halt the deployment pipeline.

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
