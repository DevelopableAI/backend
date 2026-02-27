"""
Section 15 — Response structure integrity.

Verifies consistent error envelope, 201 resource body, 204 empty body,
and pagination meta presence across all list endpoints.

Requires ctx.state: user1_token, post1_id (sections 1-5).
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("15 · RESPONSE STRUCTURE INTEGRITY")

    token1 = ctx.state.get("user1_token")
    post1_id = ctx.state.get("post1_id")

    # 15-1  404 errors use { "error": "..." } envelope
    resp = ctx.req("GET", "/api/posts/9999999")
    if resp.status_code == 404:
        data = ctx.safe_json(resp)
        if "error" in data:
            ctx.ok("404 error response uses { error: '...' } envelope")
        else:
            ctx.fail(f"404 error response missing 'error' key: {data}")

    # 15-2  400 validation errors also use { "error": "..." }
    resp = ctx.req("POST", "/api/posts", token=token1, body={})
    if resp.status_code == 400:
        data = ctx.safe_json(resp)
        if "error" in data:
            ctx.ok("400 validation error uses { error: '...' } envelope")
        else:
            ctx.fail(f"400 validation error missing 'error' key: {data}")

    # 15-3  401 errors use { "error": "..." }
    resp = ctx.req("POST", "/api/posts", body={"title": "x", "content": "y"})
    if resp.status_code == 401:
        data = ctx.safe_json(resp)
        if "error" in data:
            ctx.ok("401 error response uses { error: '...' } envelope")
        else:
            ctx.fail(f"401 response missing 'error' key: {data}")

    # 15-4  201 Created response includes the created resource with an 'id'
    if token1 and post1_id:
        resp = ctx.req("POST", "/api/comments", token=token1,
                       body={"body": "Structure check comment", "postId": post1_id})
        if ctx.assert_status(resp, 201, "POST /api/comments → 201 with resource body"):
            data = ctx.safe_json(resp)
            for field in ("id", "body", "authorId", "postId"):
                ctx.assert_field(data, field, f"POST /api/comments response.{field}")
            ctx.state["structure_check_comment_id"] = data.get("id")

    # 15-5  204 No Content has empty response body
    sc_comment = ctx.state.get("structure_check_comment_id")
    if sc_comment and token1:
        resp = ctx.req("DELETE", f"/api/comments/{sc_comment}", token=token1)
        if ctx.assert_status(resp, 204, f"DELETE /api/comments/{sc_comment} → 204"):
            if len(resp.content) == 0:
                ctx.ok("204 response has no body (correct)")
            else:
                ctx.warn(f"204 response has non-empty body: {resp.text[:100]!r}")
            ctx.state.pop("structure_check_comment_id", None)

    # 15-6  All three list endpoints carry hasNext and hasPrev in meta
    for endpoint in ("/api/users", "/api/posts", "/api/comments"):
        resp = ctx.req("GET", endpoint)
        if resp.status_code == 200:
            meta = ctx.safe_json(resp).get("meta", {})
            if "hasNext" in meta and "hasPrev" in meta:
                ctx.ok(f"GET {endpoint}: meta.hasNext and meta.hasPrev present")
            else:
                ctx.fail(f"GET {endpoint}: meta missing hasNext/hasPrev — got {meta}")

    # 15-7  200 responses return JSON (Content-Type: application/json)
    resp = ctx.req("GET", "/api/posts")
    ct = resp.headers.get("Content-Type", "")
    if "application/json" in ct:
        ctx.ok(f"GET /api/posts Content-Type is application/json")
    else:
        ctx.warn(f"GET /api/posts unexpected Content-Type: {ct!r}")

    # 15-8  hasNext / hasPrev values are boolean, not strings
    resp = ctx.req("GET", "/api/posts", params={"page": 1})
    if resp.status_code == 200:
        meta = ctx.safe_json(resp).get("meta", {})
        for key in ("hasNext", "hasPrev"):
            val = meta.get(key)
            if isinstance(val, bool):
                ctx.ok(f"meta.{key} is a boolean: {val}")
            else:
                ctx.fail(f"meta.{key} is not a boolean: {val!r} ({type(val).__name__})")

    # 15-9  totalPages is consistent with total and limit
    resp = ctx.req("GET", "/api/posts", params={"limit": 1})
    if resp.status_code == 200:
        meta = ctx.safe_json(resp).get("meta", {})
        total = meta.get("total", 0)
        limit = meta.get("limit", 1)
        expected_pages = max(1, -(-total // limit))  # ceiling division
        if meta.get("totalPages") == expected_pages:
            ctx.ok(f"totalPages={meta['totalPages']} consistent with total={total}, limit={limit}")
        else:
            ctx.fail(
                f"totalPages={meta.get('totalPages')} inconsistent with "
                f"total={total}, limit={limit} (expected {expected_pages})"
            )
