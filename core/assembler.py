import subprocess
from pathlib import Path
from typing import Any

from generators.template import TemplateGenerator
from generators.llm import LLMGenerator


class Assembler:
    def __init__(self, out_dir: Path, use_llm: bool = True, force: bool = False):
        self.out_dir = out_dir
        self.use_llm = use_llm
        self.force = force
        self.template_gen = TemplateGenerator()
        self.llm_gen = LLMGenerator() if use_llm else None

    def assemble(self, spec: dict[str, Any], plan: dict[str, Any], env_values: dict[str, str] | None = None):
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # Locate the git root once (None when out_dir is not inside a repo,
        # or when --force is set — in both cases we always overwrite).
        git_root = None if self.force else self._find_git_root()

        skipped: list[str] = []

        for file_plan in plan["files"]:
            path = self.out_dir / file_plan["path"]
            path.parent.mkdir(parents=True, exist_ok=True)

            if git_root and path.exists() and self._is_user_modified(path, git_root):
                skipped.append(file_plan["path"])
                print(f"  skipped {file_plan['path']} (user-modified, use --force to overwrite)")
                continue

            content = self._generate_file(file_plan, spec)
            path.write_text(content)
            print(f"  wrote {file_plan['path']}")

        self._copy_schema(spec, git_root)

        if env_values:
            self._write_env_file(env_values, git_root)

        self._run_formatter()

        if skipped:
            print(f"\n  Preserved {len(skipped)} user-modified file(s).")

    def _generate_file(self, file_plan: dict, spec: dict) -> str:
        # render the template first (always)
        content = self.template_gen.render(
            template_name=file_plan["template"],
            context=file_plan["context"],
        )

        # if LLM is needed and enabled, fill the LLM sections
        if file_plan.get("needs_llm") and self.use_llm:
            # llm_entity overrides the context entity (used by test modules that target
            # a specific entity different from the template's primary context)
            entity = file_plan.get("llm_entity") or file_plan["context"].get("entity")
            task = file_plan.get("llm_task", "")
            prompt_subdir = file_plan.get("prompt_subdir", "express")
            content = self.llm_gen.fill(
                content=content,
                task=task,
                entity=entity,
                spec=spec,
                prompt_subdir=prompt_subdir,
            )

        return content

    # ── Git-diff awareness ─────────────────────────────────────────────────────

    def _find_git_root(self) -> Path | None:
        """Walk up from out_dir to find the nearest .git directory."""
        current = self.out_dir.resolve()
        while True:
            if (current / ".git").exists():
                return current
            parent = current.parent
            if parent == current:
                return None
            current = parent

    def _is_user_modified(self, path: Path, git_root: Path) -> bool:
        """
        Returns True if `path` has been changed by the user since the last commit.

        Steps:
          1. If file is untracked (never committed) → False (safe to overwrite).
          2. If tracked and differs from HEAD → True (preserve user changes).
          3. Otherwise → False (tracked but unchanged, safe to regenerate).
        """
        abs_path = path.resolve()

        # Is the file tracked by git at all?
        ls = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(abs_path)],
            cwd=git_root,
            capture_output=True,
        )
        if ls.returncode != 0:
            return False  # untracked → not user-modified

        # Does the working tree differ from HEAD?
        try:
            rel = abs_path.relative_to(git_root)
        except ValueError:
            return False  # not under git root (shouldn't happen)

        diff = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", str(rel)],
            cwd=git_root,
            capture_output=True,
        )
        return diff.returncode != 0  # 1 = changes exist

    # ── Other helpers ──────────────────────────────────────────────────────────

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
    
    def _write_env_file(self, env_values: dict[str, str], git_root: Path | None = None):
        env_path = self.out_dir / ".env"
        if git_root and env_path.exists() and self._is_user_modified(env_path, git_root):
            print("  skipped .env (user-modified, use --force to overwrite)")
            return
        lines = [f"{key}={value}" for key, value in env_values.items()]
        env_path.write_text("\n".join(lines) + "\n")
        print("  wrote .env")

    def _copy_schema(self, spec: dict, git_root: Path | None = None):
        source = spec.get("schema_path")
        if not source:
            return
        dest = self.out_dir / "prisma" / "schema.prisma"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if git_root and dest.exists() and self._is_user_modified(dest, git_root):
            print("  skipped prisma/schema.prisma (user-modified, use --force to overwrite)")
            return
        dest.write_text(Path(source).read_text())
        print("  wrote prisma/schema.prisma")