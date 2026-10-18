import cors from "cors";
import express from "express";
import morgan from "morgan";
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();
const app = express();
const port = Number(process.env.PORT || 3000);

const includes = {
  user: { memberships: true, tasks: true, comments: true },
  organization: { memberships: true, projects: true },
  membership: { user: true, organization: true },
  project: { organization: true, tasks: true },
  task: { project: true, assignee: true, comments: true },
  taskComment: { author: true, task: true }
};

app.use(cors());
app.use(express.json({ limit: "10mb" }));
app.use(morgan("dev"));
app.use((req, _res, next) => {
  req.currentUserId = currentUserIdFrom(req);
  next();
});

declare global {
  namespace Express {
    interface Request {
      currentUserId?: number;
    }
  }
}

function currentUserIdFrom(req: express.Request) {
  const raw = req.header("x-user-id") || req.query.userId || req.body?.userId;
  const value = Number(raw);
  return Number.isFinite(value) ? value : undefined;
}

function idFrom(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function scalarBody(req: express.Request) {
  const body = req.body && typeof req.body === "object" ? { ...req.body } : {};
  delete body.id;
  delete body.memberships;
  delete body.tasks;
  delete body.comments;
  delete body.projects;
  delete body.organization;
  delete body.user;
  delete body.assignee;
  delete body.author;
  delete body.task;
  return body;
}

function fail(res: express.Response, error: unknown, status = 500) {
  const message = error instanceof Error ? error.message : String(error);
  res.status(status).json({
    ok: false,
    message: "Request failed",
    error: message,
    stack: process.env.NODE_ENV === "production" ? undefined : error instanceof Error ? error.stack : undefined
  });
}

function wrap(handler: (req: express.Request, res: express.Response) => Promise<void>) {
  return (req: express.Request, res: express.Response) => {
    handler(req, res).catch((error) => fail(res, error));
  };
}

app.get("/health", (_req, res) => {
  res.json({ ok: true, now: new Date().toISOString() });
});

app.post("/auth/register", wrap(async (req, res) => {
  const body = scalarBody(req);
  const user = await prisma.user.create({
    data: {
      email: String(body.email || ""),
      username: String(body.username || ""),
      password: String(body.password || ""),
      displayName: body.displayName ? String(body.displayName) : null
    },
    include: includes.user
  });

  res.status(201).json({
    token: `debug-token-${user.id}`,
    user
  });
}));

app.post("/auth/login", wrap(async (req, res) => {
  const body = scalarBody(req);
  const user = await prisma.user.findFirst({
    where: {
      OR: [
        { email: String(body.email || "") },
        { username: String(body.username || "") }
      ],
      password: String(body.password || "")
    },
    include: includes.user
  });

  if (!user) {
    res.status(401).json({ ok: false, message: "Bad credentials" });
    return;
  }

  res.json({
    token: `debug-token-${user.id}`,
    user,
    actingAsUserId: req.currentUserId ?? user.id
  });
}));

app.get("/users", wrap(async (_req, res) => {
  const users = await prisma.user.findMany({ include: includes.user, orderBy: { id: "asc" } });
  res.json(users);
}));

app.get("/users/:id", wrap(async (req, res) => {
  const user = await prisma.user.findUnique({
    where: { id: idFrom(req.params.id) },
    include: includes.user
  });
  res.json(user);
}));

app.patch("/users/:id", wrap(async (req, res) => {
  const body = scalarBody(req);
  const user = await prisma.user.update({
    where: { id: idFrom(req.params.id) },
    data: {
      ...body,
      updatedAt: new Date()
    },
    include: includes.user
  });
  res.json(user);
}));

app.delete("/users/:id", wrap(async (req, res) => {
  const user = await prisma.user.delete({
    where: { id: idFrom(req.params.id) },
    include: includes.user
  });
  res.json(user);
}));

app.get("/organizations", wrap(async (_req, res) => {
  const organizations = await prisma.organization.findMany({
    include: includes.organization,
    orderBy: { id: "asc" }
  });
  res.json(organizations);
}));

app.get("/organizations/:id", wrap(async (req, res) => {
  const organization = await prisma.organization.findUnique({
    where: { id: idFrom(req.params.id) },
    include: includes.organization
  });
  res.json(organization);
}));

app.post("/organizations", wrap(async (req, res) => {
  const body = scalarBody(req);
  const organization = await prisma.organization.create({
    data: {
      name: String(body.name || ""),
      slug: String(body.slug || ""),
      description: body.description ? String(body.description) : null,
      website: body.website ? String(body.website) : null
    },
    include: includes.organization
  });
  res.status(201).json(organization);
}));

app.patch("/organizations/:id", wrap(async (req, res) => {
  const body = scalarBody(req);
  const organization = await prisma.organization.update({
    where: { id: idFrom(req.params.id) },
    data: {
      ...body,
      updatedAt: new Date()
    },
    include: includes.organization
  });
  res.json(organization);
}));

app.delete("/organizations/:id", wrap(async (req, res) => {
  const organization = await prisma.organization.delete({
    where: { id: idFrom(req.params.id) }
  });
  res.json(organization);
}));

app.get("/memberships", wrap(async (_req, res) => {
  const memberships = await prisma.membership.findMany({
    include: includes.membership,
    orderBy: { id: "asc" }
  });
  res.json(memberships);
}));

app.post("/memberships", wrap(async (req, res) => {
  const body = scalarBody(req);
  const membership = await prisma.membership.create({
    data: {
      role: String(body.role || "member"),
      userId: Number(body.userId || req.currentUserId || 0),
      organizationId: Number(body.organizationId || 0)
    },
    include: includes.membership
  });
  res.status(201).json(membership);
}));

app.patch("/memberships/:id", wrap(async (req, res) => {
  const membership = await prisma.membership.update({
    where: { id: idFrom(req.params.id) },
    data: scalarBody(req),
    include: includes.membership
  });
  res.json(membership);
}));

app.delete("/memberships/:id", wrap(async (req, res) => {
  const membership = await prisma.membership.delete({
    where: { id: idFrom(req.params.id) },
    include: includes.membership
  });
  res.json(membership);
}));

app.get("/projects", wrap(async (_req, res) => {
  const projects = await prisma.project.findMany({
    include: includes.project,
    orderBy: { id: "asc" }
  });
  res.json(projects);
}));

app.get("/projects/:id", wrap(async (req, res) => {
  const project = await prisma.project.findUnique({
    where: { id: idFrom(req.params.id) },
    include: includes.project
  });
  res.json(project);
}));

app.get("/organizations/:id/projects", wrap(async (req, res) => {
  const projects = await prisma.project.findMany({
    where: { organizationId: idFrom(req.params.id) },
    include: includes.project,
    orderBy: { id: "asc" }
  });
  res.json(projects);
}));

app.post("/projects", wrap(async (req, res) => {
  const body = scalarBody(req);
  const project = await prisma.project.create({
    data: {
      name: String(body.name || ""),
      description: body.description ? String(body.description) : null,
      archived: Boolean(body.archived),
      organizationId: Number(body.organizationId || 0)
    },
    include: includes.project
  });
  res.status(201).json(project);
}));

app.patch("/projects/:id", wrap(async (req, res) => {
  const body = scalarBody(req);
  const project = await prisma.project.update({
    where: { id: idFrom(req.params.id) },
    data: {
      ...body,
      updatedAt: new Date()
    },
    include: includes.project
  });
  res.json(project);
}));

app.delete("/projects/:id", wrap(async (req, res) => {
  const project = await prisma.project.delete({
    where: { id: idFrom(req.params.id) }
  });
  res.json(project);
}));

app.get("/tasks", wrap(async (_req, res) => {
  const tasks = await prisma.task.findMany({
    include: includes.task,
    orderBy: { id: "asc" }
  });
  res.json(tasks);
}));

app.get("/tasks/:id", wrap(async (req, res) => {
  const task = await prisma.task.findUnique({
    where: { id: idFrom(req.params.id) },
    include: includes.task
  });
  res.json(task);
}));

app.get("/projects/:id/tasks", wrap(async (req, res) => {
  const tasks = await prisma.task.findMany({
    where: { projectId: idFrom(req.params.id) },
    include: includes.task,
    orderBy: { id: "asc" }
  });
  res.json(tasks);
}));

app.get("/users/:id/tasks", wrap(async (req, res) => {
  const tasks = await prisma.task.findMany({
    where: { assigneeId: idFrom(req.params.id) },
    include: includes.task,
    orderBy: { id: "asc" }
  });
  res.json(tasks);
}));

app.post("/tasks", wrap(async (req, res) => {
  const body = scalarBody(req);
  const task = await prisma.task.create({
    data: {
      title: String(body.title || ""),
      description: body.description ? String(body.description) : null,
      status: String(body.status || "todo"),
      priority: Number(body.priority || 1),
      projectId: Number(body.projectId || 0),
      assigneeId: body.assigneeId === null || body.assigneeId === undefined ? null : Number(body.assigneeId)
    },
    include: includes.task
  });
  res.status(201).json(task);
}));

app.patch("/tasks/:id", wrap(async (req, res) => {
  const body = scalarBody(req);
  const task = await prisma.task.update({
    where: { id: idFrom(req.params.id) },
    data: {
      ...body,
      updatedAt: new Date()
    },
    include: includes.task
  });
  res.json(task);
}));

app.delete("/tasks/:id", wrap(async (req, res) => {
  const task = await prisma.task.delete({
    where: { id: idFrom(req.params.id) }
  });
  res.json(task);
}));

app.get("/comments", wrap(async (_req, res) => {
  const comments = await prisma.taskComment.findMany({
    include: includes.taskComment,
    orderBy: { id: "asc" }
  });
  res.json(comments);
}));

app.get("/comments/:id", wrap(async (req, res) => {
  const comment = await prisma.taskComment.findUnique({
    where: { id: idFrom(req.params.id) },
    include: includes.taskComment
  });
  res.json(comment);
}));

app.get("/tasks/:id/comments", wrap(async (req, res) => {
  const comments = await prisma.taskComment.findMany({
    where: { taskId: idFrom(req.params.id) },
    include: includes.taskComment,
    orderBy: { id: "asc" }
  });
  res.json(comments);
}));

app.post("/comments", wrap(async (req, res) => {
  const body = scalarBody(req);
  const comment = await prisma.taskComment.create({
    data: {
      body: String(body.body || ""),
      authorId: Number(body.authorId || req.currentUserId || 1),
      taskId: Number(body.taskId || 0)
    },
    include: includes.taskComment
  });
  res.status(201).json(comment);
}));

app.patch("/comments/:id", wrap(async (req, res) => {
  const body = scalarBody(req);
  const comment = await prisma.taskComment.update({
    where: { id: idFrom(req.params.id) },
    data: {
      ...body,
      updatedAt: new Date()
    },
    include: includes.taskComment
  });
  res.json(comment);
}));

app.delete("/comments/:id", wrap(async (req, res) => {
  const comment = await prisma.taskComment.delete({
    where: { id: idFrom(req.params.id) }
  });
  res.json(comment);
}));

app.get("/debug/whoami", (req, res) => {
  res.json({
    headerUserId: req.header("x-user-id"),
    effectiveUserId: req.currentUserId || null
  });
});

app.use((req, res) => {
  res.status(404).json({
    ok: false,
    message: "Route not found",
    method: req.method,
    path: req.path
  });
});

app.listen(port, () => {
  console.log(`pm-rest-improper listening on http://localhost:${port}`);
});

async function shutdown() {
  await prisma.$disconnect();
  process.exit(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
