# CLAUDE.md — Developable Backend

## Project Vision

### The Problem

When developers give an LLM requirements — through a CLAUDE.md, an AGENTS.md, a system prompt, or plain English — the model decides for itself how to structure the code, which security patterns to apply, and which OOP conventions to follow. That is the wrong default. The LLM will produce something that works in isolation but differs in file structure, naming, auth handling, and ownership logic every single time. There is no guarantee of consistency, security, or correctness across a codebase that grows feature by feature under AI assistance.

This is not a prompt quality problem. It is a missing standard problem.

### The Solution

**Developable** establishes that standard. It is an opinionated, battle-tested template for building Express + TypeScript REST APIs that encodes exact answers to the questions that LLMs otherwise guess at:

- **File structure** — routes → controllers → repositories; one file per concern, consistent naming
- **Security invariants** — non-negotiable rules baked into every generated file (see the Security Invariants section)
- **OOP patterns** — how controllers delegate, how repositories own data access, how errors propagate
- **Auth and ownership** — how JWT is verified, how ownership is checked, how sensitive fields are handled
- **Validation** — Zod schemas at the controller boundary, server-side FK injection, no client-supplied owner IDs

These decisions are encoded in the Jinja2 templates in `templates/express/`. They are not suggestions. They are the template, and the template is the product.

### The Claude Code Skill (Shipped)

The skill is live at `.claude/commands/developable.md`. A Codex skill bundle ships alongside it at `skills/developable/SKILL.md`. Both package the full generation pipeline — parse → plan → assemble — as prompt instructions that the coding agent follows when writing files. No Python runtime, no `pip install`, no `ANTHROPIC_API_KEY` setup required.

The Python CLI (`main.py` + `deploy.py`) remains the proof and deployment vehicle. Every generation run, test, and deployment failure is a signal that refines the template the skill encodes.

### Current Output

Given a Prisma schema with annotations, the platform generates a complete, production-hardened backend with:

1. **Service Architecture** — Opinionated, modular design with clear layering (routes → controllers → repositories), ready for hexagonal or event-driven evolution
2. **Transactional Guarantees** — Idempotent operations, atomic Prisma transactions, safe compensation patterns for multi-step writes
3. **Security by Default** — Enforced auth/authz, ownership checks, input validation via Zod, sensitive-field hashing, server-side FK injection (no client-supplied owner IDs)
4. **Observability Built-In** — Structured error handling, typed error classes, and extension points for metrics, distributed tracing, and audit logging
5. **Comprehensive Testing** — Unit, integration, contract, and invariant-based tests ensuring correctness under edge cases and failure scenarios
6. **CI/CD Ready** — Prisma migrations, schema validation, test automation, and early-stage security scanning

---

## Backend Engineer Architecture

The platform is modelled as a **Backend Engineer** (`main.py`) that coordinates specialised agents. Each agent has a single responsibility and communicates through well-defined interfaces (spec dict and plan dict).

```
                         ┌─────────────────────────────┐
                         │     Backend Engineer         │
                         │         main.py              │
                         │  (orchestrates all agents)   │
                         └────────────┬────────────────┘
                                      │
     ┌────────────────────────────────┼────────────────────────────────┐
     │                │               │               │                │
┌────▼──────────┐ ┌───▼───────────┐ ┌▼─────────────┐ ┌▼────────────┐ ┌▼────────────────┐
│ Developer     │ │ Tester        │ │ Version       │ │ Terraform   │ │ Deployment      │
│ agents/       │ │ agents/       │ │ Control       │ │ agents/     │ │ agents/         │
│ developer.py  │ │ tester.py     │ │ agents/       │ │ terraform.py│ │ deployment.py   │
│               │ │               │ │ version_      │ │             │ │                 │
│ Planner +     │ │ TestPlanner + │ │ control.py    │ │ Terraform   │ │ Provisions DB + │
│ Assembler     ├─► Assembler     │ │               │ │ Planner +   │ │ container on    │
│ → Express API │ │ → test suite  │ │ VCPlanner +   │ │ Assembler   │ │ AWS/GCP/Heroku  │
└───────────────┘ └───────────────┘ │ Assembler     │ │ → HCL files │ │ → deploy.yml    │
  api_plan ───────────────────────► │ → Dockerfile  │ └─────────────┘ └─────────────────┘
                                    │ → CI/CD       │
                                    │ → git push    │
                                    └───────────────┘
```

