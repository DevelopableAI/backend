"""
Section 14 — Input validation and edge cases.

Requires ctx.state: user1_token (sections 1-2).
Populates ctx.state: long_title_post_id, long_content_post_id, xss_post_id, unicode_post_id
                     (kept for cleanup in section 17).
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("14 · INPUT VALIDATION & EDGE CASES")

    token1 = ctx.state.get("user1_token")
    user1_id = ctx.state.get("user1_id")

    # 14-1  Very long title (300 chars) — should be rejected or truncated
    long_title = "A" * 300
    if token1:
        resp = ctx.req("POST", "/api/posts", token=token1,
                       body={"title": long_title, "content": "Normal content"})
        if resp.status_code in (400, 422):
            ctx.ok(f"POST /api/posts title=300 chars → HTTP {resp.status_code} (rejected)")
        elif resp.status_code == 201:
            ctx.warn(
                "POST /api/posts accepted 300-char title — consider .max(255) "
                "in the Zod 'title' schema"
            )
            ctx.state["long_title_post_id"] = ctx.safe_json(resp).get("id")

    # 14-2  Very long content (15 000 chars)
    long_content = "C" * 15_000
    if token1:
        resp = ctx.req("POST", "/api/posts", token=token1,
                       body={"title": "Long Content Test", "content": long_content})
        if resp.status_code in (400, 422):
            ctx.ok(f"POST /api/posts content=15000 chars → HTTP {resp.status_code} (rejected)")
        elif resp.status_code == 201:
            ctx.warn(
                "POST /api/posts accepted 15 000-char content — consider .max(10000) "
                "in the Zod 'content' schema"
            )
            ctx.state["long_content_post_id"] = ctx.safe_json(resp).get("id")

    # 14-3  XSS payload in content — stored verbatim (escaping is a frontend concern)
    xss_payload = '<script>alert("xss")</script>'
    if token1:
        resp = ctx.req("POST", "/api/posts", token=token1,
                       body={"title": "XSS Test Post", "content": xss_payload})
        if resp.status_code == 201:
            data = ctx.safe_json(resp)
            ctx.warn(
                "XSS payload stored verbatim in DB — ensure HTML escaping is applied "
                "when content is rendered in a browser context (frontend responsibility)."
            )
            ctx.ok("API stored content without modification (escaping is frontend concern)")
            ctx.state["xss_post_id"] = data.get("id")
        else:
            ctx.ok(f"XSS payload in content rejected with HTTP {resp.status_code}")

    # 14-4  SQL injection attempt in query param — server must not crash
    resp = ctx.req("GET", "/api/posts", params={"page": "1; DROP TABLE posts; --"})
    if resp.status_code == 200:
        ctx.ok("SQL injection in ?page param → 200 (gracefully handled / Prisma parameterised)")
    elif resp.status_code == 400:
        ctx.ok("SQL injection in ?page param → 400 (rejected by validation)")
    else:
        ctx.warn(f"SQL injection in page param → unexpected HTTP {resp.status_code}")

    # 14-5  SQL injection in path segment → 400 or 404
    resp = ctx.req("GET", "/api/posts/1; DROP TABLE posts --")
    if resp.status_code in (400, 404):
        ctx.ok(f"SQL injection in path segment → HTTP {resp.status_code} (handled)")
    else:
        ctx.warn(f"SQL injection in path segment → unexpected HTTP {resp.status_code}")

    # 14-6  Null byte in string field
    if token1:
        resp = ctx.req("POST", "/api/posts", token=token1,
                       body={"title": "Null\x00Byte", "content": "Content\x00here"})
        if resp.status_code in (400, 201):
            ctx.ok(f"Null byte in string fields → HTTP {resp.status_code} (handled)")
        else:
            ctx.warn(f"Null byte in string fields → HTTP {resp.status_code}")

    # 14-7  Unicode / emoji in content — should round-trip correctly
    if token1:
        resp = ctx.req("POST", "/api/posts", token=token1,
                       body={"title": "Unicode Test 🌍", "content": "Emoji content: 🚀✅❌⚠️"})
        if ctx.assert_status(resp, 201, "POST /api/posts with Unicode/emoji → 201"):
            data = ctx.safe_json(resp)
            if "🚀" in data.get("content", ""):
                ctx.ok("Unicode/emoji stored and returned correctly")
            else:
                ctx.fail("Emoji not present in returned content")
            ctx.state["unicode_post_id"] = data.get("id")

    # 14-8  Empty string title
    if token1:
        resp = ctx.req("POST", "/api/posts", token=token1,
                       body={"title": "", "content": "Content with empty title"})
        if resp.status_code == 400:
            ctx.ok("POST /api/posts with empty string title → 400 (validation)")
        elif resp.status_code == 201:
            ctx.warn(
                "POST /api/posts accepted empty string title — consider .min(1) "
                "in the Zod 'title' schema"
            )

    # 14-9  Non-numeric pagination params → should fall back to defaults
    resp = ctx.req("GET", "/api/posts", params={"page": "abc", "limit": "xyz"})
    if ctx.assert_status(resp, 200, "GET /api/posts?page=abc&limit=xyz → 200 (defaults)"):
        meta = ctx.safe_json(resp).get("meta", {})
        if meta.get("page") == 1 and meta.get("limit") == 20:
            ctx.ok("Non-numeric pagination params fall back to defaults page=1 limit=20")
        else:
            ctx.warn(f"Non-numeric pagination produced unexpected meta: {meta}")

    # 14-10  Integer overflow in ID
    big_id = 2 ** 53
    resp = ctx.req("GET", f"/api/posts/{big_id}")
    if resp.status_code in (400, 404):
        ctx.ok(f"GET /api/posts/{big_id} (integer overflow) → HTTP {resp.status_code} (handled)")
    else:
        ctx.warn(f"Integer overflow in ID → unexpected HTTP {resp.status_code}")

    # 14-11  Content-Type: text/plain body → 400 (Express json() middleware rejects it)
    import requests as _req
    if token1:
        url = f"{ctx.base_url}/api/posts"
        print(f"  🚀  POST /api/posts (Content-Type: text/plain)")
        try:
            resp = _req.post(
                url,
                headers={"Authorization": f"Bearer {token1}", "Content-Type": "text/plain"},
                data='{"title":"Plain","content":"body"}',
                timeout=10,
            )
            if resp.status_code in (400, 415):
                ctx.ok(
                    f"POST with Content-Type: text/plain → HTTP {resp.status_code} "
                    "(non-JSON body rejected)"
                )
            else:
                ctx.warn(
                    f"POST with Content-Type: text/plain → HTTP {resp.status_code} "
                    "— Express json() middleware may have silently ignored the body"
                )
        except Exception as exc:
            ctx.fail(f"Request failed: {exc}")

    # 14-12  Completely empty POST body (no JSON at all)
    if token1:
        import requests as _r
        url = f"{ctx.base_url}/api/posts"
        print("  🚀  POST /api/posts (no body at all)")
        try:
            resp = _r.post(
                url,
                headers={"Authorization": f"Bearer {token1}", "Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code in (400, 422):
                ctx.ok(f"POST with no body → HTTP {resp.status_code} (validation rejected)")
            else:
                ctx.warn(f"POST with no body → HTTP {resp.status_code}")
        except Exception as exc:
            ctx.fail(f"Request failed: {exc}")
