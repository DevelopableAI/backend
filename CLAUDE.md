# CLAUDE.md — Developable Backend

## Project Vision

**Developable** is an **AI-native backend engineering platform** that generates and evolves production-ready systems with built-in invariants: transactional safety, observability, security by design, and comprehensive test coverage—not just endpoints.

**Input:** Creators provide a domain model (Prisma schema with annotations for business rules, auth boundaries, and data sensitivity) rather than natural language.

**Output:** A complete, production-hardened backend service with:

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
          ┌──────────────────────┼──────────────────────┐
          │                      │                       │
┌─────────▼───────────┐ ┌────────▼────────────┐ ┌───────▼──────────────────┐
│   Developer Agent   │ │   Tester Agent      │ │  Version Control Agent   │
│  agents/developer.py│ │  agents/tester.py   │ │  agents/version_control.py│
│                     │ │                     │ │                          │
│  Planner + Assembler│ │ TestPlanner +       │ │  VCPlanner + Assembler   │
│  → Express API      ├─► Assembler           │ │  → Dockerfile,           │
└─────────────────────┘ │ → Python test suite │ │    docker-compose.yml,   │
        api_plan ───────►                     │ │    GitHub Actions CI     │
                        └─────────────────────┘ │  → git init + push       │
                                                └──────────────────────────┘
```

**Agents planned but not yet implemented:**
- **Deployment Agent** — sets up and maintains CI/CD pipelines to deploy the backend artifact

### Agent Responsibilities

| Agent | File | Responsibility |
|---|---|---|
| Backend Engineer | `main.py` | CLI entry point; parses schema, loads rules, coordinates agents |
| Developer | `agents/developer.py` | Generates Express + TypeScript API (Planner → Assembler) |
| Tester | `agents/tester.py` | Generates Python integration test suite (TestPlanner → Assembler) |
| Version Control | `agents/version_control.py` | Generates infra files (Dockerfile, Compose, CI), initialises git, creates GitHub repo, pushes |

---

## Repository Structure

```
backend/
├── main.py                          # Backend Engineer: CLI orchestrator for all agents
├── config.py                        # Paths, model name, LLM temperature
├── requirements.txt                 # Python dependencies
├── README.md                        # User-facing documentation
├── Dockerfile                       # Container for running the generator
├── PROGRESS.md                      # In-progress feature notes
├── test_schema.prisma               # Example schema used for local testing
│
├── agents/                          # Agent layer — each agent owns its generation domain
│   ├── developer.py                 # Developer agent: Express API (wraps Planner + Assembler)
│   ├── tester.py                    # Tester agent: Python test suite (wraps TestPlanner + Assembler)
│   └── version_control.py           # Version Control agent: infra files, git init, GitHub push
│
├── core/                            # Shared infrastructure used by agents
│   ├── parser.py                    # PrismaParser: schema.prisma → structured spec dict
│   ├── planner.py                   # Planner: spec → API file plan (used by Developer)
│   ├── test_planner.py              # TestPlanner: spec + api_plan → test file plan (used by Tester)
│   ├── vc_planner.py                # VCPlanner: spec → infra file plan (used by Version Control)
│   ├── assembler.py                 # Assembler: orchestrates TemplateGenerator + LLMGenerator; git-diff aware
│   └── rules_parser.py              # BusinessRulesParser: merges YAML constraints into spec
│
├── generators/
│   ├── base.py                      # BaseGenerator ABC + _cleanup_markdown utility
│   ├── template.py                  # TemplateGenerator: renders Jinja2 templates
│   └── llm.py                       # LLMGenerator: fills LLM_SECTION markers via Claude API
│
├── templates/
│   └── express/
│       └── api/                     # Jinja2 templates for Express + TypeScript REST API output
│           ├── app.ts.j2            # Express app setup, router mounting, error handler
│           ├── server.ts.j2         # HTTP server bootstrap
│           ├── package.json.j2      # npm manifest with all dependencies
│           ├── tsconfig.json.j2     # TypeScript compiler config
│           ├── controller.ts.j2     # CRUD + nested-route handlers, ID validation, ownership guards
│           ├── routes.ts.j2         # Express Router wiring (auth middleware applied per method)
│           ├── repository.ts.j2     # Prisma data-access layer (findMany, findById, create, update, delete)
│           ├── validator.ts.j2      # Zod schema wrapper — boilerplate with LLM_SECTION for logic
│           ├── types.ts.j2          # TypeScript input/output types derived from entity fields
│           ├── auth.controller.ts.j2 # Register + login handlers, JWT signing, credential hashing
│           ├── auth.routes.ts.j2    # /auth/register and /auth/login route declarations
│           ├── auth.ts.j2           # JWT authenticate middleware (populates req.user)
│           ├── errors.ts.j2         # AppError hierarchy + Express error-handler middleware
│           ├── pagination.ts.j2     # parsePagination + buildPaginatedResponse helpers
│           ├── prisma.ts.j2         # Singleton PrismaClient export
│           ├── crypto.ts.j2         # bcrypt hashValue / compareValue helpers
│           ├── env.example.j2       # .env.example with all required environment variables
│           ├── Dockerfile.j2        # Multi-stage Node.js 20 production container
│           ├── docker-compose.yml.j2 # Local dev stack: PostgreSQL, pgAdmin, API service
│           └── .github/
│               └── workflows/
│                   └── ci.yml.j2    # GitHub Actions: install, migrate, start API, run tests
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
      "auth_id_ts_type": "number",
      "auth_login_field": { ... },     # field dict used for login lookup (email preferred)
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
| Owner FK injected server-side from JWT, never accepted from request body | `controller.ts.j2` `create` + `validator.ts.j2` LLM hint |
| Auth entity self-ownership: users may only update/delete their own record | `controller.ts.j2` `update` / `remove` (`is_auth_entity` branch) |
| Resource ownership check before update/delete for owned resources | `controller.ts.j2` `update` / `remove` (`owner_fk_field` branch) |
| Sensitive fields hashed with bcrypt before storage | `auth.controller.ts.j2` |
| Sensitive fields excluded from JWT payload and all API responses | `auth.controller.ts.j2` `safeSelect` |
| JWT verified on all write routes and ownership-sensitive reads | `routes.ts.j2` + `auth.ts.j2` |

