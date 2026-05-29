"""
Developable Deploy — phase 2 of the Backend Engineer pipeline.

Reads the .developable/config.json written by main.py, generates Terraform
IaC files, provisions cloud infrastructure, builds and pushes the Docker
image, and writes .github/workflows/deploy.yml.

Usage
─────
    python deploy.py --out ./output --deploy-to aws
    python deploy.py --out ./output --deploy-to gcp  --gcp-project my-project
    python deploy.py --out ./output --deploy-to heroku

All GitHub configuration is read from .developable/config.json when available.
Override any value with CLI flags.
"""

import argparse
import os
import sys
from pathlib import Path

from core.parser import PrismaParser
from core.project_config import ProjectConfig


def _build_minimal_tf_provider_config(args: argparse.Namespace, provider: str) -> dict:
    if provider == "aws":
        return {"aws_region": getattr(args, "aws_region", None) or "us-east-1"}
    if provider == "gcp":
        return {
            "gcp_project": getattr(args, "gcp_project", None) or "",
            "gcp_region": getattr(args, "gcp_region", None) or "us-central1",
        }
    return {}  # Heroku: no provider-specific template vars needed


def main():
    parser = argparse.ArgumentParser(
        description="Developable Deploy — provision cloud infrastructure for a generated API"
    )
    parser.add_argument(
        "--out", default="./output",
        help="Output directory produced by main.py (default: ./output)",
    )
    parser.add_argument(
        "--deploy-to", required=True, metavar="PROVIDER",
        choices=["aws", "heroku", "gcp"],
        help="Cloud provider to deploy to: aws | heroku | gcp",
    )

    # ── Schema fallback ────────────────────────────────────────────────────────
    parser.add_argument(
        "--schema", default=None, metavar="PATH",
        help="Path to schema.prisma (resolved from config.json when omitted)",
    )

    # ── Provider-specific flags ────────────────────────────────────────────────
    parser.add_argument(
        "--aws-region", default=None, metavar="REGION",
        help="AWS region for ECS Fargate deployment (default: us-east-1)",
    )
    parser.add_argument(
        "--heroku-app", default=None, metavar="NAME",
        help="Heroku app name (default: <project-name>)",
    )
    parser.add_argument(
        "--gcp-project", default=None, metavar="PROJECT_ID",
        help="GCP project ID for Cloud Run deployment",
    )
    parser.add_argument(
        "--gcp-region", default=None, metavar="REGION",
        help="GCP region for Cloud Run deployment (default: us-central1)",
    )

    # ── GitHub overrides (fall back to config.json values) ────────────────────
    parser.add_argument(
        "--github-token", default=None, metavar="TOKEN",
        help="GitHub Personal Access Token (fallback: GITHUB_TOKEN env var or config.json)",
    )
    parser.add_argument(
        "--github-user", default=None, metavar="USER",
        help="GitHub username or org (fallback: config.json)",
    )
    parser.add_argument(
        "--github-repo", default=None, metavar="NAME",
        help="Repository name (fallback: config.json)",
    )

    args = parser.parse_args()

    out_dir = Path(args.out)
    if not out_dir.exists():
        print(f"Error: output directory {out_dir} not found. Run main.py first.", file=sys.stderr)
        sys.exit(1)

    # ── Load config written by main.py ─────────────────────────────────────────
    cfg = ProjectConfig(out_dir).load()

    # ── Resolve schema path ────────────────────────────────────────────────────
    schema_path: Path | None = None
    if args.schema:
        schema_path = Path(args.schema)
    elif cfg.get("schema_path"):
        schema_path = Path(cfg["schema_path"])
    else:
        candidate = out_dir / "schema.prisma"
        if candidate.exists():
            schema_path = candidate

    if schema_path is None or not schema_path.exists():
        print(
            f"Error: could not locate schema.prisma. "
            f"Pass --schema <path> or ensure main.py wrote .developable/config.json.",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Resolve tests_dir ──────────────────────────────────────────────────────
    tests_dir: Path | None = None
    if cfg.get("tests_dir"):
        candidate = Path(cfg["tests_dir"])
        if candidate.is_dir():
            tests_dir = candidate
    if tests_dir is None:
        candidate = out_dir / "tests"
        if candidate.is_dir():
            tests_dir = candidate

    # ── Resolve GitHub token (for Secrets API) ────────────────────────────────
    github_token = (
        args.github_token
        or os.environ.get("GITHUB_TOKEN", "")
        or cfg.get("github_user", "")  # token is not stored in config; env var is canonical
    )
    # Re-resolve cleanly: only CLI arg or env var supply the token; config holds user/repo.
    github_token = args.github_token or os.environ.get("GITHUB_TOKEN", "") or ""

    print(f"Parsing schema from {schema_path}...")
    spec = PrismaParser().parse(schema_path)
    print(f"Found {len(spec['entities'])} entities: {', '.join(e['name'] for e in spec['entities'])}")

    # ── Terraform agent: generate IaC files ───────────────────────────────────
    from agents.terraform import TerraformAgent
    tf_config = _build_minimal_tf_provider_config(args, args.deploy_to)
    print(f"\n[Terraform] Generating IaC files for {args.deploy_to.upper()}...")
    TerraformAgent(out_dir, args.deploy_to, tf_config).generate(spec)
    print(f"  Terraform files written to {out_dir}/terraform/")

    # ── Deployment agent: provision + deploy ──────────────────────────────────
    from agents.deployment import Deployment

    print(f"\n[Deployment] Deploying to {args.deploy_to.upper()}...")
    deployer = Deployment(
        out_dir=out_dir,
        provider=args.deploy_to,
        tests_dir=tests_dir,
        github_token=github_token,
        aws_region=args.aws_region,
        heroku_app=args.heroku_app,
        gcp_project=args.gcp_project,
        gcp_region=args.gcp_region,
    )
    record = deployer.deploy(spec, {})

    print(f"\n✓ Deployment complete!")
    print(f"  Endpoint : {record['endpoint']}")
    if record.get("region"):
        print(f"  Region   : {record['region']}")
    print(f"  Provider : {record['provider']}")
    print(f"  State    : {out_dir}/.developable/state.json")


if __name__ == "__main__":
    main()
