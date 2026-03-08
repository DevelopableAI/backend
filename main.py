import argparse
import sys
from pathlib import Path

from core.parser import PrismaParser
from core.rules_parser import BusinessRulesParser
from agents.developer import Developer
from agents.tester import Tester


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


def main():
    parser = argparse.ArgumentParser(description="Developable Backend Engineer — generates production-ready backend services from a Prisma schema")
    parser.add_argument("schema", help="Path to schema.prisma")
    parser.add_argument("--out", default="./output", help="Output directory for the generated API (default: ./output)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM calls, use placeholder logic only")
    parser.add_argument("--rules", default=None, help="Path to a schema.rules.yaml file with business logic constraints")
    parser.add_argument(
        "--tests-out",
        default=None,
        metavar="DIR",
        help="If set, generate integration test suite into this directory",
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
    developer = Developer(out_dir=out_dir, use_llm=not args.no_llm)
    api_plan = developer.generate(spec, env_values=env_values)

    print(f"\nDone. Your project is at {out_dir}/")
    print("Next steps:")
    print("  cd", out_dir)
    print("  npm install")
    print("  npx prisma migrate dev")
    print("  npm run dev")

    # ── Tester agent: generate the integration test suite ─────────────────────
    if args.tests_out:
        tests_dir = Path(args.tests_out)
        print(f"\n[Tester] Generating integration test suite into {tests_dir}/...")
        tester = Tester(tests_dir=tests_dir, use_llm=not args.no_llm)
        tester.generate(spec, api_plan)

        print(f"\nTest suite at {tests_dir}/")
        print("Run tests:")
        print(f"  pip install requests")
        print(f"  python {tests_dir}/run_all.py [API_BASE_URL]")


if __name__ == "__main__":
    main()