---

## Planner Context Variables

Key variables available in each template category:

**All entity templates:**
- `entity` — full entity dict from the spec
- `auth_entity_name` — name of the auth entity, or `None`

**Controller / Routes:**
- `owner_fk_field` — scalar FK field name pointing to auth entity (e.g. `"authorId"`), or `None`
- `nested_routes` — list of `{ relation_name, related_entity, related_entity_lower, related_entity_plural, fk_field }` for one-to-many relations

**Validator:**
- `owner_fk_field` — same as above; injected as a `SERVER-INJECTED` comment into the LLM section so the model excludes it from Zod schemas

**Auth controller:**
- `auth_entity` — the entity dict
- `sensitive_fields` — list of fields with `is_sensitive: True`

**Infra templates (Dockerfile, docker-compose, CI):**
- `spec` — full spec dict
- `project_name` — slug-safe name derived from the first entity (e.g. `"user-api"`); used for database naming in docker-compose

---

## Development Conventions

- **Python 3.11+** required; use `dict[str, Any]` and `list[dict]` type hints (not `Dict`/`List` from `typing`)
- **Templates use Jinja2 `StrictUndefined`**: every variable referenced in a template must be present in its context dict or the render will raise an error — this is intentional to catch missing context early
- **LLM sections are for logic only**: structural code (imports, class/function signatures, error handling) lives in the template; only the business logic that varies per entity belongs in an LLM section
- **Prompt files are plain text** in `prompts/express/<task>.txt`; they describe the output rules and are prepended to the entity context before each LLM call
- **`--no-llm` mode must always produce valid TypeScript** (with empty Zod objects as placeholders) so the template pipeline can be tested without API calls
- **Tests in `tests/` run against the generated project** (a live Express server), not the generator itself; they are integration + security tests for the output
- **Infra templates are fully static** — `Dockerfile.j2`, `docker-compose.yml.j2`, and `ci.yml.j2` contain no LLM sections; `VCPlanner` always sets `needs_llm: False` for them
- **GitHub Actions expressions must be escaped** — wrap the entire CI template in `{% raw %} / {% endraw %}` to prevent Jinja2 from interpreting `${{ }}` as its own template syntax
- **`--force` flag controls re-generation safety** — without it, the Assembler checks `git diff HEAD` before overwriting each file; files with local changes are skipped to preserve user edits

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

## Known Limitations

1. **Express only** — no FastAPI or other framework target yet
2. **Single auth entity** — only one `// @auth_entity` per schema is supported
3. **Integer PKs only** — `_parseId` assumes numeric IDs; string/UUID PKs require a parallel `_parseStringId` path
4. **No test suite for the generator itself** — only the generated projects are tested; consider adding pytest tests for `PrismaParser`, `Planner`, and template rendering
5. **Synchronous Anthropic client** — `LLMGenerator` uses the blocking SDK client; for parallel generation wrap calls with `asyncio.to_thread` or switch to `anthropic.AsyncAnthropic`
6. **No rate limiting or audit logging in generated output** — planned as next invariant layer
7. **GitHub token embedded in remote URL** — the VersionControl agent uses `https://<token>@github.com/...` to authenticate the push; the token may appear in `git remote -v` output inside the generated project
8. **CI uses `prisma db push` not `migrate deploy`** — freshly generated projects have no committed migration files, so CI uses `db push --accept-data-loss`; projects that adopt proper migrations should update the workflow step
9. **Deployment agent not yet implemented** — placeholder for future development
