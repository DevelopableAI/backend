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
            "enums": self._extract_enums(text),
        }

        for model_block, llm_hints, is_auth_entity_marker in self._extract_model_blocks(text):
            entity = self._parse_model(model_block, llm_hints, is_auth_entity_marker)
            spec["entities"].append(entity)

        # second pass: resolve relation types now that all entities are known
        known_entities = {e["name"] for e in spec["entities"]}
        for entity in spec["entities"]:
            for field in entity["fields"]:
                if field["prisma_type"] in known_entities:
                    field["is_relation"] = True

            entity["relations"] = self._resolve_relations(entity, known_entities)

        # third pass: mark enum fields using parsed enum definitions
        enum_map = spec["enums"]
        for entity in spec["entities"]:
            for field in entity["fields"]:
                if field["prisma_type"] in enum_map:
                    field["is_enum"] = True
                    field["enum_values"] = enum_map[field["prisma_type"]]
                    # Enum values serialize as plain strings in JSON — treat ts_type as string
                    field["ts_type"] = "string"

        # fifth pass: annotate each entity with its PK TypeScript type and strategy
        for entity in spec["entities"]:
            pk_field = next(
                (f for f in entity["fields"] if f["is_id"] and not f["is_relation"]),
                None,
            )
            entity["pk_ts_type"] = pk_field["ts_type"] if pk_field else "number"
            entity["pk_strategy"] = pk_field.get("pk_strategy") if pk_field else "none"

        # fourth pass: detect auth entity (has email field + at least one sensitive field)
        self._detect_auth_entity(spec)

        return spec

    def _extract_enums(self, text: str) -> dict[str, list[str]]:
        """
        Parse all `enum Name { VALUE ... }` blocks from the schema.
        Returns a mapping: enum_name -> [value1, value2, ...].
        Used to mark fields whose prisma_type is an enum so templates can
        generate valid enum values in test bodies instead of arbitrary strings.
        """
        enums: dict[str, list[str]] = {}
        for match in re.finditer(r"enum\s+(\w+)\s*\{([^}]+)\}", text):
            name = match.group(1)
            values = [
                v.strip()
                for v in match.group(2).splitlines()
                if v.strip() and not v.strip().startswith("//")
            ]
            if values:
                enums[name] = values
        return enums

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

    def _extract_model_blocks(self, text: str) -> list[tuple[str, list[str], bool]]:
        """Returns list of (model_block_text, llm_hints, is_auth_entity_marker) tuples."""
        results = []
        lines = text.splitlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # collect @llm hints and detect @auth_entity marker above the model declaration
            llm_hints = []
            is_auth_entity_marker = False
            j = i
            while j < len(lines) and (
                lines[j].strip().startswith("// @llm")
                or lines[j].strip() == "// @auth_entity"
            ):
                stripped = lines[j].strip()
                if stripped == "// @auth_entity":
                    is_auth_entity_marker = True
                else:
                    llm_hints.append(stripped.removeprefix("// @llm").strip())
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
                results.append(("\n".join(block_lines), llm_hints, is_auth_entity_marker))
                i = k + 1
            else:
                i = j + 1 if j > i else i + 1

        return results

    def _parse_model(self, block: str, llm_hints: list[str], is_auth_entity_marker: bool = False) -> dict:
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
            "is_auth_entity_marker": is_auth_entity_marker,
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

        # Fields Prisma manages automatically — never belong in user-facing input schemas.
        # Detected entirely from annotations Prisma already writes in the schema:
        #   @updatedAt                    — set by Prisma on every write
        #   @default(autoincrement())     — DB auto-increment
        #   @default(cuid()/uuid())       — Prisma-generated IDs
        #   @default(dbgenerated(...))    — arbitrary DB expression
        # Uses startswith() so the check is resilient to the default-regex truncation
        # that parses @default(fn()) as "fn(" rather than "fn()" (stops at inner paren).
        is_auto_managed = (
            "@updatedAt" in annotations
            or (
                default_val is not None
                and default_val.startswith(("autoincrement(", "cuid(", "uuid(", "dbgenerated("))
            )
        )

        pk_strategy: str | None = None
        if is_id:
            if default_val == "uuid()":
                pk_strategy = "uuid"
            elif default_val == "cuid()":
                pk_strategy = "cuid"
            elif default_val == "autoincrement()":
                pk_strategy = "autoincrement"
            else:
                pk_strategy = "none"

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
            "is_enum": False,        # set to True in the enum pass if prisma_type is an enum
            "enum_values": [],       # populated in the enum pass
            "is_auto_managed": is_auto_managed,
            "default": default_val,
            "annotations": annotations,
            "pk_strategy": pk_strategy,
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

        An entity is the auth entity when it is preceded by a '// @auth_entity'
        comment in the schema file (set via is_auth_entity_marker during parsing).

        Stores on the entity:
          auth_id_field    — actual PK field name (e.g. 'id', 'UserID', 'userID')
          auth_id_ts_type  — TypeScript type of the PK field (e.g. 'number', 'string')
          auth_login_field — field dict used to uniquely identify users at login
                             (email preferred, then first @unique scalar, then first scalar)
                             May be None if no suitable field exists.
        """
        for entity in spec["entities"]:
            if not entity.get("is_auth_entity_marker"):
                continue

            # Ensure password-named fields are marked sensitive so the
            # template pipeline (planner → template) can use them correctly
            # even when the schema omits the // @llm sensitive comment.
            for f in entity["fields"]:
                if not f["is_relation"] and f["name"] in self._PASSWORD_FIELD_NAMES:
                    f["is_sensitive"] = True

            # Locate the primary key field (could be named 'id', 'UserID', etc.)
            id_field = next(
                (f for f in entity["fields"] if f["is_id"] and not f["is_relation"]),
                None,
            )

            # Candidate scalar fields for login: non-relation, non-ID, non-sensitive
            candidate_scalars = [
                f for f in entity["fields"]
                if not f["is_relation"] and not f["is_id"] and not f["is_sensitive"]
            ]

            # Login field priority: email > any @unique scalar > any scalar
            email_field = next((f for f in candidate_scalars if f["name"] == "email"), None)
            unique_field = next((f for f in candidate_scalars if f["is_unique"]), None)
            any_field = next(iter(candidate_scalars), None)
            login_field = email_field or unique_field or any_field

            entity["is_auth_entity"] = True
            entity["auth_id_field"] = id_field["name"] if id_field else "id"
            entity["auth_id_ts_type"] = id_field["ts_type"] if id_field else "number"
            entity["auth_login_field"] = login_field   # may be None
            spec["auth_entity_name"] = entity["name"]
            return  # only one auth entity supported

    def _pluralize(self, word: str) -> str:
        if word.endswith("y") and not word[-2] in "aeiou":
            return word[:-1] + "ies"
        if word.endswith(("s", "x", "z", "ch", "sh")):
            return word + "es"
        return word + "s"
