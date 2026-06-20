from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from core.docker_client import DockerClient


class ProviderDeployer(ABC):
    """
    Abstract base for cloud provider deployers.

    Each concrete subclass (AWSDeployer, GCPDeployer, HerokuDeployer) owns
    exactly one provider's deploy flow:
        provision infra → migrate schema → build image → push image
        → deploy service → write tfvars → terraform import + reconcile → record

    The Deployment orchestrator dispatches to the correct subclass via DEPLOYERS
    so adding a new provider means writing a new subclass — not opening Deployment.
    """

    def __init__(
        self,
        out_dir: Path,
        docker: DockerClient,
        tf_runner_factory: Callable[[Path, dict[str, str]], Any],
    ) -> None:
        self.out_dir = out_dir
        self.docker = docker
        self.tf_runner_factory = tf_runner_factory

    @abstractmethod
    def deploy(
        self,
        spec: dict[str, Any],
        provider: Any,
        creds: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> dict[str, Any]:
        """
        Run the full provider-specific deployment flow.

        Args:
            spec:          Parsed Prisma spec.
            provider:      Configured cloud provider object (from core/providers/).
            creds:         Credentials dict returned by provider.detect/collect_credentials().
            project_name:  Slug-safe name for this project.
            deployment_id: UUID string for this deployment run.

        Returns:
            A deployment record dict compatible with DeploymentState.make_record().
        """
        ...

    def _read_env_file(self) -> dict[str, str]:
        """Parse <out_dir>/.env into a key-value dict."""
        env_file = self.out_dir / ".env"
        if not env_file.exists():
            return {}
        env_vars: dict[str, str] = {}
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env_vars[key.strip()] = value.strip().strip('"').strip("'")
        return env_vars
