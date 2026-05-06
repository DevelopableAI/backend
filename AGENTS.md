# AGENTS.md

## Purpose

This repository is a Python CLI code generator. It takes a Prisma schema, optionally merges business rules, and generates:

- An Express + TypeScript REST API
- An optional Python integration test suite
- Optional version-control/bootstrap artifacts for GitHub publishing

Treat this file as the fast working guide for agents and contributors. `CLAUDE.md` is the deeper architecture narrative; this file is the task-oriented entrypoint.

## Working Agreement

- Read this file before making changes in this repository.
- Use it as baseline working context on future tasks here.
- Do not paste the full file into responses by default; summarize only the parts that matter to the current task.
- Keep this file aligned with the code and with `CLAUDE.md` when behavior or architecture changes.

## Current Architecture

### Orchestration Flow

`main.py` is the CLI entrypoint and coordinator.

High-level flow:

1. Parse CLI arguments.
2. Parse the Prisma schema via `PrismaParser`.
3. Optionally merge business rules via `BusinessRulesParser`.
4. Collect environment-variable values referenced by the schema.
5. Run the `Developer` agent to generate the API.
6. Optionally run the `Tester` agent to generate the Python test suite.
7. Optionally run the `VersionControl` agent to generate infra files, initialize git, create a GitHub repo, and push.
8. Print LLM usage summary when LLM generation is enabled.

### Agent Responsibilities

- `agents/developer.py`
  - Uses `Planner` to build an API file manifest.
  - Uses `Assembler` to render templates and fill bounded LLM sections.
  - Returns the API plan for downstream consumers.
- `agents/tester.py`
  - Uses `TestPlanner` plus the API plan from `Developer`.
  - Generates a Python integration test suite that mirrors generated API behavior.
- `agents/version_control.py`
  - Uses `VCPlanner` and `Assembler` to add infra files.
  - Writes `.gitignore`, initializes git, creates a GitHub repository through the REST API, and pushes `main`.

### Core Modules

- `core/parser.py`
  - Converts Prisma schema text into a shared `spec` dict.
  - Extracts models, fields, relations, enums, env vars, datasource config, `@llm` hints, and auth-entity metadata.
- `core/rules_parser.py`
  - Optionally merges `schema.rules.yaml` business constraints into the parsed spec.
  - Adds denied endpoints, LLM constraints, and explicit primary-parent overrides.
- `core/planner.py`
  - Converts the shared spec into a concrete API file plan.
  - Decides project files, per-entity files, allowed routes, nested routes, owner foreign keys, auth-specific files, and validator LLM tasks.
- `core/test_planner.py`
  - Converts the shared spec plus the API plan into a test file plan.
  - Uses API route context rather than rediscovering routes independently.
  - This file is large and relatively high-coupling; edit with care.
- `core/assembler.py`
  - Renders Jinja templates for each planned file.
  - Optionally fills bounded LLM sections after template rendering.
  - Preserves tracked user-modified files unless `--force` is set.
  - Copies the schema into `prisma/schema.prisma`, writes `.env` when values were collected, and runs Prettier if available in the generated output.

## Generation Invariants

- Generation is planner-driven. Prefer changing planners and templates instead of scattering ad hoc conditional logic across multiple layers.
- LLM use is bounded to specific sections such as validator logic and some test bodies. This project is not doing whole-file AI generation.
- Test generation intentionally depends on the API plan. If API route planning changes, test planning usually needs inspection too.
- Regeneration is git-aware:
  - `Assembler` checks whether tracked generated files differ from `HEAD`.
  - Tracked user-modified files are skipped by default.
  - `--force` is the explicit escape hatch that overwrites them and disables response-cache reuse for LLM fills.
- Formatting is opportunistic:
  - Prettier only runs when it exists in the generated project's `node_modules/.bin/prettier`.

## Important Files And Their Roles

- `README.md`
  - User-facing quickstart and generated-output overview.
  - Helpful, but lighter than the real implementation and may lag details.
- `CLAUDE.md`
  - Rich architecture narrative and project vision.
  - Keep it aligned with this file when the repo evolves.
