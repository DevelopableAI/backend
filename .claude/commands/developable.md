---
description: Initialize, adopt, check, apply, or extend a Developable-managed Express + TypeScript backend.
---

# /developable

Developable is a **managed-backend command**, not a one-shot generator. Its primary job is to install and preserve the backend contract stored in `.developable/*`.

## Managed Mode

If `.developable/contract.json` exists:

- load `.developable/contract.json`, `invariants.yaml`, `architecture.yaml`, `solid.yaml`, `routes.json`, and `composition.json` before backend edits
- treat the repo as Developable-managed even if the user did not explicitly invoke `/developable`
- refuse or refactor backend edits that drift from the managed contract

## Verbs

- `/developable init` — generate a new managed backend from an existing Prisma schema or an app description
- `/developable adopt` — inspect an existing Express + TypeScript backend and move it toward Developable-managed conformance
- `/developable check` — run conformance validation only
- `/developable apply` — repair contract drift in managed files
- `/developable extend` — add or change backend behavior while preserving the managed contract

## Slash-Command Workflow

1. Prefer the command surface over direct ad hoc backend generation.
2. If the repo is already managed, load `.developable/*` first.
3. If there is no schema, design `schema.prisma` and `rules.yaml` before generation.
4. After generation or structural edits, ensure the repo contains:
   - `.developable/contract.json`
   - `.developable/manifests/managed-files.json`
   - `.developable/manifests/dependency-rules.json`
   - `.developable/manifests/ast-signatures.json`
5. Ensure managed repos pass `npm run check:developable`.

## Runtime Standard

Managed backends must preserve this shape:

- `routes` for endpoints and middleware only
- `controllers` for transport parsing/validation/response mapping only
- `services` for business orchestration and ownership checks
- `repositories` as the Prisma-only persistence boundary
- `contracts` for dependency inversion
- `adapters` for JWT/hash providers
- `bootstrap` for wiring concrete implementations

## CLI Fallback

The CLI is secondary and may be used as an implementation engine when needed:

```bash
python3 main.py path/to/schema.prisma --rules path/to/rules.yaml --out ./output
python3 main.py path/to/schema.prisma --rules path/to/rules.yaml --out ./output --no-llm
python3 deploy.py --out ./output --deploy-to aws
```
