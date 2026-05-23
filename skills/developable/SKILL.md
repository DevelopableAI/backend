---
name: developable
description: Use when a user wants to generate, regenerate, or extend a Developable-style Express + TypeScript backend from a Prisma schema or a plain-English app description, especially in Codex where they need the same workflow and standards that exist in the Claude `/developable` command.
---

# Developable

Use this skill when working with the Developable backend generator in Codex.

This skill is the Codex-facing counterpart to the Claude `/developable` command. It keeps the same product rules:

- planner-driven generation
- Express + TypeScript output
- Prisma schema as the source of truth
- bounded LLM use for validators and selected tests only
- generated `CLAUDE.md` and `AGENTS.md` instruction files for downstream agents

## When To Use It

Trigger this skill when the user asks to:

- generate a backend from `schema.prisma`
- design a Prisma schema from an app description and then generate the backend
- regenerate output after schema or rules changes
- inspect or extend the Developable generation workflow inside this repo

## Core Workflow

1. Read the repo guide in `AGENTS.md` first.
2. If a Prisma schema already exists, use it.
3. If no schema exists, design `schema.prisma` and a matching `rules.yaml` first.
4. Keep generation planner- and template-driven. Prefer using the CLI over hand-authoring large generated trees.
5. Use `main.py` as the canonical generator entrypoint.
6. When the user wants deployment or GitHub publishing, route that through the existing CLI flags and agent flow rather than inventing a parallel path.

## Canonical Commands

Basic generation:

```bash
python3 main.py path/to/schema.prisma --rules path/to/rules.yaml --out ./output
```

Fast structural generation without LLM calls:

```bash
python3 main.py path/to/schema.prisma --rules path/to/rules.yaml --out ./output --no-llm
```

Generate tests too:

```bash
python3 main.py path/to/schema.prisma --rules path/to/rules.yaml --out ./output --tests-out ./output/tests
```

Publish to GitHub:

```bash
python3 main.py path/to/schema.prisma --rules path/to/rules.yaml --out ./output --github
```

## Files To Inspect Before Editing This Repo

- `main.py`
- `core/parser.py`
- `core/planner.py`
- `core/test_planner.py`
- `core/assembler.py`
- `templates/express/api/`
- `templates/tests/`

If the requested change affects deployment or CI/CD, also inspect:

- `agents/deployment.py`
- `agents/version_control.py`
- `core/providers/`
- `templates/terraform/`

## Non-Negotiable Standards

- Do not turn stable template logic into broad free-form LLM generation.
- Preserve auth, ownership, and server-injected FK invariants.
- Keep generated route behavior and generated test expectations aligned.
- When adding support for Codex-facing agents, keep `AGENTS.md` in generated output aligned with `CLAUDE.md`.

## Reference

For the more detailed phased workflow used by the Claude command, inspect:

- `.claude/commands/developable.md`
