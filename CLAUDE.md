# CLAUDE.md — Developable Backend

## Project Vision

**Developable** an **AI-native backend engineering platform** that generates and evolves production-ready systems with built-in invariants: transactional safety, observability, security by design, and comprehensive test coverage—not just endpoints.

**Input:** Creators provide a domain model (Prisma schema with annotations for business rules, auth boundaries, and data sensitivity) rather than natural language.

**Output:** A complete, production-hardened backend service with:

1. **Service Architecture** — Opinionated, modular design with clear layering (routes → controllers → repositories), ready for hexagonal or event-driven evolution
2. **Transactional Guarantees** — Idempotent operations, atomic Prisma transactions, safe compensation patterns for multi-step writes
3. **Security by Default** — Enforced auth/authz, ownership checks, input validation via Zod, sensitive-field hashing, server-side FK injection (no client-supplied owner IDs)
4. **Observability Built-In** — Structured error handling, typed error classes, and extension points for metrics, distributed tracing, and audit logging
5. **Comprehensive Testing** — Unit, integration, contract, and invariant-based tests ensuring correctness under edge cases and failure scenarios
6. **CI/CD Ready** — Prisma migrations, schema validation, test automation, and early-stage security scanning

---

## Repository Structure

```
backend/
├── main.py                          # CLI entry point: parse → plan → assemble
├── config.py                        # Paths, model name, LLM temperature, boilerplate list
├── requirements.txt                 # Python dependencies
├── README.md                        # User-facing documentation
├── Dockerfile                       # Container for running the generator
├── PROGRESS.md                      # In-progress feature notes
├── test_schema.prisma               # Example schema used for local testing
│
├── core/
│   ├── parser.py                    # PrismaParser: schema.prisma → structured spec dict
│   ├── planner.py                   # Planner: spec → file plan (template + context + LLM task)
│   └── assembler.py                 # Assembler: orchestrates TemplateGenerator + LLMGenerator
│
├── generators/
│   ├── base.py                      # BaseGenerator ABC + _cleanup_markdown utility
│   ├── template.py                  # TemplateGenerator: renders Jinja2 templates
│   └── llm.py                       # LLMGenerator: fills LLM_SECTION markers via Claude API
│
├── templates/
│   └── express/                     # Jinja2 templates for the Express + TypeScript output
│       ├── app.ts.j2                # Express app setup, router mounting, error handler
│       ├── server.ts.j2             # HTTP server bootstrap
│       ├── package.json.j2          # npm manifest with all dependencies
│       ├── tsconfig.json.j2         # TypeScript compiler config
│       ├── controller.ts.j2         # CRUD + nested-route handlers, ID validation, ownership guards
│       ├── routes.ts.j2             # Express Router wiring (auth middleware applied per method)
│       ├── repository.ts.j2         # Prisma data-access layer (findMany, findById, create, update, delete)
│       ├── validator.ts.j2          # Zod schema wrapper — boilerplate with LLM_SECTION for logic
│       ├── types.ts.j2              # TypeScript input/output types derived from entity fields
│       ├── auth.controller.ts.j2    # Register + login handlers, JWT signing, credential hashing
│       ├── auth.routes.ts.j2        # /auth/register and /auth/login route declarations
│       ├── auth.ts.j2               # JWT authenticate middleware (populates req.user)
│       ├── errors.ts.j2             # AppError hierarchy + Express error-handler middleware
│       ├── pagination.ts.j2         # parsePagination + buildPaginatedResponse helpers
│       ├── prisma.ts.j2             # Singleton PrismaClient export
│       ├── crypto.ts.j2             # bcrypt hashValue / compareValue helpers
│       └── env.example.j2           # .env.example with all required environment variables
│
├── prompts/
│   ├── system.txt                   # System prompt: senior backend engineer persona
│   └── express/
│       └── validation_logic.txt     # Task prompt: Zod schema generation rules
│
└── tests/                           # Tests for the *generated* Express API (not the generator)
    ├── helpers.py                   # Shared HTTP client, auth helpers, state fixtures
    ├── run_all.py                   # Sequential test runner
    ├── test_00_health.py            # Health check
    ├── test_01_register.py          # User registration
    ├── test_02_login.py             # Login + JWT issuance
    ├── test_03_users_get.py         # User list + get-by-ID
    ├── test_04_users_write.py       # User update + delete (ownership enforced)
    ├── test_05_posts_seed_get.py    # Post creation seed + list/get
    ├── test_06_posts_write.py       # Post update + delete (authorship enforced)
    ├── test_07_comments_seed_get.py # Comment seed + list/get
    ├── test_08_comments_write.py    # Comment update + delete
    ├── test_09_nested_users_get.py  # GET /users/:id/posts, /users/:id/comments
    ├── test_10_nested_users_posts.py   # POST /users/posts (auth token → parentId)
    ├── test_11_nested_users_comments.py
    ├── test_12_nested_posts_comments.py
    ├── test_13_token_security.py    # Missing/invalid/expired JWT rejection
    ├── test_14_input_validation.py  # Malformed bodies, invalid IDs, edge cases
    ├── test_15_response_structure.py # Response shape contracts
    ├── test_16_security_audit.py    # Ownership violations, SQL injection, overflow
    └── test_17_cleanup.py           # Delete all seeded data
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| Generator language | Python 3.11+ |
| Templating | Jinja2 3.1.2 (`StrictUndefined`, `trim_blocks`, `lstrip_blocks`) |
| AI model | Anthropic SDK (`anthropic>=0.49.0`), model `claude-sonnet-4-6` |
| Data validation (generator) | Pydantic v2 |
| Web framework (generator API) | FastAPI 0.104.1 + Uvicorn |
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
```

