# Developable

Developable reads a Prisma schema and generates a complete, production-ready Express + TypeScript REST API — with security invariants and architectural rules baked structurally into every file, not as prompts you have to remember to follow.

The difference from asking an LLM to write a backend: the security rules are in the Jinja2 templates. The LLM cannot skip them, forget them, or override them. Every generated API enforces auth middleware on write routes, server-side FK injection, ownership checks before updates and deletes, sensitive-field hashing, and ID validation — regardless of what the model does with the rest of the code.

Use it primarily as a **Claude Code slash command** (`/developable`) or an **OpenAI Codex skill**. The Python CLI remains available as a secondary engine and automation fallback.

---

## Managed repos

Developable now treats generated and adopted backends as **managed repos**. After the first successful `/developable init` or `/developable adopt`, the repo gets a `.developable/` contract bundle that becomes the source of truth for:

- backend layer topology
- security invariants
- SOLID constraints
- route and ownership behavior
- local hook and CI conformance checks

If `.developable/contract.json` exists, later backend work should continue in managed mode even when the user does not explicitly invoke `/developable` again.

---

## How it works with Claude Code

**Claude Code** is Anthropic's AI coding assistant — available as a [CLI](https://docs.anthropic.com/en/docs/claude-code), a VS Code / JetBrains extension, and at [claude.ai/code](https://claude.ai/code). When you type `/developable` in a Claude Code session, it runs the Developable skill: an instruction set that tells Claude exactly how to parse your schema, which files to write, and which security rules are non-negotiable.

No separate server, no API calls from your machine — Claude Code handles everything using its own context window and built-in file tools.

**OpenAI Codex** works the same way: the skill ships as `AGENTS.md` instructions that Codex follows during a session.

---

## Install

### Claude Code

```bash
curl -sSL https://raw.githubusercontent.com/developableai/backend/main/install.sh | bash
```

Restart Claude Code. That's it — `/developable` is now available.

The command will appear in the `/` picker with a description. Type `/developable` and hit Enter — Claude Code handles the rest interactively.

### OpenAI Codex

```bash
$skill-installer install https://github.com/developableai/backend/tree/main/skills/developable
```

Or copy `skills/developable/SKILL.md` into your project's `AGENTS.md` manually.

Once installed, start a Codex session in your project directory and say: `run /developable` or just describe what you want — Codex will follow the skill instructions automatically.

### Python CLI

```bash
pip install developable
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## Usage

### Start from a description (no schema needed)

```
/developable "A task management app with users, projects, and tasks. Users log in with email."
```

The skill generates a `schema.prisma` with correct annotations and a `rules.yaml` with business constraints, shows them to you for review, lets you iterate, then generates the full API once you confirm.

### Start from an existing schema

Annotate your `schema.prisma`:

```prisma
// @auth_entity
// @llm Users can only access their own tasks
model User {
  id       Int    @id @default(autoincrement())
  email    String @unique
  password String // @llm sensitive
  tasks    Task[]
}

model Task {
  id      Int    @id @default(autoincrement())
  title   String
  done    Boolean @default(false)
  owner   User   @relation(fields: [ownerId], references: [id])
  ownerId Int
}
```

Then run:

```
/developable
```

The skill reads your schema, generates all API files, and writes them into your project. No Python runtime or API key needed for skill use.

### Python CLI

```bash
developable schema.prisma --out ./my-api

# Skip LLM calls — instant output, placeholder Zod schemas
developable schema.prisma --out ./my-api --no-llm

# Also generate the integration test suite
developable schema.prisma --out ./my-api --tests-out ./tests

# Push to a new GitHub repo and trigger CI
developable schema.prisma --out ./my-api --github

# Re-run after schema changes — skips files you've manually edited
developable schema.prisma --out ./my-api --no-llm

