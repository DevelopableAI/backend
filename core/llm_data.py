"""
Deterministic test data generation for integration test seeds.

Generates 3 valid data sets per entity from Prisma field metadata.
No LLM involved: correctness and repeatability matter more than realism
for seed data. Enum fields cycle through their declared values; string
fields use sensible format-aware defaults based on field name conventions.
"""

from typing import Any


def generate_test_data(
    entity_name: str,
    fields: list[dict[str, Any]],
    use_llm: bool = True,  # kept for API compatibility; no longer has effect
) -> list[dict[str, Any]]:
    """
    Return 3 dicts of {field_name: value} for the given fields.

    Values are generated deterministically from Prisma field metadata:
    - Enum fields: cycles through declared enum_values (e.g. ["todo","in_progress","done"])
    - Format strings: inferred from field name (email, url, phone, title, etc.)
    - Numbers: small integers (1, 2, 3)
    - Booleans: True

    This guarantees correctness for every Zod validator constraint that originates
    from the Prisma schema (enums, types). The use_llm parameter is retained for
    backwards compatibility but is ignored.
    """
    if not fields:
        return [{}, {}, {}]
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
    if ts_type in ("Record<string, any>", "object", "Json"):
        return {}
    if "email" in low:
        return f"user{n}@example.com"
    # URL-like field names: url, website, link, href, avatar, image, photo, picture, icon, thumbnail
    if any(w in low for w in ("url", "website", "link", "href", "avatar", "image", "photo", "picture", "icon", "thumbnail")):
        return f"https://example.com/{n}"
    if "phone" in low:
        return f"555-000{n}"
    # Slug fields: must be lowercase alphanumeric with hyphens
    if "slug" in low:
        return f"test-slug-{n}"
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