### Agent Responsibilities

| Agent | File | Responsibility |
|---|---|---|
| Backend Engineer | `main.py` | CLI entry point; parses schema, loads rules, coordinates generation agents |
| Developer | `agents/developer.py` | Generates Express + TypeScript API (Planner → Assembler) |
| Tester | `agents/tester.py` | Generates Python integration test suite (TestPlanner → Assembler) |
| Version Control | `agents/version_control.py` | Generates infra files (Dockerfile, Compose, CI), initialises git, creates GitHub repo, pushes |
| Terraform | `agents/terraform.py` | Generates HCL IaC files (no cloud calls); invoked by `deploy.py` |
| Deployment | `agents/deployment.py` | Builds Docker image, provisions cloud resources (AWS/GCP/Heroku), records endpoint URL |

---

## Repository Structure

```
backend/
├── main.py                          # Backend Engineer: CLI orchestrator for generation agents
├── deploy.py                        # Deployment orchestrator: Terraform IaC + cloud provisioning
├── config.py                        # Paths, model name, LLM temperature
├── requirements.txt                 # Python dependencies
├── README.md                        # User-facing documentation
├── Dockerfile                       # Container for running the generator
├── PROGRESS.md                      # Development diary / in-progress notes
├── test_schema.prisma               # Example schema used for local testing
│
├── .claude/
│   └── commands/
│       └── developable.md           # Claude Code skill: /developable slash command
│
├── skills/
│   └── developable/
│       └── SKILL.md                 # Codex skill bundle: same workflow for Codex
│
├── agents/                          # Agent layer — each agent owns its generation domain
│   ├── developer.py                 # Developer agent: Express API (wraps Planner + Assembler)
│   ├── tester.py                    # Tester agent: Python test suite (wraps TestPlanner + Assembler)
│   ├── version_control.py           # Version Control agent: infra files, git init, GitHub push
│   ├── deployment.py                # Deployment agent: provision cloud resources, record endpoint URL
│   └── terraform.py                 # Terraform agent: generate HCL IaC files (no cloud calls)
│
├── core/                            # Shared infrastructure used by agents
│   ├── parser.py                    # PrismaParser: schema.prisma → structured spec dict
│   ├── planner.py                   # Planner: spec → API file plan (used by Developer)
│   ├── test_planner.py              # TestPlanner: spec + api_plan → test file plan (used by Tester)
│   ├── vc_planner.py                # VCPlanner: spec → infra file plan (used by Version Control)
│   ├── terraform_planner.py         # TerraformPlanner: spec → HCL file plan (used by Terraform agent)
│   ├── terraform_backend.py         # TerraformBackend: bootstraps S3/GCS state storage at deploy time
│   ├── assembler.py                 # Assembler: orchestrates TemplateGenerator + LLMGenerator; git-diff aware
│   ├── rules_parser.py              # BusinessRulesParser: merges YAML constraints into spec
│   ├── deployment_state.py          # DeploymentState: persists deployment record to .developable/state.json
│   ├── project_config.py            # ProjectConfig: reads/writes .developable/config.json (generate→deploy handoff)
│   ├── command_builder.py           # Translates skill config dict → CLI invocation strings
│   ├── llm_data.py                  # Deterministic test data generation (no LLM; format-aware field defaults)
│   ├── gitignore.py                 # DEFAULT_GITIGNORE_CONTENT + helpers used by VersionControl agent
│   └── providers/                   # Cloud provider abstractions (AWS, GCP, Heroku)
│
├── generators/
│   ├── base.py                      # BaseGenerator ABC + _cleanup_markdown utility
│   ├── template.py                  # TemplateGenerator: renders Jinja2 templates
│   └── llm.py                       # LLMGenerator: fills LLM_SECTION markers via Claude API
│
├── templates/
│   ├── express/
│   │   └── api/                     # Jinja2 templates for Express + TypeScript REST API output
│   │       ├── app.ts.j2            # Express app setup, router mounting, error handler
│   │       ├── server.ts.j2         # HTTP server bootstrap
│   │       ├── package.json.j2      # npm manifest with all dependencies
│   │       ├── tsconfig.json.j2     # TypeScript compiler config
│   │       ├── controller.ts.j2     # CRUD + nested-route handlers, ID validation, filter/sort, ownership guards
│   │       ├── routes.ts.j2         # Express Router wiring (auth middleware applied per method)
│   │       ├── repository.ts.j2     # Prisma data-access layer (findMany with filter/sort, findById, CRUD)
│   │       ├── validator.ts.j2      # Zod schema wrapper — boilerplate with LLM_SECTION for logic
│   │       ├── types.ts.j2          # TypeScript input/output types derived from entity fields
│   │       ├── auth.controller.ts.j2 # Register + login handlers, JWT signing, credential hashing
│   │       ├── auth.routes.ts.j2    # /auth/register and /auth/login route declarations
│   │       ├── auth.ts.j2           # JWT authenticate middleware (populates req.user)
│   │       ├── errors.ts.j2         # AppError hierarchy + Express error-handler middleware
│   │       ├── pagination.ts.j2     # parsePagination, parseListQuery, buildPaginatedResponse helpers
│   │       ├── prisma.ts.j2         # Singleton PrismaClient export
│   │       ├── crypto.ts.j2         # bcrypt hashValue / compareValue helpers
│   │       ├── env.example.j2       # .env.example with all required environment variables
│   │       ├── Dockerfile.j2        # Multi-stage Node.js 20 production container
│   │       ├── docker-compose.yml.j2 # Local dev stack: PostgreSQL, pgAdmin, API service
│   │       └── .github/
│   │           └── workflows/
│   │               └── ci.yml.j2    # GitHub Actions: install, migrate, start API, run tests
│   ├── terraform/
│   │   ├── aws/                     # AWS: ECS Fargate, ALB, RDS, ECR, VPC; state in S3+DynamoDB
│   │   │   ├── main.tf.j2
│   │   │   ├── variables.tf.j2
│   │   │   ├── outputs.tf.j2
│   │   │   └── backend.tf.j2
│   │   ├── gcp/                     # GCP: Cloud Run, Cloud SQL, Artifact Registry; state in GCS
│   │   │   ├── main.tf.j2
│   │   │   ├── variables.tf.j2
│   │   │   ├── outputs.tf.j2
│   │   │   └── backend.tf.j2
│   │   └── heroku/                  # Heroku: heroku_app, heroku-postgresql addon; state in Terraform Cloud
│   │       ├── main.tf.j2
│   │       ├── variables.tf.j2
│   │       ├── outputs.tf.j2
│   │       └── backend.tf.j2
│   └── tests/                       # Jinja2 templates for the Python integration test suite
│       ├── helpers.py.j2            # Shared HTTP client, auth helpers, state fixtures
│       ├── run_all.py.j2            # Sequential test runner
│       └── test_*.py.j2             # Per-feature test module templates
│
├── prompts/
│   ├── system.txt                   # Default system prompt fallback
│   ├── express/
│   │   ├── system.txt               # System prompt: senior backend engineer persona
│   │   └── validation_logic.txt     # Task prompt: Zod schema generation rules
│   └── tests/
│       ├── system.txt               # System prompt: test engineer persona
│       └── *.txt                    # Task prompts for test section generation
│
└── tests/                           # Reference tests for a blog-schema generated API
    ├── helpers.py
    ├── run_all.py
    └── test_*.py
```

