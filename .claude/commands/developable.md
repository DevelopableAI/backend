# /developable

Generate a production-ready Express + TypeScript REST API from a Prisma schema, enforcing Developable's security invariants and file structure.

**Usage:** `/developable [path/to/schema.prisma] [--out ./output] [--rules path/to/rules.yaml]`

If no schema path is given, search for `schema.prisma` in `prisma/`, the current directory, and up to 2 levels deep. If not found, ask the user where it is.

---

## Progress Reporting (Required)

Output progress at each phase boundary and after each file write. This text appears directly in the chat on all interfaces (terminal, desktop app, web) and as tool call labels in the desktop/web expandable view.

**Phase header** — output this exact format before starting each phase:
```
━━━ Phase N/3: Phase Name ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**File edit** — after each `Edit` tool call, output:
```
  ✓ path/to/file.ts
```

**Phase complete** — after finishing each phase:
```
  Phase N complete — X files written/filled
```

**Tool call descriptions** — when calling `Edit`, set the description to `[Phase N] <action> <filename>` (e.g. `[Phase 2] Fill Zod schemas — post.validator.ts`). This becomes the label on each collapsible item in the desktop and web app.

---

## Phase 0 — Collect Configuration (ask before doing anything else)

Before reading any file or generating anything, collect the four configuration values below.

**Default values (use these when the user has not explicitly stated otherwise):**
- `out_dir` → `./output`
- `github_enabled` → `false`
- `deploy_provider` → `none`
- `project_name` → derive from schema filename or first entity name (e.g. `blog_api.prisma` → "Blog Api")

**Step 1 — Apply anything the user already stated in their invocation message.** If the user wrote `/developable the project name is Blog REST backend. The schema is at ./test_schema.prisma`, then `project_name = "Blog REST backend"` and `schema_path = ./test_schema.prisma` are already known. Do not ask for them again.

**Step 2 — Show the full proposed configuration and ask for confirmation.** Even if the user provided every value, ALWAYS show the config block and ask before proceeding. This is the only input prompt in Phase 0 — never ask follow-up questions one by one.

```
Here's the configuration I'll use — reply "ok" to proceed or tell me what to change:

  Project name : {project_name}
  Schema       : {schema_path}
  Output dir   : {out_dir}
  GitHub push  : {yes → username/repo-name (public|private) | no}
  Deploy to    : {provider | none}
```

If the user says "ok" (or equivalent like "yes", "looks good", "go ahead"): proceed to Phase 0b.

If the user changes a value: update it and show the config block again. Repeat until they confirm.

**GitHub sub-questions** — only ask these if the user changes "GitHub push" to yes and has not already provided them:
- GitHub username or org
- Repository name (default: `{project_slug}-api`)
- Public or private? (default: private)

**Deploy sub-questions** — only ask these if the user changes "Deploy to" to aws/gcp/heroku and has not already provided them:
- aws → AWS region (default: us-east-1)
- gcp → GCP project ID + region (default: us-central1)
- heroku → Heroku app name (default: `{project_slug}`)

**Store the confirmed values as variables for all later phases:**
- `project_name`, `project_slug` (lowercase, hyphens)
- `schema_path`, `out_dir`
- `github_enabled`, `github_user`, `github_repo`, `github_private`
- `deploy_provider`, `deploy_config`

After the user confirms, print:
```
━━━ Configuration confirmed ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Project : {project_name}
  Schema  : {schema_path}
  Output  : {out_dir}
  GitHub  : {yes → github_user/github_repo (public|private) | no}
  Deploy  : {provider | none}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Then proceed to Phase 0b.

---

## Phase 0b — Credential Pre-flight

Run these checks **before generating any files**. Use the `Bash` tool for each check. If any required credential is missing, stop and give the user exact setup instructions — do not proceed until the user confirms the credential is in place.

Only check credentials that are actually needed based on the Phase 0 answers.

---

### GitHub (check if `github_enabled` is true)

```bash
gh auth status
```

**If the command succeeds:** print `  ✓ GitHub: authenticated` and continue.

