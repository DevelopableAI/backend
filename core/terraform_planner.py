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
    """

    def plan(
        self,
        spec: dict[str, Any],
        provider: str,
        provider_config: dict[str, Any],
        backend_config: dict[str, Any],
    ) -> dict[str, Any]:
        project_name = self._derive_project_name(spec)

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
