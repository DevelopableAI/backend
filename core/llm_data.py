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
            return data
    except Exception:
        pass

    return _fallback_values(fields)


def _load_system_prompt() -> str:
    prompt_path = Path(__file__).parent.parent / "prompts" / "tests" / "seed_values.txt"
    if prompt_path.exists():
        return prompt_path.read_text()
    return "You generate test data as JSON arrays. Return only valid JSON."


def _build_prompt(entity_name: str, fields: list[dict]) -> str:
    field_lines = "\n".join(f"  {f['name']} ({f['ts_type']})" for f in fields)
    return (
        f"Entity: {entity_name}\n"
        f"Fields:\n{field_lines}\n\n"
        f"Return a JSON array of 3 test data objects for this entity."
    )


def _fallback_values(fields: list[dict]) -> list[dict[str, Any]]:
    """Deterministic fallback used when --no-llm or the API call fails."""
    return [
        {f["name"]: _default_val(f["name"], f["ts_type"], i) for f in fields}
        for i in range(1, 4)
    ]


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
    return f"Test Value {n}"
