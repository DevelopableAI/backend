import re
from pathlib import Path
from typing import Any


PRISMA_TO_TS = {
    "String": "string",
    "Int": "number",
    "Float": "number",
    "Boolean": "boolean",
    "DateTime": "Date",
    "Json": "Record<string, any>",
    "Bytes": "Buffer",
    "BigInt": "bigint",
    "Decimal": "number",
}


class PrismaParser:
    """
    Parses a schema.prisma file into a structured spec dict.

    Output shape:
    {
        "entities": [
            {
                "name": "User",
                "name_lower": "user",
                "name_plural": "users",
                "fields": [
                    {
                        "name": "id",
                        "prisma_type": "Int",
                        "ts_type": "number",
                        "is_optional": False,
                        "is_list": False,
                        "is_id": True,
                        "is_unique": False,
                        "is_relation": False,
                        "is_sensitive": False,   # True when // @llm sensitive comment is present
                        "default": "autoincrement()",
                        "annotations": []
                    },
                    ...
                ],
                "relations": [
                    {
                        "name": "posts",
                        "related_entity": "Post",
                        "type": "one_to_many",   # one_to_one | one_to_many | many_to_one
                        "fk_field": None          # scalar FK field name (many_to_one only)
                    }
                ],
                "is_auth_entity": False,   # True for the entity with email + sensitive field
                "llm_hints": []   # anything from // @llm comments above the model
            }
        ],
        "datasource": {
            "provider": "postgresql",
            "url": "env(\"DATABASE_URL\")"
        },
        "auth_entity_name": None   # name of the detected auth entity, or None
    }
    """

    def parse(self, path: Path) -> dict[str, Any]:
        text = path.read_text()
        spec: dict[str, Any] = {
            "entities": [],
            "datasource": self._parse_datasource(text),
            "env_vars": self._extract_env_vars(text),
            "schema_path": str(path),
            "auth_entity_name": None,
        }

        for model_block, llm_hints in self._extract_model_blocks(text):
            entity = self._parse_model(model_block, llm_hints)
            spec["entities"].append(entity)

        # second pass: resolve relation types now that all entities are known
        known_entities = {e["name"] for e in spec["entities"]}
        for entity in spec["entities"]:
            for field in entity["fields"]:
                if field["prisma_type"] in known_entities:
                    field["is_relation"] = True

            entity["relations"] = self._resolve_relations(entity, known_entities)

        # third pass: detect auth entity (has email field + at least one sensitive field)
        self._detect_auth_entity(spec)

        return spec

    def _extract_env_vars(self, text: str) -> list[str]:
        """Extract all env variable names referenced via env("VAR_NAME") in the schema."""
        matches = re.findall(r'env\("([^"]+)"\)', text)
        seen: set[str] = set()
        result = []
        for m in matches:
            if m not in seen:
                seen.add(m)
                result.append(m)
        return result

    def _parse_datasource(self, text: str) -> dict:
        datasource: dict[str, str] = {"provider": "postgresql", "url": "env(\"DATABASE_URL\")"}
        block_match = re.search(r"datasource\s+\w+\s*\{([^}]+)\}", text)
        if not block_match:
            return datasource

        for line in block_match.group(1).splitlines():
            line = line.strip()
            if line.startswith("provider"):
                m = re.search(r'=\s*"([^"]+)"', line)
                if m:
                    datasource["provider"] = m.group(1)
            elif line.startswith("url"):
                m = re.search(r"=\s*(.+)", line)
                if m:
                    datasource["url"] = m.group(1).strip()

        return datasource

    def _extract_model_blocks(self, text: str) -> list[tuple[str, list[str]]]:
        """Returns list of (model_block_text, llm_hints) tuples."""
        results = []
        lines = text.splitlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # collect @llm hints above the model declaration
            llm_hints = []
            j = i
            while j < len(lines) and lines[j].strip().startswith("// @llm"):
                llm_hints.append(lines[j].strip().removeprefix("// @llm").strip())
                j += 1

            if j < len(lines) and re.match(r"^model\s+\w+\s*\{", lines[j].strip()):
                # gather the full block
                block_lines = []
                depth = 0
                k = j
                while k < len(lines):
                    block_lines.append(lines[k])
                    depth += lines[k].count("{") - lines[k].count("}")
                    if depth == 0 and k > j:
                        break
                    k += 1
                results.append(("\n".join(block_lines), llm_hints))
                i = k + 1
            else:
                i = j + 1 if j > i else i + 1

        return results

    def _parse_model(self, block: str, llm_hints: list[str]) -> dict:
        lines = block.splitlines()
        header = lines[0].strip()
        name = re.match(r"model\s+(\w+)", header).group(1)

        fields = []
        for line in lines[1:]:
            line = line.strip()
            if not line or line == "}" or line.startswith("@@"):
                continue

            field = self._parse_field_line(line)
            if field:
                fields.append(field)

        return {
            "name": name,
            "name_lower": name[0].lower() + name[1:],
            "name_plural": self._pluralize(name[0].lower() + name[1:]),
            "fields": fields,
            "relations": [],
            "is_auth_entity": False,
            "llm_hints": llm_hints,
        }

    def _parse_field_line(self, line: str) -> dict | None:
        # detect // @llm sensitive BEFORE stripping inline comments
        is_sensitive = bool(re.search(r"//\s*@llm\s+sensitive", line))

        # strip inline comments
        line = re.sub(r"\s*//.*$", "", line).strip()
        if not line:
            return None

        parts = line.split()
        if len(parts) < 2:
            return None

        name = parts[0]
        raw_type = parts[1]

        is_optional = raw_type.endswith("?")
        is_list = raw_type.endswith("[]")
        prisma_type = raw_type.rstrip("?[]")

        annotations = re.findall(r"@\w+(?:\([^)]*\))?", line)
        is_id = "@id" in annotations
        is_unique = "@unique" in annotations

        default_match = re.search(r"@default\(([^)]+)\)", line)
        default_val = default_match.group(1) if default_match else None

        ts_type = PRISMA_TO_TS.get(prisma_type, prisma_type)
        if is_list:
            ts_type = f"{ts_type}[]"

        return {
            "name": name,
            "prisma_type": prisma_type,
            "ts_type": ts_type,
            "is_optional": is_optional,
            "is_list": is_list,
            "is_id": is_id,
            "is_unique": is_unique,
            "is_relation": False,
            "is_sensitive": is_sensitive,
            "default": default_val,
            "annotations": annotations,
        }

    def _resolve_relations(self, entity: dict, known_entities: set[str]) -> list[dict]:
        relations = []

        # Build fk_map: relation field name -> FK scalar field name
        # by parsing @relation(fields: [...]) annotations on relation fields
        fk_map: dict[str, str] = {}
        for field in entity["fields"]:
            for ann in field.get("annotations", []):
                fk_match = re.search(r"@relation\(fields:\s*\[([^\]]+)\]", ann)
                if fk_match:
                    fk_fields = [f.strip() for f in fk_match.group(1).split(",")]
                    if fk_fields:
                        fk_map[field["name"]] = fk_fields[0]

        for field in entity["fields"]:
            if field["prisma_type"] not in known_entities:
                continue

            rel_type = "one_to_many" if field["is_list"] else "many_to_one"
            fk_field = fk_map.get(field["name"])  # None for one_to_many (FK is on the child side)

            relations.append({
                "name": field["name"],
                "related_entity": field["prisma_type"],
                "type": rel_type,
                "fk_field": fk_field,
            })

        return relations

    # Common password field names that indicate an auth entity even without @llm sensitive
    _PASSWORD_FIELD_NAMES = {
        "password", "passwordHash", "hashedPassword",
        "password_hash", "hashed_password", "passwd",
    }

    def _detect_auth_entity(self, spec: dict) -> None:
        """
        Find the entity that acts as the authentication principal.
        Heuristic: has an 'email' field AND at least one field that is either
        marked @llm sensitive OR has a well-known password field name.
        Marks it with is_auth_entity=True and sets spec['auth_entity_name'].
        """
        for entity in spec["entities"]:
            has_email = any(f["name"] == "email" for f in entity["fields"] if not f["is_relation"])
            has_sensitive = any(
                f["is_sensitive"] or f["name"] in self._PASSWORD_FIELD_NAMES
                for f in entity["fields"]
                if not f["is_relation"]
            )
            if has_email and has_sensitive:
                entity["is_auth_entity"] = True
                spec["auth_entity_name"] = entity["name"]
                return  # only one auth entity supported

    def _pluralize(self, word: str) -> str:
        if word.endswith("y") and not word[-2] in "aeiou":
            return word[:-1] + "ies"
        if word.endswith(("s", "x", "z", "ch", "sh")):
            return word + "es"
        return word + "s"
