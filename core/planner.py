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
                "template": "express/api/package.json.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "tsconfig.json",
                "template": "express/api/tsconfig.json.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/app.ts",
                "template": "express/api/app.ts.j2",
                "context": {
                    "entities": entities,
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            },
            {
                "path": "src/server.ts",
                "template": "express/api/server.ts.j2",
                "context": {"auth_entity_name": auth_entity_name},
                "needs_llm": False,
            },
            {
                "path": "src/lib/prisma.ts",
                "template": "express/api/prisma.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/lib/errors.ts",
                "template": "express/api/errors.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "src/lib/pagination.ts",
                "template": "express/api/pagination.ts.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": ".env.example",
                "template": "express/api/env.example.j2",
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
                "template": "express/api/crypto.ts.j2",
                "context": {},
                "needs_llm": False,
            })

        # Add JWT auth middleware if an auth entity was detected
        if auth_entity_name:
            auth_entity = next((e for e in entities if e.get("is_auth_entity")), None)
            files.append({
                "path": "src/lib/auth.ts",
                "template": "express/api/auth.ts.j2",
                "context": {"auth_entity": auth_entity},
                "needs_llm": False,
            })

        return files

    def _plan_entity_files(self, entity: dict, spec: dict) -> list[dict]:
        name_lower = entity["name_lower"]
        name_plural = entity["name_plural"]

        # scalar (non-relation) fields for validation
        scalar_fields = [f for f in entity["fields"] if not f["is_relation"] and not f["is_id"]]

        # filterable: non-id, non-relation, non-sensitive scalar fields (string/number/boolean only)
        filterable_fields = [
            f for f in entity["fields"]
            if not f["is_relation"] and not f["is_sensitive"] and not f["is_id"]
            and f.get("ts_type") in ("string", "number", "boolean")
        ]

        # sortable: non-relation, non-sensitive scalar fields (includes id for explicit ordering)
        sortable_fields = [
            f for f in entity["fields"]
            if not f["is_relation"] and not f["is_sensitive"]
            and f.get("ts_type") in ("string", "number", "boolean")
        ]

        auth_entity_name = spec.get("auth_entity_name")
        all_entities = spec["entities"]

        # ownership: FK field on this entity pointing to the auth entity
        owner_fk_field = self._get_owner_fk_field(entity, auth_entity_name)

        # nested routes: one_to_many relations with their child FK resolved
        nested_routes = self._build_nested_routes(entity, all_entities, auth_entity_name)

        # business rules: denied endpoints and LLM constraint hints (set by BusinessRulesParser)
        endpoint_deny: list[dict] = entity.get("endpoint_deny", [])
        llm_constraints: list[str] = entity.get("llm_constraints", [])

        # primary parent: determines the canonical create endpoint for this entity
        primary_parent = self._get_primary_parent_name(entity, all_entities, auth_entity_name)

        # many_to_one relations on this entity (for findManyByFK generation in child repo)
        parent_fk_relations = [
            r for r in entity.get("relations", []) if r["type"] == "many_to_one" and r.get("fk_field")
        ]

        # child_cascade_deletes: other entities that FK-reference this entity and must be
        # deleted first (in a transaction) before this entity can be deleted.
        # For each sibling entity, find many_to_one relations pointing at this entity.
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

        # Filter out routes denied by business rules and suppress direct POST for entities
        # that have a primary parent (canonical create is the nested route under that parent)
        allowed_routes = [
            r for r in self._infer_routes(entity)
            if not self._is_route_denied(r, endpoint_deny)
            and not (r["method"] == "POST" and primary_parent is not None)
        ]

        files = [
            {
                "path": f"src/routes/{name_plural}.routes.ts",
                "template": "express/api/routes.ts.j2",
                "context": {
                    "entity": entity,
                    "routes": allowed_routes,
                    "owner_fk_field": owner_fk_field,
                    "nested_routes": nested_routes,
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            },
            {
                "path": f"src/controllers/{name_lower}.controller.ts",
                "template": "express/api/controller.ts.j2",
                "context": {
                    "entity": entity,
                    "routes": allowed_routes,
                    "owner_fk_field": owner_fk_field,
                    "nested_routes": nested_routes,
                    "auth_entity_name": auth_entity_name,
                    "filterable_fields": filterable_fields,
                    "sortable_fields": sortable_fields,
                },
                "needs_llm": False,
            },
            {
                "path": f"src/repositories/{name_lower}.repository.ts",
                "template": "express/api/repository.ts.j2",
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
                "template": "express/api/validator.ts.j2",
                "context": {
                    "entity": entity,
                    "scalar_fields": scalar_fields,
                    "owner_fk_field": owner_fk_field,
                    "parent_fk_fields": self._get_parent_fk_fields(entity, all_entities, auth_entity_name),
                    # llm_constraints from business rules file prepended to entity llm_hints for LLM context
                    "llm_constraints": llm_constraints,
                },
                "needs_llm": True,
                "llm_task": "validation_logic",
            },
            {
                "path": f"src/types/{name_lower}.types.ts",
                "template": "express/api/types.ts.j2",
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
                    "template": "express/api/auth.routes.ts.j2",
                    "context": {"auth_entity": entity},
                    "needs_llm": False,
                },
                {
                    "path": "src/controllers/auth.controller.ts",
                    "template": "express/api/auth.controller.ts.j2",
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

        Optional FK fields (e.g. `assigneeId Int?`) are skipped — they cannot serve as
        mandatory ownership anchors because they may be null, which would break ownership checks.
        """
        if not auth_entity_name:
            return None
        for rel in entity.get("relations", []):
            if rel["related_entity"] == auth_entity_name and rel["type"] == "many_to_one":
                fk_name = rel.get("fk_field")
                if fk_name:
                    fk_field_def = next(
                        (f for f in entity["fields"] if f["name"] == fk_name), None
                    )
                    if fk_field_def and fk_field_def.get("is_optional"):
                        continue
                return fk_name
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

    def _get_primary_parent_name(
        self, entity: dict, all_entities: list[dict], auth_entity_name: str | None
    ) -> str | None:
        """
        Returns the name of this entity's primary parent — the single entity whose nested route
        is the canonical write (create) path for this entity.

        Priority:
        1. Explicit override from business rules YAML: entity["primary_parent"]
        2. First non-auth many_to_one FK (e.g. Comment's postId → Post wins over authorId → User)
        3. Auth entity FK (e.g. Post's authorId → User when User is the only parent)
        4. None — no parent, entity gets a direct POST route

        This drives two decisions in the planner:
        - Suppresses the direct POST /entity route when a primary parent exists
        - Marks exactly one nested route as is_primary_parent=True (the rest expose GET only)
        """
        # Explicit override via business rules
        if entity.get("primary_parent"):
            return entity["primary_parent"]
        # First non-auth many_to_one FK
        for rel in entity.get("relations", []):
            if rel["type"] == "many_to_one" and rel["related_entity"] != auth_entity_name and rel.get("fk_field"):
                return rel["related_entity"]
        # Auth entity FK
        if auth_entity_name and self._get_owner_fk_field(entity, auth_entity_name):
            return auth_entity_name
        return None

    def _get_parent_fk_fields(
        self, entity: dict, all_entities: list[dict], auth_entity_name: str | None
    ) -> list[str]:
        """
        Returns ONLY the FK field pointing to this entity's PRIMARY parent.

        The primary parent FK is the one injected from the URL param in nested create routes
        (e.g. orderId on OrderItem when creating via POST /orders/:id/items).
        Secondary non-auth parent FKs (e.g. productId on OrderItem pointing to Product)
        must still come from the request body and must NOT be excluded from the nested schema.
        """
        primary_parent_name = self._get_primary_parent_name(entity, all_entities, auth_entity_name)
        if not primary_parent_name or primary_parent_name == auth_entity_name:
            return []
        for rel in entity.get("relations", []):
            if (rel["type"] == "many_to_one"
                    and rel["related_entity"] == primary_parent_name
                    and rel.get("fk_field")):
                return [rel["fk_field"]]
        return []

    def _build_nested_routes(
        self, entity: dict, all_entities: list[dict], auth_entity_name: str | None = None
    ) -> list[dict]:
        """
        For each one_to_many relation on this entity, build a nested route descriptor.
        E.g. User has posts Post[] → nested route for /users/:id/posts

        Each descriptor includes:
        - fk_field: the FK on the child pointing back to this parent (injected from URL)
        - child_owner_fk_field: the FK on the child pointing to the auth entity, if different
          from fk_field (needs to be additionally injected from JWT in the nested create handler)
        - has_parent_fk_schema: True if the child entity has a CreateNestedSchema (i.e. it has
          non-auth parent FKs that should be excluded in nested-route creates)
        - use_nested_schema: True if THIS nested route's fk_field is a non-auth parent FK,
          meaning the nested create handler should use CreateNestedSchema instead of CreateSchema
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

            # Child's owner FK (points to auth entity). If it equals fk_field, no extra injection
            # needed (e.g. POST /users/:id/posts — fk_field=authorId IS the owner FK).
            # If different (e.g. POST /posts/:id/comments — fk_field=postId, owner=authorId),
            # the nested create must ALSO inject the owner FK from JWT.
            child_owner_fk = self._get_owner_fk_field(child_entity, auth_entity_name)
            child_owner_fk_field = child_owner_fk if child_owner_fk != fk_field else None

            # Non-auth parent FKs on the child entity
            child_parent_fk_fields = self._get_parent_fk_fields(child_entity, all_entities, auth_entity_name)
            has_parent_fk_schema = len(child_parent_fk_fields) > 0

            # Use CreateNested only when THIS route's fk_field is a non-auth parent FK
            # (i.e. the schema for this nested create must exclude the parent FK injected from URL)
            use_nested_schema = fk_field in child_parent_fk_fields

            # is_primary_parent: True only when THIS entity (the loop parent) is the child's
            # primary parent. Only the primary parent's nested route exposes POST (create).
            # All other parents expose GET (filtered list) only.
            primary_parent_name = self._get_primary_parent_name(child_entity, all_entities, auth_entity_name)
            is_primary_parent = (entity["name"] == primary_parent_name)

            # child filterable/sortable fields (for nested list handler in parent controller)
            child_filterable = [
                f for f in child_entity["fields"]
                if not f["is_relation"] and not f["is_sensitive"] and not f["is_id"]
                and f.get("ts_type") in ("string", "number", "boolean")
            ]
            child_sortable = [
                f for f in child_entity["fields"]
                if not f["is_relation"] and not f["is_sensitive"]
                and f.get("ts_type") in ("string", "number", "boolean")
            ]

            nested.append({
                "relation_name": rel["name"],                          # e.g. "comments"
                "related_entity": child_entity_name,                   # e.g. "Comment"
                "related_entity_lower": child_entity["name_lower"],    # e.g. "comment"
                "related_entity_plural": child_entity["name_plural"],  # e.g. "comments"
                "fk_field": fk_field,                                  # e.g. "postId"
                "child_owner_fk_field": child_owner_fk_field,          # e.g. "authorId" or None
                "has_parent_fk_schema": has_parent_fk_schema,          # e.g. True
                "use_nested_schema": use_nested_schema,                # e.g. True
                "is_primary_parent": is_primary_parent,               # e.g. True for Post→Comment
                "filterable_fields": child_filterable,
                "sortable_fields": child_sortable,
            })

        return nested

    def _is_route_denied(self, route: dict, endpoint_deny: list[dict]) -> bool:
        """
        Returns True if the given route should be suppressed based on the entity's
        endpoint_deny rules from the business rules file.

        Matching is case-insensitive on method and uses simple path prefix/equality on path.
        E.g. deny {method: "POST", path: "/users/:id/users"} suppresses the create route
        when the path matches.
        """
        for deny_rule in endpoint_deny:
            rule_method = deny_rule.get("method", "").upper()
            rule_path = deny_rule.get("path", "")
            if route.get("method", "").upper() == rule_method and route.get("path", "") == rule_path:
                return True
        return False
