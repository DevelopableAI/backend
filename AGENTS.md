# AGENTS.md

## Purpose

This repository is the implementation of **Developable**, a backend code generator whose job is to turn a Prisma schema into a repeatable, opinionated Express + TypeScript REST API.

The important idea is not "generate some backend code." The product is the **template standard**:

- fixed file structure
- fixed controller/repository split
- fixed auth and ownership rules
- fixed regeneration behavior
- bounded, narrow LLM use instead of whole-file code generation

This file is the practical working guide for contributors and agents. `CLAUDE.md` is the broader product narrative. When they drift, trust the source code first and then bring the docs back into alignment.

## What The Codebase Does

Today this repo is a Python CLI orchestrator around several generation and deployment stages:

1. Parse a Prisma schema into a shared `spec` dict.
2. Optionally merge a `schema.rules.yaml` file into that spec.
3. Generate an Express + TypeScript API from Jinja templates.
4. Optionally generate a Python integration test suite from the API plan.
5. Always generate local infra files like `Dockerfile`, `docker-compose.yml`, and GitHub Actions CI.
6. Optionally generate Terraform files.
7. Optionally create a Git repo, create a GitHub repository, and push.
8. Optionally deploy the generated project to AWS, GCP, or Heroku.

The repo is also mid-migration toward a Claude Code skill. That migration has started: `.claude/commands/developable.md` already exists as a draft command, but the Python CLI is still the real implementation.

## Working Agreement

- Read this file before making significant changes.
- Prefer changing planners and templates over inserting one-off special cases.
- Keep `AGENTS.md`, `CLAUDE.md`, and `README.md` aligned when behavior changes.
- Do not treat docs as authority when the implementation says otherwise.
- Preserve the template's security invariants even when refactoring.

## Top-Level Flow

`main.py` is the entrypoint and coordinator.

Current CLI flow:

1. Parse CLI flags.
2. Parse the schema with `PrismaParser`.
3. Merge business rules with `BusinessRulesParser` if `--rules` is passed.
4. Build `.env` placeholder values from schema-referenced env vars.
5. Run `Developer.generate()` to produce the API.
6. Run `Tester.generate()` if `--tests-out` is set, or implicitly when `--github` is used.
7. Run `VersionControl.generate_infra()` on every generation run.
8. Run `TerraformAgent.generate()` when `--deploy-to` is provided.
9. Run `VersionControl.publish()` when `--github` is provided.
10. Run `Deployment.deploy()` when `--deploy-to` is provided.
11. Print LLM usage totals when LLM generation was enabled.

## Main Architectural Pattern

Most of the generator follows a shared pattern:

- parse into `spec`
- planner turns `spec` into a file plan
- assembler renders templates and optionally fills LLM sections

That pattern is used by:

- `Developer` via `Planner`
- `Tester` via `TestPlanner`
- `VersionControl` via `VCPlanner`
- `TerraformAgent` via `TerraformPlanner`

`Deployment` is the exception. It is an imperative workflow that provisions infrastructure, builds images, deploys, records state, and optionally pushes deployment workflows back to GitHub.

## Key Modules

### Entry And Agents

- `main.py`
  - CLI entrypoint and orchestrator.
  - Important reality: env collection is non-interactive, but GitHub config and some deployment steps are interactive when values are missing.

- `agents/developer.py`
  - Runs `Planner().plan(spec)` and `Assembler.assemble(...)`.
  - Returns `api_plan`, which downstream code relies on.

- `agents/tester.py`
  - Runs `TestPlanner().plan(spec, api_plan)`.
  - Depends on the API plan rather than rediscovering route shape independently.

- `agents/version_control.py`
  - Generates infra files every run.
  - `publish()` initializes git, creates a GitHub repo via REST API, and pushes `main`.
  - Important reality: `publish()` accepts `api_plan` but does not currently use it.

- `agents/terraform.py`
  - Writes static Terraform files into `terraform/`.

- `agents/deployment.py`
  - Deploys the generated project to AWS, GCP, or Heroku.
  - Bootstraps Terraform state backends, builds Docker images, deploys services, persists state, and can push deploy workflows/secrets to GitHub.
  - This is one of the largest and most side-effect-heavy files in the repo.

### Core Parsing And Planning

