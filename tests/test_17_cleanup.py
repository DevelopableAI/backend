"""
Section 17 — Cleanup: delete all test data created during the suite.

Order: comments → posts → (leave user accounts in place).
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("17 · CLEANUP — Remove test data")

    token1 = ctx.state.get("user1_token")
    token2 = ctx.state.get("user2_token")

    def _delete(path: str, token: str | None, label: str) -> None:
        if not token:
            ctx.warn(f"Cleanup: no token for {label}, skipping")
            return
        resp = ctx.req("DELETE", path, token=token)
        if resp.status_code in (204, 404):
            ctx.ok(f"Cleanup: {label} removed (HTTP {resp.status_code})")
        elif resp.status_code == 403:
            ctx.warn(f"Cleanup: {label} → 403 Forbidden (may need different token)")
        else:
            ctx.warn(f"Cleanup: {label} → unexpected HTTP {resp.status_code}")

    # Remaining comments (comment1 is still alive)
    comment_cleanups = [
        (ctx.state.get("comment1_id"), token1, "comment1"),
        (ctx.state.get("spoofed_comment_id"), token1, "spoofed_comment"),
        # Leftovers from structure check (should already be gone, 404 is fine)
        (ctx.state.get("structure_check_comment_id"), token1, "structure_check_comment"),
    ]
    for cid, tok, label in comment_cleanups:
        if cid:
            _delete(f"/api/comments/{cid}", tok, label)

    # Remaining posts
    post_cleanups = [
        (ctx.state.get("post1_id"), token1, "post1"),
        (ctx.state.get("post3_id"), token2, "post3 (user2)"),
        (ctx.state.get("spoofed_post_id"), token1, "spoofed_post"),
        (ctx.state.get("long_title_post_id"), token1, "long_title_post"),
        (ctx.state.get("long_content_post_id"), token1, "long_content_post"),
        (ctx.state.get("xss_post_id"), token1, "xss_post"),
        (ctx.state.get("unicode_post_id"), token1, "unicode_post"),
    ]
    for pid, tok, label in post_cleanups:
        if pid:
            _delete(f"/api/posts/{pid}", tok, label)
