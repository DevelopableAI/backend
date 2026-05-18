# ── Stage 1: install dependencies ────────────────────────────────────────────
FROM node:20-slim AS deps

WORKDIR /app

COPY package*.json ./
RUN npm install

# ── Stage 2: build ─────────────────────────────────────────────────────────
FROM node:20-slim AS builder

WORKDIR /app

# OpenSSL must be present before `prisma generate` so Prisma picks the
# correct binary engine (openssl-3.x). Without it Prisma defaults to
# openssl-1.1.x, causing a version mismatch at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Generate the Prisma client before compiling TypeScript
RUN npx prisma generate
RUN npm run build

# ── Stage 3: production image ──────────────────────────────────────────────
FROM node:20-slim AS runner

WORKDIR /app

ENV NODE_ENV=production

# Prisma requires libssl at runtime. node:20-slim (Debian bookworm) includes
# libssl3 as a node dependency, but we install openssl explicitly to be safe.
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*

# Only copy what is needed to run
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/prisma ./prisma
COPY package.json ./

EXPOSE 3000

CMD ["node", "dist/server.js"]
