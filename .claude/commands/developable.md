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

## Phase 5 — Generate Tests

Write a `tests/` directory alongside the API. The suite is pure Python, needs only `pip install requests`, and covers every generated endpoint. Run modules in the numbered order listed below — each one populates `ctx.state` for the next.

### `tests/helpers.py`

```python
import sys, json, base64, uuid
import requests

class TestContext:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.state: dict = {}
        self.pass_count = self.fail_count = self.warn_count = self.skip_count = 0

    def ok(self, msg):   self.pass_count += 1; print(f"  ✅  {msg}")
    def fail(self, msg): self.fail_count  += 1; print(f"  ❌  {msg}")
    def auth(self, msg): self.fail_count  += 1; print(f"  ⛔️  {msg}")
    def warn(self, msg): self.warn_count  += 1; print(f"  ⚠️   {msg}")
    def skip(self, msg): self.skip_count  += 1; print(f"  ⏭️   SKIP  {msg}")

    def req(self, method, path, token=None, body=None, params=None):
        headers = {"Content-Type": "application/json"}
        if token: headers["Authorization"] = f"Bearer {token}"
        print(f"  🚀  {method.upper()} {path}")
        try:
            return requests.request(method, f"{self.base_url}{path}",
                                    headers=headers, json=body, params=params, timeout=10)
        except requests.exceptions.ConnectionError:
            print(f"\n  ❌  Cannot connect to {self.base_url}. Is the server running?")
            sys.exit(1)

    def assert_status(self, resp, expected, label, auth_fail=False):
        if resp.status_code == expected:
            self.ok(f"{label} → HTTP {resp.status_code}"); return True
        msg = f"{label} → expected {expected}, got {resp.status_code} | {resp.text[:200]}"
        (self.auth if auth_fail else self.fail)(msg); return False

    def assert_field(self, data, field, label, absent=False):
        if absent:
            if field in data: self.fail(f"{label}: '{field}' should be absent"); return False
            self.ok(f"{label}: '{field}' correctly absent"); return True
        if field in data: self.ok(f"{label}: '{field}' present"); return True
        self.fail(f"{label}: '{field}' missing"); return False

    def assert_paginated(self, data, label):
        if "data" not in data or "meta" not in data:
            self.fail(f"{label}: missing 'data' or 'meta'"); return False
        for k in ("total", "page", "limit", "totalPages"):
            if k not in data["meta"]: self.fail(f"{label}: meta missing '{k}'"); return False
        self.ok(f"{label}: paginated — total={data['meta']['total']}"); return True

    @staticmethod
    def unique_email(prefix="user"):
        return f"{prefix}_{uuid.uuid4().hex[:8]}@test.example.com"

    @staticmethod
    def safe_json(resp):
        try: return resp.json()
        except: return {}

    @staticmethod
    def decode_jwt(token):
        try:
            p = token.split(".")[1]; p += "=" * (4 - len(p) % 4)
            return json.loads(base64.urlsafe_b64decode(p))
        except: return None

    @staticmethod
    def no_sensitive_field(obj, field):
        if isinstance(obj, dict):
            if field in obj: return False
            return all(TestContext.no_sensitive_field(v, field) for v in obj.values())
        if isinstance(obj, list):
            return all(TestContext.no_sensitive_field(i, field) for i in obj)
        return True

def section(title):
    print(f"\n{'═' * 64}\n  {title}\n{'═' * 64}")
```

### `tests/run_all.py`

```python
import sys, importlib
from helpers import TestContext

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
ctx = TestContext(BASE_URL)

# Import and run each module in order
modules = [
    "test_00_health",
    # auth modules (if auth entity exists):
    "test_01_register",
    "test_02_login",
    "test_03_{auth_plural}_get",
    "test_04_{auth_plural}_write",
    # per entity (in topological order — parents before children):
    # "test_05_{plural}_seed_get",
    # "test_06_{plural}_write",
    # ...
    "test_security_audit",
    "test_cleanup",
]

for name in modules:
    try:
        mod = importlib.import_module(name)
        mod.run(ctx)
    except Exception as e:
        ctx.fail(f"Module {name} raised: {e}")

print(f"\n{'═'*64}")
print(f"  Results: {ctx.pass_count} passed · {ctx.fail_count} failed · {ctx.warn_count} warnings · {ctx.skip_count} skipped")
print(f"{'═'*64}")
sys.exit(0 if ctx.fail_count == 0 else 1)
```

### Module 00 — `tests/test_00_health.py`

```python
from helpers import TestContext, section
def run(ctx: TestContext):
    section("0 · HEALTH CHECK")
    resp = ctx.req("GET", "/health")
    if ctx.assert_status(resp, 200, "GET /health"):
        data = ctx.safe_json(resp)
        ctx.ok("status=ok") if data.get("status") == "ok" else ctx.fail(f"unexpected: {data}")
```

