FROM node:20-alpine AS deps
WORKDIR /app
# Copy manifests first so the install layer caches across source changes.
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# No NEXT_PUBLIC_AGENT_URL build arg on purpose. NEXT_PUBLIC_* values are inlined
# into the client bundle at build time, which would mean one image per
# environment and a rebuild whenever a node's IP changed. lib/api.ts derives the
# agent URL at runtime from window.location instead, so this one image works in
# dev, in prod, and on any address.
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# `output: "standalone"` traces the server's real dependencies, so the runtime
# image needs neither node_modules nor the npm CLI.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

# Run unprivileged. node:alpine ships a `node` user already.
USER node

EXPOSE 3000
CMD ["node", "server.js"]

