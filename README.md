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

### Terraform IaC (optional)

When `--terraform` is passed to `deploy.py`, four `.tf` files are generated under `terraform/`:

| File | Description |
|------|-------------|
| `terraform/main.tf` | ECR/ECS/ALB/RDS (AWS), Cloud Run/Cloud SQL/Artifact Registry (GCP), or heroku_app/addon (Heroku) |
| `terraform/variables.tf` | Provider-specific inputs (region, project, passwords, image tag) |
| `terraform/outputs.tf` | Live endpoint URL, database connection string |
| `terraform/backend.tf` | Remote state: S3 + DynamoDB (AWS), GCS bucket (GCP), Terraform Cloud (Heroku) |

---

## REST Endpoints

Every entity gets these five routes automatically:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/{plural}` | Optional | Filtered, sorted, paginated list |
| `GET` | `/api/{plural}/:id` | Optional | Single record |
| `POST` | `/api/{plural}` | Required | Create (owner FK injected from JWT) |
| `PUT` | `/api/{plural}/:id` | Required | Partial update with ownership check |
| `DELETE` | `/api/{plural}/:id` | Required | Delete with ownership check |

**Filter and sort query params** on all list endpoints:

```
GET /api/posts?filter[title]=hello&sort=createdAt&order=asc&page=1&limit=20
```

- `filter[fieldName]=value` — restrict results; only non-sensitive scalar fields are allowed (400 otherwise)
- `sort=fieldName` — sort by any allowed field; defaults to `id desc`
- `order=asc|desc` — sort direction; defaults to `asc` when a sort field is given

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
| String ID validation — rejects whitespace, long strings; used when PK is `uuid()` / `cuid()` | `controller.ts.j2` `_parseStringId` |
| Owner FK server-injected from JWT, never from request body | `controller.ts.j2` create + `validator.ts.j2` LLM hint |
| Auth entity self-ownership: only update/delete your own record | `controller.ts.j2` `is_auth_entity` branch |
| Resource ownership check before any write on owned resources | `controller.ts.j2` `owner_fk_field` branch |
| Sensitive fields hashed before storage | `auth.controller.ts.j2` |
| Sensitive fields excluded from JWT payload and responses | `auth.controller.ts.j2` `safeSelect` |
| JWT verified on all write + ownership-sensitive read routes | `routes.ts.j2` + `auth.ts.j2` |
| Filter fields validated against allowlist — rejects sensitive or unknown fields | `controller.ts.j2` `ALLOWED_FILTER_FIELDS` |

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

Generation and deployment are split into two commands. `main.py` writes files; `deploy.py` provisions cloud infrastructure.

### `python main.py` — Generate

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
```

### `python deploy.py` — Deploy

```
python deploy.py --out DIR --deploy-to PROVIDER [options]

Core
  --out DIR              Output directory written by main.py  [default: ./output]
  --deploy-to PROVIDER   aws | gcp | heroku

AWS
  --aws-region REGION    AWS region  [default: us-east-1]

GCP
  --gcp-project ID       GCP project ID
  --gcp-region REGION    GCP region  [default: us-central1]
  --gcp-sa-path PATH     Path to GCP service account key JSON

Heroku
  --heroku-app NAME      Heroku app name  [default: derived from project]

GitHub (for pushing generated Terraform files)
  --github-token TOKEN   GitHub PAT
  --github-user USER     GitHub username or org
  --github-repo NAME     Repository name
```

**Common invocations:**

```bash
# Fast iteration — no LLM cost, instant output
python main.py schema.prisma --out ./my-api --no-llm

# Full generation with tests
python main.py schema.prisma --out ./my-api --tests-out ./tests

# Generate, push to GitHub, run CI automatically
python main.py schema.prisma --out ./my-api --github

# Re-run safely — only regenerates files you haven't touched
python main.py schema.prisma --out ./my-api --no-llm

# Force full regeneration (overwrites your edits)
python main.py schema.prisma --out ./my-api --no-llm --force

# Deploy to AWS after generating
python main.py schema.prisma --out ./my-api --github --github-token ghp_... --github-user myorg
python deploy.py --out ./my-api --deploy-to aws

# Deploy to GCP Cloud Run
python deploy.py --out ./my-api --deploy-to gcp --gcp-project my-project-id
```

