import json
import sys
from pathlib import Path
from typing import Any


class CodebaseInspector:
    """
    Lightweight inspection pass for existing Express + TypeScript repos.

    v1 intentionally focuses on surfacing adoption facts rather than performing
    refactors. The adoption planner can use this to determine whether a repo is
    eligible to become Developable-managed.
    """

    def inspect(self, repo_dir: Path) -> dict[str, Any]:
        package_json = repo_dir / "package.json"
        package = json.loads(package_json.read_text()) if package_json.exists() else {}
        src_dir = repo_dir / "src"

        return {
            "repo_dir": str(repo_dir.resolve()),
            "has_package_json": package_json.exists(),
            "has_typescript": (repo_dir / "tsconfig.json").exists(),
            "has_prisma_schema": (repo_dir / "prisma" / "schema.prisma").exists(),
            "has_developable_contract": (repo_dir / ".developable" / "contract.json").exists(),
            "dependencies": package.get("dependencies", {}),
            "dev_dependencies": package.get("devDependencies", {}),
            "route_files": sorted(str(path.relative_to(repo_dir)) for path in src_dir.glob("routes/*.ts")) if src_dir.exists() else [],
            "controller_files": sorted(str(path.relative_to(repo_dir)) for path in src_dir.glob("controllers/*.ts")) if src_dir.exists() else [],
            "repository_files": sorted(str(path.relative_to(repo_dir)) for path in src_dir.glob("repositories/*.ts")) if src_dir.exists() else [],
            "service_files": sorted(str(path.relative_to(repo_dir)) for path in src_dir.glob("services/*.ts")) if src_dir.exists() else [],
            "workflow_files": sorted(str(path.relative_to(repo_dir)) for path in (repo_dir / ".github" / "workflows").glob("*.yml")) if (repo_dir / ".github" / "workflows").exists() else [],
            "terraform_files": sorted(str(path.relative_to(repo_dir)) for path in (repo_dir / "terraform").glob("*.tf")) if (repo_dir / "terraform").exists() else [],
        }


if __name__ == "__main__":
    repo_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    print(json.dumps(CodebaseInspector().inspect(repo_dir), indent=2))
