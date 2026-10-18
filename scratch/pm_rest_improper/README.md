# PM REST Improper

This backend is intentionally written in a way that does **not** follow Developable's structure or security invariants.

## Why it is improper

- one large API file instead of routes/controllers/services/repositories
- raw Prisma calls directly in request handlers
- fake auth based on `x-user-id`, query params, or request body
- plaintext passwords
- no meaningful authorization or ownership enforcement
- broad relation exposure in responses
- minimal validation and noisy error leakage

## Run

```bash
npm install
cp .env.example .env
npm run prisma:generate
npm run prisma:push
npm run dev
```

The API listens on `http://localhost:3000`.