### Template Subdirectory Convention

Templates under `templates/express/` are organised by **backend artifact type**. Currently only `api/` (REST API) exists. Future artifact types will add sibling directories:

```
templates/express/
├── api/          # REST API (CRUD routes, controllers, repositories) — implemented
├── cron/         # Scheduled jobs — planned
├── batch/        # Batch processing workers — planned
├── library/      # Reusable TypeScript library packages — planned
└── auth-lib/     # Standalone authentication library — planned
```

Each artifact type has its own templates and a matching `prompts/express/<type>/` directory for LLM task prompts.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Platform language | Python 3.11+ |
| Templating | Jinja2 3.1.2 (`StrictUndefined`, `trim_blocks`, `lstrip_blocks`) |
| AI model | Anthropic SDK (`anthropic>=0.49.0`), model `claude-sonnet-4-6` |
| Data validation (platform) | Pydantic v2 |
| Web framework (platform API) | FastAPI 0.104.1 + Uvicorn |
| **Generated stack** | |
| Language | TypeScript (ESM, Node 18+) |
| Framework | Express.js |
| ORM | Prisma |
| Validation | Zod |
| Auth | JWT (`jsonwebtoken`) + bcrypt |

---

## Environment Variables

```env
ANTHROPIC_API_KEY=sk-ant-...   # Required — Claude API key for LLM sections
GITHUB_TOKEN=ghp_...           # Optional — GitHub PAT for --github publishing (or pass via CLI)
GITHUB_USER=your-username      # Optional — GitHub username/org for --github publishing
```

