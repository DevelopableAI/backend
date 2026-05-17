from pathlib import Path
from typing import Any

from core.terraform_planner import TerraformPlanner
from core.assembler import Assembler


class TerraformAgent:
    """
    The Terraform agent — file generation only.

    Writes four HCL files into <out_dir>/terraform/:
      backend.tf, main.tf, variables.tf, outputs.tf

    This agent has zero cloud dependencies. It runs before the GitHub push
    so the terraform/ directory is version-controlled and CI can run
    `terraform validate`. State backend names are derived deterministically
    from project_name — the Deployment agent bootstraps the actual cloud
    resources (S3, GCS) during deployment, using the same names.

    No LLM calls — all templates are static Jinja2 renders.
    """

    def __init__(self, out_dir: Path, provider: str, provider_config: dict[str, Any]):
        self.provider = provider
        self.provider_config = provider_config
        self.assembler = Assembler(out_dir=out_dir, use_llm=False)

    def generate(self, spec: dict[str, Any]) -> dict[str, Any]:
        plan = TerraformPlanner().plan(spec, self.provider, self.provider_config)
        print(f"  Planned {len(plan['files'])} Terraform files for {self.provider}")
        self.assembler.assemble(spec, plan)
        return plan
