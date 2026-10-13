FROM node:24.20.0-bookworm-slim@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e AS build

WORKDIR /src
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.json ./
COPY apps/web ./apps/web
COPY packages/contracts ./packages/contracts
COPY packages/design-tokens ./packages/design-tokens
RUN corepack pnpm install --frozen-lockfile \
    && corepack pnpm --filter @agent-trace/web build

FROM nginx:1.31.4-alpine3.24@sha256:db35bfc6b2951e7f8a72db5db120288c127ffaeeb4a6d4b95a26fead017d5913
COPY deploy/web/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /src/apps/web/dist /usr/share/nginx/html
RUN chown -R nginx:nginx /usr/share/nginx/html
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=4 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
