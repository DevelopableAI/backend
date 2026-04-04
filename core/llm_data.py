"""
LLM-powered test data generation.

Calls Claude to produce 3 realistic test data sets for an entity as JSON.
Used by TestPlanner to populate seed_values context for test templates,
keeping the LLM's role to data generation only — Jinja2 handles all code.
"""

import json
import os
from pathlib import Path
from typing import Any

import anthropic


def generate_test_data(
    entity_name: str,
    fields: list[dict[str, Any]],
    use_llm: bool = True,
) -> list[dict[str, Any]]:
    """
    Return 3 dicts of {field_name: value} for the given fields.
    Falls back to deterministic values if use_llm=False or the API call fails.
    """
    if not fields:
        return [{}, {}, {}]

    if not use_llm or not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback_values(fields)

    system_prompt = _load_system_prompt()
    user_prompt = _build_prompt(entity_name, fields)

    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        if isinstance(data, list) and len(data) == 3:
            return _merge_with_fallback(data, fields)
    except Exception:
        pass

    return _fallback_values(fields)


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent.parent / "prompts" / "tests" / "seed_values.txt"
    if prompt_path.exists():
        return prompt_path.read_text()
    return "You generate test data as JSON arrays. Return only valid JSON."


_ENUM_LIKE_DEFAULTS: dict[str, str] = {
    "role": "admin",
    "type": "standard",
    "status": "pending",
    "category": "general",
    "state": "active",
    "kind": "standard",
    "mode": "normal",
    "tier": "basic",
    "level": "beginner",
    "priority": "medium",
}


def _build_prompt(entity_name: str, fields: list[dict]) -> str:
    field_lines = []
    for f in fields:
        line = f"  {f['name']} ({f['ts_type']})"
        if f.get("is_enum") and f.get("enum_values"):
            line += f" — one of: {', '.join(f['enum_values'])}"
        elif f.get("default") and isinstance(f.get("default"), str):
            d = f["default"]
            if d.startswith('"') and d.endswith('"'):
                line += f" — default: {d[1:-1]}"
        field_lines.append(line)
    return (
        f"Entity: {entity_name}\n"
        f"Fields:\n" + "\n".join(field_lines) + "\n\n"
        f"Return a JSON array of 3 test data objects for this entity."
    )


def _merge_with_fallback(
    llm_records: list[dict],
    fields: list[dict],
) -> list[dict[str, Any]]:
    """
    Validate LLM-generated records against the declared field list.

    For each field in each record:
    - Enum field whose value is NOT in enum_values → replace with a valid enum value.
    - Missing field → fill with _field_val deterministic fallback.

    This ensures test data is always valid for the API's Zod schema even when
    the LLM omits a field or picks a value like 'in-progress' instead of 'in_progress'.
    """
    result = []
    for i, record in enumerate(llm_records):
        n = i + 1
        row: dict[str, Any] = {}
        for field in fields:
            fname = field["name"]
            val = record.get(fname)
            if field.get("is_enum") and field.get("enum_values"):
                # Only accept exact declared enum values
                if val not in field["enum_values"]:
                    val = field["enum_values"][(n - 1) % len(field["enum_values"])]
            elif val is None:
                # Field missing from LLM output — deterministic fallback
                val = _field_val(field, n)
            row[fname] = val
        result.append(row)
    return result


def _fallback_values(fields: list[dict]) -> list[dict[str, Any]]:
    """Deterministic fallback used when --no-llm or the API call fails."""
    return [
        {f["name"]: _field_val(f, i) for f in fields}
        for i in range(1, 4)
    ]


def _field_val(field: dict, n: int) -> Any:
    """Pick a deterministic test value honouring enum constraints and field defaults."""
    # Real Prisma enum → cycle through declared values
    if field.get("is_enum") and field.get("enum_values"):
        values = field["enum_values"]
        return values[(n - 1) % len(values)]

    # String field with a @default("value") → use that value (always passes Zod enum)
    default = field.get("default")
    if default and isinstance(default, str) and field.get("ts_type") == "string":
        if default.startswith('"') and default.endswith('"'):
            return default[1:-1]

    return _default_val(field["name"], field.get("ts_type", "string"), n)


def _default_val(name: str, ts_type: str, n: int) -> Any:
    low = name.lower()
    if ts_type == "number":
        return n
    if ts_type == "boolean":
        return True
    if "email" in low:
        return f"user{n}@example.com"
    if "url" in low:
        return f"https://example.com/{n}"
    if "phone" in low:
        return f"555-000{n}"
    if "username" in low:
        return f"testuser{n}"
    if "title" in low:
        return f"Test Title {n}"
    if "content" in low or "body" in low:
        return f"Test content {n}"
    if "description" in low or "bio" in low:
        return f"Test description {n}"
    if "name" in low:
        return f"Test Name {n}"
    # Enum-like field names — return a value that passes LLM-generated Zod enum validators
    if low in _ENUM_LIKE_DEFAULTS:
        return _ENUM_LIKE_DEFAULTS[low]
    return f"Test Value {n}"
