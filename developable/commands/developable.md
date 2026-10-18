---
description: Initialize, adopt, check, apply, or extend a Developable-managed Express + TypeScript backend.
---

# /developable

Developable is a managed-backend command. Its job is to create and preserve the repo-local contract stored in `.developable/*`.

## Managed Mode

If `.developable/contract.json` exists, load `.developable/*` before backend edits and keep the repo inside the managed route/controller/service/repository/adapter/bootstrap architecture.

## Verbs

- `/developable init`
- `/developable adopt`
- `/developable check`
- `/developable apply`
- `/developable extend`

## Deployment Rules

- Do not assume the user wants immediate live deployment.
- Separate `CI/CD only`, `IaC only`, and `deploy now`.
- Require an explicit provider choice for `deploy now`.
- Never default to `Heroku`.

## Standards

- managed backends must pass `npm run check:developable`
- Prisma access belongs only in repositories
- controllers depend on services, not repositories
- JWT/hash providers live behind adapters/services