- `PROGRESS.md`
  - Historical project notes, solved issues, and observed gaps.
  - Useful context, but not a source of truth for current behavior.
- `templates/`
  - Jinja templates for generated API, tests, and infra.
- `prompts/`
  - LLM prompt fragments used only for bounded generation tasks.

## Repo Realities And Cautions

- `requirements.txt` currently includes `fastapi`, `uvicorn`, and `pydantic` even though the visible entrypoint in this repo is generator-oriented rather than a live FastAPI service. Treat this as a mismatch to verify before removing or depending on those packages; they may be legacy, planned, or used outside the immediately visible CLI flow.
- `core/test_planner.py` is one of the most coupled files in the repo because it bridges spec semantics and generated route behavior. Small route-planning changes can have broad testing impact.
- `VersionControl.publish()` accepts `api_plan` but the current implementation only uses `spec` while generating infra. Treat this as a sign to verify interfaces before refactoring for cleanliness.
- `README.md` describes the product accurately at a high level, but `CLAUDE.md` and the source code are better references for detailed editing decisions.

## How To Work Safely Here

- When changing schema interpretation:
  - Check `core/parser.py`, `core/planner.py`, `core/test_planner.py`, and the relevant templates.
- When changing route behavior:
  - Inspect both generated API templates and the expectations encoded in `TestPlanner`.
- When changing auth, ownership, nested-route, or foreign-key behavior:
  - Validate both planning logic and generated test coverage assumptions.
- When changing regeneration behavior:
  - Preserve the contract around user-modified tracked files and `--force`.
- When changing LLM-related behavior:
  - Keep generation bounded and deterministic where possible; avoid turning stable template logic into broad free-form LLM output.
- When cleaning up docs:
  - Prefer aligning `AGENTS.md`, `CLAUDE.md`, and `README.md` rather than updating just one of them.

## Practical Edit Strategy

- Start from the planner or parser layer when behavior is structural.
- Start from templates when behavior is presentational or file-shape specific.
- Inspect test templates and `core/test_planner.py` early for any change that affects routes, payload shapes, auth, or relation handling.
- Prefer repo-consistent extension over one-off fixes in generated output, since this codebase exists to regenerate systems repeatably.

## Upcoming Architectural Direction: Claude Code Skill

This repo is being converted into a **publishable Claude Code skill**. Understand this before making significant structural changes.

### What This Means

- The Python CLI (`main.py` + agents) will be superseded by a skill definition file at `.claude/commands/developable.md`.
- Users will invoke `/developable` inside Claude Code instead of running `python main.py schema.prisma --out ./my-api`.
- Claude Code's native file-writing tools replace `Assembler` + `LLMGenerator`; the model writes output files directly rather than through the Anthropic SDK.
- The Jinja2 templates in `templates/express/` stay as structural guides but are no longer rendered programmatically — the skill prompt instructs Claude to follow the same patterns.
- Schema annotations (`@auth_entity`, `@llm sensitive`, `@llm hints`) and all security invariants carry over unchanged into the skill prompt.

### Migration Work Items

- [ ] Create `.claude/commands/developable.md` skill scaffold encoding the generation pipeline as prompt instructions
- [ ] Convert Jinja2 templates to inline examples or embedded reference content the skill can follow
- [ ] Publish skill manifest and update `README.md` quickstart for `/developable` install
- [ ] Achieve feature parity with the Python CLI before deprecating `main.py`

### What To Preserve During Migration

- All security invariants listed in `CLAUDE.md` (ID validation, owner FK injection, auth checks, sensitive-field hashing) must be encoded verbatim into the skill prompt — they are non-negotiable.
- The parse → plan → assemble mental model stays; it becomes reasoning steps in the skill rather than Python classes.
- Keep `templates/express/` accurate — they are the canonical reference the skill will use for output structure.

---

## Default Assumptions For Future Agent Work

- “Always attach it” means: always consult and rely on `AGENTS.md` while working in this repo.
- It does not mean: paste the full contents of `AGENTS.md` into every user-facing reply.
- This file should remain a stable engineer guide, not a running logbook.
