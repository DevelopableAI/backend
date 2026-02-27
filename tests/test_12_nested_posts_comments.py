"""
Section 12 — Nested routes: GET/POST/PUT/DELETE /api/posts/:id/comments
             (postId injected from URL, authorId must come from body).

Populates ctx.state: post_nested_comment_id, spoofed_comment_id.
Requires ctx.state: user1_id, user1_token, user2_id, user2_token, post1_id, post3_id.
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("12 · NESTED ROUTES — /api/posts/:id/comments")

    user1_id = ctx.state.get("user1_id")
    user2_id = ctx.state.get("user2_id")
    post1_id = ctx.state.get("post1_id")
    post3_id = ctx.state.get("post3_id")
    token1 = ctx.state.get("user1_token")
    token2 = ctx.state.get("user2_token")

    # 12-1  GET → 200 paginated, scoped to that post
    if post1_id:
        resp = ctx.req("GET", f"/api/posts/{post1_id}/comments")
        if ctx.assert_status(resp, 200, f"GET /api/posts/{post1_id}/comments → 200"):
            data = ctx.safe_json(resp)
            ctx.assert_paginated(data, f"GET /api/posts/{post1_id}/comments")
            comments = data.get("data", [])
            wrong = [c for c in comments if c.get("postId") != post1_id]
            if wrong:
                ctx.fail(f"Comments list contains items for other posts: {wrong}")
            else:
                ctx.ok(f"All comments correctly scoped to post {post1_id}")

    # 12-2  GET pagination
    if post1_id:
        resp = ctx.req("GET", f"/api/posts/{post1_id}/comments", params={"limit": 1})
        if ctx.assert_status(resp, 200, f"GET /api/posts/{post1_id}/comments?limit=1"):
            meta = ctx.safe_json(resp).get("meta", {})
            if meta.get("limit") == 1:
                ctx.ok("limit=1 respected on post-nested comments")

    # 12-3  Non-existent post → empty list (not 404)
    resp = ctx.req("GET", "/api/posts/9999999/comments")
    if ctx.assert_status(resp, 200, "GET /api/posts/9999999/comments → 200 empty"):
        total = ctx.safe_json(resp).get("meta", {}).get("total", -1)
        if total == 0:
            ctx.ok("Non-existent post comments returns total=0")

    # 12-4  POST without auth → 401
    if post1_id:
        resp = ctx.req("POST", f"/api/posts/{post1_id}/comments",
                       body={"body": "Anonymous", "authorId": user1_id})
        ctx.assert_status(resp, 401,
                          f"POST /api/posts/{post1_id}/comments without auth → 401",
                          auth_fail=True)

    # 12-5  POST with auth, missing body text → 400
    if post1_id and token1:
        resp = ctx.req("POST", f"/api/posts/{post1_id}/comments", token=token1,
                       body={"authorId": user1_id})
        ctx.assert_status(resp, 400,
                          f"POST /api/posts/{post1_id}/comments missing body → 400")

    # 12-6  POST with auth — postId injected from URL
    if post1_id and token1:
        resp = ctx.req("POST", f"/api/posts/{post1_id}/comments", token=token1,
                       body={"body": "Comment via post-nested route", "authorId": user1_id})
        if ctx.assert_status(resp, 201, f"POST /api/posts/{post1_id}/comments → 201"):
            data = ctx.safe_json(resp)
            if data.get("postId") == post1_id:
                ctx.ok("postId correctly injected from URL (not from body)")
            else:
                ctx.fail(f"postId mismatch: {data.get('postId')} vs {post1_id}")
            ctx.state["post_nested_comment_id"] = data.get("id")

    # 12-7  Security: authorId spoofing via post-nested route
    if post1_id and token1 and user2_id:
        resp = ctx.req("POST", f"/api/posts/{post1_id}/comments", token=token1,
                       body={"body": "Claimed as user2", "authorId": user2_id})
        if resp.status_code == 201:
            data = ctx.safe_json(resp)
            if data.get("authorId") == user2_id:
                ctx.warn(
                    f"SECURITY: POST /api/posts/:id/comments allows arbitrary authorId — "
                    f"user1 (id={user1_id}) created a comment attributed to user2 (id={user2_id}). "
                    "This route should inject authorId from req.user.id rather than trusting "
                    "the client-supplied value."
                )
                ctx.state["spoofed_comment_id"] = data.get("id")
            elif data.get("authorId") == user1_id:
                ctx.ok(
                    "POST /api/posts/:id/comments: authorId spoof ignored, set from token"
                )

    # 12-8  PUT by auth user → 200
    pnc_id = ctx.state.get("post_nested_comment_id")
    if post1_id and pnc_id and token1:
        resp = ctx.req("PUT", f"/api/posts/{post1_id}/comments/{pnc_id}", token=token1,
                       body={"body": "Updated comment body"})
        if ctx.assert_status(resp, 200, f"PUT /api/posts/{post1_id}/comments/{pnc_id} → 200"):
            data = ctx.safe_json(resp)
            if data.get("body") == "Updated comment body":
                ctx.ok("Comment body updated via post-nested PUT")

    # 12-9  PUT with wrong post parent → 404
    if post3_id and pnc_id and token1:
        resp = ctx.req("PUT", f"/api/posts/{post3_id}/comments/{pnc_id}", token=token1,
                       body={"body": "Wrong parent"})
        ctx.assert_status(resp, 404,
                          f"PUT /api/posts/{post3_id}/comments/{pnc_id} wrong parent → 404")

    # 12-10  DELETE without auth → 401
    if post1_id and pnc_id:
        resp = ctx.req("DELETE", f"/api/posts/{post1_id}/comments/{pnc_id}")
        ctx.assert_status(resp, 401,
                          f"DELETE /api/posts/{post1_id}/comments/{pnc_id} no auth → 401",
                          auth_fail=True)

    # 12-11  DELETE non-existent child → 404
    if post1_id and token1:
        resp = ctx.req("DELETE", f"/api/posts/{post1_id}/comments/9999999", token=token1)
        ctx.assert_status(resp, 404,
                          f"DELETE /api/posts/{post1_id}/comments/9999999 → 404")

    # 12-12  DELETE with auth → 204
    if post1_id and pnc_id and token1:
        resp = ctx.req("DELETE", f"/api/posts/{post1_id}/comments/{pnc_id}", token=token1)
        if ctx.assert_status(resp, 204,
                             f"DELETE /api/posts/{post1_id}/comments/{pnc_id} → 204"):
            ctx.state.pop("post_nested_comment_id", None)
