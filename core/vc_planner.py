from pathlib import Path
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
        """
        Derive a slug-safe project name from the schema.

        Priority mirrors BaseProvider.slug():
        1. Schema filename stem (skipped for generic names like "schema").
        2. First entity name.
        3. "generated-api" fallback.
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
