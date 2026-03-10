from typing import Any


class VCPlanner:
    """
    Produces the file plan for the Version Control agent.

    Generates infrastructure files that are added to the output directory before
    the project is committed and pushed to GitHub:
      - Dockerfile          (containerises the generated Express API)
      - docker-compose.yml  (local dev stack: PostgreSQL + pgAdmin + API)
      - .github/workflows/ci.yml  (GitHub Actions: run tests on every push/PR)

    All files are pure Jinja2 template renders — no LLM calls needed.
    """

    def plan(self, spec: dict[str, Any]) -> dict[str, Any]:
        project_name = self._derive_project_name(spec)

        context = {
            "spec": spec,
            "project_name": project_name,
        }

        files = [
            {
                "path": "Dockerfile",
                "template": "express/api/Dockerfile.j2",
                "context": context,
                "needs_llm": False,
            },
            {
                "path": "docker-compose.yml",
                "template": "express/api/docker-compose.yml.j2",
                "context": context,
                "needs_llm": False,
            },
            {
                "path": ".github/workflows/ci.yml",
                "template": "express/api/.github/workflows/ci.yml.j2",
                "context": context,
                "needs_llm": False,
            },
        ]

        return {"files": files}

    def _derive_project_name(self, spec: dict[str, Any]) -> str:
        """Derive a slug-safe project name from the schema."""
        entities = spec.get("entities", [])
        if entities:
            return entities[0]["name_lower"] + "-api"
        schema_path = spec.get("schema_path", "")
        if schema_path:
            from pathlib import Path
            return Path(schema_path).stem.replace("_schema", "") + "-api"
        return "generated-api"
