from pathlib import Path
from typing import Any

from core.fastify_planner import FastifyPlanner
from core.assembler import Assembler


class FastifyDeveloper:
    """
    Developer agent variant that generates a Fastify+TypeScript API.
    Uses FastifyPlanner for the file manifest; Assembler is reused unchanged.
    """

    def __init__(self, out_dir: Path, use_llm: bool = True, force: bool = False):
        self.out_dir = out_dir
        self.assembler = Assembler(out_dir=out_dir, use_llm=use_llm, force=force)

    def generate(
        self, spec: dict[str, Any], env_values: dict[str, str] | None = None
    ) -> dict[str, Any]:
        plan = FastifyPlanner().plan(spec)
        print(f"  Planned {len(plan['files'])} files across {len(spec['entities'])} entities")

        self.assembler.assemble(spec, plan, env_values=env_values)
        return plan