The `.env` file is git-ignored. The platform exits early if `ANTHROPIC_API_KEY` is missing. `GITHUB_TOKEN` and `GITHUB_USER` can alternatively be supplied interactively when `--github` is used.

---

## Running the Platform

```bash
# Install dependencies
pip install -r requirements.txt

# Generate a project from a Prisma schema
python main.py path/to/schema.prisma --out ./output

# Also generate the integration test suite
python main.py path/to/schema.prisma --out ./output --tests-out ./tests

# Skip LLM calls (uses placeholder Zod schemas — useful for fast iteration)
python main.py path/to/schema.prisma --out ./output --no-llm

# Generate, then publish to a new GitHub repository (prompts for token/user if not set)
python main.py path/to/schema.prisma --out ./output --github

# Full run: tests + GitHub push, private repo, skip LLM
python main.py path/to/schema.prisma --out ./output --no-llm \
  --github --github-token ghp_... --github-user myorg --github-repo my-api --private

# Re-run after making manual edits — skip files you've modified, overwrite untouched files
python main.py path/to/schema.prisma --out ./output --no-llm

# Force-overwrite all files including user-modified ones
python main.py path/to/schema.prisma --out ./output --no-llm --force
```

After generation without `--github`, follow the printed next steps:

```bash
cd output
npm install
npx prisma migrate dev
npm run dev
```

After `--github`, the repository is live and CI runs automatically. For local Docker development:

```bash
cd output
cp .env.example .env   # fill in secrets
docker-compose up
```

To deploy to a cloud provider after generating:

```bash
# Write generation metadata (done automatically by main.py)
# Then run deploy.py — reads .developable/config.json for schema path, project name, etc.
python deploy.py --out ./output --deploy-to aws
python deploy.py --out ./output --deploy-to gcp --gcp-project my-project-id
python deploy.py --out ./output --deploy-to heroku
```

---

## Generation Pipeline

```
schema.prisma
     │
     ▼
PrismaParser (core/parser.py)
     │  Produces a "spec" dict:
     │  { entities[], datasource, auth_entity_name, env_vars }
     │
     ▼
Backend Engineer (main.py)
     │
     ├─► Developer agent (agents/developer.py)
     │        │
     │        ├─ Planner (core/planner.py)
     │        │    Produces an "api_plan" dict:
     │        │    { files: [ { path, template, context, needs_llm, llm_task } ] }
     │        │
     │        └─ Assembler (core/assembler.py)
     │               ├─ TemplateGenerator → Jinja2 render of the template with context
     │               └─ LLMGenerator      → Fills /* LLM_SECTION_START */ … /* LLM_SECTION_END */
     │                                      markers via Claude API
     │
     ├─► Tester agent (agents/tester.py)  [optional, if --tests-out is set or --github used]
     │        │
     │        ├─ TestPlanner (core/test_planner.py)
     │        │    Produces a "test_plan" dict based on spec + api_plan
     │        │
     │        └─ Assembler (core/assembler.py)
     │               Same Assembler, different templates and prompt_subdir="tests"
     │
     └─► Version Control agent (agents/version_control.py)  [optional, if --github is set]
              │
              ├─ VCPlanner (core/vc_planner.py)
              │    Produces a "vc_plan" dict: Dockerfile, docker-compose.yml, .github/workflows/ci.yml
              │
              ├─ Assembler (core/assembler.py)
              │    Renders infra templates (no LLM calls)
              │
              ├─ Writes .gitignore
              │
              ├─ git init → git add . → git commit → git branch -M main
              │
              ├─ GitHub API: POST /user/repos  →  creates repository
              │
              └─ git push -u origin main  →  triggers GitHub Actions CI
```