The `.env` file is git-ignored. The generator exits early if `ANTHROPIC_API_KEY` is missing.

---

## Running the Generator

```bash
# Install dependencies
pip install -r requirements.txt

# Generate a project from a Prisma schema
python main.py path/to/schema.prisma --out ./output

# Skip LLM calls (uses placeholder Zod schemas — useful for fast iteration)
python main.py path/to/schema.prisma --out ./output --no-llm
```

After generation, follow the printed next steps:

```bash
cd output
npm install
npx prisma migrate dev
npm run dev
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
Planner (core/planner.py)
     │  Produces a "plan" dict:
     │  { files: [ { path, template, context, needs_llm, llm_task } ] }
     │
     ▼
Assembler (core/assembler.py)
     │
     ├─ TemplateGenerator  → Jinja2 render of the template with context
     │
     └─ LLMGenerator       → Finds /* LLM_SECTION_START */ … /* LLM_SECTION_END */
                             markers, calls Claude with the task prompt + entity context,
                             replaces each section with generated TypeScript
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

---

## Development Conventions

- **Python 3.11+** required; use `dict[str, Any]` and `list[dict]` type hints (not `Dict`/`List` from `typing`)
- **Templates use Jinja2 `StrictUndefined`**: every variable referenced in a template must be present in its context dict or the render will raise an error — this is intentional to catch missing context early
- **LLM sections are for logic only**: structural code (imports, class/function signatures, error handling) lives in the template; only the business logic that varies per entity belongs in an LLM section
- **Prompt files are plain text** in `prompts/express/<task>.txt`; they describe the output rules and are prepended to the entity context before each LLM call
- **`--no-llm` mode must always produce valid TypeScript** (with empty Zod objects as placeholders) so the template pipeline can be tested without API calls
- **Tests in `tests/` run against the generated project** (a live Express server), not the generator itself; they are integration + security tests for the output

---

## Adding a New Template File

1. Create `templates/express/<filename>.j2`
2. In `Planner._plan_entity_files` or `_plan_project_files`, add a file plan entry:
   ```python
   {
       "path": "src/...",
       "template": "express/<filename>.j2",
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

## Adding a New Target Framework

1. Create `templates/<framework>/` with templates mirroring the express structure
2. Add `prompts/<framework>/` with corresponding prompt files
3. Extend `Planner._plan_entity_files` to dispatch on `spec["target_framework"]`
4. Update `PrismaParser` or `main.py` if the new framework requires additional spec fields

---

## Known Limitations

1. **Express only** — no FastAPI or other framework target yet
2. **Single auth entity** — only one `// @auth_entity` per schema is supported
3. **Integer PKs only** — `_parseId` assumes numeric IDs; string/UUID PKs require a parallel `_parseStringId` path
4. **No test suite for the generator itself** — only the generated projects are tested; consider adding pytest tests for `PrismaParser`, `Planner`, and template rendering
5. **Synchronous Anthropic client** — `LLMGenerator` uses the blocking SDK client; for parallel generation wrap calls with `asyncio.to_thread` or switch to `anthropic.AsyncAnthropic`
6. **No rate limiting or audit logging in generated output** — planned as next invariant layer
