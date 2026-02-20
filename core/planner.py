from typing import Any


class Planner:
    """
    Takes the parsed spec and produces a concrete file plan.

    Each file in the plan has:
    - path: relative path in the output project
    - template: which template to render
    - context: variables available to that template
    - needs_llm: whether an LLM call is needed to fill logic sections
    - llm_task: short description of what the LLM needs to produce
    """

    def plan(self, spec: dict[str, Any]) -> dict[str, Any]:
        files = []

        # project-level boilerplate files
        files += self._plan_project_files(spec)

        # per-entity files
        for entity in spec["entities"]:
            files += self._plan_entity_files(entity, spec)

        return {"files": files}

    def _plan_project_files(self, spec: dict) -> list[dict]:
        datasource = spec["datasource"]
        entities = spec["entities"]

        return [
            {
                "path": "package.json",
                "template": "express/package.json.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "tsconfig.json",
                "template": "express/tsconfig.json.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/app.ts",
                "template": "express/app.ts.j2",
                "context": {"entities": entities},
                "needs_llm": False,
            },
            {
                "path": "src/server.ts",
                "template": "express/server.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/lib/prisma.ts",
                "template": "express/prisma.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/lib/errors.ts",
                "template": "express/errors.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/lib/pagination.ts",
                "template": "express/pagination.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": ".env.example",
                "template": "express/env.example.j2",
                "context": {"datasource": datasource},
                "needs_llm": False,
            },
        ]

    def _plan_entity_files(self, entity: dict, spec: dict) -> list[dict]:
        name_lower = entity["name_lower"]
        name_plural = entity["name_plural"]
        has_llm_hints = len(entity["llm_hints"]) > 0

        # scalar (non-relation) fields for validation
        scalar_fields = [f for f in entity["fields"] if not f["is_relation"] and not f["is_id"]]

        return [
            {
                "path": f"src/routes/{name_plural}.routes.ts",
                "template": "express/routes.ts.j2",
                "context": {
                    "entity": entity,
                    "routes": self._infer_routes(entity),
                },
                "needs_llm": False,
            },
            {
                "path": f"src/controllers/{name_lower}.controller.ts",
                "template": "express/controller.ts.j2",
                "context": {
                    "entity": entity,
                    "routes": self._infer_routes(entity),
                },
                "needs_llm": False,
            },
            {
                "path": f"src/repositories/{name_lower}.repository.ts",
                "template": "express/repository.ts.j2",
                "context": {"entity": entity},
                "needs_llm": False,
            },
            {
                "path": f"src/validators/{name_lower}.validator.ts",
                "template": "express/validator.ts.j2",
                "context": {
                    "entity": entity,
                    "scalar_fields": scalar_fields,
                },
                "needs_llm": True,
                "llm_task": "validation_logic",
            },
            {
                "path": f"src/types/{name_lower}.types.ts",
                "template": "express/types.ts.j2",
                "context": {
                    "entity": entity,
                    "scalar_fields": scalar_fields,
                },
                "needs_llm": False,
            },
        ]

    def _infer_routes(self, entity: dict) -> list[dict]:
        plural = entity["name_plural"]
        id_field = next((f for f in entity["fields"] if f["is_id"]), None)
        id_type = id_field["ts_type"] if id_field else "number"

        return [
            {
                "method": "GET",
                "path": f"/{plural}",
                "handler": "getAll",
                "description": f"List all {plural} with pagination and filtering",
            },
            {
                "method": "GET",
                "path": f"/{plural}/:id",
                "handler": "getById",
                "description": f"Get a single {entity['name_lower']} by ID",
                "id_type": id_type,
            },
            {
                "method": "POST",
                "path": f"/{plural}",
                "handler": "create",
                "description": f"Create a new {entity['name_lower']}",
            },
            {
                "method": "PUT",
                "path": f"/{plural}/:id",
                "handler": "update",
                "description": f"Update an existing {entity['name_lower']}",
                "id_type": id_type,
            },
            {
                "method": "DELETE",
                "path": f"/{plural}/:id",
                "handler": "remove",
                "description": f"Delete a {entity['name_lower']}",
                "id_type": id_type,
            },
        ]