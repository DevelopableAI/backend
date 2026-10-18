import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from generators.template import TemplateGenerator
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    import sys

    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from generators.template import TemplateGenerator


CONTRACT_VERSION = "1.0"
ARCHITECTURE_PROFILE = "express-ts-rest-pragmatic-solid"
TEST_PROFILE = "python-integration-canonical"
INFRA_PROFILE = "terraform-managed"
MANAGED_ROOTS = [
    "src/routes",
    "src/controllers",
    "src/services",
    "src/repositories",
    "src/validators",
    "src/types",
    "src/contracts",
    "src/adapters",
    "src/bootstrap",
]
SELECTED_MANAGED_LIBS = [
    "src/lib/auth.ts",
    "src/lib/errors.ts",
    "src/lib/pagination.ts",
]
FORBIDDEN_IMPORTS_BY_LAYER = {
    "routes": ["../repositories/", "../services/", "../lib/prisma.js", "jsonwebtoken", "bcryptjs"],
    "controllers": ["../repositories/", "../lib/prisma.js", "jsonwebtoken", "bcryptjs"],
    "services": ["../lib/prisma.js", "jsonwebtoken", "bcryptjs"],
    "repositories": ["../controllers/", "../routes/", "../bootstrap/", "jsonwebtoken", "bcryptjs"],
    "adapters": ["../controllers/", "../routes/", "../repositories/"],
}
FORBIDDEN_NEWS_BY_LAYER = {
    "routes": ["Controller", "Repository", "Service"],
    "controllers": ["Repository"],
}
REQUIRED_IMPORTS_BY_LAYER = {
    "routes": ["../bootstrap/container.js"],
    "repositories": ["../lib/prisma.js"],
}


class ContractLoader:
    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir
        self.contract_dir = repo_dir / ".developable"

    def exists(self) -> bool:
        return (self.contract_dir / "contract.json").exists()

    def load_json(self, relative_path: str) -> dict[str, Any]:
        path = self.contract_dir / relative_path
        if not path.exists():
            return {}
        return json.loads(path.read_text())

    def load_contract(self) -> dict[str, Any]:
        return self.load_json("contract.json")


