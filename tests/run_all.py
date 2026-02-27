#!/usr/bin/env python3
"""
run_all.py — Blog API test suite runner.

Executes all 18 test sections (00–17) sequentially, sharing a single
TestContext so that state (auth tokens, created IDs) flows naturally
from each section to the next.

Usage:
  python run_all.py [base_url]

  base_url defaults to $API_URL env-var or http://localhost:3000

Legend:
  🚀  Sending API request
  ✅  Test passed
  ❌  Test failed
  ⛔️  Auth / authorization issue
  ⚠️  Security warning
"""

import sys
import os

# Allow imports from the tests/ directory itself
sys.path.insert(0, os.path.dirname(__file__))

from helpers import TestContext, section

import test_00_health
import test_01_register
import test_02_login
import test_03_users_get
import test_04_users_write
import test_05_posts_seed_get
import test_06_posts_write
import test_07_comments_seed_get
import test_08_comments_write
import test_09_nested_users_get
import test_10_nested_users_posts
import test_11_nested_users_comments
import test_12_nested_posts_comments
import test_13_token_security
import test_14_input_validation
import test_15_response_structure
import test_16_security_audit
import test_17_cleanup


SECTIONS = [
    test_00_health,
    test_01_register,
    test_02_login,
    test_03_users_get,
    test_04_users_write,
    test_05_posts_seed_get,
    test_06_posts_write,
    test_07_comments_seed_get,
    test_08_comments_write,
    test_09_nested_users_get,
    test_10_nested_users_posts,
    test_11_nested_users_comments,
    test_12_nested_posts_comments,
    test_13_token_security,
    test_14_input_validation,
    test_15_response_structure,
    test_16_security_audit,
    test_17_cleanup,
]


def main() -> None:
    base_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.getenv("API_URL", "http://localhost:3000")
    )

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║        Developable — Comprehensive Test Suite                  ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  Target : {base_url}")
    print(f"  Sections: {len(SECTIONS)}")

    ctx = TestContext(base_url)

    for module in SECTIONS:
        try:
            module.run(ctx)
        except SystemExit:
            # Propagate connection failures that call sys.exit()
            raise
        except Exception as exc:
            ctx.fail(f"Unhandled exception in {module.__name__}: {exc}")

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    total = ctx.pass_count + ctx.fail_count
    print(f"\n{'═' * 64}")
    print(f"  FINAL RESULTS")
    print(f"{'═' * 64}")
    print(f"  ✅  Passed   : {ctx.pass_count}")
    print(f"  ❌  Failed   : {ctx.fail_count}")
    print(f"  ⚠️   Warnings : {ctx.warn_count}")
    print(f"  Total tests  : {total}")
    print(f"{'═' * 64}")

    if ctx.fail_count > 0:
        print(f"\n  {ctx.fail_count} test(s) FAILED. Review the ❌ / ⛔️ entries above.")
        sys.exit(1)
    else:
        print(f"\n  All {total} tests passed! 🎉")
        if ctx.warn_count > 0:
            print(f"  Review the {ctx.warn_count} ⚠️  security warning(s) above.")


if __name__ == "__main__":
    main()
