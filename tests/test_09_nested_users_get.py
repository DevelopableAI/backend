"""
Section 9 — Nested routes: GET /api/users/:id/posts and /api/users/:id/comments.

Requires ctx.state: user1_id, post1_id, comment1_id (sections 1-7).
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("9 · NESTED ROUTES — GET /api/users/:id/posts & /comments")

    user1_id = ctx.state.get("user1_id")

    # 9-1  GET /api/users/:id/posts → paginated, scoped to that user
    if user1_id:
        resp = ctx.req("GET", f"/api/users/{user1_id}/posts")
        if ctx.assert_status(resp, 200, f"GET /api/users/{user1_id}/posts → 200"):
            data = ctx.safe_json(resp)
            ctx.assert_paginated(data, f"GET /api/users/{user1_id}/posts")
            posts = data.get("data", [])
            wrong = [p for p in posts if p.get("authorId") != user1_id]
            if wrong:
                ctx.fail(
                    f"GET /api/users/{user1_id}/posts returned posts with wrong authorId: "
                    f"{[p['authorId'] for p in wrong]}"
                )
            else:
                ctx.ok(f"All posts in /api/users/{user1_id}/posts belong to user1")

    # 9-2  Pagination on nested posts
    if user1_id:
        resp = ctx.req("GET", f"/api/users/{user1_id}/posts", params={"limit": 1})
        if ctx.assert_status(resp, 200, f"GET /api/users/{user1_id}/posts?limit=1"):
            meta = ctx.safe_json(resp).get("meta", {})
            if meta.get("limit") == 1:
                ctx.ok("Pagination limit=1 respected on nested user/posts")
            else:
                ctx.fail(f"Expected limit=1, got {meta.get('limit')}")

    # 9-3  Non-existent user → empty paginated list (not 404)
    resp = ctx.req("GET", "/api/users/9999999/posts")
    if ctx.assert_status(resp, 200, "GET /api/users/9999999/posts → 200 empty"):
        data = ctx.safe_json(resp)
        if data.get("meta", {}).get("total", -1) == 0:
            ctx.ok("Non-existent user's posts returns total=0")
        else:
            ctx.fail(f"Expected total=0 for non-existent user, got {data.get('meta', {})}")

    # 9-4  Invalid user ID format → 400
    resp = ctx.req("GET", "/api/users/abc/posts")
    ctx.assert_status(resp, 400, "GET /api/users/abc/posts → 400 (invalid ID)")

    # 9-5  GET /api/users/:id/comments → paginated, scoped to that user
    if user1_id:
        resp = ctx.req("GET", f"/api/users/{user1_id}/comments")
        if ctx.assert_status(resp, 200, f"GET /api/users/{user1_id}/comments → 200"):
            data = ctx.safe_json(resp)
            ctx.assert_paginated(data, f"GET /api/users/{user1_id}/comments")
            comments = data.get("data", [])
            wrong = [c for c in comments if c.get("authorId") != user1_id]
            if wrong:
                ctx.fail("Comments list contains items from other users")
            else:
                ctx.ok(f"All comments in /api/users/{user1_id}/comments belong to user1")

    # 9-6  Non-existent user → empty list for comments too
    resp = ctx.req("GET", "/api/users/9999999/comments")
    if ctx.assert_status(resp, 200, "GET /api/users/9999999/comments → 200 empty"):
        total = ctx.safe_json(resp).get("meta", {}).get("total", -1)
        if total == 0:
            ctx.ok("Non-existent user's comments returns total=0")