### LLM section mechanism

Templates contain `/* LLM_SECTION_START */` / `/* LLM_SECTION_END */` markers around placeholder logic. The `LLMGenerator` extracts each section, calls Claude with:

1. The task-specific prompt file from `prompts/express/<task>.txt`
2. The entity's name, scalar fields (name + TypeScript type + flags), and `llm_hints`
3. The existing placeholder text as additional context

The response replaces the section in the rendered file. Markdown fences are stripped automatically.

Currently LLM-filled files:

| File | Task | Prompt |
|---|---|---|
| `src/validators/<entity>.validator.ts` | `validation_logic` | `prompts/express/validation_logic.txt` |

---

## Schema Annotations

Annotations in `schema.prisma` control generator behaviour:

| Annotation | Location | Effect |
|---|---|---|
| `// @auth_entity` | Above a `model` block | Marks this model as the authentication principal; triggers auth controller + middleware generation |
| `// @llm sensitive` | On a field line | Marks field as sensitive (hashed at rest, excluded from JWT payload and API responses) |
| `// @llm <hint text>` | Above a `model` block | Free-text hints passed to the LLM for all logic sections on this entity |

Example:

```prisma
// @auth_entity
// @llm Users can only access their own posts
model User {
  id        Int      @id @default(autoincrement())
  email     String   @unique
  password  String   // @llm sensitive
  posts     Post[]
}

model Post {
  id       Int    @id @default(autoincrement())
  title    String
  content  String
  author   User   @relation(fields: [authorId], references: [id])
  authorId Int
}
```

---

## Spec Format (`PrismaParser` output)

```python
{
  "entities": [
    {
      "name": "User",
      "name_lower": "user",
      "name_plural": "users",
      "is_auth_entity": True,
      "auth_id_field": "id",           # actual PK field name
      "auth_id_ts_type": "number",     # "number" | "string"
      "auth_login_field": { ... },     # field dict used for login lookup (email preferred)
      "pk_ts_type": "number",          # "number" | "string" — drives _parseId vs _parseStringId
      "pk_strategy": "autoincrement",  # "autoincrement" | "uuid" | "cuid" | "none"
      "llm_hints": ["hint text", ...],
      "fields": [
        {
          "name": "id",
          "prisma_type": "Int",
          "ts_type": "number",
          "is_optional": False,
          "is_list": False,
          "is_id": True,
          "is_unique": False,
          "is_relation": False,
          "is_sensitive": False,       # True for fields marked // @llm sensitive
          "default": "autoincrement()",
          "pk_strategy": "autoincrement",  # set on @id fields only
          "annotations": ["@id", "@default(autoincrement())"]
        }
      ],
      "relations": [
        {
          "name": "posts",
          "related_entity": "Post",
          "type": "one_to_many",       # one_to_one | one_to_many | many_to_one
          "fk_field": None             # scalar FK name (many_to_one side only)
        }
      ]
    }
  ],
  "datasource": { "provider": "postgresql", "url": "env(\"DATABASE_URL\")" },
  "auth_entity_name": "User",         # None if no @auth_entity annotation
  "env_vars": ["DATABASE_URL"],       # all env("...") references in the schema
  "schema_path": "/path/to/schema.prisma"
}
```

---

## Security Invariants Enforced by Templates

These are non-negotiable behaviours baked into every generated API:

| Invariant | Where enforced |
|---|---|
| Integer ID validation — rejects floats, alpha, SQL-injection suffixes, overflow | `controller.ts.j2` `_parseId` |
| String ID validation — rejects whitespace, oversized strings; used for `uuid()`/`cuid()` PKs | `controller.ts.j2` `_parseStringId` (when `entity.pk_ts_type == "string"`) |
| Owner FK injected server-side from JWT, never accepted from request body | `controller.ts.j2` `create` + `validator.ts.j2` LLM hint |
| Auth entity self-ownership: users may only update/delete their own record | `controller.ts.j2` `update` / `remove` (`is_auth_entity` branch) |
| Resource ownership check before update/delete for owned resources | `controller.ts.j2` `update` / `remove` (`owner_fk_field` branch) |
| Sensitive fields hashed with bcrypt before storage | `auth.controller.ts.j2` |
| Sensitive fields excluded from JWT payload and all API responses | `auth.controller.ts.j2` `safeSelect` |
| JWT verified on all write routes and ownership-sensitive reads | `routes.ts.j2` + `auth.ts.j2` |
| Filter fields validated against allowlist — sensitive and unknown fields rejected (400) | `controller.ts.j2` `ALLOWED_FILTER_FIELDS` / `parseListQuery` |

