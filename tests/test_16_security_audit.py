"""
Section 16 — Security audit: sensitive field leakage.

Checks that 'password' never appears in any response body or JWT payload,
and that register bodies don't echo back plaintext credentials.

Requires ctx.state: user1_id, user1_token (sections 1-2).
"""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("16 · SECURITY — SENSITIVE FIELDS AUDIT")

    user1_id = ctx.state.get("user1_id")
    token = ctx.state.get("user1_token", "")

    # 16-1  GET /api/users list — no password field
    resp = ctx.req("GET", "/api/users")
    if resp.status_code == 200:
        items = ctx.safe_json(resp).get("data", [])
        if not ctx.no_password_in(items, "GET /api/users list"):
            ctx.warn(
                "SECURITY: 'password' field is EXPOSED in GET /api/users list. "
                "Ensure safeSelect excludes password from all read queries."
            )
        else:
            ctx.ok("'password' field absent from all records in GET /api/users")

    # 16-2  GET /api/users/:id — no password field
    if user1_id:
        resp = ctx.req("GET", f"/api/users/{user1_id}")
        if resp.status_code == 200:
            data = ctx.safe_json(resp)
            if not ctx.no_password_in(data, f"GET /api/users/{user1_id}"):
                ctx.warn(
                    f"SECURITY: 'password' exposed in GET /api/users/{user1_id} response"
                )
            else:
                ctx.ok(f"'password' absent from GET /api/users/{user1_id}")

    # 16-3  JWT payload — no password field
    if token:
        payload = ctx.decode_jwt(token)
        if payload is None:
            ctx.fail("Could not decode JWT payload for audit")
        elif "password" in payload:
            ctx.warn(
                "SECURITY CRITICAL: JWT payload contains 'password' field. "
                "Tokens are base64-decodable by anyone."
            )
        else:
            ctx.ok("JWT payload does NOT contain 'password' field")

    # 16-4  JWT expiry present
    if token:
        payload = ctx.decode_jwt(token)
        if payload and "exp" not in payload:
            ctx.warn("JWT has no 'exp' claim — tokens never expire (session cannot be revoked)")
        elif payload:
            ctx.ok(f"JWT 'exp' claim present")

    # 16-5  Fresh register — plaintext password must not appear in response body
    audit_email = ctx.unique_email("audit_sec")
    resp = ctx.req("POST", "/auth/register", body={
        "email": audit_email, "password": "AuditPass!99x", "name": "Audit User",
    })
    if resp.status_code == 201:
        raw_body = resp.text
        if "AuditPass!99x" in raw_body:
            ctx.warn(
                "SECURITY CRITICAL: Plaintext password 'AuditPass!99x' found verbatim "
                "in /auth/register response body!"
            )
        elif '"password"' in raw_body:
            ctx.warn("SECURITY: 'password' key present in /auth/register response JSON")
        else:
            ctx.ok("Plaintext password does NOT appear in /auth/register response body")

    # 16-6  Login response must not contain password
    if ctx.state.get("email1"):
        resp = ctx.req("POST", "/auth/login", body={
            "email": ctx.state["email1"], "password": "SecurePass1!",
        })
        if resp.status_code == 200:
            raw_body = resp.text
            if "SecurePass1!" in raw_body:
                ctx.warn(
                    "SECURITY CRITICAL: Plaintext password found in /auth/login response!"
                )
            elif '"password"' in raw_body:
                ctx.warn("SECURITY: 'password' key present in /auth/login response JSON")
            else:
                ctx.ok("Plaintext password does NOT appear in /auth/login response")

    # 16-7  Helmet security headers should be present
    resp = ctx.req("GET", "/health")
    if resp.status_code == 200:
        security_headers = [
            "x-content-type-options",
            "x-frame-options",
            "strict-transport-security",
        ]
        present = [h for h in security_headers if h in resp.headers]
        missing = [h for h in security_headers if h not in resp.headers]
        if present:
            ctx.ok(f"Helmet security headers present: {present}")
        if missing:
            ctx.warn(
                f"Helmet security headers missing: {missing}. "
                "Ensure helmet() middleware is registered before routes."
            )
