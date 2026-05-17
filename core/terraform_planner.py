from pathlib import Path
from typing import Any


class TerraformPlanner:
    """
    Produces the file plan for the Terraform agent.

    Generates four static HCL files per cloud provider:
      backend.tf    — remote state backend configuration
      main.tf       — all provider resources (compute, database, networking)
      variables.tf  — input variable declarations
      outputs.tf    — output value declarations

    All files are pure Jinja2 template renders — no LLM calls needed.
    Paths are prefixed with "terraform/" so Assembler writes them to
    <out_dir>/terraform/, isolated from the Express project files.

    State backend names (S3 bucket, DynamoDB table, GCS bucket) are derived
    deterministically from project_name — no prior bootstrap call required.
    TerraformBackend.bootstrap() in the Deployment agent creates these exact
    resources when the user actually deploys, so terraform init connects
    immediately after bootstrap without any name mismatch.
    """

    def plan(
        self,
        spec: dict[str, Any],
        provider: str,
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        project_name = self._derive_project_name(spec)
        backend_config = self._derive_backend_config(provider, project_name, provider_config)

        context = {
            "project_name": project_name,
            "spec": spec,
            "entities": spec["entities"],
            "provider_config": provider_config,
            "backend_config": backend_config,
        }

        files = [
            {
                "path": "terraform/backend.tf",
                "template": f"terraform/{provider}/backend.tf.j2",
                "context": context,
                "needs_llm": False,
            },
            {
                "path": "terraform/main.tf",
                "template": f"terraform/{provider}/main.tf.j2",
                "context": context,
                "needs_llm": False,
            },
            {
                "path": "terraform/variables.tf",
                "template": f"terraform/{provider}/variables.tf.j2",
                "context": context,
                "needs_llm": False,
            },
            {
                "path": "terraform/outputs.tf",
                "template": f"terraform/{provider}/outputs.tf.j2",
                "context": context,
                "needs_llm": False,
            },
        ]

        return {"files": files}

    def _derive_backend_config(
        self, provider: str, project_name: str, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Derive state backend names from project_name — no bootstrap required.

        These names are identical to what TerraformBackend.bootstrap() creates,
        so backend.tf references the correct resources before and after bootstrap.
        """
        if provider == "aws":
            return {
                "bucket": f"{project_name}-tf-state",
                "region": provider_config.get("aws_region", "us-east-1"),
                "dynamodb_table": f"{project_name}-tf-lock",
            }
        if provider == "gcp":
            return {
                "bucket": f"{project_name}-tf-state",
                "project": provider_config.get("gcp_project", ""),
                "region": provider_config.get("gcp_region", "us-central1"),
            }
        # Heroku: local state backend, no remote infra needed
        return {"use_local_state": True}

    def _derive_project_name(self, spec: dict[str, Any]) -> str:
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
