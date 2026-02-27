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