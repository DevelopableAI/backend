"""
Section 7 — Comments: seed data + unauthenticated GET operations.

Creates comments that subsequent sections depend on.

Populates ctx.state: comment1_id, comment2_id.
Requires ctx.state: user1_id, user1_token, user2_token, post1_id (sections 1-5).

Note: Comments are created via POST /api/posts/:id/comments (canonical create route).
The direct POST /api/comments does not exist — Post is the primary parent of Comment.
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("7 · COMMENTS — SEED & GET (unauthenticated reads)")

    user1_id = ctx.state.get("user1_id")
    user2_id = ctx.state.get("user2_id")
    post1_id = ctx.state.get("post1_id")
    token1 = ctx.state.get("user1_token")
    token2 = ctx.state.get("user2_token")

    # --- Seed via canonical create: POST /api/posts/:id/comments ---

    if post1_id and token1:
        resp = ctx.req("POST", f"/api/posts/{post1_id}/comments", token=token1, body={
            "body": "Great article, Alice!",
        })
        if ctx.assert_status(resp, 201, f"Seed: Create comment1 by user1 on post1 via POST /api/posts/{post1_id}/comments"):
            data = ctx.safe_json(resp)
            ctx.state["comment1_id"] = data.get("id")
            if data.get("authorId") == user1_id:
                ctx.ok("authorId correctly injected from token on nested POST")
            else:
                ctx.fail(f"authorId mismatch: {data.get('authorId')} vs {user1_id}")
            if data.get("postId") == post1_id:
                ctx.ok("postId correctly injected from URL param on nested POST")
            else:
                ctx.fail(f"postId mismatch: {data.get('postId')} vs {post1_id}")

    if post1_id and token2:
        resp = ctx.req("POST", f"/api/posts/{post1_id}/comments", token=token2, body={
            "body": "Interesting perspective!",
        })
        if ctx.assert_status(resp, 201, f"Seed: Create comment2 by user2 on post1"):
            data = ctx.safe_json(resp)
            ctx.state["comment2_id"] = data.get("id")
            if data.get("authorId") == user2_id:
                ctx.ok("authorId correctly injected from token for user2")

    # --- GET tests ---

    # 7-1  Paginated list
    resp = ctx.req("GET", "/api/comments")
    if ctx.assert_status(resp, 200, "GET /api/comments → 200"):
        ctx.assert_paginated(ctx.safe_json(resp), "GET /api/comments")

    # 7-2  GET /api/comments/:id → 200
    comment1_id = ctx.state.get("comment1_id")
    if comment1_id:
        resp = ctx.req("GET", f"/api/comments/{comment1_id}")
        if ctx.assert_status(resp, 200, f"GET /api/comments/{comment1_id} → 200"):
            data = ctx.safe_json(resp)
            if data.get("id") == comment1_id:
                ctx.ok("Comment id matches")

    # 7-3  Non-existent comment → 404
    resp = ctx.req("GET", "/api/comments/9999999")
    ctx.assert_status(resp, 404, "GET /api/comments/9999999 → 404")

    # 7-4  Non-numeric ID → 400
    resp = ctx.req("GET", "/api/comments/bad")
    ctx.assert_status(resp, 400, "GET /api/comments/bad → 400 (invalid ID)")

    # 7-5  Negative ID → 400 or 404
    resp = ctx.req("GET", "/api/comments/-1")
    if resp.status_code in (400, 404):
        ctx.ok(f"GET /api/comments/-1 → HTTP {resp.status_code} (acceptable)")
    else:
        ctx.fail(f"GET /api/comments/-1 → unexpected HTTP {resp.status_code}")
