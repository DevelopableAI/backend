# Progress day by day

## 02/19/2026
- I built a quick MVP that runs on the terminal.
- It is able to generate code at 0.01$ cost.
- Highly dependent on Jinja templates (which is good and not so good)
- Completely working API with no compilation errors and works successfully so far
- I have tested Create User, Get User By ID, List User, Health check endpoints.
- I have to test other endpoints

## 02/20/2026
- MVP still runs on terminal
- Added logging with windsor as a capability
- Improved error messages on bad request scenario
- Added claude code as a contributor to the project
- Solidified localhost postgres server issues for generated API service to launch and maintain its database on this server

# 02/20/2026 [Problems detected]
- MVP output does not handle data storage security (password got stored in plain text. Need the generator to understand that we might be storing sensitive data and need an encryption key of some sort to do that. This should be true for any sort of sensitive data.)
- MVP output does not have endpoints that relate between different entities. (If user and post tables are related by authorId, then there must be endpoints indicating that.)
- MVP output does not handle basic authorization (a post can only be edited by the author themselves and not anyone else.)

# 02/27/2026 [Problems solved]
- MVP output can now store password in encrypted manner
- Finds sensitive data effectively
- Auth-related IDs for associative endpoints taken through bearer token.

# 02/27/2026 [Problems detected through test suited]
-  ❌  GET /api/users/abc → 400 (invalid ID) → expected HTTP 400, got 500 | body: {"error":"Internal server error"}
-  ❌  GET /api/users/1.5 → unexpected HTTP 200
-   🚀  PUT /api/users/1  ⚠️   SECURITY: user2 (id=2) successfully updated user1 (id=1)'s profile. The User entity has no ownership check on PUT/DELETE because it is the auth entity itself. Consider adding an explicit guard: if (req.user.id !== id) throw new AppError(403, 'Forbidden').
- 🚀  POST /api/posts ❌  Seed: Create post1 for user1 → expected HTTP 201, got 400 | body: {"error":"authorId: Required"}🚀  POST /api/posts❌  Seed: Create post2 for user1 → expected HTTP 201, got 400 | body: {"error":"authorId: Required"}🚀  POST /api/posts❌  Seed: Create post3 for user2 → expected HTTP 201, got 400 | body: {"error":"authorId: Required"}
- ❌  GET /api/posts/notanid → 400 (invalid ID) → expected HTTP 400, got 500 | body: {"error":"Internal server error"}
- ❌  GET /api/comments/bad → 400 (invalid ID) → expected HTTP 400, got 500 | body: {"error":"Internal server error"}
- ❌  GET /api/users/abc/posts → 400 (invalid ID) → expected HTTP 400, got 500 | body: {"error":"Internal server error"}
- ❌  POST /api/users/posts with auth → 201 → expected HTTP 201, got 400 | body: {"error":"authorId: Required"}
- ❌  POST /api/posts with Unicode/emoji → 201 → expected HTTP 201, got 400 | body: {"error":"authorId: Required"}
- 🚀  GET /api/posts/9007199254740992⚠️   Integer overflow in ID → unexpected HTTP 500
- 🚀  GET /api/posts/1; DROP TABLE posts --⚠️   SQL injection in path segment → unexpected HTTP 200

# 02/28/2026 [Problems solved]
- All issues above solved.
- Removed some invalid scenarios for the blog app API testing

# 02/28/2026 [Improvements to make]
- Noticed an issue with templates being too brittle in the sense that there should be a deciding algorithm/program that understands the schema structurally and semantically to determine the endpoints that are required, and those that are not. It also helps determining all mandatory foreign keys (input for the API) to CRUD on an entity and ensuring that each endpoint concerning the operations on this entity get those foreign key in some form or other on the request.
- Example: A comment requires the post ID and user ID. However, the `POST /api/posts/:id/comments` endpoint only looks for the post ID in the URL params and conveniently forgets to add validation for presence of userID (indicated as authorId in Comment model).
- Solution is to have a validator (like the senior backend engineer tries to validate his own code through templated test cases and structural representation of the prisma schema like say a tree structure or a graph structure of all data models.) 
- If we take the wrong path (like injecting an LLM call), this might cost us a lot in tokens and defeat the purpose of this work. 
- A whole new feature which has been in the backlog in the long run is the acquiring of user logic behind the prisma schema he sent the generator. What do I mean by that? One of our test cases was to try to create another user from current user which does not make sense at all although technically possible given the prisma schema. So, the user business logic is an input we should plan on taking in for both the llm hints on the API code and on the templated test cases mentioned in the third point from the top.

# 03/03/2026 [Solved issues]
- Solved the foreign entity based endpoint naming and creationg issue.
- Templated test cases created.
- Debugging issues with templated tests

# 03/03/2026 [Further progres]
- Solved all issues with templated tests and verified it is working with test_schema.
- Identified and solved issues related to entity relationships in delete operations.
- Used figma AI to develop a website for the package.

# 03/04/2026 
- Deployed the website for the package
    - Includes purchase of namecheap domain
    - Setting up terraform for deployment of website
    - Setting up github actions

