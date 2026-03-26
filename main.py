import argparse
import getpass
import os
import sys
from pathlib import Path

from core.parser import PrismaParser
from core.rules_parser import BusinessRulesParser
from agents.developer import Developer
from agents.tester import Tester
from generators.llm import get_session_summary, reset_session


def collect_env_values(env_vars: list[str]) -> dict[str, str]:
    """Prompt the user for values of env variables referenced in the schema."""
    values: dict[str, str] = {}

    if env_vars:
        print("\nThe schema references environment variables needed for the generated API.")
        print("Please provide a value for each (press Enter to skip):\n")

        for var in env_vars:
            value = input(f"  {var}: ").strip()
            values[var] = value

    # Add standard runtime defaults if not already covered by the schema
    if "PORT" not in values:
        values["PORT"] = "3000"
    if "NODE_ENV" not in values:
        values["NODE_ENV"] = "development"

    return values


def collect_github_config(args: argparse.Namespace, spec: dict) -> dict:
    """
    Interactively collect any missing GitHub configuration.

    Falls back to env vars (GITHUB_TOKEN, GITHUB_USER), then prompts the user
    for anything still missing — mirrors the collect_env_values() pattern.
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

    if getattr(args, "private", False):
        private = True
    else:
        vis = input("  Visibility — public or private? [public]: ").strip().lower()
        private = vis == "private"

    return {"token": token, "user": user, "repo": repo, "private": private}


def main():
    parser = argparse.ArgumentParser(
        description="Developable Backend Engineer — generates production-ready backend services from a Prisma schema"
    )
    parser.add_argument("schema", help="Path to schema.prisma")
    parser.add_argument(
        "--out", default="./output",
        help="Output directory for the generated API (default: ./output)",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip LLM calls, use placeholder logic only",
    )
    parser.add_argument(
        "--rules", default=None,
        help="Path to a schema.rules.yaml file with business logic constraints",
    )
    parser.add_argument(
        "--tests-out", default=None, metavar="DIR",
        help="If set, generate integration test suite into this directory",
    )

    # ── Version Control agent flags ───────────────────────────────────────────
    parser.add_argument(
        "--github", action="store_true",
        help="Publish the generated project to a new GitHub repository",
    )
    parser.add_argument(
        "--github-token", default=None, metavar="TOKEN",
        help="GitHub Personal Access Token (fallback: GITHUB_TOKEN env var)",
    )
    parser.add_argument(
        "--github-user", default=None, metavar="USER",
        help="GitHub username or org to create the repo under (fallback: GITHUB_USER env var)",
    )
    parser.add_argument(
        "--github-repo", default=None, metavar="NAME",
        help="Repository name to create (default: <first-entity>-api)",
    )
    parser.add_argument(
        "--private", action="store_true",
        help="Create the GitHub repository as private",
    )
    parser.add_argument(
        "--force", action="store_true",
        help=(
            "Overwrite all files, including ones you have modified since the last commit. "
            "By default, re-runs skip files that differ from HEAD in the output git repo."
        ),
    )

    # ── Deployment agent flags ─────────────────────────────────────────────────
    parser.add_argument(
        "--deploy-to", default=None, metavar="PROVIDER",
        choices=["aws", "heroku", "gcp"],
        help="Deploy the generated API to a cloud provider: aws | heroku | gcp",
    )
    parser.add_argument(
        "--aws-region", default=None, metavar="REGION",
        help="AWS region for ECS Fargate deployment (default: us-east-1 or from ~/.aws/config)",
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

    args = parser.parse_args()

    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"Error: {schema_path} not found")
        sys.exit(1)

    out_dir = Path(args.out)

    print(f"Parsing {schema_path.name}...")
    spec = PrismaParser().parse(schema_path)
    print(f"Found {len(spec['entities'])} entities: {', '.join(e['name'] for e in spec['entities'])}")

    if args.rules:
        rules_path = Path(args.rules)
        if not rules_path.exists():
            print(f"Error: rules file {rules_path} not found")
            sys.exit(1)
        print(f"Loading business rules from {rules_path.name}...")
        BusinessRulesParser().merge(spec, rules_path)

    env_values = collect_env_values(spec.get("env_vars", []))

    # ── Developer agent: generate the Express API ─────────────────────────────
    print(f"\n[Developer] Generating Express API into {out_dir}/...")
    developer = Developer(out_dir=out_dir, use_llm=not args.no_llm, force=args.force)
    api_plan = developer.generate(spec, env_values=env_values)

    print(f"\nDone. Your project is at {out_dir}/")
    print("Next steps:")
    print("  cd", out_dir)
    print("  npm install")
    print("  npx prisma migrate dev")
    print("  npm run dev")

    # ── Tester agent: generate the integration test suite ─────────────────────
    # When --github is used without --tests-out, put tests inside the output
    # directory so they are included in the git repository.
    tests_out = args.tests_out
    if args.github and not tests_out:
        tests_out = str(out_dir / "tests")

    if tests_out:
        tests_dir = Path(tests_out)
        print(f"\n[Tester] Generating integration test suite into {tests_dir}/...")
        tester = Tester(tests_dir=tests_dir, use_llm=not args.no_llm, force=args.force)
        tester.generate(spec, api_plan)

        print(f"\nTest suite at {tests_dir}/")
        print("Run tests:")
        print(f"  pip install requests")
        print(f"  python {tests_dir}/run_all.py [API_BASE_URL]")

    # ── Version Control agent: publish to GitHub ──────────────────────────────
    if args.github:
        from agents.version_control import VersionControl

        gh = collect_github_config(args, spec)
        print(f"\n[Version Control] Publishing to GitHub...")
        vc = VersionControl(
            out_dir=out_dir,
            github_token=gh["token"],
            github_user=gh["user"],
            repo_name=gh["repo"],
            private=gh["private"],
        )
        repo_url = vc.publish(spec, api_plan)
        print(f"\nRepository published: {repo_url}")
        print("GitHub Actions CI will run automatically on every push.")
        print("For local development with pgAdmin:")
        print(f"  cd {out_dir}")
        print("  cp .env.example .env  # fill in your values")
        print("  docker compose up")

    # ── Deployment agent: deploy to cloud ─────────────────────────────────────
    if args.deploy_to:
        from agents.deployment import Deployment

        print(f"\n[Deployment] Deploying to {args.deploy_to.upper()}...")
        deployer = Deployment(
            out_dir=out_dir,
            provider=args.deploy_to,
            tests_dir=Path(tests_out) if tests_out else None,
            aws_region=args.aws_region,
            heroku_app=args.heroku_app,
            gcp_project=args.gcp_project,
            gcp_region=args.gcp_region,
        )
        record = deployer.deploy(spec, api_plan)
        print(f"\n✓ Deployment complete!")
        print(f"  Endpoint : {record['endpoint']}")
        if record.get("region"):
            print(f"  Region   : {record['region']}")
        print(f"  Provider : {record['provider']}")
        print(f"  State    : {out_dir}/.developable/state.json")

    # ── LLM usage summary ──────────────────────────────────────────────────────
    if not args.no_llm:
        _print_usage_summary()


def _print_usage_summary():
    summary = get_session_summary()
    if not summary:
        return

    calls = summary["calls"]
    hits = summary["cache_hits"]
    api_calls = calls - hits
    total_in = summary["input_tokens"]
    total_out = summary["output_tokens"]
    cache_write = summary["cache_write_tokens"]
    cache_read = summary["cache_read_tokens"]
    cost = summary["estimated_cost_usd"]

    print("\n── LLM usage ────────────────────────────────────────────")
    print(f"  API calls       : {api_calls}  (+ {hits} response cache hits, 0 cost)")
    print(f"  Input tokens    : {total_in:,}  (uncached)")
    print(f"  Cache write     : {cache_write:,}  tokens")
    print(f"  Cache read      : {cache_read:,}  tokens  (billed at 10% rate)")
    print(f"  Output tokens   : {total_out:,}")
    print(f"  Estimated cost  : ${cost:.4f}")
    print("─────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
