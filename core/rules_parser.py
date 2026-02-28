from pathlib import Path
from typing import Any

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


class BusinessRulesParser:
    """
    Parses an optional schema.rules.yaml file that captures user business logic,
    and merges those rules into the spec produced by PrismaParser.

    YAML format:
        entities:
          EntityName:
            constraints:
              - "Free-text business rule used as LLM hint"
            primary_parent: OtherEntity  # override the auto-detected primary parent
            endpoints:
              deny:
                - method: POST
                  path: /entity/:id/entity   # suppress route generation

    Rules are merged into each entity dict under:
        entity["endpoint_deny"]     — list of {method, path} dicts
        entity["llm_constraints"]   — list of free-text constraint strings
        entity["primary_parent"]    — entity name string (overrides auto-detection)

    Missing entities in the rules file are silently ignored.
    If no rules file is provided, the spec is returned unchanged.
    """

    def merge(self, spec: dict[str, Any], rules_path: Path | None) -> dict[str, Any]:
        """
        Merge business rules from rules_path into the spec.
        Returns the (mutated) spec.
        """
        if rules_path is None:
            return spec

        if not _YAML_AVAILABLE:
            raise RuntimeError(
                "PyYAML is required to load a rules file. "
                "Install it with: pip install pyyaml"
            )

        rules_path = Path(rules_path)
        if not rules_path.exists():
            raise FileNotFoundError(f"Rules file not found: {rules_path}")

        with open(rules_path) as f:
            raw = yaml.safe_load(f) or {}

        entity_rules: dict[str, Any] = raw.get("entities", {})

        for entity in spec["entities"]:
            name = entity["name"]
            rules = entity_rules.get(name, {})

            entity["endpoint_deny"] = [
                {"method": r["method"].upper(), "path": r["path"]}
                for r in rules.get("endpoints", {}).get("deny", [])
            ]
            entity["llm_constraints"] = list(rules.get("constraints", []))
            # Explicit primary parent override (entity name string, or None if not specified)
            if "primary_parent" in rules:
                entity["primary_parent"] = rules["primary_parent"]

        return spec
