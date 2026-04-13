# developable

Point it at a `schema.prisma`. Get a fully working Express + TypeScript REST API.

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

## Roadmap

- [ ] Refinement loop — request changes in plain English, developable patches the relevant files
- [ ] Filter and sort on list endpoints
- [ ] Relation-aware endpoints (`GET /users/:id/posts`)
- [ ] UUID / cuid ID support
- [ ] Fastify target