---

## Planner Context Variables

Key variables available in each template category:

**All entity templates:**
- `entity` — full entity dict from the spec (includes `pk_ts_type`, `pk_strategy`)
- `auth_entity_name` — name of the auth entity, or `None`

**Controller / Routes:**
- `owner_fk_field` — scalar FK field name pointing to auth entity (e.g. `"authorId"`), or `None`
- `nested_routes` — list of `{ relation_name, related_entity, related_entity_lower, related_entity_plural, fk_field, filterable_fields, sortable_fields }` for one-to-many relations
- `filterable_fields` — non-id, non-relation, non-sensitive scalar fields; used to build `ALLOWED_FILTER_FIELDS`
- `sortable_fields` — same set as `filterable_fields`; used to build `ALLOWED_SORT_FIELDS`

**Validator:**
- `owner_fk_field` — same as above; injected as a `SERVER-INJECTED` comment into the LLM section so the model excludes it from Zod schemas

**Repository:**
- `filterable_fields` — used to generate per-field `where` clause helpers and type coercion for numeric fields

**Auth controller:**
- `auth_entity` — the entity dict
- `sensitive_fields` — list of fields with `is_sensitive: True`

**Infra templates (Dockerfile, docker-compose, CI):**
- `spec` — full spec dict
- `project_name` — slug-safe name derived from the first entity (e.g. `"user-api"`); used for database naming in docker-compose

**Terraform templates:**
- `spec` — full spec dict
- `project_name` — slug used for resource naming and state bucket defaults
- `provider_config` — provider-specific config dict (region, project ID, state bucket name, etc.)

---

## Development Conventions

