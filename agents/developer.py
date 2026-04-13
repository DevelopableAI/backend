from pathlib import Path
from typing import Any

from core.planner import Planner
from core.assembler import Assembler


class Developer:
    """
    The Developer agent.

    Responsible for generating a production-ready Express + TypeScript API from a parsed
    Prisma spec. Uses Planner to produce a file manifest and Assembler to render templates
    and fill LLM sections.

    Returns the file plan so downstream agents (e.g. Tester) can inspect what was generated.
    """

    def __init__(self, out_dir: Path, use_llm: bool = True, force: bool = False):
        self.out_dir = out_dir
        self.assembler = Assembler(out_dir=out_dir, use_llm=use_llm, force=force)

    def generate(
        self, spec: dict[str, Any], env_values: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Plans and assembles the Express API project.

        Args:
            spec: Parsed Prisma spec produced by PrismaParser.
            env_values: Environment variable values to write into .env.

        Returns:
            The file plan dict so it can be passed to the Tester agent.
        """
        plan = Planner().plan(spec)
        print(f"  Planned {len(plan['files'])} files across {len(spec['entities'])} entities")

        self.assembler.assemble(spec, plan, env_values=env_values)
        return plan
