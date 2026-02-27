"""
Section 2 — Auth: Login.

Refreshes ctx.state['user1_token'] with a freshly-issued token.
Requires ctx.state: email1, user1_token (from section 1).
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("2 · AUTH — LOGIN")

    email1 = ctx.state.get("email1", "")

    # 2-1  Valid login
    resp = ctx.req("POST", "/auth/login", body={"email": email1, "password": "SecurePass1!"})
    if ctx.assert_status(resp, 200, "Login user1 (valid credentials)"):
        data = ctx.safe_json(resp)
        ctx.assert_field(data, "token", "Login response has token")
        if not ctx.no_password_in(data, "login response"):
            ctx.warn("Password field leaked in /auth/login response")
        else:
            ctx.ok("Password NOT present in login response")
        if "token" in data:
            ctx.state["user1_token"] = data["token"]  # refresh

    # 2-2  Wrong password → 401
    resp = ctx.req("POST", "/auth/login", body={"email": email1, "password": "WrongPass!!"})
    ctx.assert_status(resp, 401, "Login with wrong password → 401", auth_fail=True)

    # 2-3  Non-existent user → 401
    resp = ctx.req("POST", "/auth/login", body={
        "email": ctx.unique_email("ghost"), "password": "SecurePass1!",
    })
    ctx.assert_status(resp, 401, "Login with non-existent email → 401", auth_fail=True)

    # 2-4  Missing email → 400
    resp = ctx.req("POST", "/auth/login", body={"password": "SecurePass1!"})
    ctx.assert_status(resp, 400, "Login without email → 400")

    # 2-5  Missing password → 400
    resp = ctx.req("POST", "/auth/login", body={"email": email1})
    ctx.assert_status(resp, 400, "Login without password → 400")

    # 2-6  Empty body → 400
    resp = ctx.req("POST", "/auth/login", body={})
    ctx.assert_status(resp, 400, "Login with empty body → 400")

    # 2-7  Malformed email → 400
    resp = ctx.req("POST", "/auth/login", body={"email": "bad@@email", "password": "SecurePass1!"})
    ctx.assert_status(resp, 400, "Login with malformed email → 400")

    # 2-8  Correct email, empty password string → 400 or 401
    resp = ctx.req("POST", "/auth/login", body={"email": email1, "password": ""})
    if resp.status_code in (400, 401):
        ctx.ok(f"Login with empty password string → HTTP {resp.status_code} (acceptable)")
    else:
        ctx.fail(f"Login with empty password string → unexpected HTTP {resp.status_code}")
