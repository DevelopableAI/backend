"""
Base provider interface for cloud deployment providers.

Every provider (AWS, Heroku, GCP, …) must implement this ABC.
Providers are responsible for:
  1. Detecting credentials from the environment / config files.
  2. Collecting any missing credentials interactively.
  3. Pushing the built Docker image to their registry.
  4. Deploying the container to their managed compute service.
  5. Tagging/labelling cloud resources for traceability.
  6. Returning a standardised deployment record.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseProvider(ABC):
    """Abstract cloud deployment provider."""

    #: Human-readable display name shown in interactive prompts.
    display_name: str = ""

    # Standard tag keys applied to every cloud resource we create.
    TAG_MANAGED_BY = "managedBy"
    TAG_PROJECT = "projectName"
    TAG_DEPLOYMENT_ID = "deploymentId"
    TAG_ENTITIES = "schemaEntities"
    TAG_MANAGED_VALUE = "developable"

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self._credentials: dict[str, Any] = {}

    # ── Abstract interface ─────────────────────────────────────────────────────

    @abstractmethod
    def detect_credentials(self) -> dict[str, Any] | None:
        """
        Probe standard credential locations (env vars, config files, SDK defaults).

        Returns a credentials dict if everything needed is present, or None if
        the user must be prompted.
        """

    @abstractmethod
    def collect_credentials(self) -> dict[str, Any]:
        """
        Interactively prompt the user for any missing credentials.

        Should only ask for values not already discoverable via detect_credentials().
        Returns a fully-populated credentials dict.
        """

    @abstractmethod
    def provision_database(self, spec: dict[str, Any]) -> dict[str, Any] | None:
        """
        Provision a provider-managed remote database for this deployment.

        Returns a dict with:
          - database_url: str
          - resource: optional resource descriptor for deployment state
        """

    @abstractmethod
    def deploy(
        self,
        spec: dict[str, Any],
        image_tag: str,
        env_vars: dict[str, str],
        deployment_id: str,
    ) -> dict[str, Any]:
        """
        Push the Docker image and deploy the service.

        Args:
            spec:          Parsed Prisma spec (entities, datasource, …).
            image_tag:     Local Docker image tag built in agents/deployment.py.
            env_vars:      Environment variables to inject into the container
                           (read from <out_dir>/.env by the Deployment agent).
            deployment_id: UUID string for this deployment (used in tags).

        Returns:
            A deployment record dict ready for DeploymentState.add().
            Must contain at minimum: provider, region, endpoint, image_uri,
            resources (list), tags (dict).
        """

    # ── Shared helpers ─────────────────────────────────────────────────────────

    def configure(self, credentials: dict[str, Any]) -> None:
        """Store resolved credentials for use in deploy()."""
        self._credentials = credentials

    def build_tags(
        self,
        project_name: str,
        deployment_id: str,
        spec: dict[str, Any],
    ) -> dict[str, str]:
        """Return the standard tag dict applied to all managed resources."""
        entity_names = ",".join(e["name"] for e in spec.get("entities", []))
        return {
            self.TAG_MANAGED_BY: self.TAG_MANAGED_VALUE,
            self.TAG_PROJECT: project_name,
            self.TAG_DEPLOYMENT_ID: deployment_id,
            self.TAG_ENTITIES: entity_names,
        }

    def slug(self, spec: dict[str, Any]) -> str:
        """Derive a DNS-safe project slug from the spec (e.g. 'user-api')."""
        entities = spec.get("entities", [])
        if entities:
            return entities[0]["name_lower"] + "-api"
        schema_path = spec.get("schema_path", "schema")
        return Path(schema_path).stem.replace("_", "-").lower() + "-api"
