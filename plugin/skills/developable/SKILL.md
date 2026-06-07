---
name: developable
description: Use when a user wants to generate, regenerate, or extend a Developable-style Express + TypeScript backend from a Prisma schema or a plain-English app description.
---

# Developable

Generate a production-hardened Express + TypeScript REST API from a Prisma schema or plain-English app description. Enforces security invariants, ownership checks, Zod validation, JWT auth, integration tests, and Terraform IaC — all from a single schema file.

## Setup (Required)

The Developable CLI must be on the machine before this skill can run structural generation:

```bash
# Install CLI + Claude Code skill
curl -sSL https://raw.githubusercontent.com/developableai/backend/main/install.sh | bash

# Or install CLI only
pip install developable

# Verify
developable --help
```

Set your API key:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

**Pre-flight check:** Before running generation, verify the CLI is available:

```bash
which developable 2>/dev/null || echo "NOT_FOUND"
```

If `NOT_FOUND`, and `main.py` is also not in the current directory, stop and print:

```
✗ Developable CLI not found.

Install with:
  curl -sSL https://raw.githubusercontent.com/developableai/backend/main/install.sh | bash

Or install CLI only:
  pip install developable

Then re-run this skill.
```

Do NOT proceed to generation without the CLI. AI-only generation skips the Jinja2 templates that enforce security invariants.

## When To Use This Skill

Trigger when the user asks to:
- Generate a backend from `schema.prisma`
- Design a Prisma schema from a plain-English app description and then generate the backend
- Regenerate output after schema or rules changes
- Inspect or extend the Developable generation workflow inside this repo

## Core Workflow

1. **Pre-flight**: run the CLI check above. Stop with install instructions if CLI not found.
2. **If schema exists**: use it and go directly to generation.
3. **If no schema**: design `schema.prisma` + `rules.yaml` from the user's description first (see Schema Design below).
4. **Run generation**: use the CLI (preferred) rather than hand-authoring files.
5. **Fill LLM sections**: after CLI runs, fill `LLM_SECTION_START`/`LLM_SECTION_END` markers in validators and tests.

## Canonical Commands

```bash
# Basic generation
python3 main.py path/to/schema.prisma --out ./output

# With business rules
python3 main.py path/to/schema.prisma --rules path/to/rules.yaml --out ./output

# Fast (no LLM calls — placeholder Zod schemas)
python3 main.py path/to/schema.prisma --out ./output --no-llm

# With integration test suite
python3 main.py path/to/schema.prisma --out ./output --tests-out ./output/tests

# Publish to GitHub + CI
python3 main.py path/to/schema.prisma --out ./output --github

# Deploy to cloud after generation
python3 deploy.py --out ./output --deploy-to aws   # or gcp | heroku
```

## Schema Design (when no schema exists)

Work through these questions before writing any file:

1. What are the main nouns? → Prisma models
2. Which entity logs in? → `// @auth_entity`
3. What scalar fields does each entity need?
4. What relationships exist? (who owns whom, FK direction)
5. Which fields are secrets? → `// @llm sensitive`
6. What business constraints exist? → `rules.yaml`

**Model conventions:**
- Always include `id Int @id @default(autoincrement())`
- Always include `createdAt DateTime @default(now())` and `updatedAt DateTime @updatedAt`
- Mark the auth model with `// @auth_entity` on the line above `model Name {`
- Mark password/secret fields with `// @llm sensitive` as an inline comment
- FK field names: `{relatedEntityLower}Id` (e.g. `authorId`, `projectId`)

## Non-Negotiable Security Invariants

These are enforced by the Jinja2 templates — never remove or weaken them:

| Invariant | Where enforced |
|---|---|
| Integer ID validation — rejects floats, alpha, SQL-injection suffixes | `controller.ts` `_parseId` |
| String ID validation — rejects whitespace, oversized strings | `controller.ts` `_parseStringId` |
| Owner FK injected server-side from JWT — never accepted from request body | `controller.ts` create + validator |
| Auth entity self-ownership: users may only update/delete their own record | `controller.ts` update/remove |
| Resource ownership check before update/delete | `controller.ts` update/remove |
| Sensitive fields hashed with bcrypt before storage | `auth.controller.ts` |
| Sensitive fields excluded from JWT payload and API responses | `auth.controller.ts` `safeSelect` |
| JWT verified on all write routes | `routes.ts` + `auth.ts` |
| Filter fields validated against allowlist — unknown/sensitive fields rejected (400) | `controller.ts` `ALLOWED_FILTER_FIELDS` |

## Files To Inspect Before Editing This Repo

- `main.py`, `deploy.py`
- `core/parser.py`, `core/planner.py`, `core/assembler.py`
- `templates/express/api/` — the Jinja2 templates
- `templates/tests/` — test templates
- `agents/developer.py`, `agents/tester.py`

For deployment/CI changes also inspect:
- `agents/deployment.py`, `agents/version_control.py`
- `core/providers/`, `templates/terraform/`

## Non-Negotiable Standards

- Do not replace template logic with broad free-form LLM generation.
- Preserve auth, ownership, and server-injected FK invariants.
- Keep generated route behavior and test expectations aligned.
- Maintain `AGENTS.md` in generated output aligned with `CLAUDE.md`.

## Install for OpenAI Codex

```
$skill-installer install https://github.com/developableai/backend/tree/main/skills/developable
```

## Install for Claude Code (clean /developable command)

```bash
curl -sSL https://raw.githubusercontent.com/developableai/backend/main/install.sh | bash
```

Or install the plugin (namespaced as `/developable:developable`):
```
/plugin marketplace add developableai/backend
/plugin install developable
```
