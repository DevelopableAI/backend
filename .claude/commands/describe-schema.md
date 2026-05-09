# /describe-schema

Generate a `schema.prisma` and `rules.yaml` from a plain-English application description, verify with the user, then hand off to `/developable` for code generation.

**Usage:** `/describe-schema "A task management app where users create projects and tasks"`

If no description is given in `$ARGUMENTS`, ask: *"Describe your application — what entities does it have, what do users do, and which entity handles user accounts/login?"*

---

## Phase 1 — Understand the Domain

Before writing any file, reason through the description explicitly. Work through these questions:

1. **What are the main nouns?** → These become Prisma models.
2. **Which entity represents a user account / logs in?** → This gets `// @auth_entity`.
3. **What scalar fields does each entity need?** (names, types, required vs optional)
4. **What relationships exist?** (who owns whom, FK direction)
5. **Which fields are secrets that must be hashed?** (passwords, tokens, API keys) → `// @llm sensitive`
6. **What business constraints exist?** (only the author can edit, title must be non-empty, etc.) → `rules.yaml` constraints

Write a brief 3–5 line reasoning summary before generating any files.

---

## Phase 2 — Generate `schema.prisma`

### Prisma file structure
Always include this boilerplate at the top:
```prisma
datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

generator client {
  provider = "prisma-client-js"
}
```

### Model conventions (apply to every model)
- Always include `id Int @id @default(autoincrement())`
- Always include `createdAt DateTime @default(now())`
- Always include `updatedAt DateTime @updatedAt`
- Mark the user/account model with `// @auth_entity` on the line immediately before `model Name {`
- Mark password/secret fields with `// @llm sensitive` as an **inline comment on the field line**
- Add `@unique` to `email` and `username` fields automatically
- Use `@default(false)` for boolean flags that default to inactive (published, isActive, isVerified)

### Relation conventions
- The **owning side** (the entity that holds the FK column) has `@relation(fields: [fkField], references: [id])` on the relation field
- The FK scalar field (e.g., `authorId Int`) appears on the same model as the `@relation`
- The **inverse side** has the array relation (e.g., `posts Post[]`) — no FK here
- All FK fields are `Int` (matching `@default(autoincrement())` PKs)
- FK field names follow the pattern `{relatedEntityLower}Id` (e.g., `userId`, `postId`, `projectId`)

### Schema annotation guide
```
// @auth_entity                  ← on its own line before the model
model User {
  id        Int      @id @default(autoincrement())
  email     String   @unique
  name      String
  password  String   // @llm sensitive     ← inline on the field
  bio       String?
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
  posts     Post[]
}

// @llm Users can only access their own data
model Post {
  id        Int      @id @default(autoincrement())
  title     String
  content   String
  published Boolean  @default(false)
  authorId  Int
  author    User     @relation(fields: [authorId], references: [id])
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}
```

### `// @llm <hint>` usage
Add `// @llm <hint>` lines **above the model block** (before `// @auth_entity` if both apply) to capture domain rules:
- Add when: there are ownership rules not obvious from the schema topology ("Only verified users can publish posts")
- Add when: there are cross-entity constraints ("A task must belong to a project the user has access to")
- Skip when: standard ownership checks that Developable handles automatically (auth FK injection, password hashing)

### Type reference
| Prisma | TypeScript | Use for |
|--------|-----------|---------|
| `Int` | `number` | IDs, counts, ages, quantities |
| `Float` | `number` | Prices, coordinates, scores |
| `String` | `string` | Text, email, URL, names |
| `Boolean` | `boolean` | Flags, toggles |
| `DateTime` | `Date` | Timestamps, dates |
| `Json` | `Record<string,any>` | Flexible metadata |

---

## Phase 3 — Generate `rules.yaml`

Write a `rules.yaml` alongside the schema with entity-level constraints:

```yaml
entities:
  User:
    constraints:
      - "Email must be unique and lowercased before storage"
      - "Password must be at least 8 characters; never returned in any API response"

  Post:
    constraints:
      - "Only the author can edit or delete a post"
      - "Title must be non-empty and trimmed; content must be at least 10 characters"
    # primary_parent: User  # only add if auto-detection would be wrong

  Comment:
    constraints:
      - "Comments belong to both an author and a post"
      - "Body must be non-empty"
```

**Add at minimum:**
- For the auth entity: password/credential constraints
- For any entity with an `owner_fk_field`: "Only the [role] can edit or delete a [entity]"
- For string fields with obvious length rules: include them as constraints

**Only add `primary_parent` override when the auto-detected parent would be wrong.** Auto-detection picks the first non-auth FK — override only when the schema has multiple non-auth FKs and the wrong one would be selected.

---

## Phase 4 — Write Both Files

Write `schema.prisma` and `rules.yaml` to the current directory (or `--out` path if specified).