### Auth Modules (only if auth entity exists)

**Module 01 — Register (`tests/test_01_register.py`)**

- Seed 3 users (user1, user2, user3). Store `user1_token`, `user1_id`, `user2_token`, `user2_id`, `email1`, `email2` in `ctx.state`.
- Assert: register → 201, response contains `token` and `{auth_entity_lower}` object
- Assert: sensitive field (e.g. `password`) NOT in response body or JWT payload
- Assert: duplicate email → 409
- Assert: missing required field → 400 (one test per required field)
- Assert: invalid email format → 400
- Assert: password too short (< 8 chars) → 400
- Assert: extra unknown fields silently stripped → still 201

**Module 02 — Login (`tests/test_02_login.py`)**

- Valid login with `email1` → 200, token in response
- Wrong password → 401
- Unknown email → 401
- Missing password → 400
- Assert: sensitive field not in login response

**Module 03 — Auth Entity GET (`tests/test_03_{auth_plural}_get.py`)**

- `GET /api/{auth_plural}` → 200, paginated shape (`data`, `meta.total`, `meta.page`)
- `GET /api/{auth_plural}/{user1_id}` → 200
- Assert `password` field absent from all responses

**Module 04 — Auth Entity Write (`tests/test_04_{auth_plural}_write.py`)**

- `PUT /api/{auth_plural}/{user1_id}` by user1 → 200
- `PUT /api/{auth_plural}/{user1_id}` without auth → 401
- `PUT /api/{auth_plural}/{user1_id}` by user2 → 403 (other user)
- `DELETE /api/{auth_plural}/{user1_id}` by user2 → 403
- `DELETE /api/{auth_plural}/9999999` by user1 → 404

### Per-Entity Modules (for each non-auth entity, in topological order)

**Seed + GET module (`tests/test_N_{plural}_seed_get.py`)**

Seed via the canonical create path:
- If `primary_parent` is the auth entity: `POST /api/{auth_plural}/{relation_name}` (no parent ID in URL)
- If `primary_parent` is another entity: `POST /api/{parent_plural}/{parent1_id}/{relation_name}`
- If no `primary_parent`: `POST /api/{plural}`

Seed 3 records with token1. Store `{entity_lower}1_id`, `{entity_lower}2_id`, `{entity_lower}3_id` in `ctx.state`. For each required field, use a sensible test value. For FK fields that are not server-injected (e.g. `productId` in OrderItem), supply `{related_entity}1_id` from `ctx.state`.

Then test:
- `GET /api/{plural}` → 200, paginated
- `GET /api/{plural}/{entity1_id}` → 200
- `GET /api/{plural}/9999999` → 404

**Write module (`tests/test_N+1_{plural}_write.py`)**

- `POST` canonical path without auth → 401
- `POST` with auth, empty body → 400
- `PUT /api/{plural}/{entity1_id}` by owner → 200
- `PUT /api/{plural}/{entity1_id}` without auth → 401
- `PUT /api/{plural}/{entity1_id}` by user2 (non-owner) → 403
- `DELETE /api/{plural}/{entity2_id}` without auth → 401
- `DELETE /api/{plural}/{entity2_id}` by user2 (non-owner) → 403
- `DELETE /api/{plural}/{entity2_id}` by owner → 204
- `GET /api/{plural}/{entity2_id}` after delete → 404
- `DELETE /api/{plural}/9999999` by owner → 404

### Security Audit Module (`tests/test_security_audit.py`)

For each entity, test `_parseId` enforcement:
```python
for bad_id in ["abc", "1.5", "1; DROP TABLE users", "0", "-1", "9" * 20]:
    resp = ctx.req("GET", f"/api/{plural}/{bad_id}")
    if resp.status_code not in (400, 404):
        ctx.warn(f"Suspicious: GET /api/{plural}/{bad_id} → {resp.status_code}")
    else:
        ctx.ok(f"Bad ID '{bad_id}' rejected with {resp.status_code}")
```

Test JWT tampering: decode a valid token, modify `id` field, re-sign with wrong secret, assert 401.

### Cleanup Module (`tests/test_cleanup.py`)

Delete all seeded data in reverse topological order (children before parents). Use `token1` for all deletes. Skip if the seed ID is already deleted (marked `{entity}_2_deleted`).

---

## Phase 6 — Infrastructure & CI/CD

Write these files into the output directory root:

### `Dockerfile`

Multi-stage Node.js 20 build:
```dockerfile
FROM node:20-slim AS deps
WORKDIR /app
COPY package*.json ./
RUN npm install

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npx prisma generate
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/prisma ./prisma
COPY package.json ./
EXPOSE 3000
CMD ["node", "dist/server.js"]
```