**If it fails or `gh` is not installed:**
```
  ✗ GitHub: not authenticated

  You need to authenticate the GitHub CLI before the skill can create your repository.
  Run one of these:

  Option A — Interactive login (recommended):
    gh auth login
    (follow the prompts; choose GitHub.com → HTTPS → Login with a web browser)

  Option B — Personal Access Token:
    export GITHUB_TOKEN=ghp_your_token_here
    gh auth login --with-token <<< "$GITHUB_TOKEN"

  To create a PAT: GitHub → Settings → Developer settings →
  Personal access tokens → Tokens (classic) → Generate new token
  Required scopes: repo, workflow, read:org

  Reply "done" once authenticated and I will continue.
```
Wait for the user to reply before proceeding.

---

### AWS (check if `deploy_provider == "aws"`)

```bash
aws sts get-caller-identity
```

**If it succeeds:** print `  ✓ AWS: authenticated as {Account}/{UserId}` and continue.

**If it fails:**
```
  ✗ AWS: no credentials found

  You need AWS credentials configured before deployment can be set up.
  Run one of these:

  Option A — AWS CLI configuration (recommended for local use):
    aws configure
    (prompts for Access Key ID, Secret Access Key, region, output format)

  Option B — Environment variables (for CI or temporary use):
    export AWS_ACCESS_KEY_ID=AKIA...
    export AWS_SECRET_ACCESS_KEY=wJalr...
    export AWS_DEFAULT_REGION=us-east-1

  To create access keys: AWS Console → IAM → Users → {your user} →
  Security credentials → Create access key → Application running outside AWS

  Minimum IAM permissions required:
    - AmazonECS_FullAccess
    - AmazonEC2ContainerRegistryFullAccess
    - AmazonRDSFullAccess
    - IAMLimitedAccess (for task execution role)

  Reply "done" once credentials are configured and I will continue.
```
Wait for the user to reply before proceeding.

---

### GCP (check if `deploy_provider == "gcp"`)

```bash
gcloud auth list --filter=status:ACTIVE --format="value(account)"
```

**If it returns an account:** print `  ✓ GCP: authenticated as {account}` and continue.

**If it returns nothing or fails:**
```
  ✗ GCP: not authenticated

  You need to authenticate the Google Cloud CLI before deployment can be set up.

  Option A — User account (recommended for local use):
    gcloud auth login
    gcloud config set project {gcp_project_id}

  Option B — Service account (for CI):
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

  To create a service account key: GCP Console → IAM & Admin →
  Service Accounts → Create → grant roles:
    - Cloud Run Admin
    - Cloud SQL Admin
    - Artifact Registry Administrator
    - Service Account User

  Reply "done" once authenticated and I will continue.
```
Wait for the user to reply before proceeding.

---

### Heroku (check if `deploy_provider == "heroku"`)

```bash
heroku auth:whoami
```

**If it returns an email:** print `  ✓ Heroku: authenticated as {email}` and continue.

**If it fails:**
```
  ✗ Heroku: not authenticated

  You need to authenticate the Heroku CLI before deployment can be set up.

  Option A — Interactive login:
    heroku login
    (opens browser for authentication)

  Option B — API key (for CI or non-interactive):
    export HEROKU_API_KEY=your_api_key_here

  To find your API key: Heroku Dashboard → Account Settings → API Key → Reveal

  Reply "done" once authenticated and I will continue.
```
Wait for the user to reply before proceeding.

---

After all required credentials pass, print:
```
━━━ Pre-flight complete — all credentials verified ━━━━━━━━━━━━━
```

Then proceed to Phase 1.

---

## Phase 1 — Generate Structural Files

```
━━━ Phase 1/3: Generate structural files ━━━━━━━━━━━━━━━━━━━━━━━
```

### Step 1a — Locate the CLI

Run these checks in order — stop at the first one that succeeds:

```bash
which developable 2>/dev/null || echo "NOT_FOUND"
```

- **Found:** CLI command is `developable`. Print `  ✓ CLI: developable (package install)` and continue.

- **Not found:**
  ```bash
  ls main.py 2>/dev/null || echo "NOT_FOUND"
  ```
  - **Found:** CLI command is `python main.py`. Print `  ✓ CLI: python main.py (repo root)` and continue.
  - **Not found:** Stop and tell the user:

```
  ✗ Developable CLI not found.

  Option A — install as a package (recommended, works from any project directory):
    pip install developable

  Option B — run from the Developable repo root:
    cd /path/to/developable-repo
    (then re-invoke /developable)

  Reply "done" once the CLI is available and I will continue.
```

