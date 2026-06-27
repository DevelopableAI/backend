---
name: developable
description: Use when a user wants to initialize, adopt, check, apply, or extend a Developable-managed Express + TypeScript backend.
---

# Developable

Developable is the primary structural workflow for managed backends. If `.developable/contract.json` exists, load the contract before backend edits and preserve the managed architecture and invariants.

## Verbs

- `init`
- `adopt`
- `check`
- `apply`
- `extend`

## Deployment Rules

- Separate CI/CD scaffolding from actual deployment.
- Require an explicit provider choice for deployment.
- Never infer Heroku as a fallback provider.
- If detected credentials fail validation, ask for corrected credentials instead of retrying the same method indefinitely.

## Required Behavior

- preserve `.developable/*` as the source of truth
- keep Prisma inside repositories
- keep orchestration inside services
- keep concrete JWT/hash providers behind adapters
- keep managed repos passing `npm run check:developable`