---

## Agent Architecture

```
  main.py (generate)                      deploy.py (deploy)
  ────────────────────────────            ──────────────────────────────────────
  ┌──────────────────────────┐            ┌──────────────────────────────────┐
  │    Backend Engineer       │            │    Deployment Orchestrator        │
  │    main.py                │            │    deploy.py                      │
  └──────┬───────────────────┘            └──────┬────────────────────────────┘
         │                                       │
  ┌──────┼────────────────────┐          ┌───────┼────────────────────────────┐
  │      │                    │          │       │                            │
  ▼      ▼                    ▼          ▼       ▼                            ▼
Dev.  Tester           Version      Terraform  Deployment             (reads
Agent  Agent           Control      Agent      Agent                  .developable/
                       Agent        agents/    agents/                config.json
Planner  TestPlanner   VCPlanner    terraform  deployment             written by
  +        +             +          .py        .py                    main.py)
Assembler Assembler   Assembler
→ Express  → Python   → Dockerfile  → HCL      → Docker push
  API        tests      CI/CD          files      → Cloud Run
             suite      git push       terraform/  AWS ECS
                                       aws|gcp|    GCP
                                       heroku      Heroku
```

| Agent | File | Responsibility |
|-------|------|----------------|
| Backend Engineer | `main.py` | CLI entry point; parses schema, loads rules, coordinates generation agents |
| Developer | `agents/developer.py` | Generates Express + TypeScript API via Planner → Assembler |
| Tester | `agents/tester.py` | Generates Python integration test suite (TestPlanner → Assembler) |
| Version Control | `agents/version_control.py` | Infra files (Dockerfile, Compose, CI), git init, GitHub push |
| Terraform | `agents/terraform.py` | Generates HCL IaC files for AWS / GCP / Heroku (no cloud calls) |
| Deployment | `agents/deployment.py` | Builds Docker image, provisions cloud resources, records endpoint URL |

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

## Claude Code And Codex Skills

The CLI proves the template. The **Claude Code and Codex skills** deliver it — shipped.

Developable is available as a publishable `/developable` slash command for Claude Code and as a Codex skill bundle. These package the entire standard — file structure, security invariants, OOP patterns, validation rules — as instructions that the coding agent follows when writing or modifying any file in the project.

This changes the dynamic from "hope the LLM makes good decisions" to "the decisions are made; the LLM executes them." Every feature Claude Code adds to your backend conforms to the same invariants as the original generated output, across the full lifetime of the project.

| Interface | Location | Runtime required |
|-----------|----------|------------------|
| Claude Code skill | `.claude/commands/developable.md` | Claude Code only |
| Codex skill bundle | `skills/developable/SKILL.md` | Codex only |
| Python CLI | `main.py` + `deploy.py` | Python 3.11 + Node 18 |

```
# Inside Claude Code — generate from an existing schema
/developable

# Or start from a description
/developable "A task management app with users, projects, and tasks"
```

No Python runtime. No API key setup. No install beyond the skill itself.

### Starting from a plain-English description

Don't have a schema yet? Just describe your app:

```
/developable "A task management app with users, projects, and tasks. Users log in with email and password."
```

The skill will:
1. Generate a `schema.prisma` with correct Developable annotations
2. Generate a `rules.yaml` with entity constraints
3. Show you both files for review and let you iterate
4. Generate the full API once you confirm

---

## Roadmap

- [x] **Claude Code skill** — `/developable` slash command at `.claude/commands/developable.md`
- [x] **Codex skill bundle** — same workflow via `skills/developable/SKILL.md`
- [x] **Schema from prompt** — describe your app in plain English; skill generates `schema.prisma` + `rules.yaml` before codegen
- [x] **Filter and sort on list endpoints** — `?filter[field]=value&sort=field&order=asc|desc` on all GET-all routes
- [x] **UUID / cuid ID support** — `@default(uuid())` and `@default(cuid())` PKs; `_parseStringId` replaces `_parseId`
- [x] **Terraform IaC** — `terraform/` directory with remote state for AWS (S3+DynamoDB), GCP (GCS), Heroku (Terraform Cloud)
- [ ] Fastify target