- `core/parser.py`
  - Parses Prisma models, fields, relations, enums, datasource config, env vars, and schema hints.
  - Builds the shared `spec`.
  - Important reality: auth entity detection currently depends on the explicit `// @auth_entity` marker. It is not inferred purely from `email + sensitive field`.
  - Marks password-like fields as sensitive on the auth entity even if `// @llm sensitive` is omitted.

- `core/rules_parser.py`
  - Merges `schema.rules.yaml` into the spec.
  - Adds:
    - `endpoint_deny`
    - `llm_constraints`
    - `primary_parent`

- `core/planner.py`
  - Produces the API file plan.
  - Decides:
    - shared project files
    - per-entity routes/controllers/repositories/validators/types
    - auth files
    - owner FK handling
    - primary-parent behavior
    - nested routes
    - filter/sortable fields
    - validator LLM tasks
  - This is the canonical place for route-shape changes.

- `core/test_planner.py`
  - Produces the Python test plan from `spec + api_plan`.
  - Handles test ordering, canonical create paths, nested route expectations, cleanup ordering, and per-entity test modules.
  - This is a highly coupled file. Route or ownership changes usually require edits here too.

### Assembly And LLM Plumbing

- `core/assembler.py`
  - Renders planned files to disk.
  - Skips tracked files that differ from `HEAD` unless `--force` is used.
  - Copies the source schema to `prisma/schema.prisma`.
  - Writes `.env` placeholders.
  - Injects Prisma `binaryTargets` into copied schemas when missing.
  - Runs Prettier only if the generated output already has `node_modules/.bin/prettier`.

- `generators/template.py`
  - Jinja renderer using `StrictUndefined`.

- `generators/llm.py`
  - Fills only marked LLM sections, not whole files.
  - Uses Anthropic prompt caching plus a disk cache in `~/.developable/cache`.
  - Validates and retries Python section output once when syntax/indentation is broken.
  - Tracks estimated token/cost usage for the session.

- `core/llm_data.py`
  - Deterministic seed data generation for tests.
  - Important reality: despite the older prompt/task naming, seed data is no longer LLM-generated.

### Infra And Deployment Support

- `core/vc_planner.py`
  - Plans `Dockerfile`, `docker-compose.yml`, and `.github/workflows/ci.yml`.

- `core/terraform_planner.py`
  - Plans provider-specific Terraform files.
  - Derives state backend names deterministically from the project name.

- `core/deployment_state.py`
  - Stores deployment history in `<out_dir>/.developable/state.json`.

- `core/gitignore.py`
  - Owns the default generated `.gitignore` and required ignore patterns.

- `core/providers/`
  - Provider-specific deployment backends for AWS, GCP, and Heroku.

## What Gets Generated

### API Output

The Developer agent generates an Express + TypeScript project with:

- `src/routes/`
- `src/controllers/`
- `src/repositories/`
- `src/validators/`
- `src/types/`
- shared libs under `src/lib/`
- `package.json`, `tsconfig.json`, `.env.example`, `prisma/schema.prisma`
- auth files when an auth entity exists

### Test Output

The Tester agent generates a Python integration suite with:

- ordered `test_XX_*.py` modules
- `helpers.py`
- `run_all.py`

Important reality:

- tests are generated automatically into `<out_dir>/tests` when `--github` is used, even if a different `--tests-out` was provided
- test generation mirrors the API plan and nested-route decisions

### Infra Output

The Version Control and Terraform layers generate:

- `Dockerfile`
- `docker-compose.yml`
- `.github/workflows/ci.yml`
- `terraform/backend.tf`
- `terraform/main.tf`
- `terraform/variables.tf`
- `terraform/outputs.tf`

## Security And Behavioral Invariants

These invariants are structural and should remain true after changes:

- Write routes require auth when an auth entity exists.
- Owner foreign keys are injected server-side from `req.user`, not trusted from request bodies.
- Auth entity update/delete is self-only.
- Non-auth owned entities check DB ownership before update/delete.
- Numeric IDs are validated before repository access.
- String PKs get UUID/CUID-aware validation when applicable.
- Sensitive fields are hashed before storage in auth flows.
- Sensitive fields are excluded from safe auth responses.
- Validator schemas exclude server-injected ownership fields.
- Primary-parent nested create routes suppress the direct top-level POST for child entities.

