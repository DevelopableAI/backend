"""
Section 13 — Token edge cases and JWT security.

Requires ctx.state: user1_token, post1_id (sections 1-5).
"""

import requests as _requests
from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("13 · TOKEN EDGE CASES & SECURITY")

    post1_id = ctx.state.get("post1_id")
    raw_token = ctx.state.get("user1_token", "")

    # 13-1  Malformed token (not a JWT) → 401
    if post1_id:
        resp = ctx.req("PUT", f"/api/posts/{post1_id}", token="not.a.valid.jwt",
                       body={"title": "Malformed token attempt"})
        ctx.assert_status(resp, 401, "Request with malformed JWT → 401", auth_fail=True)

    # 13-2  Tampered signature (flip last char) → 401
    if post1_id and raw_token and raw_token.count(".") == 2:
        parts = raw_token.split(".")
        sig = parts[2]
        tampered = sig[:-1] + ("A" if sig[-1] != "A" else "B")
        tampered_token = ".".join(parts[:2] + [tampered])
        resp = ctx.req("PUT", f"/api/posts/{post1_id}", token=tampered_token,
                       body={"title": "Tampered token"})
        ctx.assert_status(resp, 401, "Request with tampered JWT signature → 401",
                          auth_fail=True)

    # 13-3  Empty Bearer value → 401
    if post1_id:
        resp = ctx.req("PUT", f"/api/posts/{post1_id}", token="",
                       body={"title": "Empty token"})
        ctx.assert_status(resp, 401, "Request with empty token string → 401", auth_fail=True)

    # 13-4  Token with only two segments (missing signature) → 401
    if post1_id and raw_token:
        two_part = ".".join(raw_token.split(".")[:2])
        resp = ctx.req("PUT", f"/api/posts/{post1_id}", token=two_part,
                       body={"title": "Two-segment JWT"})
        ctx.assert_status(resp, 401, "Two-segment JWT (no signature) → 401", auth_fail=True)

    # 13-5  Authorization header without "Bearer" scheme → 401
    if post1_id and raw_token:
        headers = {
            "Content-Type": "application/json",
            "Authorization": raw_token,  # Raw token, no "Bearer " prefix
        }
        url = f"{ctx.base_url}/api/posts/{post1_id}"
        print(f"  🚀  PUT /api/posts/{post1_id} (Authorization without 'Bearer' prefix)")
        try:
            resp = _requests.put(url, headers=headers, json={"title": "No bearer"}, timeout=10)
            if resp.status_code == 401:
                ctx.ok("Missing 'Bearer' prefix → 401")
            else:
                ctx.warn(
                    f"Authorization without 'Bearer' prefix returned HTTP {resp.status_code} "
                    "— middleware should require the 'Bearer' scheme explicitly"
                )
        except Exception as exc:
            ctx.fail(f"Request failed: {exc}")

    # 13-6  JWT payload should not contain 'password'
    if raw_token:
        payload = ctx.decode_jwt(raw_token)
        if payload is None:
            ctx.fail("Could not decode JWT payload for inspection")
        else:
            if "password" in payload:
                ctx.warn(
                    "SECURITY: JWT payload contains 'password' field. "
                    "Tokens are base64-decodable without a secret and should "
                    "never carry credentials."
                )
            else:
                ctx.ok("JWT payload does NOT contain 'password'")

    # 13-7  JWT should have an 'exp' claim
    if raw_token:
        payload = ctx.decode_jwt(raw_token)
        if payload:
            if "exp" not in payload:
                ctx.warn("JWT payload has no 'exp' claim — tokens never expire!")
            else:
                ctx.ok(f"JWT has 'exp' claim (expiry timestamp: {payload['exp']})")

    # 13-8  JWT should have an 'iat' claim (issued-at)
    if raw_token:
        payload = ctx.decode_jwt(raw_token)
        if payload:
            if "iat" not in payload:
                ctx.warn("JWT payload is missing 'iat' (issued-at) claim")
            else:
                ctx.ok(f"JWT has 'iat' claim")
