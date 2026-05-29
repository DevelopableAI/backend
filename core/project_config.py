"""
Project config manager.

Persists generation metadata to <out_dir>/.developable/config.json after
main.py completes. deploy.py reads this file as its primary input source so
the two commands share state without requiring flags to be repeated.
"""

import json
from pathlib import Path
from typing import Any


CONFIG_FILE = ".developable/config.json"
_CONFIG_DIR = ".developable"


class ProjectConfig:
    """Read/write wrapper for <out_dir>/.developable/config.json."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.path = out_dir / CONFIG_FILE

    # ── Public API ─────────────────────────────────────────────────────────────

    @classmethod
    def save(
        cls,
        out_dir: Path,
        schema_path: Path,
        spec: dict[str, Any],
        tests_dir: Path | None,
        github_info: dict[str, str] | None,
    ) -> "ProjectConfig":
        """
        Write config.json after generation completes.

        Args:
            out_dir:     Generated project directory.
            schema_path: Absolute path to the source schema.prisma.
            spec:        Parsed spec dict (from PrismaParser).
            tests_dir:   Directory where tests were generated, or None.
            github_info: Dict with keys user/repo/repo_url, or None if --github
                         was not used.
        """
        cfg: dict[str, Any] = {
            "schema_path": str(schema_path.resolve()),
            "out_dir": str(out_dir.resolve()),
            "project_name": spec["entities"][0]["name_lower"] + "-api" if spec.get("entities") else "",
            "tests_dir": str(tests_dir.resolve()) if tests_dir else None,
            "github_user": github_info.get("user") if github_info else None,
            "github_repo": github_info.get("repo") if github_info else None,
            "repo_url": github_info.get("repo_url") if github_info else None,
        }
        obj = cls(out_dir)
        obj._write(cfg)
        return obj

    def load(self) -> dict[str, Any]:
        """Load and return the config dict. Returns empty dict if file absent."""
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    # ── Private ────────────────────────────────────────────────────────────────

    def _write(self, data: dict[str, Any]) -> None:
        config_dir = self.out_dir / _CONFIG_DIR
        config_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))
