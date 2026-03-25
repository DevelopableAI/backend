"""
Deployment state manager.

Tracks all deployments for a generated project in a local JSON file at
<out_dir>/.developable/state.json. This file travels with the project and
gives the Backend Engineer full visibility into deployed resources without
any cloud API calls.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_DIR = ".developable"
STATE_FILE = ".developable/state.json"

_EMPTY_STATE: dict[str, Any] = {
    "version": "1",
    "project_name": "",
    "schema_path": "",
    "deployments": [],
}


class DeploymentState:
    """
    Read/write wrapper for <out_dir>/.developable/state.json.

    Usage::

        state = DeploymentState(out_dir)
        state.initialise(project_name, schema_path)
        state.add(deployment_record)
        state.save()

        latest = state.get_latest()
        all_deploys = state.list_deployments()
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.path = out_dir / STATE_FILE
        self.state = self._load()

    # ── Public API ─────────────────────────────────────────────────────────────

    def initialise(self, project_name: str, schema_path: str) -> "DeploymentState":
        """Populate top-level metadata if not already set."""
        if not self.state["project_name"]:
            self.state["project_name"] = project_name
        if not self.state["schema_path"]:
            self.state["schema_path"] = schema_path
        return self

    def add(self, record: dict[str, Any]) -> "DeploymentState":
        """Append a deployment record. Returns self for chaining."""
        self.state["deployments"].append(record)
        return self

    def save(self) -> None:
        """Persist state to disk, creating the .developable/ directory if needed."""
        state_dir = self.out_dir / STATE_DIR
        state_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, indent=2, default=str))

    def list_deployments(self) -> list[dict[str, Any]]:
        """Return all deployment records, newest first."""
        return list(reversed(self.state["deployments"]))

    def get_latest(self, provider: str | None = None) -> dict[str, Any] | None:
        """
        Return the most recent deployment record, optionally filtered by provider.
        Returns None if no matching deployment exists.
        """
        for record in reversed(self.state["deployments"]):
            if provider is None or record.get("provider") == provider:
                return record
        return None

    # ── Class-level helper ─────────────────────────────────────────────────────

    @staticmethod
    def make_record(
        provider: str,
        region: str | None,
        endpoint: str,
        image_uri: str,
        resources: list[dict[str, Any]],
        tags: dict[str, str],
    ) -> dict[str, Any]:
        """
        Build a standardised deployment record dict.

        Args:
            provider:  Cloud provider slug, e.g. "aws", "heroku", "gcp".
            region:    Cloud region, e.g. "us-east-1". None for region-less providers.
            endpoint:  Public URL or IP of the deployed service.
            image_uri: Full image URI that was pushed and deployed.
            resources: List of cloud resource descriptors (type, id, arn/url optional).
            tags:      Key-value tags applied to cloud resources.

        Returns:
            A deployment record dict ready to be passed to DeploymentState.add().
        """
        return {
            "id": str(uuid.uuid4()),
            "provider": provider,
            "region": region,
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "image_uri": image_uri,
            "resources": resources,
            "tags": tags,
        }

    # ── Private ────────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, KeyError):
                pass
        # Return a deep copy of the empty scaffold
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _EMPTY_STATE.items()}
