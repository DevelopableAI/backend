from pathlib import Path
import json
import sys
from typing import Any

try:
    from core.codebase_inspector import CodebaseInspector
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from codebase_inspector import CodebaseInspector


class AdoptionPlanner:
    """
    Produces a staged adoption summary for an existing backend.

    The planner is intentionally conservative: a repo is only marked eligible for
    managed mode when the structural prerequisites for inspection are present.
    """

    def plan(self, repo_dir: Path) -> dict[str, Any]:
        inspection = CodebaseInspector().inspect(repo_dir)
        issues: list[str] = []

        if not inspection["has_package_json"]:
            issues.append("package.json is required for adoption")
        if not inspection["has_typescript"]:
            issues.append("tsconfig.json is required for TypeScript adoption")
        if not inspection["has_prisma_schema"]:
            issues.append("prisma/schema.prisma is required for the initial adoption workflow")
        if not inspection["route_files"]:
            issues.append("src/routes/*.ts files were not detected")
        if not inspection["controller_files"]:
            issues.append("src/controllers/*.ts files were not detected")

        return {
            "eligible": not issues,
            "inspection": inspection,
            "issues": issues,
            "stages": [
                "inspect",
                "normalize",
                "compare-to-contract",
                "refactor-to-conformance",
                "install-hooks-and-ci",
                "mark-managed",
            ],
        }


if __name__ == "__main__":
    repo_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    print(json.dumps(AdoptionPlanner().plan(repo_dir), indent=2))