- **Python 3.11+** required; use `dict[str, Any]` and `list[dict]` type hints (not `Dict`/`List` from `typing`)
- **Templates use Jinja2 `StrictUndefined`**: every variable referenced in a template must be present in its context dict or the render will raise an error — this is intentional to catch missing context early
- **LLM sections are for logic only**: structural code (imports, class/function signatures, error handling) lives in the template; only the business logic that varies per entity belongs in an LLM section
- **Prompt files are plain text** in `prompts/express/<task>.txt`; they describe the output rules and are prepended to the entity context before each LLM call
- **`--no-llm` mode must always produce valid TypeScript** (with empty Zod objects as placeholders) so the template pipeline can be tested without API calls
- **Tests in `tests/` run against the generated project** (a live Express server), not the generator itself; they are integration + security tests for the output
- **Infra templates are fully static** — `Dockerfile.j2`, `docker-compose.yml.j2`, and `ci.yml.j2` contain no LLM sections; `VCPlanner` always sets `needs_llm: False` for them
- **Terraform templates are fully static** — all `templates/terraform/**/*.j2` files render without LLM calls; `TerraformPlanner` always sets `needs_llm: False`
- **GitHub Actions expressions must be escaped** — wrap the entire CI template in `{% raw %} / {% endraw %}` to prevent Jinja2 from interpreting `${{ }}` as its own template syntax; Terraform HCL templates do NOT need this (HCL `${}` and Jinja2 `{{ }}` don't overlap — use `$${var}` if a literal HCL interpolation is needed)
- **`--force` flag controls re-generation safety** — without it, the Assembler checks `git diff HEAD` before overwriting each file; files with local changes are skipped to preserve user edits
- **`main.py` generates; `deploy.py` deploys** — generation metadata is persisted to `<out_dir>/.developable/config.json` by `main.py`; `deploy.py` reads it so cloud-specific flags don't need to be repeated

---

## Adding a New Template File (Express API)

1. Create `templates/express/api/<filename>.j2`
2. In `core/planner.py` (`Planner._plan_entity_files` or `_plan_project_files`), add a file plan entry:
   ```python
   {
       "path": "src/...",
       "template": "express/api/<filename>.j2",
       "context": { "entity": entity, ... },
       "needs_llm": False,   # True if it has LLM_SECTION markers
       "llm_task": "task_name",  # matches prompts/express/task_name.txt
   }
   ```
3. If `needs_llm: True`, add `prompts/express/task_name.txt` with generation rules

## Adding a New LLM Task

1. Add `prompts/express/<task>.txt` with clear rules for what the LLM should output
2. In the template, wrap the varying section:
   ```typescript
   /* LLM_SECTION_START */
   // Placeholder describing what should go here
   /* LLM_SECTION_END */
   ```
3. Set `needs_llm: True` and `llm_task: "<task>"` in the file plan entry
4. Pass any relevant context (e.g. `owner_fk_field`) through the file plan context, and reference it in the placeholder comment so the LLM sees it

## Adding a New Express Artifact Type

1. Create `templates/express/<artifact>/` with the new artifact's templates
2. Add `prompts/express/<artifact>/` with corresponding prompt files (including `system.txt`)
3. Create `agents/<artifact-agent>.py` with a new agent class following the Developer/Tester pattern
4. Add a new `Planner` subclass in `core/` for the new artifact's file plan
5. Wire the new agent into `main.py` with an appropriate CLI flag (e.g. `--cron-out`)

## Adding a New Target Framework

1. Create `templates/<framework>/api/` with templates mirroring the express/api structure
2. Add `prompts/<framework>/` with corresponding prompt files
3. Create `agents/<framework>_developer.py` with a new Developer variant
4. Add a new Planner class in `core/` that dispatches to the new framework's templates
5. Update `main.py` to accept a `--framework` flag and instantiate the right agent

---

## Claude Code Skill and Codex Bundle (Shipped)

Both delivery vehicles are live.

| Interface | File | Runtime |
|---|---|---|
| Claude Code `/developable` skill | `.claude/commands/developable.md` | Claude Code |
| Codex skill bundle | `skills/developable/SKILL.md` | Codex |

The skill encodes the full generation pipeline as prompt instructions — parse → plan → assemble — without requiring Python, npm, or an `ANTHROPIC_API_KEY`. Claude Code's native `Write`/`Edit`/`Bash` tools replace the `Assembler` + `LLMGenerator` layer; the security invariants, schema annotations, and file structure rules are encoded verbatim in the skill definition.

The Python CLI (`main.py` + `deploy.py`) remains alongside the skill for:
- Cloud deployment (Terraform generation + cloud provisioning)
- CI/CD integration (GitHub Actions wiring)
- Batch regeneration of existing projects

### What the Skill Encodes

- Schema annotation parsing rules (`@auth_entity`, `@llm sensitive`, `@llm <hint>`)
- All 9 security invariants (verbatim, non-negotiable)
- File generation plan (project files → per-entity files → auth files)
- Abbreviated structural templates for the 5 key file types (controller, repository, routes, validator, types)
- Plain-English schema generation mode: collect description → clarify → write `schema.prisma` + `rules.yaml` → confirm → generate

---

## Known Limitations

1. **Express only for Python CLI** — Fastify target is next; no other framework yet
2. **Single auth entity** — only one `// @auth_entity` per schema is supported (multi-tenant auth requires a second pass)
3. **No test suite for the generator itself** — only the generated projects are tested; consider adding pytest tests for `PrismaParser`, `Planner`, and template rendering
4. **Synchronous Anthropic client** — `LLMGenerator` uses the blocking SDK client; for parallel generation wrap calls with `asyncio.to_thread` or switch to `anthropic.AsyncAnthropic`
5. **No rate limiting or audit logging in generated output** — planned as next invariant layer
6. **GitHub token embedded in remote URL** — the VersionControl agent uses `https://<token>@github.com/...` to authenticate the push; the token may appear in `git remote -v` output inside the generated project
7. **CI uses `prisma db push` not `migrate deploy`** — freshly generated projects have no committed migration files, so CI uses `db push --accept-data-loss`; projects that adopt proper migrations should update the workflow step
8. **Terraform state bucket name collisions** — if the project-scoped S3 bucket name is taken by another AWS account, the Deployment agent falls back to `developablecode-terraform-state` and re-renders `backend.tf` accordingly
9. **Enum-typed PKs unsupported** — `_parseId` / `_parseStringId` only handle `Int` and `String` PKs; `@id` on an Enum field will fall back to integer parsing
