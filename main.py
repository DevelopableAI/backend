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
    """
    Build the env dict written to .env in the output directory.

    Schema-referenced vars (e.g. DATABASE_URL) are written as empty placeholders
    so the user can fill them in. PORT and NODE_ENV always get sensible defaults.
    No interactive prompting — the CLI must be safe to run non-interactively.
    """
    values: dict[str, str] = {var: "" for var in env_vars}
    values.setdefault("PORT", "3000")
    values.setdefault("NODE_ENV", "development")
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

    project_name = getattr(args, "project_name", None) or ""
    if not project_name:
        project_name = input("  Project name (used in repo description): ").strip()
        if not project_name:
            print("Error: project name is required for the repo description.", file=sys.stderr)
            sys.exit(1)

    if getattr(args, "private", False):
        private = True
    else:
        vis = input("  Visibility — public or private? [public]: ").strip().lower()
        private = vis == "private"

    return {"token": token, "user": user, "repo": repo, "private": private, "project_name": project_name}


def collect_terraform_config(args: argparse.Namespace, provider: str, project_name: str) -> dict:
    """
    Interactively collect Terraform state backend configuration.

    Detects existing cloud credentials first; only prompts for values that
    can't be resolved automatically. Terraform-specific values (bucket names,
    workspace names) are always prompted with sensible defaults.
    """
    print(f"\nTerraform Agent — {provider.upper()} configuration")
    print("(Defaults shown in brackets; press Enter to accept)\n")

    if provider == "aws":
        return _collect_terraform_config_aws(args, project_name)
    if provider == "gcp":
        return _collect_terraform_config_gcp(args, project_name)
    if provider == "heroku":
        return _collect_terraform_config_heroku(args, project_name)
    print(f"Error: unsupported Terraform provider '{provider}'", file=sys.stderr)
    sys.exit(1)


def _collect_terraform_config_aws(args: argparse.Namespace, project_name: str) -> dict:
    import boto3

    session = boto3.Session()
    creds = session.get_credentials()
    if creds:
        resolved = creds.resolve()
        access_key = resolved.access_key
        secret_key = resolved.secret_key
        session_token = resolved.token
        print("  AWS credentials detected from environment / ~/.aws/credentials")
    else:
        access_key = input("  AWS Access Key ID: ").strip()
        secret_key = getpass.getpass("  AWS Secret Access Key: ")
        session_token = None

    default_region = getattr(args, "aws_region", None) or "us-east-1"
    region = input(f"  AWS region [{default_region}]: ").strip() or default_region

    default_bucket = f"{project_name}-tf-state"
    state_bucket = input(f"  S3 state bucket name [{default_bucket}]: ").strip() or default_bucket

    default_table = f"{project_name}-tf-lock"
    dynamodb_table = input(f"  DynamoDB lock table name [{default_table}]: ").strip() or default_table

    return {
        "access_key": access_key,
        "secret_key": secret_key,
        "session_token": session_token,
        "aws_region": region,
        "state_bucket": state_bucket,
        "dynamodb_table": dynamodb_table,
    }


def _collect_terraform_config_gcp(args: argparse.Namespace, project_name: str) -> dict:
    import os

    credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    detected_project = None

    try:
        import google.auth
        _, detected_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        print("  GCP credentials detected via Application Default Credentials")
    except Exception:
        pass

    default_project = getattr(args, "gcp_project", None) or detected_project or ""
    if default_project:
        gcp_project = input(f"  GCP project ID [{default_project}]: ").strip() or default_project
    else:
        gcp_project = input("  GCP project ID: ").strip()
        if not gcp_project:
            print("Error: GCP project ID is required.", file=sys.stderr)
            sys.exit(1)

    default_region = getattr(args, "gcp_region", None) or "us-central1"
    gcp_region = input(f"  GCP region [{default_region}]: ").strip() or default_region

    default_bucket = f"{project_name}-tf-state"
    state_bucket = input(f"  GCS state bucket name [{default_bucket}]: ").strip() or default_bucket

    return {
        "gcp_project": gcp_project,
        "gcp_region": gcp_region,
        "state_bucket": state_bucket,
        "credentials_file": credentials_file,
    }


