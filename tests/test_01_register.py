"""
Section 1 — Auth: Register.

Populates ctx.state with:
  user1_token, user1_id
  user2_token, user2_id
  user3_token, user3_id   (temporary user, deleted in section 17)
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("1 · AUTH — REGISTER")

    email1 = ctx.unique_email("alice")
    email2 = ctx.unique_email("bob")
    email3 = ctx.unique_email("charlie")

    # Persist emails so login section can reuse them
    ctx.state["email1"] = email1
    ctx.state["email2"] = email2

    # 1-1  Valid registration — user 1
    resp = ctx.req("POST", "/auth/register", body={
        "email": email1, "password": "SecurePass1!", "name": "Alice Tester",
    })
    if ctx.assert_status(resp, 201, "Register user1 (valid)"):
        data = ctx.safe_json(resp)
        ctx.assert_field(data, "token", "Register user1")
        ctx.assert_field(data, "user", "Register user1")
        if "user" in data:
            ctx.assert_field(data["user"], "id", "Register user1.user")
            ctx.assert_field(data["user"], "email", "Register user1.user")
            if not ctx.no_password_in(data, "register response"):
                ctx.warn("Password field leaked in /auth/register response body")
            else:
                ctx.ok("Password is NOT present in register response body")
        ctx.state["user1_token"] = data.get("token", "")
        ctx.state["user1_id"] = data.get("user", {}).get("id")
        payload = ctx.decode_jwt(ctx.state["user1_token"])
        if payload is not None:
            if "password" in payload:
                ctx.warn("JWT payload contains 'password' field — tokens are readable!")
            else:
                ctx.ok("JWT payload does NOT contain 'password' field")

    # 1-2  Valid registration — user 2
    resp = ctx.req("POST", "/auth/register", body={
        "email": email2, "password": "AnotherPass9#", "name": "Bob Reviewer",
    })
    if ctx.assert_status(resp, 201, "Register user2 (valid)"):
        data = ctx.safe_json(resp)
        ctx.state["user2_token"] = data.get("token", "")
        ctx.state["user2_id"] = data.get("user", {}).get("id")
        ctx.ok(f"user2 registered with id={ctx.state['user2_id']}")

    # 1-3  Valid registration — user 3 (temporary, deleted later)
    resp = ctx.req("POST", "/auth/register", body={
        "email": email3, "password": "TempUser!7x", "name": "Charlie Temp",
    })
    if ctx.assert_status(resp, 201, "Register user3 (valid, temporary)"):
        data = ctx.safe_json(resp)
        ctx.state["user3_token"] = data.get("token", "")
        ctx.state["user3_id"] = data.get("user", {}).get("id")

    # 1-4  Duplicate email → 409
    resp = ctx.req("POST", "/auth/register", body={
        "email": email1, "password": "SecurePass1!", "name": "Alice Duplicate",
    })
    ctx.assert_status(resp, 409, "Register duplicate email → 409")

    # 1-5  Missing email → 400
    resp = ctx.req("POST", "/auth/register", body={
        "password": "SecurePass1!", "name": "No Email",
    })
    ctx.assert_status(resp, 400, "Register without email → 400")

    # 1-6  Missing password → 400
    resp = ctx.req("POST", "/auth/register", body={
        "email": ctx.unique_email("no_pw"), "name": "No Password",
    })
    ctx.assert_status(resp, 400, "Register without password → 400")

    # 1-7  Missing name → 400
    resp = ctx.req("POST", "/auth/register", body={
        "email": ctx.unique_email("no_name"), "password": "SecurePass1!",
    })
    ctx.assert_status(resp, 400, "Register without name → 400")

    # 1-8  Invalid email format → 400
    resp = ctx.req("POST", "/auth/register", body={
        "email": "not-an-email", "password": "SecurePass1!", "name": "Bad Email",
    })
    ctx.assert_status(resp, 400, "Register with invalid email format → 400")

    # 1-9  Password too short (< 8 chars) → 400
    resp = ctx.req("POST", "/auth/register", body={
        "email": ctx.unique_email("short_pw"), "password": "1234567", "name": "Short Pass",
    })
    ctx.assert_status(resp, 400, "Register with password <8 chars → 400")

    # 1-10  Empty password string → 400
    resp = ctx.req("POST", "/auth/register", body={
        "email": ctx.unique_email("empty_pw"), "password": "", "name": "Empty Pass",
    })
    ctx.assert_status(resp, 400, "Register with empty password string → 400")

    # 1-11  Empty body → 400
    resp = ctx.req("POST", "/auth/register", body={})
    ctx.assert_status(resp, 400, "Register with empty body → 400")

    # 1-12  Optional bio field — should succeed and be returned
    resp = ctx.req("POST", "/auth/register", body={
        "email": ctx.unique_email("bio_user"),
        "password": "WithBio!99",
        "name": "Bio User",
        "bio": "I write tests for fun.",
    })
    if ctx.assert_status(resp, 201, "Register with optional 'bio' field → 201"):
        data = ctx.safe_json(resp)
        if data.get("user", {}).get("bio") == "I write tests for fun.":
            ctx.ok("Bio field persisted and returned correctly")
        else:
            ctx.fail("Bio field not present in register response")

    # 1-13  Unknown / extra fields should be silently ignored
    resp = ctx.req("POST", "/auth/register", body={
        "email": ctx.unique_email("extra"),
        "password": "ExtraFields1!",
        "name": "Extra Fields",
        "role": "admin",
        "isAdmin": True,
    })
    ctx.assert_status(resp, 201, "Register with extra/unknown fields → 201 (stripped)")
