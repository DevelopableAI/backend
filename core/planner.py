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
        auth_entity_name = spec.get("auth_entity_name")

        has_sensitive_fields = any(
            f["is_sensitive"]
            for e in entities
            for f in e["fields"]
        )

        files = [
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
                "context": {
                    "entities": entities,
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            },
            {
                "path": "src/server.ts",
                "template": "express/server.ts.j2",
                "context": {"auth_entity_name": auth_entity_name},
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
                "context": {
                    "datasource": datasource,
                    "env_vars": spec.get("env_vars", []),
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            },
        ]

        # Add crypto utility if any entity has sensitive fields
        if has_sensitive_fields:
            files.append({
                "path": "src/lib/crypto.ts",
                "template": "express/crypto.ts.j2",
                "context": {},
                "needs_llm": False,
            })

        # Add JWT auth middleware if an auth entity was detected
        if auth_entity_name:
            auth_entity = next((e for e in entities if e.get("is_auth_entity")), None)
            files.append({
                "path": "src/lib/auth.ts",
                "template": "express/auth.ts.j2",
                "context": {"auth_entity": auth_entity},
                "needs_llm": False,
            })

        return files

    def _plan_entity_files(self, entity: dict, spec: dict) -> list[dict]:
        name_lower = entity["name_lower"]
        name_plural = entity["name_plural"]

        # scalar (non-relation) fields for validation
        scalar_fields = [f for f in entity["fields"] if not f["is_relation"] and not f["is_id"]]

        auth_entity_name = spec.get("auth_entity_name")
        all_entities = spec["entities"]

        # ownership: FK field on this entity pointing to the auth entity
        owner_fk_field = self._get_owner_fk_field(entity, auth_entity_name)

        # nested routes: one_to_many relations with their child FK resolved
        nested_routes = self._build_nested_routes(entity, all_entities)

        # many_to_one relations on this entity (for findManyByFK generation in child repo)
        parent_fk_relations = [
            r for r in entity.get("relations", []) if r["type"] == "many_to_one" and r.get("fk_field")
        ]

        files = [
            {
                "path": f"src/routes/{name_plural}.routes.ts",
                "template": "express/routes.ts.j2",
                "context": {
                    "entity": entity,
                    "routes": self._infer_routes(entity),
                    "owner_fk_field": owner_fk_field,
                    "nested_routes": nested_routes,
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            },
            {
                "path": f"src/controllers/{name_lower}.controller.ts",
                "template": "express/controller.ts.j2",
                "context": {
                    "entity": entity,
                    "routes": self._infer_routes(entity),
                    "owner_fk_field": owner_fk_field,
                    "nested_routes": nested_routes,
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            },
            {
                "path": f"src/repositories/{name_lower}.repository.ts",
                "template": "express/repository.ts.j2",
                "context": {
                    "entity": entity,
                    "parent_fk_relations": parent_fk_relations,
                },
                "needs_llm": False,
            },
            {
                "path": f"src/validators/{name_lower}.validator.ts",
                "template": "express/validator.ts.j2",
                "context": {
                    "entity": entity,
                    "scalar_fields": scalar_fields,
                    "owner_fk_field": owner_fk_field,
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

        # Auth entity gets dedicated register/login routes + controller
        if entity.get("is_auth_entity"):
            sensitive_fields = [f for f in entity["fields"] if f["is_sensitive"]]
            files += [
                {
                    "path": "src/routes/auth.routes.ts",
                    "template": "express/auth.routes.ts.j2",
                    "context": {"auth_entity": entity},
                    "needs_llm": False,
                },
                {
                    "path": "src/controllers/auth.controller.ts",
                    "template": "express/auth.controller.ts.j2",
                    "context": {
                        "auth_entity": entity,
                        "sensitive_fields": sensitive_fields,
                    },
                    "needs_llm": False,
                },
            ]

        return files

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

    def _get_owner_fk_field(self, entity: dict, auth_entity_name: str | None) -> str | None:
        """
        Returns the scalar FK field name on this entity that points to the auth entity.
        E.g. for Post with `author User @relation(fields: [authorId], ...)`, returns 'authorId'.
        """
        if not auth_entity_name:
            return None
        for rel in entity.get("relations", []):
            if rel["related_entity"] == auth_entity_name and rel["type"] == "many_to_one":
                return rel.get("fk_field")
        return None

    def _get_child_fk_field(
        self,
        parent_entity_name: str,
        child_entity_name: str,
        all_entities: list[dict],
    ) -> str | None:
        """
        Looks up the FK scalar field name on the child entity that points back to parent.
        E.g. for parent=User, child=Post: finds 'authorId' on Post's many_to_one relation to User.
        """
        child = next((e for e in all_entities if e["name"] == child_entity_name), None)
        if not child:
            return None
        for rel in child.get("relations", []):
            if rel["related_entity"] == parent_entity_name and rel["type"] == "many_to_one":
                return rel.get("fk_field")
        return None

    def _build_nested_routes(self, entity: dict, all_entities: list[dict]) -> list[dict]:
        """
        For each one_to_many relation on this entity, build a nested route descriptor.
        E.g. User has posts Post[] → nested route for /users/:id/posts
        """
        nested = []
        for rel in entity.get("relations", []):
            if rel["type"] != "one_to_many":
                continue

            child_entity_name = rel["related_entity"]
            fk_field = self._get_child_fk_field(entity["name"], child_entity_name, all_entities)

            # Look up child entity to get name variants
            child_entity = next((e for e in all_entities if e["name"] == child_entity_name), None)
            if not child_entity or not fk_field:
                continue  # skip if FK can't be resolved

            nested.append({
                "relation_name": rel["name"],           # e.g. "posts"
                "related_entity": child_entity_name,    # e.g. "Post"
                "related_entity_lower": child_entity["name_lower"],   # e.g. "post"
                "related_entity_plural": child_entity["name_plural"],  # e.g. "posts"
                "fk_field": fk_field,                   # e.g. "authorId"
            })

        return nested
