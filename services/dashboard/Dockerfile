# --- build ------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# Dependencies first so the layer survives source-only changes.
COPY services/dashboard/package.json services/dashboard/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY services/dashboard/ ./
RUN npm run build

# --- serve ------------------------------------------------------------------
FROM nginx:1.27-alpine

COPY services/dashboard/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