### Step 1b — Run the CLI

Build the command via the command builder (single source of truth for flag mapping), then run it:

```bash
CLI_CMD=$(python core/command_builder.py << 'JSON'
{"cli": "{cli_command}", "schema_path": "{schema_path}", "out_dir": "{out_dir}", "tests_out": "{out_dir}/tests"}
JSON
)
$CLI_CMD
```

- Stream the CLI output directly so the user sees the generator's own progress lines
- On non-zero exit code: stop and show the full error — do NOT proceed to Phase 2 with broken output
- The CLI always generates Dockerfile, docker-compose.yml, .github/workflows/ci.yml, and .gitignore regardless of whether GitHub push is enabled

### Step 1c — Report

```bash
find {out_dir}/src -name "*.ts" | wc -l
find {out_dir}/tests -name "*.py" 2>/dev/null | wc -l
```

Print:
```
  Phase 1 complete — {N} TypeScript files + {M} test files generated
```

---

## Phase 2 — Fill LLM Sections

```
━━━ Phase 2/3: Fill logic sections ━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Find every file the CLI left with placeholder logic:

```bash
grep -rl "LLM_SECTION_START" {out_dir}
```

There are exactly **two file types** that contain these markers. Process them in order.

---

### Type A — Zod Validators (`src/validators/*.validator.ts`)

Each file contains context hints that the template rendered before Claude's turn:

```typescript
/* LLM_SECTION_START */
// TODO: define Zod schemas for Post create and update
// SERVER-INJECTED (always): authorId is set from req.user.id — exclude from ALL schemas
// PARENT-FK: postId is required in the request body for direct POST /posts,
//            but injected from URL params in nested routes — exclude it from postCreateNestedSchema
// BUSINESS RULE: Only the author can edit
// FIELD DEFAULT: published has @default(false) — MUST be .optional() in createSchema
const postCreateSchema = z.object({});
const postCreateNestedSchema = z.object({});
const postUpdateSchema = z.object({});
/* LLM_SECTION_END */
```

Also read the corresponding `src/types/{entity}.types.ts` (fully rendered by the CLI) for the complete field list and TypeScript types.

**Generate the Zod schemas using these rules — apply them exactly:**

- `email` field → `z.string().email({ message: 'Please provide a valid email address' })` — do NOT add `.min(1)`, `.email()` already rejects empty strings
- `password` field → `z.string().min(8).max(128)`
- `url` / `website` → `z.string().url()`
- `name` / `title` → `z.string().min(1).max(255).trim()`
- `description` / `body` / `content` → `z.string().min(1).max(10000)`
- `age` → `z.number().int().min(0).max(150)`
- Numeric quantities (`price`, `amount`, `cost`, `total`, `count`, `quantity`, `fee`, `balance`, `rate`, `score`) → `z.number().min(0)`
- `boolean` fields → `z.boolean()`
- `DateTime` fields → `z.coerce.date()`
- Enum fields → `z.enum(['VALUE1', 'VALUE2'])` — infer values from field name if obvious
- Other `string` fields → `z.string().min(1).trim()`
- Other `number` fields → `z.number()`
- Fields with `@default(...)` → always `.optional()` in create schema — even if the field is otherwise required
- **SERVER-INJECTED** fields → omit entirely from ALL schemas (create, nested, update)
- **PARENT-FK** fields → include in `{lower}CreateSchema`; omit entirely from `{lower}CreateNestedSchema`
- **Update schema** → `{lower}CreateSchema.partial()` — never `.partial()` individual fields
- **Match ts_type strictly** — never use `z.string()` for a `number` field or vice versa

Use `Edit` to replace the content **between** `/* LLM_SECTION_START */` and `/* LLM_SECTION_END */`, keeping both marker lines in place.

**Tool call description:** `[Phase 2] Fill Zod schemas — {Entity} validator`
**After edit:** print `  ✓ src/validators/{entity}.validator.ts`

---

### Type B — Test Write Cases (`tests/test_*_write.py`)

Each file contains context hints the template rendered:

```python
# LLM_SECTION_START
# Generate required-field validation tests for entity "Post".
# Canonical create path: POST /api/users/posts
# Requires authentication: True
#
# Required scalar fields (excluding server-injected FK fields):
#   title: string (required)
#   content: string (required)
#
# Secondary FK fields — MUST appear in every request body:
#   (none)
#
# Owner FK field: authorId
# LLM_SECTION_END
```

**Generate the test cases using these rules — apply them exactly:**

- For **each** required scalar field: generate one test that omits only that field, includes all other required fields with sensible values, and asserts HTTP 400
  ```python
  if token1:
      resp = ctx.req("POST", "/api/users/posts", token=token1,
                     body={"content": "Hello World"})  # title omitted
      ctx.assert_status(resp, 400, "POST post missing title → 400")
  ```
- If the required fields list is **empty**: output exactly `# (no required fields to validate)` and nothing else
- Include **secondary FK fields** in every request body (they are NOT server-injected; the test must supply them)
- Do NOT include owner FK or primary parent FK (server-injected)
- Generate **one ownership spoofing test** if an owner FK field is listed:
  ```python
  if token1:
      resp = ctx.req("POST", ..., token=token1, body={..., "authorId": user2_id})
      data = ctx.safe_json(resp)
      if resp.status_code == 201 and data.get("authorId") == user1_id:
          ctx.ok("ownership spoofing prevented — authorId correctly overridden")
      elif resp.status_code == 201 and data.get("authorId") == user2_id:
          ctx.warn("SECURITY: ownership spoofing succeeded — authorId accepted from body")
  ```
