"""
Section 10 — Nested routes: POST/PUT/DELETE /api/users/posts
            (auth-entity route — parent ID from token, not URL).

Populates ctx.state: user_nested_post_id.
Requires ctx.state: user1_id, user1_token, user2_token (sections 1-2).
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("10 · NESTED ROUTES — POST/PUT/DELETE /api/users/posts")

    user1_id = ctx.state.get("user1_id")
    token1 = ctx.state.get("user1_token")
    token2 = ctx.state.get("user2_token")

    # 10-1  POST without auth → 401
    resp = ctx.req("POST", "/api/users/posts",
                   body={"title": "Anon via user route", "content": "."})
    ctx.assert_status(resp, 401, "POST /api/users/posts without auth → 401", auth_fail=True)

    # 10-2  POST with auth — authorId injected from token
    if token1:
        resp = ctx.req("POST", "/api/users/posts", token=token1, body={
            "title": "Post via user-nested route", "content": "Content body here.",
        })
        if ctx.assert_status(resp, 201, "POST /api/users/posts with auth → 201"):
            data = ctx.safe_json(resp)
            if data.get("authorId") == user1_id:
                ctx.ok("authorId set from token (not URL param) on user-nested POST")
            else:
                ctx.fail(
                    f"authorId mismatch on user-nested POST: "
                    f"expected {user1_id}, got {data.get('authorId')}"
                )
            ctx.state["user_nested_post_id"] = data.get("id")

    # 10-3  POST missing required fields → 400
    if token1:
        resp = ctx.req("POST", "/api/users/posts", token=token1, body={"title": "No content"})
        ctx.assert_status(resp, 400, "POST /api/users/posts missing content → 400")

    # 10-4  PUT by token owner → 200
    nested_post_id = ctx.state.get("user_nested_post_id")
    if nested_post_id and token1:
        resp = ctx.req("PUT", f"/api/users/posts/{nested_post_id}", token=token1,
                       body={"title": "Updated via user-nested route"})
        if ctx.assert_status(resp, 200, f"PUT /api/users/posts/{nested_post_id} by owner → 200"):
            data = ctx.safe_json(resp)
            if data.get("title") == "Updated via user-nested route":
                ctx.ok("Update via user-nested PUT persisted correctly")
            else:
                ctx.fail(f"Title not updated: {data.get('title')!r}")

    # 10-5  PUT by different user → 403 or 404
    #       Controller checks: existing.authorId !== req.user.id → AppError(404, …)
    if nested_post_id and token2:
        resp = ctx.req("PUT", f"/api/users/posts/{nested_post_id}", token=token2,
                       body={"title": "Bob hijacks user-nested post"})
        if resp.status_code in (403, 404):
            ctx.ok(
                f"PUT /api/users/posts/{nested_post_id} by non-owner → "
                f"HTTP {resp.status_code} (correct)"
            )
        else:
            ctx.auth(
                f"PUT /api/users/posts/{nested_post_id} by non-owner → "
                f"unexpected HTTP {resp.status_code}"
            )

    # 10-6  PUT non-existent child → 404
    if token1:
        resp = ctx.req("PUT", "/api/users/posts/9999999", token=token1,
                       body={"title": "Ghost"})
        ctx.assert_status(resp, 404, "PUT /api/users/posts/9999999 (non-existent) → 404")

    # 10-7  DELETE without auth → 401
    if nested_post_id:
        resp = ctx.req("DELETE", f"/api/users/posts/{nested_post_id}")
        ctx.assert_status(resp, 401, f"DELETE /api/users/posts/{nested_post_id} no auth → 401",
                          auth_fail=True)

    # 10-8  DELETE by different user → 403 or 404
    if nested_post_id and token2:
        resp = ctx.req("DELETE", f"/api/users/posts/{nested_post_id}", token=token2)
        if resp.status_code in (403, 404):
            ctx.ok(
                f"DELETE /api/users/posts/{nested_post_id} by non-owner → "
                f"HTTP {resp.status_code} (correct)"
            )
        else:
            ctx.auth(
                f"DELETE /api/users/posts/{nested_post_id} by non-owner → "
                f"unexpected HTTP {resp.status_code}"
            )

    # 10-9  DELETE by owner → 204
    if nested_post_id and token1:
        resp = ctx.req("DELETE", f"/api/users/posts/{nested_post_id}", token=token1)
        if ctx.assert_status(resp, 204, f"DELETE /api/users/posts/{nested_post_id} by owner → 204"):
            ctx.state.pop("user_nested_post_id", None)