### `docker-compose.yml`

```yaml
services:
  db:
    image: postgres:15-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-postgres}
      POSTGRES_DB: ${POSTGRES_DB:-{project_name_underscored}}
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-postgres}"]
      interval: 5s
      timeout: 5s
      retries: 10

  pgadmin:
    image: dpage/pgadmin4:latest
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@admin.com
      PGADMIN_DEFAULT_PASSWORD: admin
    ports: ["5050:80"]
    depends_on:
      db: {condition: service_healthy}

  api:
    build: .
    env_file: [.env]
    ports: ["${PORT:-3000}:3000"]
    depends_on:
      db: {condition: service_healthy}
    command: sh -c "npx prisma migrate deploy && node dist/server.js"
```

Replace `{project_name_underscored}` with the project name slug with `-` replaced by `_`.

### `.github/workflows/ci.yml`

```yaml
name: CI
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15-alpine
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: testdb
        ports: ["5432:5432"]
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    env:
      DATABASE_URL: postgresql://postgres:postgres@localhost:5432/testdb
      JWT_SECRET: ci-only-jwt-secret-not-for-production
      PORT: 3000
      NODE_ENV: test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: {node-version: "20"}
      - run: npm install
      - run: npx prisma generate
      - run: npx prisma db push --accept-data-loss
      - run: npm run dev > /tmp/api.log 2>&1 &
      - name: Wait for API
        run: |
          for i in $(seq 1 30); do
            curl -sf http://localhost:3000/health && exit 0
            sleep 2
          done
          cat /tmp/api.log && exit 1
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install requests
      - run: python tests/run_all.py
        if: ${{ hashFiles('tests/run_all.py') != '' }}
      - if: failure()
        run: cat /tmp/api.log || true
```

### `.gitignore`

```
node_modules/
dist/
.env
*.log
prisma/migrations/
```

### Optional — Push to GitHub

If the user asks for GitHub setup, run:
```bash
gh repo create {project-name} --private --source=. --remote=origin
git init && git add . && git commit -m "Initial Developable-generated API"
git push -u origin main
```

---

## Phase 7 — Update CLAUDE.md

Generate or overwrite `CLAUDE.md` in the project root (the output directory). This file tells future Claude Code sessions the standards they must follow when modifying this codebase.

Write the following content, substituting entity names and fields from the parsed schema:

```markdown
# CLAUDE.md — {project-name} API

This project was generated by [Developable](https://developablecode.app). All code follows the
Developable standard. When adding features or modifying files, adhere to these rules exactly.

---

## Architecture

```
HTTP → src/routes/{plural}.routes.ts
     → src/controllers/{lower}.controller.ts
     → src/repositories/{lower}.repository.ts
     → Prisma (PostgreSQL)
```

- **Routes** — wire HTTP methods to controller methods + apply `authenticate` middleware. No logic here.
- **Controllers** — validate input (Zod), enforce ownership, call repository, send response. No direct Prisma.
- **Repositories** — all Prisma calls live here. All reads use `safeSelect`. No business logic.
- **Validators** — Zod schemas for each entity. Owner FKs are never in any Zod schema.
- **Types** — TypeScript input/output interfaces derived from the schema.

---

## Security Invariants (NON-NEGOTIABLE)

These rules must be preserved in every file you create or modify:

### 1. Integer ID Validation
```typescript
private _parseId(raw: string): number {
  if (!/^\d+$/.test(raw)) throw new AppError(400, 'Invalid ID format');
  const id = Number(raw);
  if (id > Number.MAX_SAFE_INTEGER) throw new AppError(400, 'ID out of range');
  return id;
}
```
**Never use `parseInt` alone** — `parseInt('1.5abc')` silently returns `1`.

### 2. Owner FK is Always Server-Injected
```typescript
// CORRECT
const record = await this.repository.create({ ...data, {owner_fk_field}: req.user!.id });

