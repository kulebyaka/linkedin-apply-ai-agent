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

# Release version shown in the header (set from the git tag by CI).
# vite.config.ts prefers this over `git describe`, which is unavailable
# here since the build context is ./ui without the .git directory.
ARG VITE_APP_VERSION=""
ENV VITE_APP_VERSION=$VITE_APP_VERSION

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
