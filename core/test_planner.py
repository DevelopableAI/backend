from typing import Any


class TestPlanner:
    """
    Takes the parsed spec and the API file plan and produces a test file plan.

    The test plan mirrors the structure of the generated API so that every
    generated endpoint is covered. Each test module is a self-contained
    Python file that shares state with other modules via a TestContext object.

    File plan entries follow the same schema as Planner:
    - path: relative path inside the tests output directory
    - template: template name under templates/
    - context: variables for the Jinja2 template
    - needs_llm: whether LLM section filling is needed
    - llm_task: task name (maps to prompts/tests/<task>.txt)
    - llm_entity: entity dict passed to LLMGenerator (overrides context["entity"])
    - prompt_subdir: "tests" for all test modules (instructs LLM to generate Python)
    """

    def plan(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> dict[str, Any]:
        entities = spec["entities"]
        auth_entity = next((e for e in entities if e.get("is_auth_entity")), None)
        auth_entity_name = spec.get("auth_entity_name")
        non_auth_entities = [e for e in entities if not e.get("is_auth_entity")]

        # Extract per-entity route contexts from the API plan.
        # Each routes file plan has context with: entity, routes, owner_fk_field,
        # nested_routes, auth_entity_name.
        entity_route_ctx: dict[str, dict] = self._extract_entity_route_contexts(api_plan)

        modules: list[dict] = []
        num = 0

        # ── 00 Health check ──────────────────────────────────────────────────
        modules.append({
            "path": f"test_{num:02d}_health.py",
            "template": "tests/test_00_health.py.j2",
            "context": {},
            "needs_llm": False,
        })
        num += 1

        # ── Auth entity tests (01-04) ─────────────────────────────────────────
        if auth_entity:
            auth_rctx = entity_route_ctx.get(auth_entity["name"], {})
            sensitive_fields = [f for f in auth_entity["fields"] if f.get("is_sensitive")]
            login_field = self._find_login_field(auth_entity)
            required_scalar, optional_scalar = self._split_scalar_fields(auth_entity, auth_entity_name)

            # 01 Register
            modules.append({
                "path": f"test_{num:02d}_register.py",
                "template": "tests/test_auth_register.py.j2",
                "context": {
                    "section_num": num,
                    "auth_entity": auth_entity,
                    "auth_entity_name": auth_entity_name,
                    "sensitive_fields": sensitive_fields,
                    "login_field": login_field,
                    "required_scalar_fields": required_scalar,
                    "optional_scalar_fields": optional_scalar,
                },
                "needs_llm": True,
                "llm_task": "seed_data",
                "llm_entity": auth_entity,
                "prompt_subdir": "tests",
            })
            num += 1

            # 02 Login
            modules.append({
                "path": f"test_{num:02d}_login.py",
                "template": "tests/test_auth_login.py.j2",
                "context": {
                    "section_num": num,
                    "auth_entity": auth_entity,
                    "auth_entity_name": auth_entity_name,
                    "login_field": login_field,
                    "sensitive_fields": sensitive_fields,
                },
                "needs_llm": False,
            })
            num += 1

            # 03 Auth entity GET
            modules.append({
                "path": f"test_{num:02d}_{auth_entity['name_plural']}_get.py",
                "template": "tests/test_auth_entity_get.py.j2",
                "context": {
                    "section_num": num,
                    "auth_entity": auth_entity,
                    "auth_entity_name": auth_entity_name,
                    "sensitive_fields": sensitive_fields,
                },
                "needs_llm": False,
            })
            num += 1

            # 04 Auth entity WRITE
            allowed_routes = auth_rctx.get("routes", [])
            modules.append({
                "path": f"test_{num:02d}_{auth_entity['name_plural']}_write.py",
                "template": "tests/test_auth_entity_write.py.j2",
                "context": {
                    "section_num": num,
                    "auth_entity": auth_entity,
                    "auth_entity_name": auth_entity_name,
                    "allowed_routes": allowed_routes,
                    "required_scalar_fields": required_scalar,
                    "sensitive_fields": sensitive_fields,
                },
                "needs_llm": False,
            })
            num += 1

        # ── Per non-auth entity: seed_get + write ─────────────────────────────
        for entity in non_auth_entities:
            rctx = entity_route_ctx.get(entity["name"], {})
            owner_fk_field = rctx.get("owner_fk_field")
            nested_routes = rctx.get("nested_routes", [])
            allowed_routes = rctx.get("routes", [])

            required_scalar, optional_scalar = self._split_scalar_fields(entity, auth_entity_name)
            all_fk_fields = self._get_all_fk_field_names(entity)

            # Primary parent determines the canonical create path
            primary_parent_entity, primary_parent_fk_field, canonical_create_path, requires_auth = (
                self._resolve_canonical_create(entity, entities, auth_entity, nested_routes, auth_entity_name)
            )

            # seed_get
            modules.append({
                "path": f"test_{num:02d}_{entity['name_plural']}_seed_get.py",
                "template": "tests/test_entity_seed_get.py.j2",
                "context": {
                    "section_num": num,
                    "entity": entity,
                    "auth_entity": auth_entity,
                    "auth_entity_name": auth_entity_name,
                    "owner_fk_field": owner_fk_field,
                    "primary_parent_entity": primary_parent_entity,
                    "primary_parent_fk_field": primary_parent_fk_field,
                    "canonical_create_path": canonical_create_path,
                    "requires_auth": requires_auth,
                    "required_scalar_fields": required_scalar,
                    "optional_scalar_fields": optional_scalar,
                    "all_fk_fields": all_fk_fields,
                },
                "needs_llm": True,
                "llm_task": "seed_data",
                "llm_entity": entity,
                "prompt_subdir": "tests",
            })
            num += 1

            # write
            modules.append({
                "path": f"test_{num:02d}_{entity['name_plural']}_write.py",
                "template": "tests/test_entity_write.py.j2",
                "context": {
                    "section_num": num,
                    "entity": entity,
                    "auth_entity": auth_entity,
                    "auth_entity_name": auth_entity_name,
                    "owner_fk_field": owner_fk_field,
                    "nested_routes": nested_routes,
                    "allowed_routes": allowed_routes,
                    "canonical_create_path": canonical_create_path,
                    "requires_auth": requires_auth,
                    "required_scalar_fields": required_scalar,
                    "optional_scalar_fields": optional_scalar,
                    "all_fk_fields": all_fk_fields,
                    "primary_parent_entity": primary_parent_entity,
                    "primary_parent_fk_field": primary_parent_fk_field,
                },
                "needs_llm": True,
                "llm_task": "entity_write_validation",
                "llm_entity": entity,
                "prompt_subdir": "tests",
            })
            num += 1

        # ── Nested GET tests (auth entity with nested routes) ─────────────────
        if auth_entity:
            auth_rctx = entity_route_ctx.get(auth_entity["name"], {})
            auth_nested = auth_rctx.get("nested_routes", [])
            if auth_nested:
                modules.append({
                    "path": f"test_{num:02d}_nested_{auth_entity['name_lower']}_get.py",
                    "template": "tests/test_nested_auth_get.py.j2",
                    "context": {
                        "section_num": num,
                        "auth_entity": auth_entity,
                        "auth_entity_name": auth_entity_name,
                        "nested_routes": auth_nested,
                        "entities": entities,
                    },
                    "needs_llm": False,
                })
                num += 1

            # Nested POST tests: auth entity is primary parent for some child entities
            primary_nested = [nr for nr in auth_nested if nr.get("is_primary_parent")]
            for nested_route in primary_nested:
                child_entity = next(
                    (e for e in entities if e["name"] == nested_route["related_entity"]), None
                )
                if not child_entity:
                    continue
                child_rctx = entity_route_ctx.get(child_entity["name"], {})
                child_owner_fk = nested_route.get("child_owner_fk_field")
                child_req_scalar, child_opt_scalar = self._split_scalar_fields(child_entity, auth_entity_name)

                modules.append({
                    "path": f"test_{num:02d}_nested_{auth_entity['name_lower']}_{nested_route['relation_name']}.py",
                    "template": "tests/test_nested_auth_post.py.j2",
                    "context": {
                        "section_num": num,
                        "auth_entity": auth_entity,
                        "auth_entity_name": auth_entity_name,
                        "nested_route": nested_route,
                        "child_entity": child_entity,
                        "child_owner_fk_field": child_owner_fk,
                        "child_required_scalar_fields": child_req_scalar,
                        "child_optional_scalar_fields": child_opt_scalar,
                    },
                    "needs_llm": True,
                    "llm_task": "seed_data",
                    "llm_entity": child_entity,
                    "prompt_subdir": "tests",
                })
                num += 1

        # ── Nested POST tests: non-auth primary parent → child ────────────────
        for entity in non_auth_entities:
            rctx = entity_route_ctx.get(entity["name"], {})
            nested_routes = rctx.get("nested_routes", [])
            primary_nested = [nr for nr in nested_routes if nr.get("is_primary_parent")]
            for nested_route in primary_nested:
                child_entity = next(
                    (e for e in entities if e["name"] == nested_route["related_entity"]), None
                )
                if not child_entity:
                    continue
                child_req_scalar, child_opt_scalar = self._split_scalar_fields(child_entity, auth_entity_name)
                child_owner_fk = nested_route.get("child_owner_fk_field")

                modules.append({
                    "path": f"test_{num:02d}_nested_{entity['name_lower']}_{nested_route['relation_name']}.py",
                    "template": "tests/test_nested_nonauth_post.py.j2",
                    "context": {
                        "section_num": num,
                        "parent_entity": entity,
                        "auth_entity": auth_entity,
                        "auth_entity_name": auth_entity_name,
                        "nested_route": nested_route,
                        "child_entity": child_entity,
                        "child_owner_fk_field": child_owner_fk,
                        "child_required_scalar_fields": child_req_scalar,
                        "child_optional_scalar_fields": child_opt_scalar,
                    },
                    "needs_llm": True,
                    "llm_task": "seed_data",
                    "llm_entity": child_entity,
                    "prompt_subdir": "tests",
                })
                num += 1

        # ── Token security (requires any write endpoint) ───────────────────────
        write_endpoint = self._find_write_endpoint(entity_route_ctx, non_auth_entities, auth_entity)
        if write_endpoint:
            modules.append({
                "path": f"test_{num:02d}_token_security.py",
                "template": "tests/test_token_security.py.j2",
                "context": {
                    "section_num": num,
                    "write_endpoint": write_endpoint,
                    "auth_entity_name": auth_entity_name,
                },
                "needs_llm": False,
            })
            num += 1

        # ── Input validation (first writable non-auth entity) ─────────────────
        if non_auth_entities:
            first_entity = non_auth_entities[0]
            first_rctx = entity_route_ctx.get(first_entity["name"], {})
            first_owner_fk = first_rctx.get("owner_fk_field")
            first_req_scalar, _ = self._split_scalar_fields(first_entity, auth_entity_name)
            first_primary, _, first_create_path, first_req_auth = self._resolve_canonical_create(
                first_entity, entities, auth_entity,
                first_rctx.get("nested_routes", []), auth_entity_name
            )

            modules.append({
                "path": f"test_{num:02d}_input_validation.py",
                "template": "tests/test_input_validation.py.j2",
                "context": {
                    "section_num": num,
                    "entity": first_entity,
                    "auth_entity": auth_entity,
                    "auth_entity_name": auth_entity_name,
                    "canonical_create_path": first_create_path,
                    "requires_auth": first_req_auth,
                    "required_scalar_fields": first_req_scalar,
                    "owner_fk_field": first_owner_fk,
                },
                "needs_llm": False,
            })
            num += 1

        # ── Response structure (all entities) ─────────────────────────────────
        writable_entity = non_auth_entities[0] if non_auth_entities else None
        modules.append({
            "path": f"test_{num:02d}_response_structure.py",
            "template": "tests/test_response_structure.py.j2",
            "context": {
                "section_num": num,
                "entities": entities,
                "auth_entity": auth_entity,
                "auth_entity_name": auth_entity_name,
                "writable_entity": writable_entity,
            },
            "needs_llm": False,
        })
        num += 1

        # ── Security audit (if auth entity) ───────────────────────────────────
        if auth_entity:
            sensitive_fields = [f for f in auth_entity["fields"] if f.get("is_sensitive")]
            modules.append({
                "path": f"test_{num:02d}_security_audit.py",
                "template": "tests/test_security_audit.py.j2",
                "context": {
                    "section_num": num,
                    "auth_entity": auth_entity,
                    "auth_entity_name": auth_entity_name,
                    "sensitive_fields": sensitive_fields,
                    "login_field": self._find_login_field(auth_entity),
                },
                "needs_llm": False,
            })
            num += 1

        # ── Cleanup ───────────────────────────────────────────────────────────
        # Build a list of all entity cleanup targets in reverse dependency order
        cleanup_targets = self._build_cleanup_targets(
            entities, auth_entity, entity_route_ctx, non_auth_entities
        )
        modules.append({
            "path": f"test_{num:02d}_cleanup.py",
            "template": "tests/test_cleanup.py.j2",
            "context": {
                "section_num": num,
                "entities": entities,
                "auth_entity": auth_entity,
                "auth_entity_name": auth_entity_name,
                "cleanup_targets": cleanup_targets,
            },
            "needs_llm": False,
        })
        num += 1

        # ── Helpers and runner ────────────────────────────────────────────────
        module_names = [m["path"][:-3] for m in modules]  # strip .py

        support_files = [
            {
                "path": "helpers.py",
                "template": "tests/helpers.py.j2",
                "context": {},
                "needs_llm": False,
            },
            {
                "path": "run_all.py",
                "template": "tests/run_all.py.j2",
                "context": {
                    "module_names": module_names,
                    "auth_entity_name": spec.get("auth_entity_name"),
                },
                "needs_llm": False,
            },
        ]

        return {"files": support_files + modules}

    # ── Private helpers ───────────────────────────────────────────────────────

    def _extract_entity_route_contexts(self, api_plan: dict) -> dict[str, dict]:
        """
        Scans the API file plan and extracts the context dict from each entity's
        routes file plan (template = express/routes.ts.j2, not auth.routes.ts.j2).
        Returns a mapping from entity name → route context dict.
        """
        result: dict[str, dict] = {}
        for file_plan in api_plan.get("files", []):
            tmpl = file_plan.get("template", "")
            if tmpl == "express/routes.ts.j2":
                ctx = file_plan.get("context", {})
                entity = ctx.get("entity")
                if entity:
                    result[entity["name"]] = ctx
        return result

    def _split_scalar_fields(
        self, entity: dict, auth_entity_name: str | None
    ) -> tuple[list[dict], list[dict]]:
        """
        Split entity scalar fields into (required, optional).
        Excludes: relation fields, ID fields, FK fields (injected server-side).
        """
        fk_field_names = {
            rel["fk_field"]
            for rel in entity.get("relations", [])
            if rel.get("fk_field") and rel["type"] == "many_to_one"
        }
        scalar_non_fk = [
            f for f in entity["fields"]
            if not f["is_relation"] and not f["is_id"] and f["name"] not in fk_field_names
            # also skip auto-managed timestamp fields
            and f.get("prisma_type") not in ("DateTime",)
        ]
        required = [f for f in scalar_non_fk if not f["is_optional"] and not f.get("default")]
        optional = [f for f in scalar_non_fk if f["is_optional"] or f.get("default")]
        return required, optional

    def _get_all_fk_field_names(self, entity: dict) -> list[str]:
        """Returns all FK scalar field names on this entity (many_to_one relations)."""
        return [
            rel["fk_field"]
            for rel in entity.get("relations", [])
            if rel.get("fk_field") and rel["type"] == "many_to_one"
        ]

    def _find_login_field(self, auth_entity: dict) -> dict | None:
        """Find the field used for login (prefer 'email', then first unique field)."""
        email_field = next(
            (f for f in auth_entity["fields"] if f["name"] == "email"), None
        )
        if email_field:
            return email_field
        return next(
            (f for f in auth_entity["fields"] if f.get("is_unique") and not f["is_id"]), None
        )

    def _resolve_canonical_create(
        self,
        entity: dict,
        all_entities: list[dict],
        auth_entity: dict | None,
        nested_routes: list[dict],
        auth_entity_name: str | None,
    ) -> tuple[dict | None, str | None, str, bool]:
        """
        Determines the canonical create path for an entity.

        Returns: (primary_parent_entity, primary_parent_fk_field, create_path, requires_auth)

        For entities whose primary parent IS the auth entity:
            POST /api/{auth_plural}/{child_plural}  (no :id since auth is from JWT)
        For entities whose primary parent is a NON-auth entity:
            POST /api/{parent_plural}/:id/{child_plural}  (parent ID in URL)
        For entities with no primary parent:
            POST /api/{entity_plural}
        """
        auth_entity_name_str = auth_entity_name or ""

        # Find the primary parent nested route (the one with is_primary_parent=True)
        primary_nr = next((nr for nr in nested_routes if nr.get("is_primary_parent")), None)

        if primary_nr is None:
            # No primary parent → direct POST route
            return None, None, f"/api/{entity['name_plural']}", bool(auth_entity_name)

        # Find which entity this nested route belongs to (the parent)
        parent_entity = None
        for e in all_entities:
            if e.get("is_auth_entity"):
                # Check if this auth entity has a nested route for our entity
                for rel in e.get("relations", []):
                    if (rel["type"] == "one_to_many" and
                            rel["related_entity"] == entity["name"]):
                        if primary_nr.get("fk_field") and primary_nr["fk_field"] in [
                            r["fk_field"] for r in entity.get("relations", [])
                            if r.get("fk_field") and r["related_entity"] == e["name"]
                        ]:
                            parent_entity = e
                            break
            if not parent_entity:
                for rel in e.get("relations", []):
                    if (rel["type"] == "one_to_many" and
                            rel["related_entity"] == entity["name"] and
                            not e.get("is_auth_entity")):
                        parent_entity = e
                        break

        # Determine primary parent FK field (the FK on child pointing to parent)
        parent_fk = primary_nr.get("fk_field")

        if parent_entity and parent_entity.get("is_auth_entity"):
            # Primary parent is auth entity → no :id in path
            path = f"/api/{parent_entity['name_plural']}/{entity['name_plural']}"
            return parent_entity, parent_fk, path, True

        if parent_entity:
            # Primary parent is a non-auth entity → :id in path
            path = f"/api/{parent_entity['name_plural']}/:id/{entity['name_plural']}"
            return parent_entity, parent_fk, path, bool(auth_entity_name)

        # Fallback: direct POST
        return None, None, f"/api/{entity['name_plural']}", bool(auth_entity_name)

    def _find_write_endpoint(
        self,
        entity_route_ctx: dict,
        non_auth_entities: list[dict],
        auth_entity: dict | None,
    ) -> dict | None:
        """
        Returns a descriptor for the first available write (PUT) endpoint.
        Used by the token security test module.
        Returns: {method, path, entity_plural, entity_lower, needs_body}
        """
        # Prefer the first non-auth entity with a PUT route
        for entity in non_auth_entities:
            rctx = entity_route_ctx.get(entity["name"], {})
            routes = rctx.get("routes", [])
            put_route = next((r for r in routes if r["method"] == "PUT"), None)
            if put_route:
                return {
                    "method": "PUT",
                    "path": f"/api/{entity['name_plural']}/",
                    "entity_plural": entity["name_plural"],
                    "entity_lower": entity["name_lower"],
                    "state_id_key": f"{entity['name_lower']}1_id",
                }
        # Fall back to auth entity PUT
        if auth_entity:
            rctx = entity_route_ctx.get(auth_entity["name"], {})
            routes = rctx.get("routes", [])
            put_route = next((r for r in routes if r["method"] == "PUT"), None)
            if put_route:
                return {
                    "method": "PUT",
                    "path": f"/api/{auth_entity['name_plural']}/",
                    "entity_plural": auth_entity["name_plural"],
                    "entity_lower": auth_entity["name_lower"],
                    "state_id_key": f"{auth_entity['name_lower']}1_id",
                }
        return None

    def _build_cleanup_targets(
        self,
        entities: list[dict],
        auth_entity: dict | None,
        entity_route_ctx: dict,
        non_auth_entities: list[dict],
    ) -> list[dict]:
        """
        Builds an ordered list of cleanup targets (delete operations) for the cleanup module.

        Order: child entities first (reverse dependency order), then auth entity last.
        Each target specifies: entity, state_id_keys (list), token_key, api_path_prefix.
        """
        targets = []
        auth_name = auth_entity["name"] if auth_entity else None

        # Process non-auth entities in REVERSE order (children before parents)
        for entity in reversed(non_auth_entities):
            rctx = entity_route_ctx.get(entity["name"], {})
            routes = rctx.get("routes", [])
            has_delete = any(r["method"] == "DELETE" for r in routes)
            if not has_delete:
                continue

            owner_fk = rctx.get("owner_fk_field")
            # Determine which token to use: owner token if ownership-enforced
            token_key = f"{auth_entity['name_lower']}1_token" if auth_entity else None

            targets.append({
                "entity": entity,
                "state_id_keys": [
                    f"{entity['name_lower']}1_id",
                    f"{entity['name_lower']}2_id",
                    f"{entity['name_lower']}3_id",
                ],
                # Extra IDs created in special test sections
                "extra_state_keys": [
                    f"spoofed_{entity['name_lower']}_id",
                    f"long_title_{entity['name_lower']}_id",
                    f"long_content_{entity['name_lower']}_id",
                    f"xss_{entity['name_lower']}_id",
                    f"unicode_{entity['name_lower']}_id",
                ],
                "token_key": token_key,
                "api_path_prefix": f"/api/{entity['name_plural']}/",
                "has_ownership": bool(owner_fk),
            })

        return targets
