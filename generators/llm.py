import ast
import hashlib
import re
import textwrap
from pathlib import Path
from typing import Any

import config
from generators.base import BaseGenerator


SECTION_START = "/* LLM_SECTION_START */"
SECTION_END = "/* LLM_SECTION_END */"

# Module-level session usage accumulator — reset between CLI runs via reset_session()
_session_usage: list[dict] = []


def get_session_summary() -> dict[str, Any]:
    """Return aggregated token usage and estimated cost for the current session."""
    if not _session_usage:
        return {}

    totals: dict[str, Any] = {
        "calls": len(_session_usage),
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "cache_hits": 0,
        "estimated_cost_usd": 0.0,
    }
    for entry in _session_usage:
        totals["input_tokens"] += entry["input_tokens"]
        totals["output_tokens"] += entry["output_tokens"]
        totals["cache_write_tokens"] += entry["cache_write_tokens"]
        totals["cache_read_tokens"] += entry["cache_read_tokens"]
        if entry.get("response_cache_hit"):
            totals["cache_hits"] += 1
        totals["estimated_cost_usd"] += entry["cost_usd"]

    return totals


def reset_session():
    _session_usage.clear()


class LLMGenerator(BaseGenerator):
    def __init__(self, use_response_cache: bool = True):
        import anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.use_response_cache = use_response_cache
        self._cache_dir = Path.home() / ".developable" / "cache"

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
        model: str | None = None,
    ) -> str:
        """
        Finds LLM_SECTION_START / LLM_SECTION_END markers in content,
        calls the LLM to replace each section, and returns the filled content.

        prompt_subdir controls which subdirectory under prompts/ is used for
        the task prompt file (default: "express", use "tests" for Python test generation).
        model overrides the default model for this task (e.g. config.MODEL_FAST for
        simple seed_data tasks).
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
                model=model,
            )

        return pattern.sub(replace_section, content)

    def _generate_section(
        self,
        task: str,
        placeholder: str,
        entity: dict | None,
        spec: dict,
        prompt_subdir: str = "express",
        model: str | None = None,
    ) -> str:
        prompt_path = config.PROMPTS_DIR / prompt_subdir / f"{task}.txt"
        if not prompt_path.exists():
            print(f"Warning: prompt file not found for task '{task}', leaving placeholder")
            return placeholder

        task_prompt = prompt_path.read_text()
        language = "Python" if prompt_subdir == "tests" else "TypeScript"
        system_prompt = self._load_system_prompt(prompt_subdir)
        dynamic_part = self._build_dynamic_part(placeholder, entity, spec, language)

        resolved_model = model or config.MODEL
        max_tokens = config.TASK_MAX_TOKENS.get(task, config.TASK_MAX_TOKENS_DEFAULT)
        entity_label = entity["name"] if entity else "project"

        # ── Response cache check (skip API call entirely for repeated runs) ──
        if self.use_response_cache:
            cache_key = self._cache_key(resolved_model, system_prompt, task_prompt, dynamic_part)
            cached = self._get_cached_response(cache_key)
            if cached is not None:
                print(f"  [response cache hit] {task} / {entity_label}")
                _session_usage.append({
                    "task": task,
                    "model": resolved_model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_write_tokens": 0,
                    "cache_read_tokens": 0,
                    "cost_usd": 0.0,
                    "response_cache_hit": True,
                })
                return cached

        # ── Build prompt-cached content blocks ────────────────────────────────
        # system and task_prompt are identical across all entity calls within
        # a generation run — mark them for Anthropic's server-side prompt cache
        # so subsequent calls pay only 10% of the input token cost.
        system_content = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        user_content = [
            {
                "type": "text",
                "text": task_prompt,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": dynamic_part,
            },
        ]

        response = self.client.messages.create(
            model=resolved_model,
            max_tokens=max_tokens,
            temperature=config.TEMPERATURE_LLM,
            system=system_content,
            messages=[{"role": "user", "content": user_content}],
        )
        self._record_usage(task, resolved_model, response.usage)

        raw = response.content[0].text
        cleaned = self._cleanup_markdown(raw).strip()

        if prompt_subdir == "tests":
            cleaned = self._normalize_test_indent(cleaned)

            # Validate the indented code by parsing it as a Python function body.
            # If ast.parse fails (IndentationError or SyntaxError), retry the LLM
            # call once with an explicit correction instruction and re-normalize.
            if not self._valid_python_section(cleaned):
                print(
                    f"  ⚠️  LLM output for task '{task}' failed syntax validation "
                    f"(likely indentation). Retrying once..."
                )
                correction = (
                    "\n\nYour previous response had Python indentation errors. "
                    "Remember: every top-level statement must start at column 0 "
                    "(zero leading spaces). Code nested inside `if`, `for`, or `with` "
                    "blocks uses exactly 4 spaces. Do NOT add extra leading spaces to "
                    "outermost statements."
                )
                retry_user_content = [
                    {
                        "type": "text",
                        "text": task_prompt,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": dynamic_part + correction,
                    },
                ]
                retry_response = self.client.messages.create(
                    model=resolved_model,
                    max_tokens=max_tokens,
                    temperature=config.TEMPERATURE_LLM,
                    system=system_content,
                    messages=[{"role": "user", "content": retry_user_content}],
                )
                self._record_usage(f"{task}:retry", resolved_model, retry_response.usage)
                retry_raw = retry_response.content[0].text
                retry_cleaned = self._normalize_test_indent(
                    self._cleanup_markdown(retry_raw).strip()
                )
                if self._valid_python_section(retry_cleaned):
                    cleaned = retry_cleaned
                else:
                    print(
                        f"  ⚠️  Retry also produced syntax issues for task '{task}'. "
                        "Using original output — manual review may be needed."
                    )

        # Store in response cache so re-runs of the same schema skip the API call
        if self.use_response_cache:
            self._set_cached_response(cache_key, cleaned)

        return cleaned

    def _build_dynamic_part(
        self,
        placeholder: str,
        entity: dict | None,
        spec: dict,
        language: str,
    ) -> str:
        """Build the dynamic (per-entity) portion of the user message."""
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

        return f"""{entity_section}
            The section to fill replaces this placeholder:
            {placeholder}

            Output only the {language} code for this section, no explanation, no markdown fences."""

    # Keep _build_user_message as an alias for any external callers (unused internally)
    def _build_user_message(
        self,
        task_prompt: str,
        placeholder: str,
        entity: dict | None,
        spec: dict,
        language: str = "TypeScript",
    ) -> str:
        return task_prompt + self._build_dynamic_part(placeholder, entity, spec, language)

    def _normalize_test_indent(self, code: str) -> str:
        """
        Normalize LLM-generated Python test code to sit correctly inside a function body.

        The LLM may emit code at col 0 (ideal), col 4 (treating itself as already inside
        def run), or mixed. Strategy:

        1. textwrap.dedent — strips the common leading prefix. Handles the uniform
           col-4+ case perfectly.
        2. "function-body-as-base" detection — if every non-blank, non-comment line
           still starts with at least one space after dedent, the LLM anchored its
           zero-level at col 4 but left comments at col 0. Strip that minimum indent
           from code lines, keep comments as-is.
        3. Apply 4-space prefix — shift everything into the function body.
        """
        dedented = textwrap.dedent(code)
        code_lines = [
            l for l in dedented.splitlines()
            if l.strip() and not l.lstrip().startswith("#")
        ]
        if code_lines:
            min_code_indent = min(len(l) - len(l.lstrip()) for l in code_lines)
            if min_code_indent > 0:
                result = []
                for l in dedented.splitlines():
                    if not l.strip():
                        result.append(l)
                    elif len(l) - len(l.lstrip()) >= min_code_indent:
                        result.append(l[min_code_indent:])
                    else:
                        result.append(l)  # comment below the min indent — keep as-is
                dedented = "\n".join(result)
        return "\n".join(
            ("    " + line) if line.strip() else line
            for line in dedented.splitlines()
        )

    @staticmethod
    def _valid_python_section(code: str) -> bool:
        """
        Return True if `code` is syntactically valid Python when placed inside a
        function body. Uses ast.parse to catch IndentationError and SyntaxError.
        The `pass` at the end ensures an empty code string is also accepted.
        """
        try:
            ast.parse(f"def _section():\n{code}\n    pass\n")
            return True
        except SyntaxError:
            return False

    # ── Response cache (disk-based, SHA256-keyed) ──────────────────────────────

    def _cache_key(self, model: str, system: str, static: str, dynamic: str) -> str:
        data = f"{model}|{system}|{static}|{dynamic}"
        return hashlib.sha256(data.encode()).hexdigest()

    def _get_cached_response(self, key: str) -> str | None:
        path = self._cache_dir / f"{key}.txt"
        return path.read_text() if path.exists() else None

    def _set_cached_response(self, key: str, value: str):
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        (self._cache_dir / f"{key}.txt").write_text(value)

    # ── Token usage tracking ───────────────────────────────────────────────────

    @staticmethod
    def _record_usage(task: str, model: str, usage: Any):
        """Record token usage for one API call and print a per-call summary line."""
        pricing = config.MODEL_PRICING.get(model, config.MODEL_PRICING[config.MODEL])

        # Per Anthropic docs: input_tokens = uncached input (NOT including cache_creation
        # or cache_read tokens); those are billed at their own rates.
        input_t = getattr(usage, "input_tokens", 0)
        output_t = getattr(usage, "output_tokens", 0)
        cache_write_t = getattr(usage, "cache_creation_input_tokens", 0)
        cache_read_t = getattr(usage, "cache_read_input_tokens", 0)

        cost = (
            input_t * pricing["input"] / 1_000_000
            + cache_write_t * pricing["cache_write"] / 1_000_000
            + cache_read_t * pricing["cache_read"] / 1_000_000
            + output_t * pricing["output"] / 1_000_000
        )

        _session_usage.append({
            "task": task,
            "model": model,
            "input_tokens": input_t,
            "output_tokens": output_t,
            "cache_write_tokens": cache_write_t,
            "cache_read_tokens": cache_read_t,
            "cost_usd": cost,
            "response_cache_hit": False,
        })

        cache_info = ""
        if cache_write_t:
            cache_info += f", {cache_write_t} cache-write"
        if cache_read_t:
            cache_info += f", {cache_read_t} cache-read"
        print(f"  [{task}] {input_t} in / {output_t} out{cache_info} → ${cost:.4f}")

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
