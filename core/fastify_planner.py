import os
from typing import Any

from core.planner import Planner

TDIR = "fastify/api"


class FastifyPlanner(Planner):
    """
    File planner for Fastify+TypeScript API generation.

    Inherits all spec-analysis helpers from Planner unchanged
    (_infer_routes, _get_owner_fk_field, _build_nested_routes, etc.).
    Overrides only the two file-list methods to point at Fastify templates
    and merge routes+controller into a single plugin.ts per entity.
    """

    def _plan_project_files(self, spec: dict) -> list[dict]:
        datasource = spec["datasource"]
        entities = spec["entities"]
        auth_entity_name = spec.get("auth_entity_name")

        has_sensitive_fields = any(
            f["is_sensitive"]
            for e in entities
            for f in e["fields"]
        )

        schema_stem = os.path.splitext(os.path.basename(spec.get("schema_path", "schema.prisma")))[0]
        project_name = schema_stem.replace("_", "-").replace(".", "-")

        files = [
            {
                "path": "CLAUDE.md",
                "template": f"{TDIR}/CLAUDE.md.j2",
                "context": {
                    "project_name": project_name,
                    "entities": entities,
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            },
            {
                "path": "package.json",
                "template": f"{TDIR}/package.json.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "tsconfig.json",
                "template": f"{TDIR}/tsconfig.json.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/app.ts",
                "template": f"{TDIR}/app.ts.j2",
                "context": {
                    "entities": entities,
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            },
            {
                "path": "src/server.ts",
                "template": f"{TDIR}/server.ts.j2",
                "context": {"auth_entity_name": auth_entity_name},
                "needs_llm": False,
            },
            {
                "path": "src/lib/prisma.ts",
                "template": f"{TDIR}/prisma.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/lib/errors.ts",
                "template": f"{TDIR}/errors.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/lib/pagination.ts",
                "template": f"{TDIR}/pagination.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": ".env.example",
                "template": f"{TDIR}/env.example.j2",
                "context": {
                    "datasource": datasource,
                    "env_vars": spec.get("env_vars", []),
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            },
        ]

        if has_sensitive_fields:
            files.append({
                "path": "src/lib/crypto.ts",
                "template": f"{TDIR}/crypto.ts.j2",
                "context": {},
                "needs_llm": False,
            })

        # No separate auth.ts — JWT registration and authenticate decorator
        # live inside auth.plugin.ts (generated per auth entity below)

        return files

    def _plan_entity_files(self, entity: dict, spec: dict) -> list[dict]:
        name_lower = entity["name_lower"]
        name_plural = entity["name_plural"]

        scalar_fields = [f for f in entity["fields"] if not f["is_relation"] and not f["is_id"] and not f["is_auto_managed"]]

        filterable_fields = [
            f for f in entity["fields"]
            if not f["is_relation"] and not f["is_sensitive"] and not f["is_id"]
            and f.get("ts_type") in ("string", "number", "boolean")
        ]

        sortable_fields = [
            f for f in entity["fields"]
            if not f["is_relation"] and not f["is_sensitive"]
            and f.get("ts_type") in ("string", "number", "boolean")
        ]

        auth_entity_name = spec.get("auth_entity_name")
        all_entities = spec["entities"]

        owner_fk_field = self._get_owner_fk_field(entity, auth_entity_name)
        nested_routes = self._build_nested_routes(entity, all_entities, auth_entity_name)
        endpoint_deny: list[dict] = entity.get("endpoint_deny", [])
        llm_constraints: list[str] = entity.get("llm_constraints", [])
        primary_parent = self._get_primary_parent_name(entity, all_entities, auth_entity_name)

        parent_fk_relations = [
            r for r in entity.get("relations", []) if r["type"] == "many_to_one" and r.get("fk_field")
        ]

        child_cascade_deletes = []
        for sibling in all_entities:
            if sibling["name"] == entity["name"]:
                continue
            for rel in sibling.get("relations", []):
                if (rel["type"] == "many_to_one"
                        and rel.get("fk_field")
                        and rel.get("related_entity") == entity["name"]):
                    child_cascade_deletes.append({
                        "child_name_lower": sibling["name_lower"],
                        "fk_field": rel["fk_field"],
                    })

        allowed_routes = [
            r for r in self._infer_routes(entity)
            if not self._is_route_denied(r, endpoint_deny)
            and not (r["method"] == "POST" and primary_parent is not None)
        ]

        plugin_context: dict[str, Any] = {
            "entity": entity,
            "routes": allowed_routes,
            "owner_fk_field": owner_fk_field,
            "nested_routes": nested_routes,
            "auth_entity_name": auth_entity_name,
            "filterable_fields": filterable_fields,
            "sortable_fields": sortable_fields,
        }

        files = [
            {
                # Single plugin.ts replaces both routes.ts + controller.ts
                "path": f"src/plugins/{name_plural}.plugin.ts",
                "template": f"{TDIR}/plugin.ts.j2",
                "context": plugin_context,
                "needs_llm": False,
            },
            {
                "path": f"src/repositories/{name_lower}.repository.ts",
                "template": f"{TDIR}/repository.ts.j2",
                "context": {
                    "entity": entity,
                    "parent_fk_relations": parent_fk_relations,
                    "child_cascade_deletes": child_cascade_deletes,
                    "filterable_fields": filterable_fields,
                    "sortable_fields": sortable_fields,
                },
                "needs_llm": False,
            },
            {
                "path": f"src/validators/{name_lower}.validator.ts",
                "template": f"{TDIR}/validator.ts.j2",
                "context": {
                    "entity": entity,
                    "scalar_fields": scalar_fields,
                    "owner_fk_field": owner_fk_field,
                    "parent_fk_fields": self._get_parent_fk_fields(entity, all_entities, auth_entity_name),
                    "llm_constraints": llm_constraints,
                },
                "needs_llm": True,
                "llm_task": "validation_logic",
                "prompt_subdir": "fastify",
            },
            {
                "path": f"src/types/{name_lower}.types.ts",
                "template": f"{TDIR}/types.ts.j2",
                "context": {
                    "entity": entity,
                    "scalar_fields": scalar_fields,
                },
                "needs_llm": False,
            },
        ]

        # Auth entity: single auth.plugin.ts replaces auth.ts + auth.routes.ts + auth.controller.ts
        if entity.get("is_auth_entity"):
            sensitive_fields = [f for f in entity["fields"] if f["is_sensitive"]]
            files.append({
                "path": "src/plugins/auth.plugin.ts",
                "template": f"{TDIR}/auth.plugin.ts.j2",
                "context": {
                    "auth_entity": entity,
                    "sensitive_fields": sensitive_fields,
                },
                "needs_llm": False,
            })

        return files
