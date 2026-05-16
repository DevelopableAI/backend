from pathlib import Path
from typing import Any

from core.terraform_backend import TerraformBackend
from core.terraform_planner import TerraformPlanner
from core.assembler import Assembler


class TerraformAgent:
    """
    The Terraform agent.

    Responsible for:
    1. Bootstrapping remote state backend infrastructure (S3+DynamoDB / GCS / TFC workspace).
    2. Planning four HCL files (backend.tf, main.tf, variables.tf, outputs.tf).
    3. Rendering them into <out_dir>/terraform/ via the Assembler.

    Follows the same Planner → Assembler pattern as Developer and VersionControl.
    No LLM calls — all templates are static Jinja2 renders.
    """

    def __init__(self, out_dir: Path, provider: str, provider_config: dict[str, Any]):
        self.provider = provider
        self.provider_config = provider_config
        self.assembler = Assembler(out_dir=out_dir, use_llm=False)

    def generate(self, spec: dict[str, Any]) -> dict[str, Any]:
        planner = TerraformPlanner()
        project_name = planner._derive_project_name(spec)

        print(f"  Bootstrapping {self.provider.upper()} Terraform state backend...")
        backend_config = TerraformBackend().bootstrap(
            self.provider, self.provider_config, project_name
        )

        plan = planner.plan(spec, self.provider, self.provider_config, backend_config)
        print(f"  Planned {len(plan['files'])} Terraform files for {self.provider}")
        self.assembler.assemble(spec, plan)
        return plan