def _collect_terraform_config_heroku(args: argparse.Namespace, project_name: str) -> dict:
    import os

    heroku_api_key = os.getenv("HEROKU_API_KEY") or ""
    if heroku_api_key:
        print("  Heroku API key detected from HEROKU_API_KEY environment variable")
    else:
        heroku_api_key = getpass.getpass("  Heroku API Key: ").strip()
        if not heroku_api_key:
            print("Error: Heroku API key is required.", file=sys.stderr)
            sys.exit(1)

    heroku_email = input("  Heroku account email: ").strip()
    if not heroku_email:
        print("Error: Heroku account email is required.", file=sys.stderr)
        sys.exit(1)

    tfc_token = getpass.getpass("  Terraform Cloud API token: ").strip()
    if not tfc_token:
        print("Error: Terraform Cloud API token is required.", file=sys.stderr)
        sys.exit(1)

    tfc_org = input("  Terraform Cloud organization: ").strip()
    if not tfc_org:
        print("Error: Terraform Cloud organization is required.", file=sys.stderr)
        sys.exit(1)

    tfc_workspace = input(f"  Terraform Cloud workspace [{project_name}]: ").strip() or project_name

    return {
        "heroku_api_key": heroku_api_key,
        "heroku_email": heroku_email,
        "tfc_token": tfc_token,
        "tfc_organization": tfc_org,
        "tfc_workspace": tfc_workspace,
    }


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
        "--project-name", default=None, metavar="NAME",
        help="Project name used in the GitHub repo description (e.g. 'My Blog')",
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
        "--terraform", action="store_true",
        help=(
            "Generate Terraform IaC files in <out>/terraform/ for the provider chosen "
            "with --deploy-to. Bootstraps remote state backend automatically."
        ),
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
    # When --github is used, tests MUST live inside out_dir/tests so that
    # `git add .` includes them and CI's hashFiles('tests/run_all.py') finds
    # them. An explicit --tests-out outside out_dir is silently ignored in
    # favour of out_dir/tests when publishing to GitHub.
    tests_out = args.tests_out
    if args.github:
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

    # ── Version Control agent ──────────────────────────────────────────────────
    # Infra files (Dockerfile, docker-compose, CI, .gitignore) are always
    # generated so every output directory is deployment-ready regardless of
    # whether --github is passed.
    from agents.version_control import VersionControl

    print(f"\n[Version Control] Generating infrastructure files...")
    vc = VersionControl(out_dir=out_dir)
    vc.generate_infra(spec)

    if args.github:
        gh = collect_github_config(args, spec)
        vc.github_token = gh["token"]
        vc.github_user = gh["user"]
        vc.repo_name = gh["repo"]
        vc.private = gh["private"]
        vc.project_name = gh["project_name"]

        print(f"\n[Version Control] Publishing to GitHub...")
        repo_url = vc.publish(spec, api_plan)
        print(f"\nRepository published: {repo_url}")
        print("GitHub Actions CI will run automatically on every push.")
        print("For local development with pgAdmin:")
        print(f"  cd {out_dir}")
        print("  cp .env.example .env  # fill in your values")
        print("  docker compose up")

    # ── Terraform agent: generate IaC files ───────────────────────────────────
    if args.terraform:
        if not args.deploy_to:
            print("Error: --terraform requires --deploy-to <provider>", file=sys.stderr)
            sys.exit(1)
        from agents.terraform import TerraformAgent

        tf_project_name = spec["entities"][0]["name_lower"] + "-api"
        print(f"\n[Terraform] Generating IaC for {args.deploy_to.upper()}...")
        tf_config = collect_terraform_config(args, args.deploy_to, tf_project_name)
        TerraformAgent(out_dir, args.deploy_to, tf_config).generate(spec)
        print(f"\n  Terraform files written to {out_dir}/terraform/")
        print(f"  Next steps:")
        print(f"    cd {out_dir}/terraform")
        print(f"    terraform init")
        print(f"    terraform plan -var='db_password=<your-password>'")

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