class ProjectContractWriter:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.template_gen = TemplateGenerator()

    def write(
        self,
        spec: dict[str, Any],
        api_plan: dict[str, Any],
        test_plan: dict[str, Any] | None = None,
        infra_plan: dict[str, Any] | None = None,
        repo_mode: str = "generated",
    ) -> None:
        bundle = self._build_bundle(spec, api_plan, test_plan, infra_plan, repo_mode)
        for relative_path, content in bundle.items():
            target = self.out_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

        for relative_path, template_name in self._script_templates().items():
            target = self.out_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                self.template_gen.render(
                    template_name=template_name,
                    context={"managed_roots": MANAGED_ROOTS},
                )
            )

    def _build_bundle(
        self,
        spec: dict[str, Any],
        api_plan: dict[str, Any],
        test_plan: dict[str, Any] | None,
        infra_plan: dict[str, Any] | None,
        repo_mode: str,
    ) -> dict[str, str]:
        managed_files = self._managed_files(api_plan)
        project_name = self._project_name(spec)
        route_inventory = self._route_inventory(api_plan, spec)
        composition = self._composition(spec, api_plan)
        signatures = self._ast_signatures(spec, api_plan)

        contract = {
            "managed_by": "developable",
            "contract_version": CONTRACT_VERSION,
            "repo_mode": repo_mode,
            "stack": {
                "runtime": "node",
                "framework": "express",
                "language": "typescript",
                "orm": "prisma",
            },
            "project_name": project_name,
            "architecture_profile": ARCHITECTURE_PROFILE,
            "test_profile": TEST_PROFILE,
            "infra_profile": INFRA_PROFILE,
            "slash_command_required": True,
            "cli_role": "secondary",
            "managed_mode": True,
            "last_applied_at": _utc_now(),
        }

        entities = []
        for entity in spec["entities"]:
            id_field = next((field for field in entity["fields"] if field["is_id"] and not field["is_relation"]), None)
            entities.append(
                {
                    "name": entity["name"],
                    "name_lower": entity["name_lower"],
                    "name_plural": entity["name_plural"],
                    "is_auth_entity": entity.get("is_auth_entity", False),
                    "id_field": id_field["name"] if id_field else "id",
                    "id_type": id_field["ts_type"] if id_field else "number",
                    "owner_fk_field": _owner_fk_field(entity, spec.get("auth_entity_name")),
                    "sensitive_fields": [
                        field["name"]
                        for field in entity["fields"]
                        if field["is_sensitive"] and not field["is_relation"]
                    ],
                    "fields": [
                        {
                            "name": field["name"],
                            "type": field["ts_type"],
                            "is_optional": field["is_optional"],
                            "is_relation": field["is_relation"],
                            "is_sensitive": field["is_sensitive"],
                            "is_auto_managed": field["is_auto_managed"],
                        }
                        for field in entity["fields"]
                    ],
                    "relations": entity.get("relations", []),
                }
            )

        tests = {
            "profile": TEST_PROFILE,
            "required": True,
            "test_runner": "python tests/run_all.py",
            "files": [entry["path"] for entry in (test_plan or {}).get("files", [])],
            "acceptance_requirements": [
                "Generated Python integration suite remains the canonical backend acceptance layer.",
                "Route, security, and ownership behavior must be covered by Python integration tests.",
            ],
        }

        infra = {
            "profile": INFRA_PROFILE,
            "dockerfile": "Dockerfile",
            "compose_file": "docker-compose.yml",
            "ci_workflow": ".github/workflows/ci.yml",
            "terraform_files": [entry["path"] for entry in (infra_plan or {}).get("files", []) if entry["path"].startswith("terraform/")],
            "deployment_tracking": ".developable/infra.json",
            "managed_hooks": [
                ".developable/hooks/pre-commit",
                ".developable/hooks/pre-push",
            ],
        }

        dependency_rules = {
            "managed_roots": MANAGED_ROOTS,
            "selected_managed_libs": SELECTED_MANAGED_LIBS,
            "forbidden_imports_by_layer": FORBIDDEN_IMPORTS_BY_LAYER,
            "required_imports_by_layer": REQUIRED_IMPORTS_BY_LAYER,
            "forbidden_new_by_layer": FORBIDDEN_NEWS_BY_LAYER,
        }

        bundle = {
            ".developable/contract.json": _json_dump(contract),
            ".developable/entities.json": _json_dump({"auth_entity_name": spec.get("auth_entity_name"), "entities": entities}),
            ".developable/routes.json": _json_dump(route_inventory),
            ".developable/tests.json": _json_dump(tests),
            ".developable/infra.json": _json_dump(infra),
            ".developable/provenance.json": _json_dump(
                {
                    "mode": repo_mode,
                    "contract_version": CONTRACT_VERSION,
                    "planner": "Planner",
                    "generated_at": _utc_now(),
                    "last_conformance_status": "passing",
                }
            ),
            ".developable/composition.json": _json_dump(composition),
            ".developable/manifests/managed-files.json": _json_dump({"paths": managed_files}),
            ".developable/manifests/ast-signatures.json": _json_dump(signatures),
            ".developable/manifests/dependency-rules.json": _json_dump(dependency_rules),
            ".developable/invariants.yaml": self._invariants_yaml(spec),
            ".developable/architecture.yaml": self._architecture_yaml(),
            ".developable/solid.yaml": self._solid_yaml(),
            ".developable/extensions.yaml": self._extensions_yaml(),
        }
        return bundle

    def _managed_files(self, api_plan: dict[str, Any]) -> list[str]:
        managed = [
            entry["path"]
            for entry in api_plan.get("files", [])
            if any(entry["path"].startswith(prefix + "/") or entry["path"] == prefix for prefix in MANAGED_ROOTS)
            or entry["path"] in SELECTED_MANAGED_LIBS
        ]
        return sorted(dict.fromkeys(managed))

    def _route_inventory(self, api_plan: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
        entities: list[dict[str, Any]] = []
        auth_entity_name = spec.get("auth_entity_name")
        for entry in api_plan.get("files", []):
            if not entry["path"].startswith("src/routes/") or entry["path"] == "src/routes/auth.routes.ts":
                continue
            context = entry.get("context", {})
            entity = context.get("entity")
            if not entity:
                continue
            entities.append(
                {
                    "entity": entity["name"],
                    "base_path": f"/api/{entity['name_plural']}",
                    "requires_auth_on_write": bool(auth_entity_name),
                    "routes": context.get("routes", []),
                    "nested_routes": context.get("nested_routes", []),
                    "owner_fk_field": context.get("owner_fk_field"),
                }
            )

        auth = None
        auth_entity = next((entity for entity in spec["entities"] if entity.get("is_auth_entity")), None)
        if auth_entity:
            auth = {
                "entity": auth_entity["name"],
                "base_path": "/auth",
                "routes": [
                    {"method": "POST", "path": "/auth/register", "handler": "register"},
                    {"method": "POST", "path": "/auth/login", "handler": "login"},
                ],
            }

        return {"entities": entities, "auth": auth}

    def _composition(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> dict[str, Any]:
        controllers: list[dict[str, Any]] = []
        services: list[dict[str, Any]] = []
        repositories: list[dict[str, Any]] = []
        auth_entity_name = spec.get("auth_entity_name")

        for entry in api_plan.get("files", []):
            context = entry.get("context", {})
            entity = context.get("entity")
            if not entity:
                continue
            if entry["path"].startswith("src/controllers/") and entry["path"] != "src/controllers/auth.controller.ts":
                controllers.append(
                    {
                        "file": entry["path"],
                        "service_contract": f"src/contracts/{entity['name_lower']}.service.contract.ts",
                        "nested_service_contracts": [
                            f"src/contracts/{nested['related_entity_lower']}.service.contract.ts"
                            for nested in context.get("nested_routes", [])
                        ],
                    }
                )
            if entry["path"].startswith("src/services/"):
                services.append(
                    {
                        "file": entry["path"],
                        "repository_contract": f"src/contracts/{entity['name_lower']}.repository.contract.ts",
                        "owner_fk_field": _owner_fk_field(entity, auth_entity_name),
                    }
                )
            if entry["path"].startswith("src/repositories/"):
                repositories.append(
                    {
                        "file": entry["path"],
                        "uses_prisma": True,
                    }
                )

        auth = None
        if auth_entity_name:
            auth = {
                "controller": "src/controllers/auth.controller.ts",
                "service": "src/services/auth.service.ts",
                "repository": f"src/repositories/{auth_entity_name[0].lower() + auth_entity_name[1:]}.repository.ts",
                "token_service": "src/adapters/jwt-token.service.ts",
                "password_hasher": "src/adapters/bcrypt-password-hasher.ts",
            }

        return {
            "controllers": controllers,
            "services": services,
            "repositories": repositories,
            "auth": auth,
        }

    def _ast_signatures(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> dict[str, Any]:
        required_classes: dict[str, str] = {}
        required_exports: dict[str, list[str]] = {}

        for entry in api_plan.get("files", []):
            context = entry.get("context", {})
            entity = context.get("entity")
            if entry["path"].startswith("src/controllers/") and entity:
                required_classes[entry["path"]] = f"{entity['name']}Controller"
            if entry["path"].startswith("src/services/") and entity:
                required_classes[entry["path"]] = f"{entity['name']}Service"
            if entry["path"].startswith("src/repositories/") and entity:
                required_classes[entry["path"]] = f"{entity['name']}Repository"

        if spec.get("auth_entity_name"):
            required_classes["src/controllers/auth.controller.ts"] = "AuthController"
            required_classes["src/services/auth.service.ts"] = "AuthService"

        return {
            "required_classes": required_classes,
            "required_exports": required_exports,
        }

    def _invariants_yaml(self, spec: dict[str, Any]) -> str:
        auth_enabled = bool(spec.get("auth_entity_name"))
        lines = [
            "security:",
            f"  require_auth_on_write_routes: {'true' if auth_enabled else 'false'}",
            f"  inject_owner_fk_server_side: {'true' if auth_enabled else 'false'}",
            f"  forbid_client_owned_fk_input: {'true' if auth_enabled else 'false'}",
            f"  auth_entity_self_only_update_delete: {'true' if auth_enabled else 'false'}",
            f"  hash_sensitive_fields: {'true' if auth_enabled else 'false'}",
            f"  exclude_sensitive_fields_from_responses: {'true' if auth_enabled else 'false'}",
            "",
            "architecture:",
            "  require_layer_split:",
            "    - routes",
            "    - controllers",
            "    - services",
            "    - repositories",
            "    - validators",
            "    - adapters",
            "  forbid_prisma_calls_in_controllers: true",
            "  forbid_validation_logic_in_repositories: true",
            "",
            "testing:",
            "  python_integration_tests_canonical: true",
            "  require_route_coverage_from_contract: true",
            "",
            "infrastructure:",
            "  require_ci_pipeline: true",
            "  require_dockerfile: true",
            "  require_terraform_tracking: true",
        ]
        return "\n".join(lines) + "\n"

    def _architecture_yaml(self) -> str:
        return "\n".join(
            [
                "source_layout:",
                "  routes: src/routes",
                "  controllers: src/controllers",
                "  services: src/services",
                "  repositories: src/repositories",
                "  validators: src/validators",
                "  types: src/types",
                "  contracts: src/contracts",
                "  adapters: src/adapters",
                "  bootstrap: src/bootstrap",
                "  lib: src/lib",
                "",
                "dependency_flow:",
                "  - routes -> bootstrap",
                "  - bootstrap -> controllers",
                "  - controllers -> services",
                "  - services -> repositories",
                "  - services -> adapters",
                "  - repositories -> lib/prisma",
                "",
                "managed_roots:",
                *[f"  - {root}" for root in MANAGED_ROOTS],
                "",
                "selected_managed_libs:",
                *[f"  - {lib_path}" for lib_path in SELECTED_MANAGED_LIBS],
            ]
        ) + "\n"

    def _solid_yaml(self) -> str:
        return "\n".join(
            [
                "single_responsibility:",
                "  routes: declare endpoints and apply middleware only",
                "  controllers: transport parsing, validation, and response mapping only",
                "  services: business policy, ownership checks, orchestration only",
                "  repositories: persistence only",
                "  adapters: provider integration only",
                "",
                "open_closed:",
                "  token_and_hash_providers_must_be_replaceable_via_contracts: true",
                "",
                "liskov_substitution:",
                "  repository_contracts_must_preserve_not_found_semantics: true",
                "  service_contracts_must_preserve_authorization_semantics: true",
                "",
                "interface_segregation:",
                "  controllers_depend_only_on_service_contracts: true",
                "  services_depend_only_on_repository_and_adapter_contracts: true",
                "",
                "dependency_inversion:",
                "  only_bootstrap_knows_concrete_implementations: true",
                "  repositories_must_not_be_constructed_in_routes_or_controllers: true",
            ]
        ) + "\n"

    def _extensions_yaml(self) -> str:
        return "\n".join(
            [
                "allowed_extension_zones:",
                "  - src/lib",
                "  - src/services",
                "  - src/types",
                "",
                "notes:",
                "  - Extension zones do not permit violating managed layer contracts.",
                "  - Core managed architecture may only change through a Developable template upgrade.",
            ]
        ) + "\n"

    def _project_name(self, spec: dict[str, Any]) -> str:
        schema_path = Path(spec.get("schema_path", "schema.prisma"))
        stem = schema_path.stem.replace("_", "-").replace(".", "-")
        if stem and stem not in {"schema", "prisma", "database", "db"}:
            return stem
        if spec.get("entities"):
            return spec["entities"][0]["name_lower"] + "-api"
        return "generated-api"

    def _script_templates(self) -> dict[str, str]:
        return {
            "scripts/check-developable.js": "express/api/check-developable.js.j2",
            "scripts/install-developable-hooks.js": "express/api/install-developable-hooks.js.j2",
            ".developable/hooks/pre-commit": "express/api/developable-pre-commit.sh.j2",
            ".developable/hooks/pre-push": "express/api/developable-pre-push.sh.j2",
            ".developable/prompts/local_rules.md": "express/api/local_rules.md.j2",
        }


def _json_dump(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=False) + "\n"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _owner_fk_field(entity: dict[str, Any], auth_entity_name: str | None) -> str | None:
    if not auth_entity_name:
        return None
    for relation in entity.get("relations", []):
        if relation.get("related_entity") == auth_entity_name and relation.get("type") == "many_to_one":
            return relation.get("fk_field")
    return None
