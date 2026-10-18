"""
Deployment Agent — pure orchestrator.

Provider-specific deployment logic lives in ProviderDeployer subclasses:
  agents/aws_deployer.py    → AWSDeployer
  agents/gcp_deployer.py    → GCPDeployer
  agents/heroku_deployer.py → HerokuDeployer

Adding a new cloud provider: write a new ProviderDeployer subclass and register
it in _DEPLOYERS — no changes to this file required (Open/Closed Principle).
"""

import os
import sys
import uuid
from pathlib import Path
from typing import Any

from core.deployment_state import DeploymentState
from core.docker_client import DockerClient
from core.git_client import GitClient
from core.github_client import GitHubClient
from core.gitignore import DEFAULT_GITIGNORE_CONTENT, ensure_required_gitignore_patterns
from core.providers import PROVIDER_MAP, get_provider
from core.terraform_runner import TerraformRunner

from agents.aws_deployer import AWSDeployer
from agents.gcp_deployer import GCPDeployer
from agents.heroku_deployer import HerokuDeployer
from core.provider_deployer import ProviderDeployer

_DEPLOYERS: dict[str, type[ProviderDeployer]] = {
    "aws":    AWSDeployer,
    "gcp":    GCPDeployer,
    "heroku": HerokuDeployer,
}


