# /developable

Generate a production-ready Express + TypeScript REST API from a Prisma schema, enforcing Developable's security invariants and file structure.

**Usage:** `/developable [path/to/schema.prisma] [--out ./output] [--rules path/to/rules.yaml]`

If no schema path is given, search for `schema.prisma` in `prisma/`, the current directory, and up to 2 levels deep. If not found, ask the user where it is.

---

## Phase 1 — Parse the Schema

Read the `schema.prisma` file. Extract:

### Datasource & Generator (copy verbatim to output)
```
datasource db { provider = "postgresql"; url = env("DATABASE_URL") }
generator client { provider = "prisma-client-js" }
```

### Entities (one per `model` block)
For each model, collect:
- **Name**: the model name (e.g., `User`, `Post`)
- **name_lower**: lowercase (e.g., `user`, `post`)
- **name_plural**: pluralised (add `s`; handle `y→ies`)
- **is_auth_entity**: `true` if the line immediately before `model Name {` is exactly `// @auth_entity`
- **llm_hints**: any `// @llm <hint text>` lines appearing before the model block (not `@llm sensitive`)
- **fields**: see below
- **relations**: see below

### Field Annotations
For each field line inside a model block:
- `name`: first token
- `prisma_type`: second token (`Int`, `String`, `Boolean`, `DateTime`, `Float`, `Json`, `BigInt`)
- `ts_type`: map via `Int→number`, `Float→number`, `BigInt→bigint`, `Decimal→number`, `Boolean→boolean`, `DateTime→Date`, `Json→Record<string,any>`, `String→string`
- `is_optional`: field line ends with `?`
- `is_list`: type ends with `[]`
- `is_id`: has `@id` annotation
- `is_unique`: has `@unique` annotation
- `is_relation`: field type matches a known model name (skip these in most contexts — use `relations` list instead)
- `is_sensitive`: inline comment on the field line matches `// @llm sensitive`
  - **Also auto-mark as sensitive** any field whose name is in: `password`, `passwordHash`, `passcode`, `secret`, `token`, `apiKey`, `api_key`, `secretKey`, `privateKey`, `credential`
- `default`: value inside `@default(...)` if present (e.g., `autoincrement()`, `now()`, `false`, `uuid()`)
- `is_enum`: true if `prisma_type` matches a declared `enum` block name

### Relation Resolution
For each field whose type is another model name:
- If the field line has `@relation(fields: [fkField], references: [id])`: this is **many_to_one** from this entity to the related entity; `fk_field = fkField`
- If the field type is `ModelName[]`: this is **one_to_many** from this entity to the related entity
- Collect each relation as `{ name, related_entity, type: "one_to_many"|"many_to_one"|"one_to_one", fk_field }`

### Auth Entity
The entity with `is_auth_entity: true`. Set:
- `auth_id_field`: name of the field with `@id` (usually `id`)
- `auth_login_field`: the field used for login lookup — prefer `email` (unique string), else first `@unique` string scalar, else first non-sensitive non-relation scalar
- Collect `sensitive_fields`: fields with `is_sensitive: true`

### Rules YAML (optional)
If a `rules.yaml` exists alongside the schema or is passed via `--rules`, merge it:
```yaml
entities:
  EntityName:
    constraints:         # free-text hints for Zod validator LLM logic
      - "Only the author can edit"
    primary_parent: Post # override auto-detected primary parent
    endpoints:
      deny:
        - method: POST
          path: /comments/:id/comments
```

---

## Phase 2 — Plan (compute per-entity decisions)

For each entity, determine:

### `owner_fk_field`
The scalar FK field on this entity that points to the **auth entity**, where the FK field is **not optional**.
- Walk the entity's `many_to_one` relations → find one where `related_entity == auth_entity_name`
- Return the `fk_field` of that relation if the corresponding scalar field is `!is_optional`
- If optional, skip it — optional FKs cannot serve as ownership anchors
- Example: `Post.authorId Int` (non-optional, points to User) → `owner_fk_field = "authorId"`

