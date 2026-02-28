"""
Section 11 — Comment write ownership: PUT/DELETE /api/comments/:id.

POST /api/users/comments does not exist — Post is Comment's primary parent.
The canonical create is POST /api/posts/:id/comments (tested in sections 7 & 12).

This section tests ownership enforcement on the direct PUT/DELETE routes
using a comment created here via the canonical route.

Populates ctx.state: user_nested_comment_id.
Requires ctx.state: user1_id, user1_token, user2_token, post1_id (sections 1-5).
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("11 · COMMENT OWNERSHIP — PUT/DELETE /api/comments/:id")

    user1_id = ctx.state.get("user1_id")
    post1_id = ctx.state.get("post1_id")
    token1 = ctx.state.get("user1_token")
    token2 = ctx.state.get("user2_token")

    # 11-1  Seed: create a comment via canonical route (POST /api/posts/:id/comments)
    nested_comment_id = None
    if post1_id and token1:
        resp = ctx.req("POST", f"/api/posts/{post1_id}/comments", token=token1, body={
            "body": "Comment for ownership tests",
        })
        if ctx.assert_status(resp, 201, f"Seed: Create comment via POST /api/posts/{post1_id}/comments"):
            data = ctx.safe_json(resp)
            nested_comment_id = data.get("id")
            ctx.state["user_nested_comment_id"] = nested_comment_id
            if data.get("authorId") == user1_id:
                ctx.ok("authorId correctly injected from token")
            if data.get("postId") == post1_id:
                ctx.ok("postId correctly injected from URL param")

    # 11-2  PUT without auth → 401
    if nested_comment_id:
        resp = ctx.req("PUT", f"/api/comments/{nested_comment_id}",
                       body={"body": "Anon edit attempt"})
        ctx.assert_status(resp, 401, f"PUT /api/comments/{nested_comment_id} without auth → 401",
                          auth_fail=True)

    # 11-3  PUT by owner → 200
    if nested_comment_id and token1:
        resp = ctx.req("PUT", f"/api/comments/{nested_comment_id}", token=token1,
                       body={"body": "Edited by owner via direct PUT"})
        if ctx.assert_status(resp, 200, f"PUT /api/comments/{nested_comment_id} by owner → 200"):
            data = ctx.safe_json(resp)
            if data.get("body") == "Edited by owner via direct PUT":
                ctx.ok("Comment update via direct PUT persisted correctly")
            else:
                ctx.fail(f"Body not updated: {data.get('body')!r}")

    # 11-4  PUT by different user → 403 or 404
    if nested_comment_id and token2:
        resp = ctx.req("PUT", f"/api/comments/{nested_comment_id}", token=token2,
                       body={"body": "Bob hijacks comment"})
        if resp.status_code in (403, 404):
            ctx.ok(
                f"PUT /api/comments/{nested_comment_id} by non-owner → "
                f"HTTP {resp.status_code} (correct)"
            )
        else:
            ctx.auth(
                f"PUT /api/comments/{nested_comment_id} by non-owner → "
                f"unexpected HTTP {resp.status_code}"
            )

    # 11-5  DELETE without auth → 401
    if nested_comment_id:
        resp = ctx.req("DELETE", f"/api/comments/{nested_comment_id}")
        ctx.assert_status(resp, 401, f"DELETE /api/comments/{nested_comment_id} no auth → 401",
                          auth_fail=True)

    # 11-6  DELETE by different user → 403 or 404
    if nested_comment_id and token2:
        resp = ctx.req("DELETE", f"/api/comments/{nested_comment_id}", token=token2)
        if resp.status_code in (403, 404):
            ctx.ok(
                f"DELETE /api/comments/{nested_comment_id} by non-owner → "
                f"HTTP {resp.status_code} (correct)"
            )
        else:
            ctx.auth(
                f"DELETE /api/comments/{nested_comment_id} by non-owner → "
                f"unexpected HTTP {resp.status_code}"
            )

    # 11-7  DELETE by owner → 204
    if nested_comment_id and token1:
        resp = ctx.req("DELETE", f"/api/comments/{nested_comment_id}", token=token1)
        if ctx.assert_status(resp, 204, f"DELETE /api/comments/{nested_comment_id} by owner → 204"):
            ctx.state.pop("user_nested_comment_id", None)
