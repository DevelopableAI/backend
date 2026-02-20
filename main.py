import argparse
import sys
from pathlib import Path

from core.parser import PrismaParser
from core.planner import Planner
from core.assembler import Assembler


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

    print("Planning file structure...")
    plan = Planner().plan(spec)
    print(f"Planned {len(plan['files'])} files across {len(spec['entities'])} entities")

    print(f"Generating into {out_dir}/...")
    assembler = Assembler(out_dir=out_dir, use_llm=not args.no_llm)
    assembler.assemble(spec, plan)

    print(f"\nDone. Your project is at {out_dir}/")
    print("Next steps:")
    print("  cd", out_dir)
    print("  npm install")
    print("  npx prisma migrate dev")
    print("  npm run dev")


if __name__ == "__main__":
    main()