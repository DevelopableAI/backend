"""
Deployment Agent.

The Deployment Agent is the fourth component of the Backend Engineer. It takes
a fully-generated Express API project (output of Developer + optionally Tester
and VersionControl) and deploys it to a cloud provider of the user's choice.

Responsibilities
────────────────
1. Present an interactive provider menu (or accept --deploy-to via CLI).
2. Detect existing credentials; prompt for missing ones.
3. Ensure the Dockerfile exists in the output directory (generate it via
   VCPlanner if absent — same approach as VersionControl agent).
4. Build a Docker image from the output directory.
5. Delegate push + deploy to the chosen cloud provider implementation.
6. Record the deployment result in <out_dir>/.developable/state.json.
7. Print the live endpoint URL.

Zero LLM cost
─────────────
This agent makes no Anthropic API calls. All operations are pure Python SDK /
subprocess calls against the cloud provider APIs.

Usage (from main.py)
────────────────────
    deployer = Deployment(out_dir=out_dir, provider="aws", aws_region="eu-west-1")
    record = deployer.deploy(spec, api_plan)
    print(record["endpoint"])
"""

import subprocess
import sys
from pathlib import Path
from typing import Any

from core.deployment_state import DeploymentState
from core.providers import PROVIDER_MAP, get_provider


class Deployment:
    """
    The Deployment Agent.

    Args:
        out_dir:   Path to the generated project directory (must contain a
                   Dockerfile or one will be generated).
        provider:  Cloud provider slug ("aws", "heroku", "gcp"). If None, the
                   user is prompted interactively.
        **kwargs:  Provider-specific configuration forwarded to the provider
                   constructor (e.g. aws_region, heroku_app, gcp_project,
                   gcp_region).
    """

    def __init__(
        self,
        out_dir: Path,
        provider: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.out_dir = out_dir
        self.provider_name = provider
        self.provider_kwargs = self._normalise_kwargs(kwargs)

    # ── Public API ─────────────────────────────────────────────────────────────

    def deploy(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> dict[str, Any]:
        """
        Run the full deployment pipeline.

        Args:
            spec:     Parsed Prisma spec (from PrismaParser).
            api_plan: File plan returned by the Developer agent.

        Returns:
            A deployment record dict (also persisted to state.json).
        """
        # 1. Resolve provider
        provider_name = self.provider_name or self._ask_provider()

        # 2. Instantiate provider
        kwargs = self.provider_kwargs.get(provider_name, {})
        provider = get_provider(provider_name, self.out_dir, **kwargs)

        # 3. Credentials
        print(f"\n  Detecting {provider.display_name} credentials...")
        creds = provider.detect_credentials()
        if creds is None:
            creds = provider.collect_credentials()
        else:
            print(f"  Found existing credentials.")
        provider.configure(creds)

        # 4. Ensure Dockerfile exists
        self._ensure_dockerfile(spec)

        # 5. Build Docker image
        project_name = provider.slug(spec)
        image_tag = f"developable/{project_name}:latest"
        print(f"\n  Building Docker image '{image_tag}'...")
        self._docker_build(image_tag)

        # 6. Read env vars from .env file
        env_vars = self._read_env_file()
        if "DATABASE_URL" not in env_vars or not env_vars["DATABASE_URL"]:
            print(
                "\n  Warning: DATABASE_URL is not set in .env. "
                "The deployed container may fail to connect to Postgres.\n"
                "  Set DATABASE_URL in .env before deploying, or configure it "
                "in your cloud provider's environment settings.",
            )

        # 7. Deploy via provider
        import uuid
        deployment_id = str(uuid.uuid4())
        print(f"\n  Deploying to {provider.display_name}...")
        record = provider.deploy(spec, image_tag, env_vars, deployment_id)

        # 8. Persist state
        state = DeploymentState(self.out_dir)
        state.initialise(
            project_name=project_name,
            schema_path=str(spec.get("schema_path", "")),
        )
        state.add(record)
        state.save()

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
            # Accept number
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(providers):
                    return providers[idx][0]
            # Accept slug directly
            if choice in PROVIDER_MAP:
                return choice
            print(f"  Invalid choice. Enter 1-{len(providers)} or one of: {', '.join(PROVIDER_MAP)}")

    def _ensure_dockerfile(self, spec: dict[str, Any]) -> None:
        """
        Generate infrastructure files (Dockerfile, docker-compose, CI) if the
        Dockerfile is absent. Mirrors the VersionControl agent's _generate_infra_files.
        """
        dockerfile = self.out_dir / "Dockerfile"
        if dockerfile.exists():
            return

        print("  Dockerfile not found — generating infrastructure files...")
        from core.vc_planner import VCPlanner
        from core.assembler import Assembler

        plan = VCPlanner().plan(spec)
        assembler = Assembler(out_dir=self.out_dir, use_llm=False)
        assembler.assemble(spec, plan)
        print(f"  Generated {len(plan['files'])} infrastructure file(s).")

    def _docker_build(self, image_tag: str) -> None:
        """Build the Docker image from the output directory."""
        result = subprocess.run(
            ["docker", "build", "-t", image_tag, str(self.out_dir)],
            capture_output=False,  # show build output to user
        )
        if result.returncode != 0:
            print(
                "\nDocker build failed. Ensure Docker is running and the "
                "Dockerfile in the output directory is valid.",
                file=sys.stderr,
            )
            sys.exit(1)

    def _read_env_file(self) -> dict[str, str]:
        """
        Parse <out_dir>/.env into a key-value dict.
        Skips comments and blank lines. Returns empty dict if file is absent.
        """
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

    @staticmethod
    def _normalise_kwargs(kwargs: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """
        Map flat CLI kwargs (aws_region, heroku_app, gcp_project, gcp_region)
        to per-provider dicts passed to provider constructors.
        """
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
