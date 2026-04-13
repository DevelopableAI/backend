"""Section 0 — Health check."""

from helpers import TestContext, section


def run(ctx: TestContext) -> None:
    section("0 · HEALTH CHECK")

    resp = ctx.req("GET", "/health")
    if ctx.assert_status(resp, 200, "GET /health"):
        data = ctx.safe_json(resp)
        if data.get("status") == "ok":
            ctx.ok("Health check body contains status=ok")
        else:
            ctx.fail(f"Health check body unexpected: {data}")
