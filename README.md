```
██████╗ ███████╗██╗   ██╗███████╗██╗      ██████╗ ██████╗  █████╗ ██████╗ ██╗     ███████╗
██╔══██╗██╔════╝██║   ██║██╔════╝██║     ██╔═══██╗██╔══██╗██╔══██╗██╔══██╗██║     ██╔════╝
██║  ██║█████╗  ██║   ██║█████╗  ██║     ██║   ██║██████╔╝███████║██████╔╝██║     █████╗  
██║  ██║██╔══╝  ╚██╗ ██╔╝██╔══╝  ██║     ██║   ██║██╔═══╝ ██╔══██║██╔══██╗██║     ██╔══╝  
██████╔╝███████╗ ╚████╔╝ ███████╗███████╗╚██████╔╝██║     ██║  ██║██████╔╝███████╗███████╗
╚═════╝ ╚══════╝  ╚═══╝  ╚══════╝╚══════╝ ╚═════╝ ╚═╝     ╚═╝  ╚═╝╚═════╝ ╚══════╝╚══════╝
```

A stable, opinionated template for Express + TypeScript backends — and the tooling that makes any AI coding agent follow it.

---

## The Problem

When you hand an LLM your requirements and ask it to build a backend, it decides for itself how to structure the code, which security patterns to apply, and what OOP conventions to follow. The result works in isolation, but every session, every developer, and every project produces something different. There is no guarantee that ownership is enforced, that sensitive fields are handled correctly, or that the auth middleware is actually wired on the right routes.

This is not a prompt quality problem. It is a missing standard problem.

## What Developable Does

Developable is a **proven backend template** — a specific, non-negotiable answer to how an Express + TypeScript REST API should be built:

- Exact file structure: routes → controllers → repositories, one file per concern
- Security invariants that hold unconditionally: ID validation, server-side FK injection, auth middleware on all write routes, sensitive-field hashing, ownership checks before update/delete
- Consistent OOP patterns: controllers delegate, repositories own data access, errors propagate through a typed hierarchy
- Validated against multiple real schemas and deployed services

The CLI in this repo generates that template from a Prisma schema. The generated output is not a starting point you clean up — it is the standard, applied to your domain.

---

## What is this?

Developable is an **AI-native backend engineering platform** that reads your Prisma schema and generates a complete, production-ready Express + TypeScript REST API — not just CRUD skeletons, but a real system with:

- Transactional safety and atomic Prisma operations
- Security invariants baked in by default (JWT auth, ownership checks, input validation)
- AI-generated Zod validators tailored to your domain
- A full Python integration test suite (100+ test cases per project)
- Docker, docker-compose, GitHub Actions CI — all wired up
- One-command cloud deployment (AWS, Heroku, GCP Cloud Run)

**Input:** a `schema.prisma` file with lightweight annotations.  
**Output:** a shippable backend service.

---

## Quickstart

```bash
# 1. Install platform dependencies
pip install -r requirements.txt

# 2. Set your Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# 3. Generate
python main.py path/to/schema.prisma --out ./my-api

# 4. Run it
cd my-api && npm install && npx prisma migrate dev --name init && npm run dev
```

Server starts on `http://localhost:3000`.

> **Skip LLM calls** with `--no-llm` — validators use empty placeholders but the API compiles and runs immediately. Useful for iterating on schema structure without burning tokens.

---

## Generation Pipeline

```
  schema.prisma
       │
       ▼
  ┌────────────────────┐
  │   PrismaParser     │  Reads models, fields, relations, @auth_entity,
  │   core/parser.py   │  @llm annotations → produces a typed "spec" dict
  └────────┬───────────┘
           │  spec{}
           ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │                     Backend Engineer  (main.py)                    │
  │                    Orchestrates all four agents                     │
  └──────┬───────────────────┬──────────────────┬──────────────────────┘
         │                   │                  │                  │
         ▼                   ▼                  ▼                  ▼
  ┌─────────────┐   ┌────────────────┐  ┌──────────────┐  ┌──────────────────┐
  │  Developer  │   │    Tester      │  │   Version    │  │   Deployment     │
  │   Agent     │   │    Agent       │  │   Control    │  │     Agent        │
  │             │   │                │  │    Agent     │  │                  │
  │  Planner    │   │  TestPlanner   │  │  VCPlanner   │  │  Provider SDK    │
  │     +       │   │      +         │  │      +       │  │  (AWS/Heroku/    │
  │  Assembler  │   │  Assembler     │  │  Assembler   │  │   GCP)           │
  └──────┬──────┘   └───────┬────────┘  └──────┬───────┘  └──────┬───────────┘
         │                  │                  │                  │
         ▼                  ▼                  ▼                  ▼
   Express API        Python tests       Dockerfile +       Live endpoint
   TypeScript         100+ cases        docker-compose      URL printed
   fully typed        per project       GitHub Actions      to console
                                        CI/CD + git push
```