# Force-overwrite everything including user-modified files
developable schema.prisma --out ./my-api --no-llm --force
```

**After generation:**

```bash
cd my-api
npm install
npx prisma migrate dev --name init
npm run dev
# → http://localhost:3000
```

**Cloud deploy (CLI only):**

```bash
python deploy.py --out ./my-api --deploy-to aws
python deploy.py --out ./my-api --deploy-to gcp --gcp-project my-project-id
python deploy.py --out ./my-api --deploy-to heroku
```

---

## What gets generated

### Per entity

| File | Description |
|---|---|
| `src/routes/<entity>.routes.ts` | Express Router; JWT middleware applied per method |
| `src/controllers/<entity>.controller.ts` | HTTP handlers, ID validation, ownership guards |
| `src/repositories/<entity>.repository.ts` | Prisma data-access layer |
| `src/validators/<entity>.validator.ts` | Zod schemas generated by Claude for your domain |
| `src/types/<entity>.types.ts` | TypeScript input/output interfaces |

### Shared infrastructure

| File | Description |
|---|---|
| `src/auth.ts` | JWT `authenticate` middleware |
| `src/auth.controller.ts` | Register + login, JWT signing, bcrypt |
| `src/errors.ts` | `AppError` hierarchy + error-handler middleware |
| `src/pagination.ts` | `parsePagination` + `buildPaginatedResponse` |
| `src/app.ts` | Express app: helmet, cors, morgan, router mounting |

### DevOps

| File | Description |
|---|---|
| `Dockerfile` | Multi-stage Node 20 production image |
| `docker-compose.yml` | Local stack: PostgreSQL + pgAdmin + API |
| `.github/workflows/ci.yml` | GitHub Actions: install → migrate → start → test |
| `.env.example` | All required environment variables |

---

## REST endpoints

Every entity gets five routes:

| Method | Path | Auth |
|---|---|---|
| `GET` | `/api/{plural}` | Optional |
| `GET` | `/api/{plural}/:id` | Optional |
| `POST` | `/api/{plural}` | Required |
| `PUT` | `/api/{plural}/:id` | Required |
| `DELETE` | `/api/{plural}/:id` | Required |

Filtering, sorting, and pagination on all list endpoints:

```
GET /api/tasks?filter[done]=false&sort=createdAt&order=desc&page=1&limit=20
```

One-to-many relations generate nested routes automatically:

```
GET  /api/users/:id/tasks
POST /api/users/:id/tasks
```

---

## Security invariants

Every generated API enforces these unconditionally — they live in the templates, not in prompts:

| What is enforced |
|---|
| Integer IDs validated before Prisma is touched — rejects floats, alpha, SQL injection suffixes, overflow |
| String IDs (uuid/cuid PKs) validated — rejects whitespace and oversized strings |
| Owner FK stripped from request body, injected server-side from the verified JWT |
| Auth entity self-ownership — users can only update or delete their own record |
| Ownership check on every update and delete for resources owned by a user |
| Sensitive fields hashed with bcrypt before any database write |
| Sensitive fields excluded from JWT payload and all API responses |
| JWT verified on all write routes and ownership-sensitive reads |
| Filter fields validated against an allowlist — sensitive or unknown fields return 400 |

---

## Schema annotations

| Annotation | Where | Effect |
|---|---|---|
| `// @auth_entity` | Above a model | Marks the login principal; generates auth controller + JWT middleware |
| `// @llm sensitive` | On a field | Hashed at rest; excluded from JWT and all responses |
| `// @llm <hint>` | Above a model | Free-text hint forwarded to Claude when generating validators |

---

## Output scale and cost

Real measurements from full project runs with the Python CLI:

| Project | Entities | TypeScript | Tests | Cost |
|---|---|---|---|---|
| E-commerce API | 5 | ~1,200 lines | ~2,400 lines (100+ cases) | $0.08 |
| Project management API | 6 | ~1,450 lines | ~2,900 lines | $0.10 |

The Claude Code and Codex skills produce the same output at zero API cost (Claude Code's own context handles generation).

---

## Requirements

| | |
|---|---|
| Skill use (Claude Code / Codex) | No Python or Node required |
| CLI use | Python 3.11+, Node 18+, PostgreSQL |
| Anthropic API key | CLI only — not needed for skill use |

---

## Marketplace status

| Distribution | Status |
|---|---|
| `pip install developable` | Published — [pypi.org/project/developable](https://pypi.org/project/developable/) |
| Claude Code skill (`install.sh`) | Available |
| Claude Code plugin marketplace | Not yet submitted |
| OpenAI Codex skill registry | Available via GitHub URL above |
