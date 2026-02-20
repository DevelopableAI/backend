import re
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

    def fill(self, content: str, task: str, entity: dict, spec: dict) -> str:
        """
        Finds LLM_SECTION_START / LLM_SECTION_END markers in content,
        calls the LLM to replace each section, and returns the filled content.
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
            )

        return pattern.sub(replace_section, content)

    def _generate_section(self, task: str, placeholder: str, entity: dict, spec: dict) -> str:
        prompt_path = config.PROMPTS_DIR / "express" / f"{task}.txt"
        if not prompt_path.exists():
            print(f"Warning: prompt file not found for task '{task}', leaving placeholder")
            return placeholder

        task_prompt = prompt_path.read_text()
        user_message = self._build_user_message(task_prompt, placeholder, entity, spec)

        response = self.client.messages.create(
            model=config.MODEL,
            max_tokens=2048,
            temperature=config.TEMPERATURE_LLM,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        raw = response.content[0].text
        return self._cleanup_markdown(raw).strip()

    def _build_user_message(self, task_prompt: str, placeholder: str, entity: dict, spec: dict) -> str:
        field_summary = "\n".join(
            f"  - {f['name']}: {f['ts_type']}"
            + (" (optional)" if f["is_optional"] else "")
            + (" (unique)" if f["is_unique"] else "")
            for f in entity["fields"]
            if not f["is_relation"]
        )

        hints = "\n".join(f"  - {h}" for h in entity.get("llm_hints", [])) or "  (none)"

        return f"""{task_prompt}

            Entity: {entity['name']}
            Fields:
            {field_summary}

            Custom hints from schema:
            {hints}

            The section to fill replaces this placeholder:
            {placeholder}

            Output only the TypeScript code for this section, no explanation, no markdown fences."""

    def _load_system_prompt(self) -> str:
        path = config.PROMPTS_DIR / "system.txt"
        if path.exists():
            return path.read_text()

        return (
            "You are a senior backend engineer with 10+ years of production experience. "
            "You write TypeScript for Express APIs backed by Prisma. "
            "You never take shortcuts: you handle edge cases, write optimal queries, "
            "implement proper pagination, use atomic transactions where data integrity requires it, "
            "validate inputs thoroughly, and return clear error messages. "
            "You output only code — no comments about what you did, no markdown."
        )