- Use `body=` keyword argument (not `json=`)
- Outermost statements at column 0; nested code indented 4 spaces
- All string values must be single-line, under 80 characters

Use `Edit` to replace the content **between** `# LLM_SECTION_START` and `# LLM_SECTION_END`, keeping both marker lines in place.

**Tool call description:** `[Phase 2] Fill test cases — {filename}`
**After edit:** print `  ✓ tests/{filename}`

---

After all files are processed, print:
```
  Phase 2 complete — {N} validators + {M} test files filled
```

---

## Phase 3 — Publish

```
━━━ Phase 3/3: Publish ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### GitHub (if `github_enabled` is true)

Dockerfile, docker-compose.yml, .github/workflows/ci.yml, and .gitignore were
written by the CLI in Phase 1 (always, unconditionally). All that remains is
git init, repo creation, and the push.

```bash
cd {out_dir}
git init && git add . && git commit -m "Initial Developable-generated API"
gh repo create {github_user}/{github_repo} \
  --{public|private} \
  --source=. --remote=origin \
  --description="{project_name} API by Developable (developablecode.app)"
git push -u origin main
```

Replace `--{public|private}` with `--public` or `--private` based on `github_private`.

After a successful push, print:
```
  ✓ Repository live: https://github.com/{github_user}/{github_repo}
  ✓ GitHub Actions CI triggered — check the Actions tab
```

### Final Summary

Print the done block, adapting next steps to what was enabled:

```
━━━ Done ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Generated {N} API files across {Y} entities
✓ Generated {M} test modules in tests/
✓ Generated CLAUDE.md with Developable standards
✓ Generated Dockerfile, docker-compose.yml, .github/workflows/ci.yml, .gitignore
{if github_enabled: ✓ Repository live: https://github.com/{github_user}/{github_repo}}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Next steps:
  cd {out_dir}
  npm install
  cp .env.example .env   # fill in DATABASE_URL and JWT_SECRET
  npx prisma migrate dev --name init
  npm run dev             # http://localhost:3000

Run tests (requires running server):
  python tests/run_all.py http://localhost:3000
```

Only append deploy-specific blocks when `github_enabled` is true:

**aws:**
```
Deploy to AWS (ECS Fargate):
  See .github/workflows/ci.yml for automated deployment on push to main.
  Ensure AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION are set as GitHub secrets.
```

**gcp:**
```
Deploy to GCP (Cloud Run):
  See .github/workflows/ci.yml for automated deployment on push to main.
  Ensure GCP_PROJECT_ID, GCP_SA_KEY are set as GitHub secrets.
```

**heroku:**
```
Deploy to Heroku:
  heroku login
  heroku addons:create heroku-postgresql:essential-0 --app {heroku_app}
  heroku config:set DATABASE_URL=$(heroku config:get DATABASE_URL --app {heroku_app})
  git push heroku main
```
