import re
import textwrap
from pathlib import Path
from typing import Any

import config
from generators.base import BaseGenerator


SECTION_START = "/* LLM_SECTION_START */"
SECTION_END = "/* LLM_SECTION_END */"


class LLMGenerator(BaseGenerator):
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.system_prompt = self._load_system_prompt()

    def render(self, **kwargs) -> str:
        # LLMGenerator.fill() is the main entry point, render() satisfies the ABC
        return self.fill(**kwargs)

    def fill(
        self,
        content: str,
        task: str,
        entity: dict | None,
        spec: dict,
        prompt_subdir: str = "express",
    ) -> str:
        """
        Finds LLM_SECTION_START / LLM_SECTION_END markers in content,
        calls the LLM to replace each section, and returns the filled content.

        prompt_subdir controls which subdirectory under prompts/ is used for
        the task prompt file (default: "express", use "tests" for Python test generation).
        """
        pattern = re.compile(
            r"/\* LLM_SECTION_START \*/(.*?)/\* LLM_SECTION_END \*/",
            re.DOTALL,
        )

        def replace_section(match: re.Match) -> str:
            placeholder = match.group(1).strip()
            return self._generate_section(
                task=task,
                placeholder=placeholder,
                entity=entity,
                spec=spec,
                prompt_subdir=prompt_subdir,
            )

        return pattern.sub(replace_section, content)

    def _generate_section(
        self,
        task: str,
        placeholder: str,
        entity: dict | None,
        spec: dict,
        prompt_subdir: str = "express",
    ) -> str:
        prompt_path = config.PROMPTS_DIR / prompt_subdir / f"{task}.txt"
        if not prompt_path.exists():
            print(f"Warning: prompt file not found for task '{task}', leaving placeholder")
            return placeholder

        task_prompt = prompt_path.read_text()
        language = "Python" if prompt_subdir == "tests" else "TypeScript"
        user_message = self._build_user_message(task_prompt, placeholder, entity, spec, language)

        system_prompt = self._load_system_prompt(prompt_subdir)

        response = self.client.messages.create(
            model=config.MODEL,
            max_tokens=2048,
            temperature=config.TEMPERATURE_LLM,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        raw = response.content[0].text
        cleaned = self._cleanup_markdown(raw).strip()

        # Python test sections sit inside `def run(ctx):` — enforce 4-space indent.
        # Step 1: textwrap.dedent strips the *common* leading whitespace.
        #   This works perfectly when the LLM uses consistent 0-based indentation.
        # Step 2: detect the "function-body-as-base" failure mode — the LLM puts
        #   comments at col 0 (anchoring the common prefix to 0 so dedent is a noop)
        #   while placing all code lines at col 4 (treating the function body as col 0).
        #   We detect this by checking if ALL non-empty, non-comment lines start with
        #   at least one space after dedent. If so, strip that minimum code-line indent
        #   from every non-empty line before applying the 4-space function-body offset.
        if prompt_subdir == "tests":
            cleaned = textwrap.dedent(cleaned)
            code_lines = [
                l for l in cleaned.splitlines()
                if l.strip() and not l.lstrip().startswith("#")
            ]
            if code_lines:
                min_code_indent = min(len(l) - len(l.lstrip()) for l in code_lines)
                if min_code_indent > 0:
                    normalized = []
                    for l in cleaned.splitlines():
                        if not l.strip():
                            normalized.append(l)
                        elif len(l) - len(l.lstrip()) >= min_code_indent:
                            normalized.append(l[min_code_indent:])
                        else:
                            normalized.append(l)  # comment/line below the min — keep as-is
                    cleaned = "\n".join(normalized)
            cleaned = "\n".join(
                ("    " + line) if line.strip() else line
                for line in cleaned.splitlines()
            )

        return cleaned

    def _build_user_message(
        self,
        task_prompt: str,
        placeholder: str,
        entity: dict | None,
        spec: dict,
        language: str = "TypeScript",
    ) -> str:
        if entity:
            field_summary = "\n".join(
                f"  - {f['name']}: {f['ts_type']}"
                + (" (optional)" if f["is_optional"] else "")
                + (" (unique)" if f["is_unique"] else "")
                for f in entity["fields"]
                if not f["is_relation"]
            )
            hints = "\n".join(f"  - {h}" for h in entity.get("llm_hints", [])) or "  (none)"
            constraints = entity.get("llm_constraints", [])
            constraints_section = (
                "\n            Business-rule constraints (from rules file):\n"
                + "\n".join(f"            - {c}" for c in constraints)
            ) if constraints else ""
            entity_section = f"""
            Entity: {entity['name']}
            Fields:
            {field_summary}

            Custom hints from schema:
            {hints}
{constraints_section}
"""
        else:
            entity_section = ""

        return f"""{task_prompt}
{entity_section}
            The section to fill replaces this placeholder:
            {placeholder}

            Output only the {language} code for this section, no explanation, no markdown fences."""

    def _load_system_prompt(self, prompt_subdir: str = "express") -> str:
        path = config.PROMPTS_DIR / prompt_subdir / "system.txt"
        if path.exists():
            return path.read_text()

        # Fall back to the root system.txt
        root_path = config.PROMPTS_DIR / "system.txt"
        if root_path.exists():
            return root_path.read_text()

        return (
            "You are a senior backend engineer with 10+ years of production experience. "
            "You write TypeScript for Express APIs backed by Prisma. "
            "You never take shortcuts: you handle edge cases, write optimal queries, "
            "implement proper pagination, use atomic transactions where data integrity requires it, "
            "validate inputs thoroughly, and return clear error messages. "
            "You output only code — no comments about what you did, no markdown."
        )