After writing, display both files back to the user and show a summary table:

```
Schema summary:
  Entities : User, Post, Comment
  Auth entity : User (login via email)
  Sensitive fields : User.password
  Relations : User→Post (one-to-many), Post→Comment (one-to-many)
  rules.yaml : 3 entities, X constraints
```

---

## Phase 5 — Confirm Before Generating Code

Ask the user explicitly:

> "Does this schema look correct?
> - Reply **`ok`** to generate the API now
> - Reply **`edit: <instruction>`** to modify the schema (e.g., "edit: add a `category` field to Post")
> - Reply **`stop`** to end here without generating code"

### If the user says `edit: <instruction>`
Apply the requested change to both `schema.prisma` and `rules.yaml` as needed. Redisplay the changed file(s) and ask again.

### If the user says `ok`
Proceed to generate the full API by following the `/developable` skill instructions, using the schema just created.

### If the user says `stop`
Confirm: "Schema saved to `schema.prisma`. Run `/developable schema.prisma` whenever you're ready to generate the API."

---

## Worked Example Files

Two complete reference schemas are available in the repository root:

- **`test_schema_ecommerce.prisma`** + **`test_schema_ecommerce.rules.yaml`** — e-commerce app (User → Order → OrderItem, standalone Product)
- **`test_schema_saas.prisma`** — SaaS hierarchy (User → Organization → Project → Task with optional assignee)

Read these files if you need a fully concrete example to guide schema generation.

---

## Reference Patterns

### Pattern 1 — Auth + owned content (Blog)
```prisma
// @auth_entity
model User {
  id        Int      @id @default(autoincrement())
  email     String   @unique
  name      String
  password  String   // @llm sensitive
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
  posts     Post[]
}

model Post {
  id        Int      @id @default(autoincrement())
  title     String
  content   String
  published Boolean  @default(false)
  authorId  Int
  author    User     @relation(fields: [authorId], references: [id])
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}
```

### Pattern 2 — Two-level ownership (Blog with comments)
```prisma
model Comment {
  id        Int      @id @default(autoincrement())
  body      String
  postId    Int
  post      Post     @relation(fields: [postId], references: [id])
  authorId  Int
  author    User     @relation(fields: [authorId], references: [id])
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}
```
`Comment` has two FKs: `postId` (primary parent → drives nested routes) and `authorId` (owner FK → injected from JWT). Developable auto-detects `Post` as the primary parent (first non-auth FK).

### Pattern 3 — Hierarchy without auth ownership (Products → Reviews)
```prisma
model Product {
  id          Int      @id @default(autoincrement())
  name        String
  description String?
  price       Float
  createdAt   DateTime @default(now())
  updatedAt   DateTime @updatedAt
  reviews     Review[]
}

model Review {
  id        Int      @id @default(autoincrement())
  rating    Int
  body      String
  productId Int
  product   Product  @relation(fields: [productId], references: [id])
  authorId  Int
  author    User     @relation(fields: [authorId], references: [id])
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}
```

### Pattern 4 — SaaS hierarchy (Org → Project → Task)
When an entity has only one non-auth FK, that's auto-detected as primary parent. No `primary_parent` override needed.
```prisma
model Organization {
  id        Int       @id @default(autoincrement())
  name      String
  ownerId   Int
  owner     User      @relation(fields: [ownerId], references: [id])
  createdAt DateTime  @default(now())
  updatedAt DateTime  @updatedAt
  projects  Project[]
}

model Project {
  id        Int      @id @default(autoincrement())
  name      String
  orgId     Int
  org       Organization @relation(fields: [orgId], references: [id])
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
  tasks     Task[]
}

model Task {
  id          Int      @id @default(autoincrement())
  title       String
  description String?
  done        Boolean  @default(false)
  projectId   Int
  project     Project  @relation(fields: [projectId], references: [id])
  assigneeId  Int?
  assignee    User?    @relation(fields: [assigneeId], references: [id])
  createdAt   DateTime @default(now())
  updatedAt   DateTime @updatedAt
}
```
Note `assigneeId` is optional (`Int?`) — optional FKs are not treated as ownership anchors by Developable.

---

## Common Mistakes to Avoid

- **`// @auth_entity` must be on its own line directly before `model`** — not inline on the model declaration
- **`// @llm sensitive` must be inline on the field line** — not on a separate line above it
- **Use `@updatedAt` not `@default(now())` for `updatedAt`** — the former auto-updates on every write
- **Both sides of a relation must be declared** — if Post has `author User @relation(...)`, User must have `posts Post[]`
- **Don't create direct many-to-many Prisma relations** — use an explicit join table model (e.g., `UserOrg` with `userId` and `orgId`) so the FK structure is visible to Developable's planner
- **Don't add `// @llm sensitive` to FK fields** (e.g., `authorId`) — sensitive applies to secret values, not foreign keys
