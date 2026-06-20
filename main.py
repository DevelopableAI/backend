import argparse
import sys
from pathlib import Path

from core.parser import PrismaParser
from core.rules_parser import BusinessRulesParser
from core.project_config import ProjectConfig
from core.cli_helpers import collect_env_values, collect_github_config, print_llm_usage_summary
from agents.developer import Developer
from agents.tester import Tester


def main():
    parser = argparse.ArgumentParser(
        description="Developable Backend Engineer — generates production-ready backend services from a Prisma schema"
    )
    parser.add_argument("schema", help="Path to schema.prisma")
    parser.add_argument("--out", default="./output",
        help="Output directory for the generated API (default: ./output)")
    parser.add_argument("--no-llm", action="store_true",
        help="Skip LLM calls, use placeholder logic only")
    parser.add_argument("--rules", default=None,
        help="Path to a schema.rules.yaml file with business logic constraints")
    parser.add_argument("--tests-out", default=None, metavar="DIR",
        help="If set, generate integration test suite into this directory")
    parser.add_argument("--github", action="store_true",
        help="Publish the generated project to a new GitHub repository")
    parser.add_argument("--github-token", default=None, metavar="TOKEN",
        help="GitHub Personal Access Token (fallback: GITHUB_TOKEN env var)")
    parser.add_argument("--github-user", default=None, metavar="USER",
        help="GitHub username or org to create the repo under (fallback: GITHUB_USER env var)")
    parser.add_argument("--github-repo", default=None, metavar="NAME",
        help="Repository name to create (default: <first-entity>-api)")
    parser.add_argument("--project-name", default=None, metavar="NAME",
        help="Project name used in the GitHub repo description")
    parser.add_argument("--private", action="store_true",
        help="Create the GitHub repository as private")
    parser.add_argument("--force", action="store_true",
        help="Overwrite all files including ones modified since the last commit")

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

    # ── Developer agent ────────────────────────────────────────────────────────
    print(f"\n[Developer] Generating Express API into {out_dir}/...")
    developer = Developer(out_dir=out_dir, use_llm=not args.no_llm, force=args.force)
    api_plan = developer.generate(spec, env_values=env_values)

    print(f"\nDone. Your project is at {out_dir}/")
    print("Next steps:")
    print(f"  cd {out_dir} && npm install && npx prisma migrate dev && npm run dev")

    # ── Tester agent ───────────────────────────────────────────────────────────
    # When --github is used, tests must live inside out_dir/tests so CI finds them.
    tests_out = str(out_dir / "tests") if args.github else args.tests_out
    if tests_out:
        tests_dir = Path(tests_out)
        print(f"\n[Tester] Generating integration test suite into {tests_dir}/...")
        Tester(tests_dir=tests_dir, use_llm=not args.no_llm, force=args.force).generate(spec, api_plan)
        print(f"\nTest suite at {tests_dir}/")
        print(f"  pip install requests && python {tests_dir}/run_all.py [API_BASE_URL]")

    # ── Version Control agent ──────────────────────────────────────────────────
    from agents.version_control import VersionControl
    print(f"\n[Version Control] Generating infrastructure files...")
    vc = VersionControl(out_dir=out_dir)
    vc.generate_infra(spec)

    github_info: dict | None = None
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
        print(f"  cd {out_dir} && cp .env.example .env && docker compose up")

        github_info = {"user": gh["user"], "repo": gh["repo"], "repo_url": repo_url}

    # ── Persist generation metadata ────────────────────────────────────────────
    ProjectConfig.save(
        out_dir=out_dir,
        schema_path=schema_path,
        spec=spec,
        tests_dir=Path(tests_out) if tests_out else None,
        github_info=github_info,
    )
    print(f"\nGeneration config saved to {out_dir}/.developable/config.json")
    print(f"  python deploy.py --out {out_dir} --deploy-to aws|gcp|heroku")

    if not args.no_llm:
        print_llm_usage_summary()


if __name__ == "__main__":
    main()
