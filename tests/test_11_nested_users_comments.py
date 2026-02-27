"""
Section 11 — Nested routes: POST/PUT/DELETE /api/users/comments
             (auth-entity route — authorId from token, postId from body).

Populates ctx.state: user_nested_comment_id.
Requires ctx.state: user1_id, user1_token, user2_token, post1_id.
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("11 · NESTED ROUTES — POST/PUT/DELETE /api/users/comments")

    user1_id = ctx.state.get("user1_id")
    post1_id = ctx.state.get("post1_id")
    token1 = ctx.state.get("user1_token")
    token2 = ctx.state.get("user2_token")

    # 11-1  POST without auth → 401
    if post1_id:
        resp = ctx.req("POST", "/api/users/comments",
                       body={"body": "Anon via user", "postId": post1_id})
        ctx.assert_status(resp, 401, "POST /api/users/comments without auth → 401",
                          auth_fail=True)

    # 11-2  POST with auth, missing postId → 400
    if token1:
        resp = ctx.req("POST", "/api/users/comments", token=token1, body={"body": "No postId"})
        ctx.assert_status(resp, 400, "POST /api/users/comments missing postId → 400")

    # 11-3  POST with auth, missing body text → 400
    if post1_id and token1:
        resp = ctx.req("POST", "/api/users/comments", token=token1, body={"postId": post1_id})
        ctx.assert_status(resp, 400, "POST /api/users/comments missing 'body' text → 400")

    # 11-4  POST with auth, valid payload — authorId from token
    if post1_id and token1:
        resp = ctx.req("POST", "/api/users/comments", token=token1, body={
            "body": "Comment via user-nested route", "postId": post1_id,
        })
        if ctx.assert_status(resp, 201, "POST /api/users/comments with auth → 201"):
            data = ctx.safe_json(resp)
            if data.get("authorId") == user1_id:
                ctx.ok("authorId set from token on user-nested comment POST")
            else:
                ctx.fail(f"authorId mismatch: {data.get('authorId')} vs {user1_id}")
            if data.get("postId") == post1_id:
                ctx.ok("postId from body stored correctly")
            else:
                ctx.fail(f"postId mismatch: {data.get('postId')} vs {post1_id}")
            ctx.state["user_nested_comment_id"] = data.get("id")

    # 11-5  PUT by owner → 200
    nested_comment_id = ctx.state.get("user_nested_comment_id")
    if nested_comment_id and token1:
        resp = ctx.req("PUT", f"/api/users/comments/{nested_comment_id}", token=token1,
                       body={"body": "Edited via user-nested comment route"})
        if ctx.assert_status(resp, 200,
                             f"PUT /api/users/comments/{nested_comment_id} by owner → 200"):
            data = ctx.safe_json(resp)
            if data.get("body") == "Edited via user-nested comment route":
                ctx.ok("Comment update via user-nested PUT persisted correctly")
            else:
                ctx.fail(f"Body not updated: {data.get('body')!r}")

    # 11-6  PUT by different user → 403 or 404
    if nested_comment_id and token2:
        resp = ctx.req("PUT", f"/api/users/comments/{nested_comment_id}", token=token2,
                       body={"body": "Bob hijacks comment"})
        if resp.status_code in (403, 404):
            ctx.ok(
                f"PUT /api/users/comments/{nested_comment_id} by non-owner → "
                f"HTTP {resp.status_code} (correct)"
            )
        else:
            ctx.auth(
                f"PUT /api/users/comments/{nested_comment_id} by non-owner → "
                f"unexpected HTTP {resp.status_code}"
            )

    # 11-7  DELETE without auth → 401
    if nested_comment_id:
        resp = ctx.req("DELETE", f"/api/users/comments/{nested_comment_id}")
        ctx.assert_status(resp, 401,
                          f"DELETE /api/users/comments/{nested_comment_id} no auth → 401",
                          auth_fail=True)

    # 11-8  DELETE by owner → 204
    if nested_comment_id and token1:
        resp = ctx.req("DELETE", f"/api/users/comments/{nested_comment_id}", token=token1)
        if ctx.assert_status(resp, 204,
                             f"DELETE /api/users/comments/{nested_comment_id} by owner → 204"):
            ctx.state.pop("user_nested_comment_id", None)