### `primary_parent`
The entity this entity primarily "belongs to" (drives nested route creation).
1. First check rules YAML `primary_parent` override
2. Else: first non-auth `many_to_one` relation (e.g., Comment's `postId → Post` wins over `authorId → User`)
3. Else: the auth entity FK (if that's the only `many_to_one` relation)
4. Else: `null`

### `nested_routes`
For each `one_to_many` relation on this entity:
- `relation_name`: the field name (e.g., `posts`)
- `related_entity`: target entity name (e.g., `Post`)
- `related_entity_lower` / `related_entity_plural`
- `fk_field`: the FK on the child entity pointing back to this entity
- `child_owner_fk_field`: the `owner_fk_field` of the child entity (may be null)
- `is_primary_parent`: true if this entity is the `primary_parent` of the child entity
- `use_nested_schema`: true when `is_primary_parent` is true AND the FK being injected from the URL is **not** the same as the child's `owner_fk_field` (i.e., the child has a separate non-auth parent FK).
  - Example: `Post` (primary parent) → `Comment` has `fk_field=postId` and `child_owner_fk_field=authorId` — different fields → `use_nested_schema=true` → use `validateCommentCreateNested` (omits `postId` from body)
  - Counter-example: `User` (auth entity, primary parent) → `Post` has `fk_field=authorId` and `child_owner_fk_field=authorId` — same field — `use_nested_schema=false` → use `validatePostCreate` (already omits `authorId` as owner FK)

### `parent_fk_relations`
The `many_to_one` relations where this entity is the child. Used in repository `findManyBy<FK>` methods.

### `child_cascade_deletes`
For entities with `one_to_many` relations: when this entity is deleted, list child entities and their FK field that need to be deleted first (to avoid FK constraint violations).
```python
[{ "child_name_lower": "comment", "fk_field": "postId" }]
```

### `scalar_fields`
All fields where `!is_relation` and `!is_id`. Used for validator and type generation.

### `routes` (standard CRUD per entity)
```
GET    /api/{plural}        → getAll
GET    /api/{plural}/:id    → getById
POST   /api/{plural}        → create   (suppressed if primary_parent != null)
PUT    /api/{plural}/:id    → update
DELETE /api/{plural}/:id    → remove
```
Apply any `endpoint_deny` rules from the rules YAML to suppress routes.

---

## Phase 3 — Security Invariants (NON-NEGOTIABLE)

These rules apply to **every generated file**. No exceptions.

### 1. Integer ID Validation (`_parseId`)
```typescript
private _parseId(raw: string): number {
  if (!/^\d+$/.test(raw)) {
    throw new AppError(400, 'Invalid ID format');
  }
  const id = Number(raw);
  if (id > Number.MAX_SAFE_INTEGER) {
    throw new AppError(400, 'ID out of range');
  }
  return id;
}
```
**Never use `parseInt` alone** — `parseInt('1.5abc')` returns `1`, not an error.

### 2. Owner FK Server Injection
In `create`, the owner FK is **always injected from `req.user!.id`**, never accepted from `req.body`:
```typescript
// CORRECT — owner FK comes from auth token
const record = await this.repository.create({ ...data, authorId: req.user!.id });

// WRONG — never do this
const record = await this.repository.create(data); // if data could contain authorId
```
The Zod validator **must not include the owner FK field** in any schema.

### 3. Auth Entity Self-Ownership
When the entity being updated/deleted is the auth entity itself:
```typescript
if (id !== req.user!.id) {
  throw new AppError(403, 'Forbidden');
}
```

### 4. Resource Ownership Check
When the entity has a `owner_fk_field` (but is not the auth entity):
```typescript
const existing = await this.repository.findById(id);
if (!existing) throw new NotFoundError('EntityName', id);
if (existing.authorId !== req.user!.id) {
  throw new AppError(403, 'Forbidden');
}
```
This check runs **before** the update/delete, not after.

### 5. Sensitive Field Hashing in Repository
```typescript
const securedData: Record<string, any> = { ...data };
if (securedData.password) {
  securedData.password = await hashValue(String(securedData.password));
}
return prisma.user.create({ data: securedData as any, select: this.safeSelect });
```
Applied in both `create` and `update`.

### 6. Sensitive Field Exclusion (`safeSelect`)
```typescript
private readonly safeSelect = {
  id: true,
  email: true,
  name: true,
  password: false,   // sensitive — never returned
  createdAt: true,
} as const;
```
All Prisma reads use `select: this.safeSelect`. Every read, every time.

### 7. JWT Middleware on Write Routes
```typescript
router.post('/', authenticate, controller.create.bind(controller));
router.put('/:id', authenticate, controller.update.bind(controller));
router.delete('/:id', authenticate, controller.remove.bind(controller));
```
`authenticate` **must be the second argument** (before the handler). `GET` routes do not require auth.

---

## Phase 4 — Generate Files

Create output at `--out` path (default `./output`). Run `mkdir -p src/routes src/controllers src/repositories src/validators src/types src/lib` first.

### Order: project-level files first, then per-entity files

---

### `package.json`
```json
{
  "name": "{project-name}-api",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/server.ts",
    "build": "tsc",
    "start": "node dist/server.js"
  },
  "dependencies": {
    "@prisma/client": "^5.0.0",
    "bcrypt": "^5.1.0",
    "cors": "^2.8.5",
    "express": "^4.18.0",
    "helmet": "^7.0.0",
    "jsonwebtoken": "^9.0.0",
    "morgan": "^1.10.0",
    "zod": "^3.22.0"
  },
  "devDependencies": {
    "@types/bcrypt": "^5.0.0",
    "@types/cors": "^2.8.0",
    "@types/express": "^4.17.0",
    "@types/jsonwebtoken": "^9.0.0",
    "@types/morgan": "^1.9.0",
    "@types/node": "^20.0.0",
    "prisma": "^5.0.0",
    "tsx": "^4.0.0",
    "typescript": "^5.0.0"
  }
}
```

---

### `tsconfig.json`
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

---

### `src/server.ts`
```typescript
import app from './app.js';

const PORT = parseInt(process.env.PORT ?? '3000', 10);

app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
```

---

### `src/app.ts`
```typescript
import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import morgan from 'morgan';
// import one router per entity, e.g.:
// import { usersRouter } from './routes/users.routes.js';
// import { postsRouter } from './routes/posts.routes.js';
// if auth entity: import { authRouter } from './routes/auth.routes.js';
import { errorHandler } from './lib/errors.js';

const app = express();

app.use(helmet());
app.use(cors());
app.use(express.json());
app.use(morgan('dev'));

// if auth entity: app.use('/auth', authRouter);
// mount each entity router: app.use('/api/users', usersRouter);

app.get('/health', (_req, res) => {
  res.json({ status: 'ok' });
});

app.use((_req, res) => {
  res.status(404).json({ error: 'Not found' });
});

app.use(errorHandler);

export default app;
```
Replace the comments with the actual imports and `app.use()` calls for every entity.

---

### `src/lib/prisma.ts`
```typescript
import { PrismaClient } from '@prisma/client';

const globalForPrisma = global as unknown as { prisma: PrismaClient };

export const prisma =
  globalForPrisma.prisma ?? new PrismaClient({ log: ['error'] });

if (process.env.NODE_ENV !== 'production') {
  globalForPrisma.prisma = prisma;
}
```

---

### `src/lib/errors.ts`
```typescript
import { Request, Response, NextFunction } from 'express';

export class AppError extends Error {
  constructor(public statusCode: number, message: string) {
    super(message);
    this.name = 'AppError';
  }
}

export class NotFoundError extends AppError {
  constructor(resource: string, id: string | number) {
    super(404, `${resource} with id ${id} not found`);
  }
}

export class ValidationError extends AppError {
  constructor(message: string) { super(400, message); }
}

export class ConflictError extends AppError {
  constructor(message: string) { super(409, message); }
}

export function errorHandler(err: Error, _req: Request, res: Response, _next: NextFunction): void {
  if (err instanceof AppError) {
    res.status(err.statusCode).json({ error: err.message });
    return;
  }
  if (err.constructor.name === 'PrismaClientKnownRequestError') {
    const prismaErr = err as any;
    if (prismaErr.code === 'P2002') { res.status(409).json({ error: 'A record with this value already exists' }); return; }
    if (prismaErr.code === 'P2025') { res.status(404).json({ error: 'Record not found' }); return; }
  }
  console.error(err);
  res.status(500).json({ error: 'Internal server error' });
}
```

---

### `src/lib/pagination.ts`
```typescript
import { Request } from 'express';

export interface PaginationParams {
  skip: number;
  limit: number;
}

export function parsePagination(req: Request): PaginationParams {
  const page  = Math.max(1, parseInt(String(req.query.page  ?? '1'),  10) || 1);
  const limit = Math.min(100, Math.max(1, parseInt(String(req.query.limit ?? '20'), 10) || 20));
  return { skip: (page - 1) * limit, limit };
}

export function buildPaginatedResponse(
  data: any[],
  total: number,
  pagination: PaginationParams,
): object {
  const page = Math.floor(pagination.skip / pagination.limit) + 1;
  return {
    data,
    meta: {
      total,
      page,
      limit: pagination.limit,
      totalPages: Math.ceil(total / pagination.limit),
    },
  };
}
```

---

### `src/lib/crypto.ts` (only if any entity has sensitive fields)
```typescript
import bcrypt from 'bcrypt';

const SALT_ROUNDS = 12;

export async function hashValue(value: string): Promise<string> {
  return bcrypt.hash(value, SALT_ROUNDS);
}

export async function compareValue(plain: string, hashed: string): Promise<boolean> {
  return bcrypt.compare(plain, hashed);
}
```

---

### `src/lib/auth.ts` (only if auth entity exists)

Write exactly the content from the `auth.ts.j2` template, filled in for the actual auth entity. Key points:
- `AuthUser` interface has `id: number` (or the PK ts_type), plus all non-sensitive non-relation non-id fields
- `authenticate` middleware verifies `Bearer <token>` header, sets `req.user`, calls `next()` on success
- Returns `401` for missing or invalid tokens

```typescript
import { Request, Response, NextFunction } from 'express';
import jwt from 'jsonwebtoken';

export interface AuthUser {
  id: number;           // adjust to auth entity PK type
  email: string;        // include all non-sensitive scalar fields
  name: string;
  // ... etc
}

declare global {
  namespace Express {
    interface Request { user?: AuthUser; }
  }
}

export function authenticate(req: Request, res: Response, next: NextFunction): void {
  const token = req.headers.authorization?.startsWith('Bearer ')
    ? req.headers.authorization.slice(7)
    : undefined;
  if (!token) { res.status(401).json({ error: 'Unauthorized' }); return; }
  try {
    req.user = jwt.verify(token, process.env.JWT_SECRET!) as AuthUser;
    next();
  } catch {
    res.status(401).json({ error: 'Invalid or expired token' });
  }
}
```

---

### `.env.example`
```
DATABASE_URL="postgresql://postgres:postgres@localhost:5432/mydb"
JWT_SECRET="change-me-in-production"
PORT=3000
NODE_ENV=development
```
Include any additional `env("VAR_NAME")` references found in the schema datasource.

---

## Per-Entity Files

For each entity, generate these 5 files. Walk through all entities including the auth entity.

---

### `src/types/{name_lower}.types.ts`

```typescript
export interface {Name}CreateInput {
  // required fields: non-optional, no default, non-relation, non-id scalars
  title: string;
  content: string;
  // optional fields: is_optional OR has default value
  published?: boolean;
  authorId?: number;    // FK fields: always optional here (FK is injected by controller)
}

export type {Name}UpdateInput = Partial<{Name}CreateInput>;
```

Rules:
- Only include scalar (non-relation) fields, excluding the `id` field
- A field is required if `!is_optional && !default`
- A field is optional if `is_optional || default`
- FK fields (e.g., `authorId`) should always be `optional` here since they may be injected server-side

---

### `src/validators/{name_lower}.validator.ts`

Generate Zod schemas following these rules precisely:

**SERVER-INJECTED** fields (the `owner_fk_field`): **omit entirely from all schemas**
**PARENT-FK** fields (the FK pointing to `primary_parent`):
  - Include in `{lower}CreateSchema` (direct POST needs it)
  - Omit entirely from `{lower}CreateNestedSchema` (injected from URL)

**Zod type mapping and constraints:**
- `email` field → `z.string().email({ message: 'Please provide a valid email address' })`
- `password` field → `z.string().min(8).max(128)`
- `url`/`website` → `z.string().url()`
- `name`/`title` → `z.string().min(1).max(255).trim()`
- `description`/`body`/`content` → `z.string().min(1).max(10000)`
- `age` → `z.number().int().min(0).max(150)`
- Numeric quantities (`price`, `amount`, `cost`, `total`, `count`, `quantity`, `fee`, `balance`, `score`) → `z.number().min(0)`
- `boolean` fields → `z.boolean()`
- `DateTime` fields → `z.coerce.date()`
- Enum fields → `z.enum(['VALUE1', 'VALUE2'])`
- Other `string` fields → `z.string().min(1).trim()`
- Other `number` fields → `z.number()`
- Fields with `@default(...)` → always `.optional()` in create schema
- **Match ts_type strictly**: never use `z.string()` for a `number` field

**Update schema**: use `.partial()` on the create schema object.

Full file structure:
```typescript
import { z } from 'zod';
import { ValidationError } from '../lib/errors.js';
import { {Name}CreateInput, {Name}UpdateInput } from '../types/{lower}.types.js';

const {lower}CreateSchema = z.object({
  // ... fields
});

// Only if this entity has a primary_parent (has parent_fk_fields):
const {lower}CreateNestedSchema = z.object({
  // ... same as createSchema but WITHOUT the parent FK field
});

const {lower}UpdateSchema = {lower}CreateSchema.partial();

function formatErrors(errors: z.ZodIssue[]): string {
  const seen = new Set<string>();
  return errors
    .map(e => { const f = e.path.length > 0 ? e.path.join('.') : null; return f ? `${f}: ${e.message}` : e.message; })
    .filter(msg => { if (seen.has(msg)) return false; seen.add(msg); return true; })
    .join(', ');
}

export function validate{Name}Create(body: unknown): {Name}CreateInput {
  const result = {lower}CreateSchema.safeParse(body);
  if (!result.success) throw new ValidationError(formatErrors(result.error.errors));
  return result.data as {Name}CreateInput;
}

// Only if has parent_fk_fields:
export function validate{Name}CreateNested(body: unknown): {Name}CreateInput {
  const result = {lower}CreateNestedSchema.safeParse(body);
  if (!result.success) throw new ValidationError(formatErrors(result.error.errors));
  return result.data as {Name}CreateInput;
}

export function validate{Name}Update(body: unknown): {Name}UpdateInput {
  const result = {lower}UpdateSchema.safeParse(body);
  if (!result.success) throw new ValidationError(formatErrors(result.error.errors));
  return result.data as {Name}UpdateInput;
}
```

---

### `src/repositories/{name_lower}.repository.ts`

Full pattern (apply invariants 5 and 6 strictly):

```typescript
import { prisma } from '../lib/prisma.js';
import { PaginationParams } from '../lib/pagination.js';
import { {Name}CreateInput, {Name}UpdateInput } from '../types/{lower}.types.js';
// Only if sensitive fields exist:
import { hashValue } from '../lib/crypto.js';

export class {Name}Repository {
  // safeSelect: only if sensitive fields exist
  private readonly safeSelect = {
    id: true,
    email: true,
    name: true,
    password: false,  // sensitive field — always false
    createdAt: true,
    updatedAt: true,
  } as const;

  async findMany(pagination: PaginationParams): Promise<{ data: any[]; total: number }> {
    const [data, total] = await prisma.$transaction([
      prisma.{lower}.findMany({
        skip: pagination.skip,
        take: pagination.limit,
        orderBy: { id: 'desc' },
        select: this.safeSelect,  // omit if no sensitive fields
      }),
      prisma.{lower}.count(),
    ]);
    return { data, total };
  }

  async findById(id: number): Promise<any | null> {
    return prisma.{lower}.findUnique({
      where: { id },
      select: this.safeSelect,  // omit if no sensitive fields
    });
  }

  async create(data: {Name}CreateInput): Promise<any> {
    // If sensitive fields: hash them before storing
    const securedData: Record<string, any> = { ...data };
    if (securedData.password) {
      securedData.password = await hashValue(String(securedData.password));
    }
    return prisma.{lower}.create({
      data: securedData as any,
      select: this.safeSelect,
    });
  }

  async update(id: number, data: {Name}UpdateInput): Promise<any | null> {
    try {
      // If sensitive fields: hash them if present in the update payload
      const securedData: Record<string, any> = { ...data };
      if (securedData.password) {
        securedData.password = await hashValue(String(securedData.password));
      }
      return await prisma.{lower}.update({
        where: { id },
        data: securedData as any,
        select: this.safeSelect,
      });
    } catch (err: any) {
      if (err.code === 'P2025') return null;
      throw err;
    }
  }

  async delete(id: number): Promise<boolean> {
    try {
      // If child_cascade_deletes: delete children in a transaction first
      await prisma.$transaction([
        prisma.comment.deleteMany({ where: { postId: id } }),
        prisma.{lower}.delete({ where: { id } }),
      ]);
      // If no children, just: await prisma.{lower}.delete({ where: { id } });
      return true;
    } catch (err: any) {
      if (err.code === 'P2025') return false;
      throw err;
    }
  }

  // One method per parent_fk_relation:
  async findManyByAuthorId(parentId: number, pagination: PaginationParams): Promise<{ data: any[]; total: number }> {
    const [data, total] = await prisma.$transaction([
      prisma.{lower}.findMany({
        where: { authorId: parentId },
        skip: pagination.skip,
        take: pagination.limit,
        orderBy: { id: 'desc' },
        select: this.safeSelect,
      }),
      prisma.{lower}.count({ where: { authorId: parentId } }),
    ]);
    return { data, total };
  }
}
```

Adapt each placeholder to the real entity. Include `findManyBy{FK}` methods for all `many_to_one` relations (one method per FK).

---

### `src/controllers/{name_lower}.controller.ts`

Full pattern (apply all 7 invariants):

```typescript
import { Request, Response, NextFunction } from 'express';
import { {Name}Repository } from '../repositories/{lower}.repository.js';
import { validate{Name}Create, validate{Name}Update } from '../validators/{lower}.validator.js';
import { parsePagination, buildPaginatedResponse } from '../lib/pagination.js';
import { NotFoundError, AppError } from '../lib/errors.js';
// For each nested route: import child repository and validators

export class {Name}Controller {
  private repository: {Name}Repository;
  // private {child}Repository: {Child}Repository; — one per nested route

  constructor() {
    this.repository = new {Name}Repository();
  }

  async getAll(req: Request, res: Response, next: NextFunction): Promise<void> {
    try {
      const pagination = parsePagination(req);
      const { data, total } = await this.repository.findMany(pagination);
      res.json(buildPaginatedResponse(data, total, pagination));
    } catch (err) { next(err); }
  }

  async getById(req: Request, res: Response, next: NextFunction): Promise<void> {
    try {
      const id = this._parseId(req.params.id);
      const record = await this.repository.findById(id);
      if (!record) throw new NotFoundError('{Name}', id);
      res.json(record);
    } catch (err) { next(err); }
  }

  async create(req: Request, res: Response, next: NextFunction): Promise<void> {
    try {
      const data = validate{Name}Create(req.body);
      // Invariant 2: inject owner FK from token, never from body
      const record = await this.repository.create({ ...data, authorId: req.user!.id });
      res.status(201).json(record);
    } catch (err) { next(err); }
  }

  async update(req: Request, res: Response, next: NextFunction): Promise<void> {
    try {
      const id = this._parseId(req.params.id);
      // Invariant 3 (auth entity): if (id !== req.user!.id) throw new AppError(403, 'Forbidden');
      // Invariant 4 (owned resource):
      const existing = await this.repository.findById(id);
      if (!existing) throw new NotFoundError('{Name}', id);
      if (existing.authorId !== req.user!.id) throw new AppError(403, 'Forbidden');
      const data = validate{Name}Update(req.body);
      const record = await this.repository.update(id, data);
      if (!record) throw new NotFoundError('{Name}', id);
      res.json(record);
    } catch (err) { next(err); }
  }

  async remove(req: Request, res: Response, next: NextFunction): Promise<void> {
    try {
      const id = this._parseId(req.params.id);
      // Same ownership checks as update
      const existing = await this.repository.findById(id);
      if (!existing) throw new NotFoundError('{Name}', id);
      if (existing.authorId !== req.user!.id) throw new AppError(403, 'Forbidden');
      const deleted = await this.repository.delete(id);
      if (!deleted) throw new NotFoundError('{Name}', id);
      res.status(204).send();
    } catch (err) { next(err); }
  }

  // --- Nested route handlers (one pair per nested_route) ---

  async get{Child}sFor{Name}(req: Request, res: Response, next: NextFunction): Promise<void> {
    try {
      // If this entity is the auth entity: parentId = req.user!.id
      // Otherwise: parentId = this._parseId(req.params.id)
      const parentId = this._parseId(req.params.id);
      const pagination = parsePagination(req);
      const { data, total } = await this.{child}Repository.findManyBy{FkField}(parentId, pagination);
      res.json(buildPaginatedResponse(data, total, pagination));
    } catch (err) { next(err); }
  }

  // Only generate create{Child}For{Name} when nested.is_primary_parent === true
  async create{Child}For{Name}(req: Request, res: Response, next: NextFunction): Promise<void> {
    try {
      // If this entity is_auth_entity: parentId from token (no :id in URL)
      // Otherwise: parentId from URL param
      const parentId = this._parseId(req.params.id); // or req.user!.id if auth entity
      // use_nested_schema=true  → use validate{Child}CreateNested (omits the parent FK from body)
      // use_nested_schema=false → use validate{Child}Create (owner FK already omitted as server-injected)
      const data = validate{Child}CreateNested(req.body); // or validate{Child}Create
      // Always inject the parent FK (fk_field):
      // If child has a separate child_owner_fk_field, also inject it from req.user!.id
      const record = await this.{child}Repository.create({ ...data, postId: parentId, authorId: req.user!.id });
      res.status(201).json(record);
    } catch (err) { next(err); }
  }

  // Invariant 1: always at the bottom
  private _parseId(raw: string): number {
    if (!/^\d+$/.test(raw)) throw new AppError(400, 'Invalid ID format');
    const id = Number(raw);
    if (id > Number.MAX_SAFE_INTEGER) throw new AppError(400, 'ID out of range');
    return id;
  }
}
```

**Decision rules for each method:**
- `create`: add `owner_fk_field` injection only if `owner_fk_field != null`
- `update`/`remove`:
  - If `entity.is_auth_entity`: use Invariant 3 pattern
  - Else if `owner_fk_field != null`: use Invariant 4 pattern
  - Else: no ownership check (public resource)
- Nested `create{Child}For{Name}`:
  - If this entity `is_auth_entity`: `parentId = req.user!.id` (no `:id` in URL)
  - Inject `fk_field: parentId` always
  - If child has `child_owner_fk_field`: also inject it from `req.user!.id`

---

### `src/routes/{name_plural}.routes.ts`

```typescript
import { Router } from 'express';
import { {Name}Controller } from '../controllers/{lower}.controller.js';
import { authenticate } from '../lib/auth.js';  // only if auth entity exists

const router = Router();
const controller = new {Name}Controller();

// Standard CRUD routes
// GET routes: no auth required
router.get('/', controller.getAll.bind(controller));
router.get('/:id', controller.getById.bind(controller));

// Write routes: authenticate if auth system exists (Invariant 7)
router.post('/', authenticate, controller.create.bind(controller));
router.put('/:id', authenticate, controller.update.bind(controller));
router.delete('/:id', authenticate, controller.remove.bind(controller));

// Nested routes (one pair per nested_route):
router.get('/:id/comments', controller.getCommentsForPost.bind(controller));
// POST nested: if parent is_auth_entity → no :id in URL
//              else → /:id/comments
router.post('/:id/comments', authenticate, controller.createCommentForPost.bind(controller));

export { router as {plural}Router };
```

**Decision rules:**
- If no auth entity exists in schema: omit `authenticate` import and all `authenticate` middleware
- **Direct `POST /` is suppressed** for any entity whose `primary_parent != null` — that entity can only be created through its parent's nested route (e.g., Post is created via `POST /users/posts`, not `POST /posts`)
- Nested POST route is **only wired** when `nested.is_primary_parent === true`:
  - If parent `is_auth_entity`: `router.post('/{relation_name}', authenticate, ...)` (no `/:id` — parent ID comes from JWT)
  - Else with auth: `router.post('/:id/{relation_name}', authenticate, ...)`
  - Else no auth: `router.post('/:id/{relation_name}', ...)`
- Nested GET (`/:id/{relation_name}`) is always wired regardless of `is_primary_parent`
- The controller generates `create{Child}For{Name}` for ALL nested routes but it is only **routed** for `is_primary_parent` routes

---

## Auth Entity Extra Files

If an auth entity exists, also generate:

### `src/routes/auth.routes.ts`
```typescript
import { Router } from 'express';
import { AuthController } from '../controllers/auth.controller.js';

const router = Router();
const controller = new AuthController();

router.post('/register', controller.register.bind(controller));
router.post('/login', controller.login.bind(controller));

export { router as authRouter };
```

### `src/controllers/auth.controller.ts`

Follow the `auth.controller.ts.j2` pattern exactly:
- `registerSchema`: Zod schema with login field (`email` → `.email()`) and credential field (`password` → `.min(8).max(128)`), plus any other required non-sensitive non-id scalar fields
- `loginSchema`: only login field + credential field
- `safeSelect`: all non-sensitive non-relation fields set to `true`
- `register`: parse → hash credential → prisma.create with safeSelect → JWT sign → return `{ token, user }`
- `login`: parse → prisma.findUnique/findFirst by login field → compareValue → JWT sign → return `{ token }` only
- JWT payload: `{ id: pkValue, ...allSafeFields }` — map the PK field name to `id`
- **Never** expose the credential field in any response
- Handle `P2002` (unique constraint) → `409 Conflict`

---

## Final Steps

After all files are written:
1. Copy the `schema.prisma` into `prisma/schema.prisma` in the output directory
2. Print the next steps for the user:

```
✓ Generated X files across Y entities

Next steps:
  cd <output>
  npm install
  cp .env.example .env   # fill in DATABASE_URL and JWT_SECRET
  npx prisma migrate dev --name init
  npm run dev
```
