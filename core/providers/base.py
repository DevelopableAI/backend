"""
Base provider interface for cloud deployment providers.

Every provider (AWS, Heroku, GCP, …) must implement this ABC.
Providers are responsible for:
  1. Detecting credentials from the environment / config files.
  2. Collecting any missing credentials interactively.
  3. Provisioning a managed PostgreSQL database.
  4. Applying the Prisma schema to the remote database.
  5. Pushing the built Docker image to their registry.
  6. Deploying the container to their managed compute service.
  7. Tagging/labelling cloud resources for traceability.
  8. Returning a standardised deployment record.
  9. Generating a provider-specific GitHub Actions deploy workflow.
"""

import os
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

_SCHEMA_APPLY_RETRIES = 4
_SCHEMA_APPLY_BACKOFF_S = 20  # RDS DNS propagation can take ~30–60s after "available"


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
    def provision_database(
        self,
        spec: dict[str, Any],
        project_name: str,
        deployment_id: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Provision a managed PostgreSQL database on the cloud provider.

        Args:
            spec:          Parsed Prisma spec.
            project_name:  DNS-safe project slug (e.g. "user-api").
            deployment_id: UUID string for this deployment (used in tags).

        Returns:
            (remote_database_url, resource_descriptors)
            remote_database_url: full postgresql:// connection string.
            resource_descriptors: list of {type, id, arn/url, ...} dicts.
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
            env_vars:      Environment variables to inject into the container.
                           DATABASE_URL will already point to the remote DB
                           provisioned by provision_database().
            deployment_id: UUID string for this deployment (used in tags).

        Returns:
            A deployment record dict ready for DeploymentState.add().
            Must contain at minimum: provider, region, endpoint, image_uri,
            resources (list), tags (dict).
        """

    @abstractmethod
    def generate_deploy_workflow(
        self,
        project_name: str,
        record: dict[str, Any],
    ) -> str:
        """
        Return the YAML string for a provider-specific GitHub Actions
        deploy.yml that triggers after CI passes on main.

        The workflow must:
        - Trigger via `workflow_run` on the "CI" workflow completing on main.
        - Only run when CI conclusion is 'success'.
        - Build + push the Docker image to the provider's registry.
        - Re-deploy the service with the new image.
        """

    # ── Shared concrete methods ────────────────────────────────────────────────

    def configure(self, credentials: dict[str, Any]) -> None:
        """Store resolved credentials for use in provision_database() and deploy()."""
        self._credentials = credentials

    def apply_schema(self, remote_db_url: str) -> None:
        """
        Run `prisma db push --accept-data-loss` in out_dir against the remote
        database URL. Retries up to _SCHEMA_APPLY_RETRIES times with backoff to
        handle RDS DNS propagation delay (instance reports "available" before its
        endpoint is resolvable).

        Requires Node.js to be available on the local machine.
        """
        print(f"  Applying Prisma schema to remote database...")
        env = {**os.environ, "DATABASE_URL": remote_db_url}

        # Ensure node_modules are present so the project's own Prisma version
        # is used. `npx --yes prisma` downloads the latest release (currently
        # v7), which dropped support for `url = env(...)` in schema.prisma.
        subprocess.run(["npm", "install"], cwd=self.out_dir, capture_output=True)

        for attempt in range(1, _SCHEMA_APPLY_RETRIES + 1):
            result = subprocess.run(
                ["./node_modules/.bin/prisma", "db", "push", "--accept-data-loss"],
                cwd=self.out_dir,
                env=env,
            )
            if result.returncode == 0:
                return
            if attempt < _SCHEMA_APPLY_RETRIES:
                print(
                    f"  Schema migration failed (attempt {attempt}/{_SCHEMA_APPLY_RETRIES}), "
                    f"retrying in {_SCHEMA_APPLY_BACKOFF_S}s..."
                )
                time.sleep(_SCHEMA_APPLY_BACKOFF_S)

        print(
            "\n  Warning: Prisma schema migration failed after "
            f"{_SCHEMA_APPLY_RETRIES} attempts.\n"
            "  Apply it manually with:\n"
            f"    DATABASE_URL='{remote_db_url}' npx prisma db push --accept-data-loss\n"
            f"  (run from: {self.out_dir})"
        )

    def build_tags(
        self,
        project_name: str,
        deployment_id: str,
        spec: dict[str, Any],
    ) -> dict[str, str]:
        """Return the standard tag dict applied to all managed resources."""
        entity_names = " ".join(e["name"] for e in spec.get("entities", []))
        return {
            self.TAG_MANAGED_BY: self.TAG_MANAGED_VALUE,
            self.TAG_PROJECT: project_name,
            self.TAG_DEPLOYMENT_ID: deployment_id,
            self.TAG_ENTITIES: entity_names,
        }

    def slug(self, spec: dict[str, Any]) -> str:
        """
        Derive a DNS-safe project slug from the spec.

        Priority:
        1. Schema filename stem (e.g. blog.prisma → blog-api).
           Skipped when the stem is a generic placeholder like "schema".
        2. First entity name (e.g. User → user-api).
        3. Hard-coded fallback "generated-api".
        """
        schema_path = spec.get("schema_path", "")
        if schema_path:
            stem = Path(schema_path).stem.lower()
            for suffix in ("_schema", "-schema", "_prisma", "-prisma"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            name = stem.replace("_", "-")
            if name and name not in ("schema", "prisma", "database", "db"):
                return name + "-api"
        entities = spec.get("entities", [])
        if entities:
            return entities[0]["name_lower"] + "-api"
        return "generated-api"

    def wait_for_ready(self, endpoint: str) -> None:
        """
        Block until the deployed service is accepting traffic.

        Default implementation is a no-op. Providers that deploy containers
        asynchronously (e.g. Heroku) should override this to poll the health
        endpoint. This is called by the deployment agent AFTER apply_schema()
        so the service has its DATABASE_URL before the readiness check starts.
        """