class Deployment:
    """
    The Deployment Agent.

    Args:
        out_dir:   Path to the generated project directory.
        provider:  Cloud provider slug ("aws", "heroku", "gcp"). Prompted if None.
        tests_dir: Directory containing the generated test suite. Auto-detected if None.
        **kwargs:  Provider-specific config (aws_region, heroku_app, gcp_project, gcp_region).
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
        self.provider_kwargs = self._normalise_kwargs(kwargs)
        self.git = GitClient(out_dir)
        self.github = GitHubClient(github_token)
        self.docker = DockerClient(out_dir)

    def deploy(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> dict[str, Any]:
        """Run the full deployment pipeline and return the deployment record."""
        provider_name = self.provider_name or self._ask_provider()
        provider = get_provider(provider_name, self.out_dir, **self.provider_kwargs.get(provider_name, {}))

        print(f"\n  Detecting {provider.display_name} credentials...")
        creds = self._resolve_credentials(provider)
        provider.configure(creds)

        project_name = provider.slug(spec)
        deployment_id = str(uuid.uuid4())

        self._bootstrap_tf_state(provider_name, spec, creds, project_name)
        self._ensure_dockerfile(spec)

        deployer = _DEPLOYERS[provider_name](self.out_dir, self.docker, TerraformRunner)
        record: dict[str, Any] | None = None
        try:
            record = deployer.deploy(spec, provider, creds, project_name, deployment_id)
        finally:
            if self.git.has_remote() and record is not None:
                self._post_deploy_github(provider_name, provider, creds, project_name, record)

        if record is None:
            sys.exit(1)

        state = DeploymentState(self.out_dir)
        state.initialise(project_name=project_name, schema_path=str(spec.get("schema_path", "")))
        state.add(record)
        state.save()

        self._run_remote_tests(record["endpoint"])
        return record

    def _resolve_credentials(self, provider: Any) -> dict[str, Any]:
        detected = provider.detect_credentials()
        if detected:
            is_valid, reason = provider.validate_credentials(detected)
            if is_valid:
                print("  Found existing credentials.")
                return detected
            print(
                "  Detected credentials, but they failed validation.\n"
                f"  Reason: {reason or 'unknown validation failure'}"
            )

        max_manual_attempts = 2
        for attempt in range(1, max_manual_attempts + 1):
            creds = provider.collect_credentials()
            is_valid, reason = provider.validate_credentials(creds)
            if is_valid:
                print("  Credentials validated.")
                return creds
            print(
                f"  The supplied credentials were not accepted (attempt {attempt}/{max_manual_attempts}).\n"
                f"  Reason: {reason or 'unknown validation failure'}"
            )

        print(
            "\n  Deployment stopped: provider credentials could not be validated after multiple attempts.\n"
            "  Fix the credentials or authentication method, then rerun deployment.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Provider selection ─────────────────────────────────────────────────────

    def _ask_provider(self) -> str:
        providers = list(PROVIDER_MAP.items())
        print("\nDeployment Agent — select a cloud provider:")
        for i, (slug, name) in enumerate(providers, 1):
            print(f"  [{i}] {slug:<10} — {name}")
        print()
        while True:
            choice = input("  Enter number or provider name: ").strip().lower()
            if choice.isdigit() and 0 <= int(choice) - 1 < len(providers):
                return providers[int(choice) - 1][0]
            if choice in PROVIDER_MAP:
                return choice
            print(f"  Invalid choice. Enter 1–{len(providers)} or one of: {', '.join(PROVIDER_MAP)}")

    # ── Pre-deploy ─────────────────────────────────────────────────────────────

    def _bootstrap_tf_state(
        self, provider_name: str, spec: dict[str, Any], creds: dict[str, Any], project_name: str
    ) -> None:
        from core.terraform_backend import TerraformBackend
        from core.terraform_planner import TerraformPlanner
        from generators.template import TemplateGenerator

        planner = TerraformPlanner()
        minimal_config = {
            "aws_region": creds.get("region", "us-east-1"),
            "gcp_project": creds.get("project_id", ""),
            "gcp_region": creds.get("region", "us-central1"),
        }
        backend_cfg = planner._derive_backend_config(provider_name, project_name, minimal_config)
        print(f"\n  Bootstrapping Terraform state backend...")
        actual = TerraformBackend().bootstrap(
            provider_name,
            {**creds, **minimal_config,
             "state_bucket": backend_cfg.get("bucket", ""),
             "dynamodb_table": backend_cfg.get("dynamodb_table", "")},
            project_name,
        )
        if provider_name == "aws":
            actual_bucket = actual.get("bucket", "")
            if actual_bucket and actual_bucket != backend_cfg.get("bucket", ""):
                backend_tf = self.out_dir / "terraform" / "backend.tf"
                if backend_tf.exists():
                    ctx = {"project_name": project_name, "spec": spec, "entities": spec["entities"],
                           "provider_config": {}, "backend_config": actual}
                    backend_tf.write_text(TemplateGenerator().render(
                        f"terraform/{provider_name}/backend.tf.j2", ctx))
                    print(f"    Updated terraform/backend.tf → bucket: {actual_bucket}")

    def _ensure_dockerfile(self, spec: dict[str, Any]) -> None:
        if (self.out_dir / "Dockerfile").exists():
            return
        print("  Dockerfile not found — generating infrastructure files...")
        from core.vc_planner import VCPlanner
        from core.assembler import Assembler
        plan = VCPlanner().plan(spec)
        Assembler(out_dir=self.out_dir, use_llm=False).assemble(spec, plan)
        print(f"  Generated {len(plan['files'])} infrastructure file(s).")

    # ── Post-deploy ────────────────────────────────────────────────────────────

    def _post_deploy_github(
        self, provider_name: str, provider: Any, creds: dict[str, Any],
        project_name: str, record: dict[str, Any]
    ) -> None:
        try:
            repo = self.github.repo_fullname(self.git)
            gitignore_path = self.out_dir / ".gitignore"
            if not gitignore_path.exists():
                gitignore_path.write_text(DEFAULT_GITIGNORE_CONTENT)
                self.git.commit("Ignore Developable deployment state\n\nGenerated by Developable.", ".gitignore")
                self.git.push()
            elif ensure_required_gitignore_patterns(gitignore_path):
                self.git.commit("Ignore Developable deployment state\n\nGenerated by Developable.", ".gitignore")
                self.git.push()

            if provider_name == "gcp" and creds.get("credentials_type") == "adc":
                from core.providers.gcp import GCPProvider
                project_id = creds.get("project_id", "")
                if project_id:
                    gcp = GCPProvider(project_id=project_id)
                    key_b64 = gcp.ensure_ci_service_account(project_id, gcp._load_credentials(creds))
                    if key_b64:
                        creds = {**creds, "credentials_b64": key_b64}

            print(f"\n  Preparing GitHub Actions deploy workflow...")
            secrets_ready = self.github.provision_deploy_secrets(provider_name, creds, repo)
            workflow_yaml = provider.generate_deploy_workflow(project_name, record)
            print(f"  Pushing CI/CD deploy workflow to GitHub...")
            self.github.push_workflow(
                self.git, workflow_yaml, ".github/workflows/deploy.yml",
                "Add cloud deployment CI/CD workflow\n\nGenerated by Developable Deployment Agent.",
            )
            if not secrets_ready and repo:
                print(
                    f"\n  deploy.yml pushed. Add required secrets at:\n"
                    f"  https://github.com/{repo}/settings/secrets/actions"
                )
        except Exception as exc:
            print(f"\n  Warning: could not push deploy.yml: {exc}", file=sys.stderr)

    # ── Smoke tests ────────────────────────────────────────────────────────────

    def _run_remote_tests(self, endpoint: str) -> None:
        import subprocess
        tests_dir = self.tests_dir
        if tests_dir is None:
            candidate = self.out_dir / "tests"
            if candidate.is_dir() and (candidate / "run_all.py").exists():
                tests_dir = candidate
        if tests_dir is None or not (tests_dir / "run_all.py").exists():
            return
        if "pending" in endpoint:
            print(f"\n  Skipping remote smoke tests: endpoint not yet available.\n"
                  f"  Run manually: python {tests_dir}/run_all.py <endpoint>")
            return
        print(f"\n  Running remote smoke tests against {endpoint}...")
        subprocess.run([sys.executable, str(tests_dir / "run_all.py"), endpoint])

    # ── Static helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_kwargs(kwargs: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            "aws":    {k: v for k, v in {"region": kwargs.get("aws_region")}.items() if v is not None},
            "heroku": {k: v for k, v in {"app_name": kwargs.get("heroku_app")}.items() if v is not None},
            "gcp":    {k: v for k, v in {
                "project_id": kwargs.get("gcp_project"),
                "region": kwargs.get("gcp_region"),
            }.items() if v is not None},
        }