### LLM Section Mechanism

Templates contain `/* LLM_SECTION_START */` / `/* LLM_SECTION_END */` markers. The `LLMGenerator` extracts each section, calls Claude with the task prompt + entity context, and splices the response back in. Only domain-specific logic (Zod validation rules) is LLM-filled — all structural code lives in the Jinja2 templates.

```
Jinja2 template  ──►  TemplateGenerator  ──►  rendered file with placeholders
                                                         │
                                                         ▼
                                               LLMGenerator scans for
                                               LLM_SECTION markers
                                                         │
                                            ┌────────────┘
                                            │  Claude API call:
                                            │    system prompt (persona)
                                            │  + task prompt (rules)
                                            │  + entity context (fields, hints)
                                            │  + existing placeholder text
                                            └────────────►  filled section
                                                                  │
                                                                  ▼
                                                        final .ts / .py file
```

---

## What Gets Generated

### Per Entity

| File | Description |
|------|-------------|
| `src/routes/<entity>.routes.ts` | Express Router with JWT middleware wired per method |
| `src/controllers/<entity>.controller.ts` | HTTP handlers, ID validation, ownership guards |
| `src/repositories/<entity>.repository.ts` | Prisma data-access layer (findMany, findById, CRUD) |
| `src/validators/<entity>.validator.ts` | **AI-generated** Zod schemas matching your domain |
| `src/types/<entity>.types.ts` | TypeScript input/output interfaces |

### Shared Infrastructure

| File | Description |
|------|-------------|
| `src/auth.ts` | JWT `authenticate` middleware — populates `req.user` |
| `src/auth.controller.ts` | Register + login handlers, JWT signing, bcrypt |
| `src/errors.ts` | `AppError` hierarchy + Express error-handler middleware |
| `src/pagination.ts` | `parsePagination` + `buildPaginatedResponse` helpers |
| `src/prisma.ts` | Singleton `PrismaClient` export |
| `src/crypto.ts` | `hashValue` / `compareValue` bcrypt helpers |
| `src/app.ts` | Express app: helmet, cors, morgan, router mounting |

### Integration Tests (Python)

| File | Description |
|------|-------------|
| `tests/helpers.py` | HTTP client, auth helpers, shared fixtures |
| `tests/test_<entity>.py` | Full CRUD + security test suite per entity |
| `tests/test_auth.py` | Register, login, JWT, credential tests |
| `tests/run_all.py` | Sequential test runner with result summary |

### DevOps / Infra

| File | Description |
|------|-------------|
| `Dockerfile` | Multi-stage Node.js 20 production image |
| `docker-compose.yml` | Local dev stack: PostgreSQL + pgAdmin + API |
| `.github/workflows/ci.yml` | GitHub Actions: install → migrate → start → test |
| `.env.example` | All required environment variables documented |

---

## REST Endpoints

Every entity gets these five routes automatically:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/{plural}` | Optional | Paginated list — `?page=1&limit=20` |
| `GET` | `/api/{plural}/:id` | Optional | Single record |
| `POST` | `/api/{plural}` | Required | Create (owner FK injected from JWT) |
| `PUT` | `/api/{plural}/:id` | Required | Partial update with ownership check |
| `DELETE` | `/api/{plural}/:id` | Required | Delete with ownership check |

For one-to-many relations, nested routes are generated automatically:

```
GET    /api/users/:id/posts      → all posts belonging to user :id
POST   /api/users/:id/posts      → create a post owned by user :id
```

---

## Security Invariants

These are **non-negotiable behaviours** baked into every generated API — not suggestions, not best practices to remember to add later. They are structural, enforced by the templates.

```
  Request arrives
       │
       ▼
  ┌──────────────────────────────────────────────────────┐
  │  Route layer                                         │
  │  • JWT authenticate() middleware on all write routes │
  │  • req.user populated from verified token payload    │
  └──────────────────────────────┬───────────────────────┘
                                 │
                                 ▼
  ┌──────────────────────────────────────────────────────┐
  │  Controller layer                                    │
  │  • _parseId() rejects floats, alpha, SQL suffixes,   │
  │    overflow — returns 400 before Prisma is touched   │
  │  • Owner FK stripped from body, injected from JWT    │
  │  • Auth entity: req.user.id !== id → 403 Forbidden   │
  │  • Non-auth entity: DB ownership check before write  │
  └──────────────────────────────┬───────────────────────┘
                                 │
                                 ▼
  ┌──────────────────────────────────────────────────────┐
  │  Validator layer                                     │
  │  • Zod schema rejects unexpected or malformed input  │
  │  • owner FK explicitly excluded with SERVER-INJECTED │
  │    comment so the LLM never puts it in the schema   │
  └──────────────────────────────┬───────────────────────┘
                                 │
                                 ▼
  ┌──────────────────────────────────────────────────────┐
  │  Auth controller (register/login only)               │
  │  • Sensitive fields hashed with bcrypt before INSERT │
  │  • safeSelect excludes sensitive fields from JWT     │
  │    payload and all API responses                     │
  └──────────────────────────────────────────────────────┘
