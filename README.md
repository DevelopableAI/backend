# developable

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

## What it generates

For every model in your schema you get:

- A **repository** wrapping Prisma with `findMany`, `findById`, `create`, `update`, `delete`
- A **controller** that handles HTTP and delegates to the repository
- A **validator** with AI-generated Zod schemas based on your field names and types
- A **router** wired up with 5 CRUD endpoints
- **TypeScript interfaces** for create and update inputs

Plus shared infrastructure: pagination, a typed error hierarchy, Prisma singleton, Express app with helmet/cors/morgan, and a health check endpoint.

---

## Quickstart

```bash
pip install jinja2 anthropic
export ANTHROPIC_API_KEY=sk-ant-...

python main.py path/to/schema.prisma --out ./my-api
```

Then run the generated project:

```bash
cd my-api
npm install
npx prisma migrate dev --name init
npm run dev
```

Server starts on `http://localhost:3000`.

> Run with `--no-llm` to skip Claude API calls entirely. Validators will have empty placeholder schemas.

---

## Endpoints

Every entity gets these five routes:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/{plural}` | Paginated list (`?page=1&limit=20`) |
| GET | `/api/{plural}/:id` | Single record |
| POST | `/api/{plural}` | Create |
| PUT | `/api/{plural}/:id` | Partial update |
| DELETE | `/api/{plural}/:id` | Delete |

---

## Schema annotations

Add `// @llm` comments above a model to pass custom instructions into the generation prompt:

```prisma
// @llm add soft delete support using a deletedAt field
// @llm prevent deletion if the user has active posts
model User {
  id    Int    @id @default(autoincrement())
  email String @unique
  ...
}
```

---

## Requirements

- Python 3.11+
- Node.js 18+
- PostgreSQL (or any Prisma-supported database)
- Anthropic API key (optional with `--no-llm`)

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