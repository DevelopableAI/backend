import json
import sys
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from core.project_contract import ContractLoader
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from project_contract import ContractLoader


@dataclass
class ConformanceResult:
    ok: bool
    errors: list[str]


class ConformanceChecker:
    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir
        self.loader = ContractLoader(repo_dir)

    def check(self) -> ConformanceResult:
        if not self.loader.exists():
            return ConformanceResult(ok=False, errors=[".developable/contract.json is missing"])

        managed_files = self.loader.load_json("manifests/managed-files.json").get("paths", [])
        dependency_rules = self.loader.load_json("manifests/dependency-rules.json")
        signatures = self.loader.load_json("manifests/ast-signatures.json")

        errors: list[str] = []
        for relative_path in managed_files:
            path = self.repo_dir / relative_path
            if not path.exists():
                errors.append(f"Managed file missing: {relative_path}")
                continue

            layer = _layer_for_path(relative_path)
            text = path.read_text()

            for needle in dependency_rules.get("forbidden_imports_by_layer", {}).get(layer, []):
                if needle in text:
                    errors.append(f"{relative_path} imports forbidden dependency fragment '{needle}'")

            for needle in dependency_rules.get("required_imports_by_layer", {}).get(layer, []):
                if needle not in text:
                    errors.append(f"{relative_path} is missing required import fragment '{needle}'")

            for suffix in dependency_rules.get("forbidden_new_by_layer", {}).get(layer, []):
                if re.search(rf"new\s+\w*{re.escape(suffix)}\s*\(", text):
                    errors.append(f"{relative_path} constructs forbidden concrete dependency ending in '{suffix}'")

            if layer == "controllers" and "../contracts/" not in text:
                errors.append(f"{relative_path} must depend on service contracts")
            if layer == "repositories" and "../lib/prisma.js" not in text:
                errors.append(f"{relative_path} must remain the Prisma boundary")

        required_classes = signatures.get("required_classes", {})
        for relative_path, class_name in required_classes.items():
            path = self.repo_dir / relative_path
            if not path.exists():
                errors.append(f"Required class file missing: {relative_path}")
                continue
            if f"class {class_name}" not in path.read_text():
                errors.append(f"{relative_path} must define class {class_name}")

        required_exports = signatures.get("required_exports", {})
        for relative_path, exports in required_exports.items():
            path = self.repo_dir / relative_path
            if not path.exists():
                errors.append(f"Required export file missing: {relative_path}")
                continue
            text = path.read_text()
            for export_name in exports:
                if export_name not in text:
                    errors.append(f"{relative_path} must expose {export_name}")

        return ConformanceResult(ok=not errors, errors=errors)


def _layer_for_path(relative_path: str) -> str:
    if relative_path.startswith("src/routes/"):
        return "routes"
    if relative_path.startswith("src/controllers/"):
        return "controllers"
    if relative_path.startswith("src/services/"):
        return "services"
    if relative_path.startswith("src/repositories/"):
        return "repositories"
    if relative_path.startswith("src/adapters/"):
        return "adapters"
    if relative_path.startswith("src/bootstrap/"):
        return "bootstrap"
    return "other"


if __name__ == "__main__":
    repo_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    result = ConformanceChecker(repo_dir).check()
    if not result.ok:
        for error in result.errors:
            print(error)
        sys.exit(1)
    print("Developable conformance passed.")
