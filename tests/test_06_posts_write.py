"""
Section 6 — Posts: authenticated write operations (POST / PUT / DELETE).

Requires ctx.state: user1_id, user1_token, user2_token, post1_id, post2_id, post3_id.
Populates ctx.state: spoofed_post_id.
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("6 · POSTS — WRITE OPERATIONS")

    user1_id = ctx.state.get("user1_id")
    user2_id = ctx.state.get("user2_id")
    post1_id = ctx.state.get("post1_id")
    post2_id = ctx.state.get("post2_id")
    token1 = ctx.state.get("user1_token")
    token2 = ctx.state.get("user2_token")

    # 6-1  POST without auth → 401
    resp = ctx.req("POST", "/api/posts", body={"title": "Anon Post", "content": "Anon content"})
    ctx.assert_status(resp, 401, "POST /api/posts without auth → 401", auth_fail=True)

    # 6-2  POST with auth, missing title → 400
    resp = ctx.req("POST", "/api/posts", token=token1, body={"content": "No title"})
    ctx.assert_status(resp, 400, "POST /api/posts missing title → 400")

    # 6-3  POST with auth, missing content → 400
    resp = ctx.req("POST", "/api/posts", token=token1, body={"title": "Title Only"})
    ctx.assert_status(resp, 400, "POST /api/posts missing content → 400")

    # 6-4  POST with auth, empty body → 400
    resp = ctx.req("POST", "/api/posts", token=token1, body={})
    ctx.assert_status(resp, 400, "POST /api/posts empty body → 400")

    # 6-5  Spoof authorId in body — must be overridden by token
    if user1_id and user2_id and token1:
        resp = ctx.req("POST", "/api/posts", token=token1, body={
            "title": "Spoof Test", "content": "Trying to set authorId to user2",
            "authorId": user2_id,
        })
        if ctx.assert_status(resp, 201, "POST /api/posts with spoofed authorId → 201"):
            data = ctx.safe_json(resp)
            if data.get("authorId") == user1_id:
                ctx.ok("authorId spoofing prevented: injected from token, not from body")
            elif data.get("authorId") == user2_id:
                ctx.warn(
                    "SECURITY: authorId spoofing succeeded — user1 created a post attributed "
                    "to user2. The controller should override authorId with req.user.id."
                )
            ctx.state["spoofed_post_id"] = data.get("id")

    # 6-6  PUT without auth → 401
    if post1_id:
        resp = ctx.req("PUT", f"/api/posts/{post1_id}", body={"title": "Anon Edit"})
        ctx.assert_status(resp, 401, f"PUT /api/posts/{post1_id} without auth → 401",
                          auth_fail=True)

    # 6-7  PUT with owner token → 200
    if post1_id and token1:
        resp = ctx.req("PUT", f"/api/posts/{post1_id}", token=token1,
                       body={"title": "Updated Title by Alice"})
        if ctx.assert_status(resp, 200, f"PUT /api/posts/{post1_id} by owner → 200"):
            data = ctx.safe_json(resp)
            if data.get("title") == "Updated Title by Alice":
                ctx.ok("Title update persisted correctly")
            else:
                ctx.fail(f"Title not updated: {data.get('title')!r}")

    # 6-8  PUT with non-owner token → 403
    if post1_id and token2:
        resp = ctx.req("PUT", f"/api/posts/{post1_id}", token=token2,
                       body={"title": "Hijack by Bob"})
        ctx.assert_status(resp, 403, f"PUT /api/posts/{post1_id} by non-owner → 403",
                          auth_fail=True)

    # 6-9  PUT non-existent post → 404
    if token1:
        resp = ctx.req("PUT", "/api/posts/9999999", token=token1, body={"title": "Ghost Update"})
        ctx.assert_status(resp, 404, "PUT /api/posts/9999999 (non-existent) → 404")

    # 6-10  PUT with empty title string
    if post1_id and token1:
        resp = ctx.req("PUT", f"/api/posts/{post1_id}", token=token1, body={"title": ""})
        if resp.status_code == 400:
            ctx.ok("PUT /api/posts with empty string title → 400 (validation)")
        elif resp.status_code == 200:
            ctx.warn(
                "PUT /api/posts accepted empty string title — consider .min(1) "
                "in the Zod 'title' schema to reject blank titles"
            )

    # 6-11  DELETE without auth → 401
    if post2_id:
        resp = ctx.req("DELETE", f"/api/posts/{post2_id}")
        ctx.assert_status(resp, 401, f"DELETE /api/posts/{post2_id} without auth → 401",
                          auth_fail=True)

    # 6-12  DELETE with non-owner token → 403
    if post2_id and token2:
        resp = ctx.req("DELETE", f"/api/posts/{post2_id}", token=token2)
        ctx.assert_status(resp, 403, f"DELETE /api/posts/{post2_id} by non-owner → 403",
                          auth_fail=True)

    # 6-13  DELETE with owner token → 204
    if post2_id and token1:
        resp = ctx.req("DELETE", f"/api/posts/{post2_id}", token=token1)
        if ctx.assert_status(resp, 204, f"DELETE /api/posts/{post2_id} by owner → 204"):
            ctx.state["post2_deleted"] = True

    # 6-14  GET after delete → 404
    if post2_id and ctx.state.get("post2_deleted"):
        resp = ctx.req("GET", f"/api/posts/{post2_id}")
        ctx.assert_status(resp, 404, f"GET /api/posts/{post2_id} after deletion → 404")

    # 6-15  DELETE non-existent post → 404
    if token1:
        resp = ctx.req("DELETE", "/api/posts/9999999", token=token1)
        ctx.assert_status(resp, 404, "DELETE /api/posts/9999999 (non-existent) → 404")
