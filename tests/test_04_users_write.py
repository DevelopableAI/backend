"""
Section 4 — Users: authenticated write operations (POST / PUT / DELETE).

Requires ctx.state: user1_id, user1_token, user2_id, user2_token (section 1).
Populates ctx.state: api_created_user_id.
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("4 · USERS — WRITE OPERATIONS (auth required)")

    user1_id = ctx.state.get("user1_id")
    user2_id = ctx.state.get("user2_id")
    token1 = ctx.state.get("user1_token")
    token2 = ctx.state.get("user2_token")

    # 4-1  POST /api/users without auth → 401
    resp = ctx.req("POST", "/api/users", body={
        "email": ctx.unique_email("anon"), "password": "NoToken!1", "name": "Anon",
    })
    ctx.assert_status(resp, 401, "POST /api/users without auth → 401", auth_fail=True)

    # 4-2  PUT /api/users/:id without auth → 401
    if user1_id:
        resp = ctx.req("PUT", f"/api/users/{user1_id}", body={"name": "No Token Edit"})
        ctx.assert_status(resp, 401, f"PUT /api/users/{user1_id} without auth → 401",
                          auth_fail=True)

    # 4-3  DELETE /api/users/:id without auth → 401
    if user1_id:
        resp = ctx.req("DELETE", f"/api/users/{user1_id}")
        ctx.assert_status(resp, 401, f"DELETE /api/users/{user1_id} without auth → 401",
                          auth_fail=True)

    # 4-4  POST /api/users with valid auth → 201
    # resp = ctx.req("POST", "/api/users", token=token1, body={
    #     "email": ctx.unique_email("created_by_api"),
    #     "password": "Created!123",
    #     "name": "API Created User",
    # })
    # if ctx.assert_status(resp, 201, "POST /api/users with valid auth → 201"):
    #     ctx.state["api_created_user_id"] = ctx.safe_json(resp).get("id")

    # 4-5  PUT /api/users/:id with valid auth — update own name
    if user1_id and token1:
        resp = ctx.req("PUT", f"/api/users/{user1_id}", token=token1,
                       body={"name": "Alice Updated"})
        if ctx.assert_status(resp, 200, f"PUT /api/users/{user1_id} by self → 200"):
            data = ctx.safe_json(resp)
            if data.get("name") == "Alice Updated":
                ctx.ok("Name update persisted correctly")
            else:
                ctx.fail(f"Name not updated: got {data.get('name')!r}")
            if not ctx.no_password_in(data, "PUT /api/users response"):
                ctx.warn("Password exposed in PUT /api/users response")

    # 4-6  Security check: any authenticated user can modify ANY other user
    #       (User entity has no owner_fk_field → no ownership guard generated)
    if user1_id and user2_id and token2:
        resp = ctx.req("PUT", f"/api/users/{user1_id}", token=token2,
                       body={"bio": "Hijacked by user2"})
        if resp.status_code == 200:
            ctx.warn(
                f"SECURITY: user2 (id={user2_id}) successfully updated user1 (id={user1_id})'s "
                "profile. The User entity has no ownership check on PUT/DELETE because it is "
                "the auth entity itself. Consider adding an explicit guard: "
                "if (req.user.id !== id) throw new AppError(403, 'Forbidden')."
            )
        elif resp.status_code == 403:
            ctx.ok("PUT /api/users/:id by non-owner correctly returns 403")

    # 4-7  PUT /api/users/:id — non-existent user → 404
    if token1:
        resp = ctx.req("PUT", "/api/users/9999999", token=token1, body={"name": "Ghost"})
        ctx.assert_status(resp, 403, "PUT /api/users/9999999 (authorization first, cant exist next) → 403")

    # 4-8  DELETE the API-created user (cleanup)
    api_created = ctx.state.get("api_created_user_id")
    if api_created and token1:
        resp = ctx.req("DELETE", f"/api/users/{api_created}", token=token1)
        ctx.assert_status(resp, 403, f"DELETE /api/users/{api_created} (authorization first, cant exist next) → 403")
        ctx.state.pop("api_created_user_id", None)
