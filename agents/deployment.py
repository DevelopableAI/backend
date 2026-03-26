"""
Deployment Agent.

The Deployment Agent is the fourth component of the Backend Engineer. It takes
an already generated API project and deploys it to a chosen cloud provider.

This agent intentionally avoids LLM calls to keep runtime cost minimal.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.deployment_state import DeploymentState
from core.providers import PROVIDER_MAP, get_provider


class Deployment:
    def __init__(
        self,
        out_dir: Path,
        provider: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.out_dir = out_dir
        self.provider_name = provider
        self.provider_kwargs = self._normalise_kwargs(kwargs)

    def deploy(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> dict[str, Any]:
        provider_name = self.provider_name or self._ask_provider()
        kwargs = self.provider_kwargs.get(provider_name, {})
        provider = get_provider(provider_name, self.out_dir, **kwargs)

        print(f"\n  Detecting {provider.display_name} credentials...")
        creds = provider.detect_credentials()
        if creds is None:
            creds = provider.collect_credentials()
        else:
            print("  Found existing credentials.")
        provider.configure(creds)

        env_vars = self._read_env_file()
        db_resource = None
        print(f"\n  Provisioning managed database on {provider.display_name}...")
        db_result = provider.provision_database(spec)
        if not db_result or not db_result.get("database_url"):
            print(
                "\nError: provider did not return a remote DATABASE_URL. "
                "Deployment requires remote DB provisioning first.",
                file=sys.stderr,
            )
            sys.exit(1)
        env_vars["DATABASE_URL"] = db_result["database_url"]
        db_resource = db_result.get("resource")
        self._upsert_env("DATABASE_URL", db_result["database_url"])
        print("  DATABASE_URL saved to generated project's .env")

        self._ensure_dockerfile(spec)

        project_name = provider.slug(spec)
        image_tag = f"developable/{project_name}:latest"
        print(f"\n  Building Docker image '{image_tag}'...")
        self._docker_build(image_tag)

        env_vars = self._read_env_file()
        if "DATABASE_URL" not in env_vars or not env_vars["DATABASE_URL"]:
            print(
                "\n  Warning: DATABASE_URL is not set in .env. "
                "The deployed container may fail to connect to Postgres.",
            )
        else:
            self._push_schema_to_remote_db(env_vars["DATABASE_URL"])

        import uuid

        deployment_id = str(uuid.uuid4())
        print(f"\n  Deploying to {provider.display_name}...")
        record = provider.deploy(spec, image_tag, env_vars, deployment_id)
        if db_resource:
            record.setdefault("resources", []).append(db_resource)

        state = DeploymentState(self.out_dir)
        state.initialise(
            project_name=project_name,
            schema_path=str(spec.get("schema_path", "")),
        )
        state.add(record)
        state.save()

        self._configure_post_ci_deploy_workflow(record)
        self._run_remote_smoke_tests(record["endpoint"])

        return record

    def _ask_provider(self) -> str:
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
            print(f"  Invalid choice. Enter 1-{len(providers)} or one of: {', '.join(PROVIDER_MAP)}")

    def _ensure_dockerfile(self, spec: dict[str, Any]) -> None:
        dockerfile = self.out_dir / "Dockerfile"
        if dockerfile.exists():
            return

        print("  Dockerfile not found — generating infrastructure files...")
        from core.assembler import Assembler
        from core.vc_planner import VCPlanner

        plan = VCPlanner().plan(spec)
        assembler = Assembler(out_dir=self.out_dir, use_llm=False)
        assembler.assemble(spec, plan)
        print(f"  Generated {len(plan['files'])} infrastructure file(s).")

    def _docker_build(self, image_tag: str) -> None:
        result = subprocess.run(
            ["docker", "build", "-t", image_tag, str(self.out_dir)],
            capture_output=False,
        )
        if result.returncode != 0:
            print("\nDocker build failed.", file=sys.stderr)
            sys.exit(1)

    def _read_env_file(self) -> dict[str, str]:
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

    def _upsert_env(self, key: str, value: str) -> None:
        env_file = self.out_dir / ".env"
        if not env_file.exists():
            env_file.write_text(f'{key}="{value}"\n')
            return

        lines = env_file.read_text().splitlines()
        replaced = False
        new_lines: list[str] = []
        for line in lines:
            if line.strip().startswith(f"{key}="):
                new_lines.append(f'{key}="{value}"')
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f'{key}="{value}"')
        env_file.write_text("\n".join(new_lines) + "\n")

    def _push_schema_to_remote_db(self, database_url: str) -> None:
        print("\n  Applying Prisma schema to remote database (prisma db push)...")
        env = dict(os.environ, DATABASE_URL=database_url)
        result = subprocess.run(
            ["npx", "prisma", "db", "push", "--accept-data-loss"],
            cwd=self.out_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("\n  Warning: Remote schema push failed. Deployment will continue.")
            if result.stderr:
                print(result.stderr)

    def _configure_post_ci_deploy_workflow(self, record: dict[str, Any]) -> None:
        git_dir = self.out_dir / ".git"
        if not git_dir.exists():
            return

        workflow = self.out_dir / ".github/workflows/deploy-after-ci.yml"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        provider = record.get("provider", "cloud")
        workflow.write_text(
            f"""name: CD Deploy

on:
  workflow_run:
    workflows: [\"CI\"]
    types: [completed]
    branches: [main]

jobs:
  deploy:
    if: ${{{{ github.event.workflow_run.conclusion == 'success' }}}}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Trigger {provider.upper()} deployment webhook
        env:
          DEPLOY_WEBHOOK_URL: ${{{{ secrets.DEPLOY_WEBHOOK_URL }}}}
        run: |
          if [ -z \"$DEPLOY_WEBHOOK_URL\" ]; then
            echo \"DEPLOY_WEBHOOK_URL is not set; skipping deploy.\"
            exit 0
          fi
          curl -fsSL -X POST \"$DEPLOY_WEBHOOK_URL\"
"""
        )

        self._git_commit_and_push(
            "Add post-CI deployment workflow",
            [".github/workflows/deploy-after-ci.yml"],
        )

    def _run_remote_smoke_tests(self, endpoint: str) -> None:
        test_runner = self.out_dir / "tests/run_all.py"
        if not test_runner.exists():
            print("\n  No generated Python test suite found at tests/run_all.py; skipping remote tests.")
            return

        print(f"\n  Running generated tests against remote API: {endpoint}")
        result = subprocess.run(
            [sys.executable, str(test_runner), endpoint],
            cwd=self.out_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("\n  Warning: Remote API test run failed.")
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
        else:
            print("  Remote API test run succeeded.")

    def _git_commit_and_push(self, message: str, paths: list[str]) -> None:
        try:
            subprocess.run(["git", "add", *paths], cwd=self.out_dir, check=True, capture_output=True, text=True)
            status = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.out_dir)
            if status.returncode == 0:
                return
            subprocess.run(["git", "commit", "-m", message], cwd=self.out_dir, check=True, capture_output=True, text=True)
            subprocess.run(["git", "push", "origin", "main"], cwd=self.out_dir, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            print("  Warning: could not auto-commit/push post-deploy workflow.")

    @staticmethod
    def _normalise_kwargs(kwargs: dict[str, Any]) -> dict[str, dict[str, Any]]:
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
