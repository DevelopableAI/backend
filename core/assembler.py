import subprocess
from pathlib import Path
from typing import Any

from generators.template import TemplateGenerator
from generators.llm import LLMGenerator


class Assembler:
    def __init__(self, out_dir: Path, use_llm: bool = True):
        self.out_dir = out_dir
        self.use_llm = use_llm
        self.template_gen = TemplateGenerator()
        self.llm_gen = LLMGenerator() if use_llm else None

    def assemble(self, spec: dict[str, Any], plan: dict[str, Any], env_values: dict[str, str] | None = None):
        self.out_dir.mkdir(parents=True, exist_ok=True)

        for file_plan in plan["files"]:
            path = self.out_dir / file_plan["path"]
            path.parent.mkdir(parents=True, exist_ok=True)

            content = self._generate_file(file_plan, spec)
            path.write_text(content)
            print(f"  wrote {file_plan['path']}")

        self._copy_schema(spec)

        if env_values:
            self._write_env_file(env_values)

        self._run_formatter()

    def _generate_file(self, file_plan: dict, spec: dict) -> str:
        # render the template first (always)
        content = self.template_gen.render(
            template_name=file_plan["template"],
            context=file_plan["context"],
        )

        # if LLM is needed and enabled, fill the LLM sections
        if file_plan.get("needs_llm") and self.use_llm:
            entity = file_plan["context"].get("entity")
            task = file_plan.get("llm_task", "")
            content = self.llm_gen.fill(
                content=content,
                task=task,
                entity=entity,
                spec=spec,
            )

        return content

    def _run_formatter(self):
        prettier = self.out_dir / "node_modules" / ".bin" / "prettier"
        if not prettier.exists():
            return

        try:
            subprocess.run(
                [str(prettier), "--write", str(self.out_dir / "src")],
                check=True,
                capture_output=True,
            )
            print("Formatted with prettier")
        except subprocess.CalledProcessError:
            print("Prettier not available, skipping formatting")
    
    def _write_env_file(self, env_values: dict[str, str]):
        env_path = self.out_dir / ".env"
        lines = [f"{key}={value}" for key, value in env_values.items()]
        env_path.write_text("\n".join(lines) + "\n")
        print("  wrote .env")

    def _copy_schema(self, spec: dict):
        source = spec.get("schema_path")
        if not source:
            return
        dest = self.out_dir / "prisma" / "schema.prisma"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(Path(source).read_text())
        print("  wrote prisma/schema.prisma")