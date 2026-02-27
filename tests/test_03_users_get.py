"""
Section 3 — Users: unauthenticated GET operations.

Requires ctx.state: user1_id (from section 1).
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("3 · USERS — GET (unauthenticated reads)")

    user1_id = ctx.state.get("user1_id")

    # 3-1  Paginated list
    resp = ctx.req("GET", "/api/users")
    if ctx.assert_status(resp, 200, "GET /api/users → 200"):
        data = ctx.safe_json(resp)
        ctx.assert_paginated(data, "GET /api/users")
        items = data.get("data", [])
        if not ctx.no_password_in(items, "GET /api/users list"):
            ctx.warn("Password field exposed in GET /api/users list response")
        else:
            ctx.ok("Password field absent from all user records in list response")

    # 3-2  Pagination: limit=1
    resp = ctx.req("GET", "/api/users", params={"page": 1, "limit": 1})
    if ctx.assert_status(resp, 200, "GET /api/users?page=1&limit=1"):
        meta = ctx.safe_json(resp).get("meta", {})
        if meta.get("limit") == 1:
            ctx.ok("Pagination limit=1 respected in meta")
        else:
            ctx.fail(f"Expected meta.limit=1, got {meta.get('limit')}")

    # 3-3  page=0 normalised to page 1
    resp = ctx.req("GET", "/api/users", params={"page": 0})
    if ctx.assert_status(resp, 200, "GET /api/users?page=0 → normalised to page 1"):
        meta = ctx.safe_json(resp).get("meta", {})
        if meta.get("page", 0) >= 1:
            ctx.ok(f"page=0 normalised to page={meta.get('page')}")
        else:
            ctx.fail(f"page=0 not normalised correctly: {meta}")

    # 3-4  limit=9999 capped at 100
    resp = ctx.req("GET", "/api/users", params={"limit": 9999})
    if ctx.assert_status(resp, 200, "GET /api/users?limit=9999 → capped at 100"):
        meta = ctx.safe_json(resp).get("meta", {})
        if meta.get("limit", 0) <= 100:
            ctx.ok(f"limit capped at {meta.get('limit')}")
        else:
            ctx.fail(f"limit not capped: meta.limit={meta.get('limit')}")

    # 3-5  Very high page → empty data array
    resp = ctx.req("GET", "/api/users", params={"page": 99999})
    if ctx.assert_status(resp, 200, "GET /api/users?page=99999 → empty data"):
        data = ctx.safe_json(resp).get("data", None)
        if isinstance(data, list) and len(data) == 0:
            ctx.ok("High page number returns empty data array")
        elif data is not None:
            ctx.fail(f"Expected empty data array on high page, got {len(data)} items")

    # 3-6  Get user by ID
    if user1_id:
        resp = ctx.req("GET", f"/api/users/{user1_id}")
        if ctx.assert_status(resp, 200, f"GET /api/users/{user1_id} → 200"):
            data = ctx.safe_json(resp)
            if data.get("id") == user1_id:
                ctx.ok("User id matches requested id")
            else:
                ctx.fail(f"User id mismatch: expected {user1_id}, got {data.get('id')}")
            if not ctx.no_password_in(data, f"GET /api/users/{user1_id}"):
                ctx.warn("Password field exposed in single-user response")
            else:
                ctx.ok("Password field absent from single-user response")

    # 3-7  Non-existent user → 404
    resp = ctx.req("GET", "/api/users/9999999")
    ctx.assert_status(resp, 404, "GET /api/users/9999999 → 404")

    # 3-8  Non-numeric ID → 400
    resp = ctx.req("GET", "/api/users/abc")
    ctx.assert_status(resp, 400, "GET /api/users/abc → 400 (invalid ID)")

    # 3-9  Negative ID → 400 or 404
    resp = ctx.req("GET", "/api/users/-1")
    if resp.status_code in (400, 404):
        ctx.ok(f"GET /api/users/-1 → HTTP {resp.status_code} (acceptable)")
    else:
        ctx.fail(f"GET /api/users/-1 → unexpected HTTP {resp.status_code}")

    # 3-10  Floating-point ID → 400 or 404
    resp = ctx.req("GET", "/api/users/1.5")
    if resp.status_code in (400, 404):
        ctx.ok(f"GET /api/users/1.5 → HTTP {resp.status_code} (invalid ID handled)")
    else:
        ctx.fail(f"GET /api/users/1.5 → unexpected HTTP {resp.status_code}")
