"""
Shared CLI helper functions used by main.py and deploy.py.
"""

import getpass
import sys
from typing import Any


def collect_env_values(env_vars: list[str]) -> dict[str, str]:
    """
    Build the env dict written to .env in the output directory.

    Schema-referenced vars are written as empty placeholders; PORT and NODE_ENV
    always get sensible defaults. No interactive prompting.
    """
    values: dict[str, str] = {var: "" for var in env_vars}
    values.setdefault("PORT", "3000")
    values.setdefault("NODE_ENV", "development")
    return values


def collect_github_config(args: Any, spec: dict) -> dict:
    """
    Interactively collect any missing GitHub configuration.

    Falls back to env vars (GITHUB_TOKEN, GITHUB_USER), then prompts.
    """
    from config import GITHUB_TOKEN, GITHUB_USER

    default_repo = spec["entities"][0]["name_lower"] + "-api"
    token = getattr(args, "github_token", None) or GITHUB_TOKEN
    user = getattr(args, "github_user", None) or GITHUB_USER

    print("\nVersion Control Agent — GitHub configuration")
    print("(Defaults shown in brackets; press Enter to accept)\n")

    if not token:
        token = getpass.getpass("  GitHub Personal Access Token: ").strip()
        if not token:
            print("Error: a GitHub Personal Access Token is required.", file=sys.stderr)
            sys.exit(1)

    if not user:
        user = input("  GitHub username or org: ").strip()
        if not user:
            print("Error: GitHub username/org is required.", file=sys.stderr)
            sys.exit(1)

    repo = getattr(args, "github_repo", None) or ""
    if not repo:
        repo = input(f"  Repository name [{default_repo}]: ").strip() or default_repo

    project_name = getattr(args, "project_name", None) or spec["entities"][0]["name"] + " API"
    return {
        "token": token,
        "user": user,
        "repo": repo,
        "private": getattr(args, "private", False),
        "project_name": project_name,
    }


def print_llm_usage_summary() -> None:
    from generators.llm import get_session_summary
    summary = get_session_summary()
    if not summary:
        return
    calls = summary["calls"]
    hits = summary["cache_hits"]
    print("\n── LLM usage ────────────────────────────────────────────")
    print(f"  API calls       : {calls - hits}  (+ {hits} response cache hits, 0 cost)")
    print(f"  Input tokens    : {summary['input_tokens']:,}  (uncached)")
    print(f"  Cache write     : {summary['cache_write_tokens']:,}  tokens")
    print(f"  Cache read      : {summary['cache_read_tokens']:,}  tokens  (billed at 10% rate)")
    print(f"  Output tokens   : {summary['output_tokens']:,}")
    print(f"  Estimated cost  : ${summary['estimated_cost_usd']:.4f}")
    print("─────────────────────────────────────────────────────────")


def print_deploy_eta(provider: str) -> None:
    _eta_steps: dict[str, tuple[str, list[tuple[str, str]]]] = {
        "aws": (
            "AWS deployment — estimated 20–25 min total",
            [
                ("Validate AWS credentials",                         "~5s"),
                ("Bootstrap S3 state bucket + DynamoDB lock table",  "~30s"),
                ("Provision 15 AWS resources via boto3",             "~10–15 min  ← longest step"),
                ("Build Docker image",                               "~3 min"),
                ("Push image to ECR",                                "~1 min"),
                ("Terraform import + reconciliation apply",          "~3 min"),
                ("Run Prisma migration (ECS task inside VPC)",       "~2 min"),
                ("Push deploy.yml + set GitHub Actions secrets",     "~30s"),
                ("Remote smoke tests",                               "~1 min"),
            ],
        ),
        "gcp": (
            "GCP deployment — estimated 15–20 min total",
            [
                ("Validate GCP credentials",                               "~5s"),
                ("Bootstrap GCS state bucket",                             "~30s"),
                ("Provision Cloud SQL + Artifact Registry via Python SDK", "~8–10 min  ← longest step"),
                ("Apply Prisma schema to Cloud SQL",                       "~1 min"),
                ("Build Docker image",                                     "~3 min"),
                ("Push image to Artifact Registry",                        "~1 min"),
                ("Deploy Cloud Run service via SDK",                       "~1 min"),
                ("Terraform import + reconciliation apply",                "~2 min"),
                ("Push deploy.yml + set GitHub Actions secrets",           "~30s"),
                ("Remote smoke tests",                                     "~1 min"),
            ],
        ),
        "heroku": (
            "Heroku deployment — estimated 6–10 min total",
            [
                ("Validate Heroku credentials",                  "~5s"),
                ("Terraform apply — app + Postgres addon",       "~2 min"),
                ("Apply Prisma schema to Heroku Postgres",       "~1 min"),
                ("Build Docker image",                           "~3 min"),
                ("Push image + release container",               "~1 min"),
                ("Push deploy.yml + set GitHub Actions secrets", "~30s"),
                ("Remote smoke tests",                           "~1 min"),
            ],
        ),
    }
    if provider not in _eta_steps:
        return
    title, steps = _eta_steps[provider]
    sep = "─" * 58
    print(f"\n{sep}")
    print(f"  {title}")
    print(sep)
    for i, (label, eta) in enumerate(steps, 1):
        print(f"  {i:>2}. {label:<50} {eta}")
    print(f"{sep}\n")
