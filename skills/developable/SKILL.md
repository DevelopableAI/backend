---
name: developable
description: Use when a user wants to initialize, adopt, check, apply, or extend a Developable-managed Express + TypeScript backend.
---

# Developable

Developable is the primary structural workflow for managed backends. It must preserve the repo-local contract in `.developable/*`, not just generate files once.

## Use This Skill When

- the user wants a new backend generated from `schema.prisma`
- the user wants an existing Express + TypeScript backend adopted into the Developable standard
- the user wants to check or repair backend conformance
- the user wants to add backend features in a repo that already contains `.developable/contract.json`

## Managed Mode Rules

If `.developable/contract.json` exists, before backend edits:

1. load `.developable/contract.json`
2. load `.developable/invariants.yaml`
3. load `.developable/architecture.yaml`
4. load `.developable/solid.yaml`
5. load `.developable/routes.json`
6. load `.developable/composition.json`

Then:

- plan edits against the managed contract
- keep code inside the declared route/controller/service/repository/adapter/bootstrap shape
- reject or refactor drift instead of silently allowing it

## Command Model

- `init`: create a new managed backend and write `.developable/*`
- `adopt`: inspect an existing backend, compare it to the template standard, and move it toward managed conformance
- `check`: validate conformance only
- `apply`: restore conformance in managed files
- `extend`: add features while preserving the managed contract

## Core Standards

- Do not replace template logic with whole-file free-form LLM generation.
- Managed repos must pass `npm run check:developable`.
- Prisma access belongs only in repositories.
- Controllers must depend on service contracts, not repositories.
- JWT and password hashing belong behind adapters/services.
- Security invariants in `.developable/invariants.yaml` are non-negotiable.

## CLI Fallback

The CLI is secondary and may be used as an execution engine:

```bash
python3 main.py path/to/schema.prisma --rules path/to/rules.yaml --out ./output
python3 main.py path/to/schema.prisma --rules path/to/rules.yaml --out ./output --github
python3 deploy.py --out ./output --deploy-to aws
```
