# ---- build stage ----
FROM node:20-alpine AS build
WORKDIR /app

# Install deps with deterministic resolution
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Build the static SvelteKit site.
# Production deploy serves UI and API same-origin behind Caddy
# (UI at `/`, API at `/api/*`), so the build embeds an empty API base
# and the runtime uses relative `/api/*` URLs.
ENV VITE_API_BASE_URL=""
ENV VITE_USE_MOCK="false"

COPY . .
RUN npm run build

# ---- artifact stage ----
# This image carries ONLY the built static files at /build.
# It runs no web server. In production a one-shot `ui-publisher`
# service (see docker-compose.prod.yml) copies /build into a shared
# `ui_static` volume that host-level Caddy serves.
FROM busybox:stable
COPY --from=build /app/build /build
CMD ["true"]