# 03/05/2026
- Identified following issues templated tests:
    - Test case API calls not sensitive to request body format expected in API, hence having unrealistic expectation and declaring functional API as defective.
    - Test scripts might have indentation issue. The last API and test suite generated after fix did not have indentation issues. But, still something to watch out for and find definitive solution
- Formulated reasoning for first issue might be that the test planner only knows the routes of the API, schema and rules yaml. Some part of the error can be mitigated if test_planner receives the zod validation schema for POST/PUT requests.
- In test_planner line 300 (under plan method), the 'first_primary' variable is not used. Why? Could that be the reason for error? We need to find out.
- Verify this and see if the total test cases count is different as we pass more test cases. If true, then we need to change the console output in test cases to show non-executed test cases.


# 03/09/2026
- Fixed all the issues with test templates
- All 3 REST APIs are generated and tested perfectly with 100+ test cases.
- Refactored the code to express agent-based architecture.
- Updated CLAUDE.md to reflect the same.
- Time to move to next steps

# 03/10/2026
- Implemented version control agent and tested against all 3 example schemas

# 03/22/2026
- Output for controlled cost E-commerce API:
 ── LLM usage ────────────────────────────────────────────
  API calls       : 18  (+ 0 response cache hits, 0 cost)
  Input tokens    : 25,993  (uncached)
  Cache write     : 0  tokens
  Cache read      : 0  tokens  (billed at 10% rate)
  Output tokens   : 4,998
  Estimated cost  : $0.0837
─────────────────────────────────────────────────────────
Test files: 2600 lines total (per file average 120 lines, all python code)
CI/CD job action: 90
API files:
    Controller files: 5 * 120 = 600
    Utility files: 5 * 35 = 175
    Repository files: 5 * 76 = 380
    Custom types and DTOs: 5 * 10 = 50
    Total = 1205 
- Output for controlled cost PM API:
── LLM usage ────────────────────────────────────────────
  API calls       : 22  (+ 0 response cache hits, 0 cost)
  Input tokens    : 31,030  (uncached)
  Cache write     : 0  tokens
  Cache read      : 0  tokens  (billed at 10% rate)
  Output tokens   : 6,150
  Estimated cost  : $0.1020
─────────────────────────────────────────────────────────

# 03/27/2026 [Deployment Agent]
- Implemented Deployment Agent (`agents/deployment.py`) as the 4th component of the Backend Engineer, alongside Developer, Tester, and Version Control agents.
- Supports three cloud providers: **AWS ECS Fargate**, **Heroku**, and **GCP Cloud Run**
- Zero LLM cost — pure SDK/subprocess calls, no Anthropic API usage
- Key features implemented:
    - Provider selection and credential detection/collection
    - Managed database provisioning per provider (RDS, Heroku Postgres, Cloud SQL)
    - Prisma schema applied to remote DB via `npx prisma db push --accept-data-loss`
    - Container build and push to provider registry
    - Container deployment
    - Local resource tracking at `<out_dir>/.developable/state.json`
    - CI/CD deploy workflow pushed to GitHub (`.github/workflows/deploy.yml`) triggering after CI passes on `main`
    - Remote smoke tests run against live endpoint after deploy
- Added `--deploy-to`, `--aws-region`, `--heroku-app`, `--gcp-project`, `--gcp-region`, `--project-name` CLI flags to `main.py`
- GitHub repo description now set to `"<project_name> API by developable (developablecode.app)"`
- Changed generated `Dockerfile.j2` from `npm ci` to `npm install` (generated projects have no `package-lock.json`)

## 03/27/2026 [Heroku deployment issues and solutions]

**Issue 1 — Docker push rejected with "error from registry: unsupported"**
- Newer Docker Desktop defaults to OCI image format (`application/vnd.oci.image.index.v1+json`), which Heroku's registry rejects.
- **Fix:** Use `docker buildx build --platform linux/amd64 --provenance=false --load`. The `--provenance=false` flag forces Docker manifest v2 format; `--load` is required when using `--platform` with buildx.

**Issue 2 — Release API returning 404 "process_type (web) not found"**
- The Heroku Formation API requires `Accept: application/vnd.heroku+json; version=3.docker-releases` for container registry releases. Using `version=3` (slug mode) fails with 404 on new apps that have no existing web dyno.
- **Fix:** Added the `docker-releases` Accept header to the Formation API call.

**Issue 3 — Release API returning 404 "Couldn't find that record"**
- Root cause traced through multiple hypotheses. The diagnostic output revealed:
    - `docker buildx` sets the local image's `.Id` to the **manifest digest** (sha256 of the manifest JSON)
    - Regular `docker build` sets `.Id` to the **config digest** (sha256 of the image config JSON)
    - Heroku's Formation API indexes images by **config digest**, but we were sending the manifest digest
    - The build output makes the distinction visible: `exporting manifest sha256:6d733b...` vs `exporting config sha256:c7b523...` — two different values
- **Fix:** After pushing, run `docker manifest inspect registry.heroku.com/{app}/web` to read the manifest JSON from the registry, then extract `config.digest` — this is the identifier Heroku stores and expects in `docker_image`.

# 03/28/2026
- Think about Engineer entity -> especially new features that have not been supported by templates -> TemplateGenerator + open-source contribution opportunity for a few extra tokens
- Engineer entity with template-supported tasks are more like +/- between what is already there and what is the correct template to pick.