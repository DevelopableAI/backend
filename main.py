import argparse
import sys
from pathlib import Path

from core.parser import PrismaParser
from core.planner import Planner
from core.assembler import Assembler


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
    parser = argparse.ArgumentParser(description="Generate an Express + TypeScript API from a schema.prisma file")
    parser.add_argument("schema", help="Path to schema.prisma")
    parser.add_argument("--out", default="./output", help="Output directory (default: ./output)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM calls, use placeholder logic only")
    args = parser.parse_args()

    schema_path = Path(args.schema)
    if not schema_path.exists():
        print(f"Error: {schema_path} not found")
        sys.exit(1)

    out_dir = Path(args.out)

    print(f"Parsing {schema_path.name}...")
    spec = PrismaParser().parse(schema_path)
    print(f"Found {len(spec['entities'])} entities: {', '.join(e['name'] for e in spec['entities'])}")

    env_values = collect_env_values(spec.get("env_vars", []))

    print("\nPlanning file structure...")
    plan = Planner().plan(spec)
    print(f"Planned {len(plan['files'])} files across {len(spec['entities'])} entities")

    print(f"Generating into {out_dir}/...")
    assembler = Assembler(out_dir=out_dir, use_llm=not args.no_llm)
    assembler.assemble(spec, plan, env_values=env_values)

    print(f"\nDone. Your project is at {out_dir}/")
    print("Next steps:")
    print("  cd", out_dir)
    print("  npm install")
    print("  npx prisma migrate dev")
    print("  npm run dev")


if __name__ == "__main__":
    main()