```

| Invariant | Enforced in |
|-----------|-------------|
| Integer ID validation — rejects floats, alpha, SQL injection, overflow | `controller.ts.j2` `_parseId` |
| Owner FK server-injected from JWT, never from request body | `controller.ts.j2` create + `validator.ts.j2` LLM hint |
| Auth entity self-ownership: only update/delete your own record | `controller.ts.j2` `is_auth_entity` branch |
| Resource ownership check before any write on owned resources | `controller.ts.j2` `owner_fk_field` branch |
| Sensitive fields hashed before storage | `auth.controller.ts.j2` |
| Sensitive fields excluded from JWT payload and responses | `auth.controller.ts.j2` `safeSelect` |
| JWT verified on all write + ownership-sensitive read routes | `routes.ts.j2` + `auth.ts.j2` |

---

## Schema Annotations

```prisma
// @auth_entity              ← marks this model as the authentication principal
// @llm Users can only access their own posts   ← free-text hint to Claude
model User {
  id        Int      @id @default(autoincrement())
  email     String   @unique
  password  String   // @llm sensitive   ← hashed at rest, excluded from responses
  posts     Post[]
}

model Post {
  id        Int    @id @default(autoincrement())
  title     String
  content   String
  author    User   @relation(fields: [authorId], references: [id])
  authorId  Int
}
```

| Annotation | Location | Effect |
|------------|----------|--------|
| `// @auth_entity` | Above a model block | Triggers auth controller + middleware generation |
| `// @llm sensitive` | On a field line | Field is hashed at rest and scrubbed from all responses |
| `// @llm <hint>` | Above a model block | Free-text hint forwarded to Claude for all LLM sections on this entity |

---

## CLI Reference

```
python main.py <schema.prisma> [options]

Core
  --out DIR              Output directory for the generated API  [default: ./output]
  --no-llm               Skip Claude API calls; use placeholder Zod schemas
  --force                Overwrite all files, including user-modified ones
  --rules FILE           Path to schema.rules.yaml with business logic constraints

Test generation
  --tests-out DIR        Generate Python integration test suite into this directory

Version Control (GitHub)
  --github               Initialise git, create GitHub repo, push
  --github-token TOKEN   GitHub Personal Access Token (or set GITHUB_TOKEN)
  --github-user USER     GitHub username or org (or set GITHUB_USER)
  --github-repo NAME     Repository name  [default: <first-entity>-api]
  --private              Create a private repository

Deployment
  --deploy               Deploy the generated project to a cloud provider
  --deploy-to PROVIDER   aws | heroku | gcp  [prompted interactively if omitted]
```

**Common invocations:**

```bash
# Fast iteration — no LLM cost, instant output
python main.py schema.prisma --out ./my-api --no-llm

# Full generation with tests
python main.py schema.prisma --out ./my-api --tests-out ./tests

# Generate, push to GitHub, run CI automatically
python main.py schema.prisma --out ./my-api --github

# Complete pipeline: generate → test → push → deploy
python main.py schema.prisma --out ./my-api --tests-out ./tests \
  --github --github-token ghp_... --github-user myorg \
  --deploy --deploy-to aws

# Re-run safely — only regenerates files you haven't touched
python main.py schema.prisma --out ./my-api --no-llm

# Force full regeneration (overwrites your edits)
python main.py schema.prisma --out ./my-api --no-llm --force
```

---

## Agent Architecture

