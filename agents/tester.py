from pathlib import Path
from typing import Any

from core.test_planner import TestPlanner
from core.assembler import Assembler


class Tester:
    """
    The Tester agent.

    Responsible for generating a Python integration test suite for a generated Express API.
    Uses TestPlanner to produce a test file manifest and Assembler to render test templates
    and fill LLM sections (seed data, write validation tests).

    Requires the API file plan produced by the Developer agent so it can map entity routes
    and nested route descriptors into the correct test modules.
    """

    def __init__(self, tests_dir: Path, use_llm: bool = True):
        self.tests_dir = tests_dir
        self.assembler = Assembler(out_dir=tests_dir, use_llm=use_llm)

    def generate(self, spec: dict[str, Any], api_plan: dict[str, Any]) -> dict[str, Any]:
        """
        Plans and assembles the Python integration test suite.

        Args:
            spec: Parsed Prisma spec produced by PrismaParser.
            api_plan: File plan returned by Developer.generate(), used to extract
                      per-entity route contexts for test generation.

        Returns:
            The test file plan dict.
        """
        test_plan = TestPlanner().plan(spec, api_plan)
        print(f"  Planned {len(test_plan['files'])} test files")

        self.assembler.assemble(spec, test_plan, env_values=None)
        return test_plan
