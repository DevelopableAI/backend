from pathlib import Path
import json
import sys
from typing import Any

try:
    from core.project_contract import CONTRACT_VERSION, ContractLoader
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from project_contract import CONTRACT_VERSION, ContractLoader


class TemplateUpgradePlanner:
    """
    Compares a managed repo's stored contract version against the current
    generator contract version so Developable can decide whether a formal
    template upgrade is required.
    """

    def plan(self, repo_dir: Path) -> dict[str, Any]:
        loader = ContractLoader(repo_dir)
        contract = loader.load_contract()
        current_version = contract.get("contract_version")
        return {
            "managed": loader.exists(),
            "current_version": current_version,
            "target_version": CONTRACT_VERSION,
            "upgrade_required": loader.exists() and current_version != CONTRACT_VERSION,
        }


if __name__ == "__main__":
    repo_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    print(json.dumps(TemplateUpgradePlanner().plan(repo_dir), indent=2))