If a change alters any of those behaviors, inspect both templates and tests before merging it.

## Non-Obvious Planner Behavior

These are easy to break if you change logic casually:

- Auth detection:
  - The parser currently supports only one auth entity.
  - It is driven by `// @auth_entity`.

- Primary parent selection:
  1. explicit `primary_parent` override from rules
  2. first non-auth `many_to_one` FK
  3. auth-entity FK
  4. no parent

- Nested route behavior:
  - every one-to-many relation gets nested GET support
  - only the child's primary parent gets nested POST support

- Nested create validation:
  - only the FK for the active nested parent route is excluded from the nested create schema
  - secondary non-auth FKs still have to come from the request body

- Test ordering:
  - non-auth entities are topologically sorted by FK dependency
  - cleanup runs in reverse dependency order

## LLM Boundaries

The system is intentionally not a broad free-form generator.

Current bounded LLM use is mainly:

- validator logic in `templates/express/api/validator.ts.j2`
- selected Python test bodies such as entity write validation cases

Everything else should remain template- or planner-driven unless there is a strong reason to widen LLM scope.

## Regeneration Rules

The generated output is git-aware.

- If the output directory is inside a git repo, tracked files modified since `HEAD` are preserved by default.
- Untracked files are treated as safe to overwrite.
- `--force` disables that protection and also disables disk response-cache reuse for LLM fills.

This behavior is part of the product contract. Be careful when changing it.

## Important Files To Check For Common Change Types

When changing schema interpretation:

- `core/parser.py`
- `core/planner.py`
- `core/test_planner.py`
- relevant templates under `templates/express/api/`

When changing route shape or ownership logic:

- `core/planner.py`
- `templates/express/api/routes.ts.j2`
- `templates/express/api/controller.ts.j2`
- `core/test_planner.py`
- affected test templates under `templates/tests/`

When changing validation logic behavior:

- `templates/express/api/validator.ts.j2`
- `prompts/express/validation_logic.txt`
- `core/planner.py`

When changing auth behavior:

- `core/parser.py`
- `templates/express/api/auth.ts.j2`
- `templates/express/api/auth.controller.ts.j2`
- `templates/express/api/routes.ts.j2`
- `core/test_planner.py`

When changing infra or publish behavior:

- `core/vc_planner.py`
- `agents/version_control.py`
- `core/gitignore.py`

When changing deployment or Terraform:

- `agents/deployment.py`
- `agents/terraform.py`
- `core/terraform_planner.py`
- `core/terraform_backend.py`
- `core/providers/`

## Known Realities And Drift Risks

- `requirements.txt` still includes FastAPI/Uvicorn/Pydantic even though the visible product is the generator CLI; treat that as legacy or future-facing until proven otherwise.
- `README.md` and `CLAUDE.md` are directionally accurate, but some low-level details drift from implementation.
- `.claude/commands/developable.md` exists already, so the Claude Code skill migration is not purely hypothetical.
- `agents/deployment.py` is large, imperative, and integration-heavy; small changes there can have broad side effects.
- `core/test_planner.py` is one of the most coupled files in the codebase.

## Safe Edit Strategy

- Start at the planner when the behavior change is structural.
- Start at templates when the behavior change is about generated file shape.
- Touch tests early when route, auth, or ownership behavior changes.
- Prefer one consistent generation rule over entity-specific exceptions.
- Keep LLM use bounded unless the product direction explicitly changes.
- If you notice docs drifting from code, update the docs in the same change when practical.

## Claude Code Skill Direction

This repository is moving toward a Claude Code command/skill, but the Python implementation remains the live source of truth.

Current migration status:

- `.claude/commands/developable.md` exists
- Jinja templates are still canonical
- Python agents/planners/assembler still implement real behavior

Preserve during migration:

- parse -> plan -> assemble mental model
- template-enforced security invariants
- bounded LLM usage
- the exact generated backend conventions already proven by this repo

## Default Assumption For Future Work

When working in this repo, "understand the system first" means:

- inspect the parser
- inspect the planner
- inspect the test planner
- verify the template behavior
- only then patch code or docs

This file should stay as a stable engineering guide, not a changelog.
