"""
Section 8 — Comments: authenticated write operations (POST / PUT / DELETE).

Requires ctx.state: user1_token, user2_token, post1_id, comment1_id, comment2_id.
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("8 · COMMENTS — WRITE OPERATIONS")

    post1_id = ctx.state.get("post1_id")
    comment1_id = ctx.state.get("comment1_id")
    comment2_id = ctx.state.get("comment2_id")
    token1 = ctx.state.get("user1_token")
    token2 = ctx.state.get("user2_token")

    # 8-1  POST without auth → 401
    if post1_id:
        resp = ctx.req("POST", "/api/comments",
                       body={"body": "Anonymous comment", "postId": post1_id})
        ctx.assert_status(resp, 401, "POST /api/comments without auth → 401", auth_fail=True)

    # 8-2  POST with auth, missing 'body' field → 400
    if post1_id and token1:
        resp = ctx.req("POST", "/api/comments", token=token1, body={"postId": post1_id})
        ctx.assert_status(resp, 400, "POST /api/comments missing 'body' → 400")

    # 8-3  POST with auth, missing 'postId' → 400
    if token1:
        resp = ctx.req("POST", "/api/comments", token=token1, body={"body": "No postId"})
        ctx.assert_status(resp, 400, "POST /api/comments missing 'postId' → 400")

    # 8-4  POST with non-existent postId → FK violation (400 / 404 / 409 / 500)
    if token1:
        resp = ctx.req("POST", "/api/comments", token=token1,
                       body={"body": "Ghost post comment", "postId": 9999999})
        if resp.status_code in (400, 404, 409, 422, 500):
            ctx.ok(
                f"POST /api/comments with non-existent postId → HTTP {resp.status_code} "
                "(DB referential integrity enforced)"
            )
        else:
            ctx.fail(
                f"POST /api/comments with non-existent postId → unexpected HTTP "
                f"{resp.status_code}"
            )

    # 8-5  PUT without auth → 401
    if comment1_id:
        resp = ctx.req("PUT", f"/api/comments/{comment1_id}", body={"body": "Anon edit"})
        ctx.assert_status(resp, 401, f"PUT /api/comments/{comment1_id} without auth → 401",
                          auth_fail=True)

    # 8-6  PUT with owner token → 200
    if comment1_id and token1:
        resp = ctx.req("PUT", f"/api/comments/{comment1_id}", token=token1,
                       body={"body": "Edited comment by Alice"})
        if ctx.assert_status(resp, 200, f"PUT /api/comments/{comment1_id} by owner → 200"):
            data = ctx.safe_json(resp)
            if data.get("body") == "Edited comment by Alice":
                ctx.ok("Comment body update persisted")
            else:
                ctx.fail(f"Comment body not updated: {data.get('body')!r}")

    # 8-7  PUT with non-owner token → 403
    if comment1_id and token2:
        resp = ctx.req("PUT", f"/api/comments/{comment1_id}", token=token2,
                       body={"body": "Bob steals Alice comment"})
        ctx.assert_status(resp, 403, f"PUT /api/comments/{comment1_id} by non-owner → 403",
                          auth_fail=True)

    # 8-8  DELETE without auth → 401
    if comment2_id:
        resp = ctx.req("DELETE", f"/api/comments/{comment2_id}")
        ctx.assert_status(resp, 401, f"DELETE /api/comments/{comment2_id} without auth → 401",
                          auth_fail=True)

    # 8-9  DELETE with non-owner token → 403
    if comment2_id and token1:
        resp = ctx.req("DELETE", f"/api/comments/{comment2_id}", token=token1)
        ctx.assert_status(resp, 403, f"DELETE /api/comments/{comment2_id} by non-owner → 403",
                          auth_fail=True)

    # 8-10  DELETE with owner token → 204
    if comment2_id and token2:
        resp = ctx.req("DELETE", f"/api/comments/{comment2_id}", token=token2)
        if ctx.assert_status(resp, 204, f"DELETE /api/comments/{comment2_id} by owner → 204"):
            ctx.state["comment2_deleted"] = True

    # 8-11  GET deleted comment → 404
    if comment2_id and ctx.state.get("comment2_deleted"):
        resp = ctx.req("GET", f"/api/comments/{comment2_id}")
        ctx.assert_status(resp, 404, f"GET /api/comments/{comment2_id} after deletion → 404")

    # 8-12  DELETE non-existent comment → 404
    if token1:
        resp = ctx.req("DELETE", "/api/comments/9999999", token=token1)
        ctx.assert_status(resp, 404, "DELETE /api/comments/9999999 (non-existent) → 404")