```
                    ┌────────────────────────────────┐
                    │       Backend Engineer          │
                    │           main.py               │
                    │   CLI entry point, orchestrates │
                    │   all agents in sequence        │
                    └──────────────┬─────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         │                         │                         │
         ▼                         ▼                         ▼
┌─────────────────┐   ┌────────────────────┐   ┌──────────────────────┐
│  Developer      │   │  Tester Agent      │   │  Version Control     │
│  Agent          │   │  agents/tester.py  │   │  Agent               │
│  agents/        │   │                    │   │  agents/             │
│  developer.py   │   │  TestPlanner +     │   │  version_control.py  │
│                 │   │  Assembler         │   │                      │
│  Planner +      │   │  → Python test     │   │  VCPlanner +         │
│  Assembler      │   │    suite per       │   │  Assembler           │
│  → Express API  │   │    entity          │   │  → Dockerfile        │
│  (TypeScript)   │   │  → run_all.py      │   │  → docker-compose    │
│                 │   │  → helpers.py      │   │  → CI workflow       │
└────────┬────────┘   └──────────┬─────────┘   │  → git init + push   │
  api_plan ──────────────────────►             └──────────────────────┘
                                                          │
                                                          ▼
                                              ┌──────────────────────┐
                                              │  Deployment Agent    │
                                              │  agents/             │
                                              │  deployment.py       │
                                              │                      │
                                              │  Zero LLM cost —     │
                                              │  pure provider SDK   │
                                              │  AWS ECS / Heroku /  │
                                              │  GCP Cloud Run       │
                                              │  → live endpoint URL │
                                              └──────────────────────┘
```

| Agent | File | Responsibility |
|-------|------|----------------|
| Backend Engineer | `main.py` | CLI entry point; parses schema, loads rules, coordinates agents |
| Developer | `agents/developer.py` | Generates Express + TypeScript API via Planner → Assembler |
| Tester | `agents/tester.py` | Generates Python integration test suite (TestPlanner → Assembler) |
| Version Control | `agents/version_control.py` | Infra files (Dockerfile, Compose, CI), git init, GitHub push |
| Deployment | `agents/deployment.py` | Builds Docker image, deploys to cloud, records endpoint URL |

---

## Cost Profile

Generation is deliberately cheap. Real measurements from full project runs:

```
  E-commerce API (5 entities, 18 LLM calls)
  ─────────────────────────────────────────
  Input tokens    25,993  uncached
  Output tokens    4,998
  Estimated cost  $0.084

  Project Management API (6 entities, 22 LLM calls)
  ──────────────────────────────────────────────────
  Input tokens    31,030  uncached
  Output tokens    6,150
  Estimated cost  $0.102
```

**Output scale for an average project:**

```
  API source files
  ├── Controllers    5 × ~120 lines  =   600 lines
  ├── Repositories   5 ×  ~76 lines  =   380 lines
  ├── Utilities      5 ×  ~35 lines  =   175 lines
  └── Types/DTOs     5 ×  ~10 lines  =    50 lines
                                     ─────────────
  API total                          ~  1,205 lines TypeScript

  Test suite
  ├── Per-entity tests               ~2,400 lines Python (100+ cases)
  ├── Helpers + runner               ~  200 lines
                                     ─────────────
  Tests total                        ~  2,600 lines Python

  CI/CD workflow                          90 lines YAML
```

> Full project, 5–6 entities: **~1,300 lines of TypeScript + 2,600 lines of tests, under $0.11.**

---

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | 3.11+ |
| Node.js | 18+ |
| PostgreSQL | any (or any Prisma-supported database) |
| Anthropic API key | optional with `--no-llm` |
| GitHub PAT | optional, only for `--github` |
| Docker | optional, only for `--deploy` |

---

## After Generation

**Without `--github`:**

```bash
cd output
npm install
npx prisma migrate dev --name init
npm run dev
# → http://localhost:3000
```

**With `--github`:**

```bash
# Repository is created, code is pushed, GitHub Actions CI starts automatically.
# Open the printed GitHub URL to watch the first CI run.
```

**With Docker locally:**

```bash
cd output
cp .env.example .env   # fill in JWT_SECRET, DATABASE_URL, etc.
docker-compose up
# → API at http://localhost:3000, pgAdmin at http://localhost:5050
```

---

## The Long-Term Vision: A Claude Code Skill

The CLI proves the template. The **Claude Code skill** delivers it.

Once the template is stable, Developable ships as a publishable `/developable` slash command that any developer installs in Claude Code. The skill packages the entire standard — file structure, security invariants, OOP patterns, validation rules — as instructions that Claude Code follows when writing or modifying any file in the project.

This changes the dynamic from "hope the LLM makes good decisions" to "the decisions are made; the LLM executes them." Every feature Claude Code adds to your backend conforms to the same invariants as the original generated output, across the full lifetime of the project.

```
# Future usage (inside Claude Code)
/developable path/to/schema.prisma
```

No Python runtime. No API key setup. No install beyond the skill itself.

---

## Roadmap

- [ ] **Claude Code skill** — package the proven template as a publishable `/developable` slash command
- [ ] Refinement loop — request changes in plain English, developable patches the relevant files
- [ ] Filter and sort on list endpoints
- [ ] UUID / cuid ID support
- [ ] Fastify target