// WRONG — never trust body for ownership fields
const record = await this.repository.create(data);
```
The Zod validator must never include the owner FK field. It is injected in the controller.

### 3. Auth Entity Self-Ownership
```typescript
if (id !== req.user!.id) throw new AppError(403, 'Forbidden');
```

### 4. Resource Ownership Check Before Mutate
```typescript
const existing = await this.repository.findById(id);
if (!existing) throw new NotFoundError('{Name}', id);
if (existing.{owner_fk_field} !== req.user!.id) throw new AppError(403, 'Forbidden');
```

### 5. Sensitive Field Hashing
```typescript
if (data.password) data.password = await hashValue(String(data.password));
```
Applied in both `create` and `update` in the repository.

### 6. Sensitive Fields Excluded from All Responses
```typescript
private readonly safeSelect = {
  id: true,
  email: true,
  password: false,   // NEVER return this
  createdAt: true,
} as const;
```
Every Prisma read uses `select: this.safeSelect`.

### 7. JWT Middleware on All Write Routes
```typescript
router.post('/', authenticate, controller.create.bind(controller));
router.put('/:id', authenticate, controller.update.bind(controller));
router.delete('/:id', authenticate, controller.remove.bind(controller));
```
`authenticate` is always the second argument, before the handler.

---

## Error Handling

- Use `AppError(statusCode, message)` — caught by `errorHandler` in `src/lib/errors.ts`
- Use `NotFoundError(resource, id)` for missing records
- Use `ValidationError(message)` for Zod failures
- Use `ConflictError(message)` for P2002 unique constraint violations
- Never throw raw `Error` objects from controllers or repositories
- Never call `res.status().json()` directly in a catch block — always call `next(err)`

---

## Validation Rules

- Validate at the controller boundary using Zod — never in routes or repositories
- Use `z.string().min(1).trim()` for required strings; never allow empty strings
- Use `z.string().email()` for email fields
- Use `z.string().min(8).max(128)` for password fields
- Use `z.number().min(0)` for prices, quantities, and other non-negative numbers
- `.partial()` on the create schema produces the update schema — no separate definition needed
- All FK fields (e.g. `authorId`) must be omitted from every Zod schema — they are server-injected

---

## Database Access Patterns

- All Prisma calls go in the repository — never in controllers or routes
- Use `prisma.$transaction([findMany, count])` for paginated list queries
- All reads use `select: this.safeSelect` if the entity has sensitive fields
- For cascade deletes: delete children in a `$transaction` before deleting the parent
- Catch `P2025` (record not found) in `update` and `delete` — return `null` / `false` rather than throwing

---

## Auth Patterns

- JWT payload contains `id` + all non-sensitive non-relation scalar fields
- `authenticate` middleware sets `req.user` — always typed as `AuthUser` from `src/lib/auth.ts`
- Login endpoint returns `{ token }` only — never the user object
- Register endpoint returns `{ token, {auth_entity_lower} }` where `{auth_entity_lower}` uses `safeSelect`
- Token expiry: `1h` default — configurable via `JWT_EXPIRES_IN` env var

---

## TypeScript Conventions

- All files use ESM (`"type": "module"` in package.json) — imports must use `.js` extension
- Strict mode enabled — no `any` except where Prisma requires it for dynamic select patterns
- Controller methods are always `async (req, res, next): Promise<void>` — never use return values
- Repository methods return the entity type or `null` — never throw for not-found in repositories
- Use `as const` for `safeSelect` to get the narrowest Prisma return type

---

## Adding a New Entity

1. Add the model to `prisma/schema.prisma` with `// @auth_entity` or `// @llm` annotations as needed
2. Run `/developable` to regenerate — it will write new files without overwriting modified ones
3. Run `npx prisma migrate dev --name add_{entity_lower}` to create the migration
4. Add test coverage for the new entity following the patterns in `tests/`

---

## Adding a New Field to an Existing Entity

1. Add the field to `prisma/schema.prisma`
2. Add the field to `src/types/{lower}.types.ts` (in Create and Update interfaces)
3. Add the field to the Zod schema in `src/validators/{lower}.validator.ts`
4. If sensitive: set it to `false` in `safeSelect` and add hashing in `src/repositories/{lower}.repository.ts`
5. Run `npx prisma migrate dev --name add_{lower}_{field}`

---

## Running Locally

```bash
npm install
cp .env.example .env   # fill in DATABASE_URL and JWT_SECRET
npx prisma migrate dev --name init
npm run dev            # http://localhost:3000
```

## Running Tests

```bash
npm run dev &          # start API first
python tests/run_all.py http://localhost:3000
```

## Production Build

```bash
npm run build
node dist/server.js
```

Or with Docker:
```bash
docker compose up
```
```

Substitute `{project-name}`, `{auth_entity_lower}`, `{owner_fk_field}`, `{Name}`, and sensitive field names with the actual values from the parsed schema.

---

## Final Steps

After all phases are complete:
1. Copy `schema.prisma` to `prisma/schema.prisma` in the output directory
2. Print the summary:

```
✓ Generated {N} API files across {Y} entities
✓ Generated {M} test modules in tests/
✓ Generated Dockerfile, docker-compose.yml, .github/workflows/ci.yml
✓ Generated CLAUDE.md with Developable standards

Next steps:
  cd <output>
  npm install
  cp .env.example .env   # fill in DATABASE_URL and JWT_SECRET
  npx prisma migrate dev --name init
  npm run dev

Run tests (requires running server):
  python tests/run_all.py http://localhost:3000

Deploy locally:
  docker compose up
```
