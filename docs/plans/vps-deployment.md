# Feature Specification: VPS Deployment via GHCR + GitHub Actions + Watchtower

## Overview
- **Feature**: VPS deployment pipeline (Docker Compose + GHCR + GitHub Actions + Watchtower + Caddy)
- **Status**: Live (first end-to-end deploy completed 2026-05-16 at `https://app.kuule.cc`)
- **Created**: 2026-04-19
- **Author**: User + Claude Code

## Problem Statement
The project currently has a local `docker-compose.yml` and a `Dockerfile` for the API only. There is no production deployment story: no CI-built images, no UI image, no automated path from `git` → VPS, no TLS, and no mechanism to ship new versions without manual SSH work. We need a reproducible, automated pipeline so a GitHub Release translates into a running new version on the VPS — served over HTTPS on a real domain — with minimal human steps.

## Goals & Success Criteria
- Cutting a GitHub Release causes new API + UI images to appear on GHCR, tagged with the release version and `latest`.
- The VPS runs updated containers shortly after the release with no manual SSH required.
- All public traffic is served over HTTPS on a real domain with automatically-renewed Let's Encrypt certificates.
- UI and API are served same-origin (UI at `/`, API at `/api/*`) so no CORS configuration is needed.
- Data (SQLite DB, generated PDFs, logs, Caddy certs) survives container recreation.
- Rollback to any prior release is possible by editing a single tag in `docker-compose.yml` on the VPS.
- **Success Metrics**:
  - Time from release publish → running containers on VPS < 5 minutes.
  - Zero manual VPS commands required during a normal release.
  - `docker compose ps` on the VPS shows all services `Up` and healthy after deploy.
  - `curl -I https://<domain>/` returns 200 with a valid Let's Encrypt cert; `curl https://<domain>/api/health` returns healthy.

## User Stories
1. As the maintainer, I want to cut a GitHub Release and have a new version deployed automatically, so that releasing does not require SSH access.
2. As the maintainer, I want to roll back to an older tag by editing one line in `docker-compose.yml`, so that a bad release can be reverted in seconds.
3. As the maintainer, I want persistent data (SQLite + generated CVs) to survive container updates, so that no user data is lost on deploy.
4. As a user, I want to reach the app at `https://<domain>/` with a valid TLS certificate, so that browsers don't warn about an insecure connection and credentials/cookies are encrypted in transit.

## Functional Requirements

### Core Capabilities
- **Two GHCR images** published from this repo:
  - `ghcr.io/<owner>/linkedin-apply-ai-agent-api` — existing `Dockerfile` (FastAPI + Playwright + WeasyPrint).
  - `ghcr.io/<owner>/linkedin-apply-ai-agent-ui` — **build-artifact-only image**: contains the SvelteKit static build under `/build` and a one-shot command that copies it into a shared volume on container start. No web server runs inside this image.
- **Host-level Caddy** as the single TLS-terminating entry point:
  - Serves UI static files from the shared volume.
  - Reverse-proxies `/api/*` to the API container over the internal Docker network.
  - Auto-provisions and renews Let's Encrypt certificates for the configured domain.
  - Exposes `:80` (HTTP→HTTPS redirect) and `:443` on the host.
- **Release-triggered CI**: on GitHub Release `published`, build both images for `linux/amd64`, tag with `${release_tag}` and `latest`, push to GHCR.
- **Remote deploy step** in the same workflow: SSH to VPS, scp the rendered `docker-compose.prod.yml` and `Caddyfile`, then `docker compose pull && docker compose up -d && docker compose run --rm ui-publisher` to refresh static assets.
- **Watchtower as safety net**: polls GHCR, updates only containers with the `com.centurylinklabs.watchtower.enable=true` label (label-scoped, not host-wide). The `ui-publisher` is excluded from Watchtower since it is a one-shot.
- **SQLite + file data persistence** via bind mounts under `/opt/linkedin-apply/data` on the VPS; Caddy cert/state persisted in named volumes `caddy_data` and `caddy_config`.
- **Same-origin routing**: UI on `https://<domain>/`, API on `https://<domain>/api/*`. The UI calls the API via relative URLs only — no `VITE_API_URL` baked into the image, and no CORS configuration needed in FastAPI.

### User Flows

**Release flow (happy path):**
1. Maintainer creates a GitHub Release with tag `vX.Y.Z`.
2. GitHub Actions workflow fires:
   1. Checkout.
   2. Log into GHCR with `GITHUB_TOKEN`.
   3. Build API image → push `:vX.Y.Z` and `:latest`.
   4. Build UI image (no `VITE_API_URL` needed — UI uses relative `/api/*` URLs) → push `:vX.Y.Z` and `:latest`.
   5. Generate rendered `docker-compose.prod.yml` pinned to `:vX.Y.Z`.
   6. Copy rendered compose + `Caddyfile` to VPS via `scp` (or `appleboy/scp-action`).
   7. SSH to VPS and run `docker compose pull && docker compose up -d --remove-orphans` (brings up Caddy, API, Watchtower) then `docker compose run --rm ui-publisher` (copies new build artifacts into the `ui_static` volume Caddy serves).
3. Caddy continues serving on `:443`; new UI assets are visible on next browser load. Watchtower continues polling as a safety net for any missed update.

**Rollback flow:**
1. Maintainer SSHes to VPS.
2. Edits `/opt/linkedin-apply/docker-compose.yml`, changes `image: ...:vX.Y.Z` → `:vX.Y.(Z-1)`.
3. `docker compose pull && docker compose up -d && docker compose run --rm ui-publisher`.

**Bootstrap flow (first-time VPS setup):**
1. Install Docker + Docker Compose plugin on VPS (Ubuntu 22.04 assumed).
2. Point the chosen domain's A record at the VPS IP (Let's Encrypt's HTTP-01 challenge requires DNS to resolve before the first cert issuance).
3. Open ports 80 and 443 in the VPS firewall; `:8000` is no longer published to the host (API only reachable via the Docker network through Caddy).
4. Create `/opt/linkedin-apply/` with `data/`, `logs/` subdirs.
5. Create `/opt/linkedin-apply/.env` from `.env.example`, fill in real secrets (including `APP_URL=https://<domain>` so magic links point at the right host).
6. Create `/opt/linkedin-apply/Caddyfile` with the domain and email for ACME registration (the Release Action also overwrites this on each deploy, but it must exist for the first manual `docker compose up`).
7. `docker login ghcr.io` with a GHCR read token (if images are private).
8. First Release triggers the Action, which scps compose + Caddyfile and brings services up; Caddy obtains the certificate on first start (verify with `docker logs caddy`).

### Data Model
Not a code feature — no new Pydantic models. Files introduced:

```
.github/workflows/release.yml      # CI pipeline
ui/Dockerfile                      # new — builds SvelteKit static then COPYs /build into a busybox-based artifact image
Caddyfile                          # new — host-level reverse proxy + TLS config (templated with __DOMAIN__ / __ACME_EMAIL__)
docker-compose.prod.yml            # VPS compose (GHCR images, no `build:`, Caddy + ui-publisher + API + Watchtower)
.dockerignore                      # (if missing) exclude data/, logs/, ui/node_modules
docs/plans/vps-deployment.md       # this spec
```

On the VPS, layout is:
```
/opt/linkedin-apply/
├── docker-compose.yml   # renamed from docker-compose.prod.yml on upload
├── Caddyfile            # rendered from repo template, scp'd by CI
├── .env                 # hand-managed
├── data/                # SQLite + generated PDFs (bind mount)
└── logs/                # app logs (bind mount)
                         # caddy_data, caddy_config, ui_static are named docker volumes (not bind mounts)
```

### Integration Points
- Existing `Dockerfile` (API) is kept as-is — CI builds it unchanged. The API container no longer publishes `:8000` to the host; only Caddy exposes ports externally. API and Caddy share a Docker network so Caddy reaches the API as `http://api:8000`.
- Existing `docker-compose.yml` stays for local dev (uses `build:`). A new `docker-compose.prod.yml` references GHCR images only (no `build:` keys). This keeps local dev and prod configs independent.
- UI currently builds statically via `vite build` (SvelteKit + `@sveltejs/adapter-static`). Because the UI and API are served same-origin in production, the UI code should call the API via **relative URLs** (`fetch('/api/...')`). Audit the UI source during implementation — any hardcoded `http://localhost:8000` or absolute API URL must be replaced with a relative path. No `VITE_API_URL` build arg is needed.

## Technical Design

### Architecture
```
┌─────────────────────────────────────────────────────┐
│                       GitHub                         │
│  ┌───────────┐   release:published                   │
│  │ Release   │──────────────┐                        │
│  └───────────┘              ▼                        │
│                  ┌────────────────────────┐          │
│                  │ Actions workflow       │          │
│                  │ - buildx API + UI      │          │
│                  │ - push to GHCR         │          │
│                  │ - scp compose+Caddyfile│          │
│                  │ - ssh `compose up`     │          │
│                  │ - ssh `run ui-publisher│          │
│                  └──────────┬─────────────┘          │
└─────────────────────────────┼────────────────────────┘
                              │ GHCR pull + SSH
                              ▼
┌─────────────────────────────────────────────────────┐
│                       VPS                            │
│                                                      │
│   Browser ── https://<domain> ──► :443               │
│                                    │                 │
│                                    ▼                 │
│                            ┌──────────────┐          │
│                            │    Caddy     │          │
│                            │  (TLS + LB)  │          │
│                            │  :80 → :443  │          │
│                            └──┬────────┬──┘          │
│                /api/*         │        │  / (static) │
│                ▼              │        │  ▼          │
│         ┌─────────────┐       │        │  ┌────────┐ │
│         │     API     │◄──────┘        └─►│ui_static│ │
│         │  (fastapi)  │                   │ (volume)│ │
│         │   :8000     │                   └────▲───┘ │
│         └─────────────┘                        │     │
│                                                │     │
│         ┌─────────────────┐    one-shot copy   │     │
│         │  ui-publisher   │────────────────────┘     │
│         │ (busybox+build) │                          │
│         └─────────────────┘                          │
│                                                      │
│         ┌─────────────────┐                          │
│         │   Watchtower    │  (label-scoped, polls    │
│         │                 │   GHCR for api & caddy)  │
│         └─────────────────┘                          │
└─────────────────────────────────────────────────────┘
```

### Technology Stack
- **Registry**: GitHub Container Registry (GHCR), images private by default — owner is `<gh-owner>`.
- **CI**: GitHub Actions — `docker/login-action`, `docker/build-push-action`, `appleboy/ssh-action`, `appleboy/scp-action`.
- **Runtime on VPS**: Docker Engine + `docker compose` v2 plugin.
- **Reverse proxy + TLS**: `caddy:2-alpine` with automatic Let's Encrypt cert issuance and renewal (HTTP-01 challenge on `:80`).
- **Auto-update**: `containrrr/watchtower` (image `containrrr/watchtower:latest`) in label-scoped mode.
- **UI delivery**: build-artifact image based on `busybox:stable` (no web server inside); a one-shot `ui-publisher` container copies `/build` into the `ui_static` named volume that Caddy serves.

### Data Persistence
- **SQLite**: bind-mounted from `/opt/linkedin-apply/data` into API container at `/app/data`. Matches current `REPO_TYPE=sqlite` + `DB_PATH=./data/jobs.db`.
- **Generated PDFs**: same `./data` bind mount covers `data/generated_cvs/`.
- **Logs**: `/opt/linkedin-apply/logs` → `/app/logs` bind mount.
- **LinkedIn cookies**: included under `./data/linkedin_cookies.json` via the same bind mount.
- Backup strategy is out of scope for this iteration — noted in Open Questions.

### API / Interface Design

**`.github/workflows/release.yml` skeleton:**
```yaml
name: Release
on:
  release:
    types: [published]

permissions:
  contents: read
  packages: write

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build & push API
        uses: docker/build-push-action@v5
        with:
          context: .
          file: Dockerfile
          push: true
          tags: |
            ghcr.io/${{ github.repository_owner }}/linkedin-apply-ai-agent-api:${{ github.event.release.tag_name }}
            ghcr.io/${{ github.repository_owner }}/linkedin-apply-ai-agent-api:latest

      - name: Build & push UI (build-artifact-only image)
        uses: docker/build-push-action@v5
        with:
          context: ./ui
          file: ui/Dockerfile
          push: true
          tags: |
            ghcr.io/${{ github.repository_owner }}/linkedin-apply-ai-agent-ui:${{ github.event.release.tag_name }}
            ghcr.io/${{ github.repository_owner }}/linkedin-apply-ai-agent-ui:latest

      - name: Render compose with version tag
        run: sed "s|__VERSION__|${{ github.event.release.tag_name }}|g" docker-compose.prod.yml > compose.rendered.yml

      - name: Render Caddyfile with domain + email
        run: |
          sed -e "s|__DOMAIN__|${{ vars.APP_DOMAIN }}|g" \
              -e "s|__ACME_EMAIL__|${{ vars.ACME_EMAIL }}|g" \
              Caddyfile > Caddyfile.rendered

      - uses: appleboy/scp-action@v0.1.7
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          source: "compose.rendered.yml,Caddyfile.rendered"
          target: /opt/linkedin-apply/
          overwrite: true

      - uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /opt/linkedin-apply
            mv compose.rendered.yml docker-compose.yml
            mv Caddyfile.rendered Caddyfile
            echo ${{ secrets.GHCR_PULL_TOKEN }} | docker login ghcr.io -u ${{ github.actor }} --password-stdin
            docker compose pull
            docker compose up -d --remove-orphans
            docker compose run --rm ui-publisher
            docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
            docker image prune -f
```

**`docker-compose.prod.yml` skeleton (VPS):**
```yaml
services:
  caddy:
    image: caddy:2-alpine
    container_name: linkedin-apply-caddy
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data        # certs, ACME state — MUST persist
      - caddy_config:/config
      - ui_static:/srv/ui:ro    # static UI artifacts published by ui-publisher
    depends_on: [api]
    restart: unless-stopped
    labels:
      - com.centurylinklabs.watchtower.enable=true

  api:
    image: ghcr.io/<owner>/linkedin-apply-ai-agent-api:latest
    container_name: linkedin-apply-api
    # No `ports:` — API is only reachable inside the docker network via Caddy
    expose: ["8000"]
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    restart: unless-stopped
    labels:
      - com.centurylinklabs.watchtower.enable=true

  ui-publisher:
    image: ghcr.io/<owner>/linkedin-apply-ai-agent-ui:latest
    container_name: linkedin-apply-ui-publisher
    # One-shot: copies /build into ui_static volume, then exits.
    # Not auto-restarted; CI deploy step runs `docker compose run --rm ui-publisher`
    # to refresh assets on every release. Excluded from Watchtower on purpose.
    command: ["sh", "-c", "rm -rf /srv/ui/* && cp -r /build/. /srv/ui/ && echo 'UI artifacts published'"]
    volumes:
      - ui_static:/srv/ui
    restart: "no"

  watchtower:
    image: containrrr/watchtower:latest
    container_name: watchtower
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ~/.docker/config.json:/config.json:ro   # for GHCR auth
    environment:
      WATCHTOWER_LABEL_ENABLE: "true"
      WATCHTOWER_CLEANUP: "true"
      WATCHTOWER_POLL_INTERVAL: "300"
    restart: unless-stopped

volumes:
  caddy_data:
  caddy_config:
  ui_static:
```

**`Caddyfile` skeleton (templated — `__DOMAIN__` and `__ACME_EMAIL__` substituted by CI):**
```caddy
{
    email __ACME_EMAIL__
}

__DOMAIN__ {
    encode zstd gzip

    # API: same-origin under /api/*
    handle /api/* {
        reverse_proxy api:8000
    }

    # UI: static SPA with client-side router fallback
    handle {
        root * /srv/ui
        try_files {path} /index.html
        file_server
    }

    log {
        output stdout
        format console
    }
}
```

**`ui/Dockerfile` skeleton (build-artifact only, no web server):**
```dockerfile
# ---- build stage ----
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# ---- artifact stage ----
# No web server: this image only carries the built static files.
# The deploy compose runs a one-shot `ui-publisher` service that copies
# /build into a shared volume served by host-level Caddy.
FROM busybox:stable
COPY --from=build /app/build /build
CMD ["true"]
```

## Non-Functional Requirements
- **Performance**: deploy end-to-end under 5 minutes. Image pulls on the VPS reuse layers; `docker image prune -f` runs after deploy to cap disk usage. Caddy enables `zstd`/`gzip` compression for static assets.
- **Security**:
  - All public traffic is HTTPS — Caddy auto-redirects `:80` → `:443` and serves Let's Encrypt certs renewed automatically (~60-day cycle). The `caddy_data` volume MUST persist across deploys; losing it means re-issuing certs and risking Let's Encrypt rate limits.
  - API port `:8000` is **not** published to the host — the API is reachable only via Caddy on the internal Docker network. This shrinks the public attack surface to ports 80/443.
  - GHCR images are **private**; VPS logs in with a read-only PAT (stored only on the VPS, not in CI).
  - SSH to VPS uses a dedicated deploy key scoped to the `linkedin-apply` user (non-root, with `docker` group membership).
  - `.env` lives only on the VPS (`chmod 600`), never committed, never written by CI.
  - Watchtower has `docker.sock` access — acknowledged risk; mitigated by label-scoping so it only touches this stack.
- **Observability**:
  - Containers use Docker's default `json-file` log driver with rotation (`max-size: 10m`, `max-file: 3`) — add to compose.
  - App logs continue to write to `/app/logs` via existing logger config.
  - Caddy access + cert-renewal logs go to stdout; inspect with `docker logs caddy`. Cert expiry visible via `docker exec caddy caddy list-certificates` (or via the `/data/caddy/certificates/` tree).
  - Watchtower logs to stdout; inspect with `docker logs watchtower`.
- **Error Handling**:
  - If `docker compose up -d` fails on the VPS, the Action step fails and GitHub surfaces it in the release UI; old containers keep running (compose does not stop until new ones start).
  - If Caddy fails to obtain a cert on first boot (DNS not propagated, ports 80/443 blocked, Let's Encrypt rate-limited), Caddy logs the failure and retries with exponential backoff. The site stays down until resolved — there is no fallback to plain HTTP.
  - No automatic rollback on deploy failure — manual rollback via tag edit.

## Implementation Considerations

### Design Trade-offs
- **Host-level Caddy for TLS, same-origin routing** — picked Caddy over nginx for automatic Let's Encrypt with one line of config. Same-origin path-based routing (`/` UI, `/api/*` API) means zero CORS config and the UI image carries no hostname — it's portable across domains. The single Caddy container is now the only ingress; if it dies, the site is down (mitigated by `restart: unless-stopped` + Watchtower).
- **No in-container web server in the UI image** — UI image is a `busybox`-based artifact container; a one-shot `ui-publisher` copies `/build` into a shared `ui_static` volume on each deploy. Pros: smaller UI image, no duplicated nginx config, no inter-container HTTP hop for static assets. Cons: deploy must explicitly run the publisher (`docker compose run --rm ui-publisher`) — a forgotten step ships old UI assets even after API updates. Mitigated by always invoking it as part of the Action's deploy step.
- **Watchtower + Action-driven deploy (belt and suspenders)** — the Action is authoritative for version bumps (writes the compose file with the new tag) AND runs the ui-publisher. Watchtower runs as a safety net for the `:latest` tag on API and Caddy; it will rarely act because the Action already pulled. Note: Watchtower auto-pulling the UI image does NOT republish artifacts to the volume — only the Action's explicit `compose run ui-publisher` step does. This is acceptable because Watchtower is a safety net for missed Action runs, not the primary path.
- **Semver + `:latest` dual tags** — `:latest` is required for Watchtower to follow a stable tag; semver pin preserves rollback clarity. The VPS compose may reference either — default is `:latest` so Watchtower can act; rollback switches to a semver pin.
- **SQLite bind mount (not Postgres)** — matches current architecture and keeps VPS setup trivial. Revisit when moving to multi-instance or HA.
- **`caddy_data` as a named volume, not a bind mount** — keeps Caddy's internal cert directory structure opaque; bind-mounting `/data` is more fragile across Caddy version upgrades. Back up the volume separately (`docker run --rm -v caddy_data:/data -v $PWD:/backup busybox tar czf /backup/caddy.tgz /data`).

### Dependencies
- VPS prerequisites: Ubuntu 22.04+, Docker Engine 24+, `docker compose` plugin, non-root deploy user with docker group.
- **Domain + DNS**: `app.kuule.cc` is the canonical app host, A record → VPS IP (`37.114.41.69`), propagated before first deploy (Let's Encrypt HTTP-01 requires DNS to resolve). The unrelated `kuule.cc` (`@`) and `www.kuule.cc` records are reserved for a future marketing site and intentionally NOT served by this stack — Caddy will return a TLS handshake error / 404 for those hosts until they're explicitly configured. The pre-existing `api.kuule.cc` A record from earlier subdomain-split designs is no longer needed and should be deleted.
- **Firewall**: ports 80 and 443 open to the public internet; port 8000 should NOT be exposed.
- GitHub repo secrets required: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `GHCR_PULL_TOKEN` (read:packages PAT for VPS login).
- GitHub repo variables required: `APP_DOMAIN=app.kuule.cc`, `ACME_EMAIL=<maintainer-email>` (used for Let's Encrypt registration and expiry warnings).
- New files: `.github/workflows/release.yml`, `ui/Dockerfile`, `Caddyfile`, `docker-compose.prod.yml`, `.dockerignore` (if missing).
- Verify UI source calls the API via **relative URLs** (`fetch('/api/...')`). Any hardcoded `http://localhost:8000` or absolute API URL must be replaced — that's a code change scoped to this feature.

### Setup Status (as of 2026-05-17)

First end-to-end deploy succeeded via `workflow_dispatch` on master with image tag `v0.1.0`. Stack is running at `https://app.kuule.cc` with a valid Let's Encrypt cert.

| Item | Status | Notes |
|---|---|---|
| DNS: `app.kuule.cc` A → `37.114.41.69` | ✅ Done | Confirmed; cert issued by Caddy on first start. |
| DNS: `api.kuule.cc` A record | ⚠️ Pending cleanup | Vestigial from subdomain-split design. Safe to delete. Not blocking. |
| GH variable `APP_DOMAIN` | ✅ Set | Value: `app.kuule.cc` |
| GH variable `ACME_EMAIL` | ✅ Set | Value: `kiril.elizarov@gmail.com` |
| GH secret `VPS_HOST` | ✅ Set | Value: `37.114.41.69` |
| GH secret `VPS_USER` | ✅ Set | Value: `root` — see note below on hardening |
| GH secret `VPS_SSH_KEY` | ✅ Set | Verified end-to-end via `appleboy/ssh-action` in CI. |
| GH secret `GHCR_PULL_TOKEN` | ✅ Set | Classic PAT for `kulebyaka`, scope `read:packages`. Used by the deploy step to refresh `/root/.docker/config.json` before `compose pull`. |
| VPS: Docker + compose installed | ✅ Verified | Docker 26.1.5, compose plugin 2.26.1 (Debian 13). |
| VPS: ports 80/443 reachable, 8000 not exposed | ✅ Verified | Caddy binds 80/443; API container uses `expose:` only (no host mapping). |
| VPS: `/opt/linkedin-apply/` + `.env` present | ✅ Done | `.env` chmod 600, hand-managed (never written by CI). |
| VPS: `/root/.docker/config.json` GHCR auth | ✅ Done | Created via `docker login` on first deploy; persists across deploys. |
| Repo: `.github/workflows/release.yml` | ✅ Merged | `release.types=[published]` + `workflow_dispatch` for ad-hoc redeploys. |
| Repo: `Caddyfile` template | ✅ Merged | `__DOMAIN__` / `__ACME_EMAIL__` substituted by CI. |
| Repo: `docker-compose.prod.yml` | ✅ Merged | `__VERSION__` substituted by CI. |
| Repo: `ui/Dockerfile` (build-artifact-only) | ✅ Merged | `busybox:stable` artifact image; build runs with `VITE_API_BASE_URL=""` for same-origin relative `/api/*` URLs. |
| UI source uses relative `/api/*` URLs | ✅ Done | `||` swapped for `??` in `ui/src/lib/api/*.ts` so an explicit empty `VITE_API_BASE_URL` disables the dev localhost fallback. SvelteKit `adapter-static` set to `fallback: 'index.html'`, `strict: false` — SPA mode (matches `+layout.ts` having `prerender = false`). |

**Security note on `VPS_USER=root`** — original plan called for a non-root deploy user with docker-group membership. Using `root` works and unblocks the first deploy, but every CI step runs as root on the box. Mitigation for later: create a non-root user (`deploy` or `linkedin-apply`), add its pubkey to its `authorized_keys`, add it to the `docker` group, copy `/root/.docker/config.json` to `~deploy/.docker/`, then `gh secret set VPS_USER --body deploy`. Tracked as a follow-up hardening task.

**Deploy-step resilience** — the workflow's `docker login` is now best-effort: if `GHCR_PULL_TOKEN` is missing or rejected, the script falls through and relies on the cached `/root/.docker/config.json`. The credential is also passed via `env:` + `envs:` instead of inline `${{ }}` interpolation, which avoids shell-quoting surprises with secret values containing special characters.

### Testing Strategy
- **CI**: after workflow implementation, cut a throwaway pre-release (e.g., `v0.0.1-rc1`) on a scratch branch. Verify images appear on GHCR with both tags.
- **VPS manual test**: SSH to VPS, run `docker compose ps`, confirm Caddy/API `Up` and `ui-publisher` `Exited (0)`; from outside the VPS run `curl -I https://<domain>/` (expect 200 + valid cert) and `curl https://<domain>/api/health` (expect healthy).
- **TLS test**: `openssl s_client -connect <domain>:443 -servername <domain> </dev/null | openssl x509 -noout -dates` — confirms issuer is Let's Encrypt and cert is fresh. Also confirm HTTP→HTTPS redirect: `curl -I http://<domain>/` should return 308.
- **Rollback test**: after two releases, manually pin the API image to the previous `:vX.Y.Z`, run `docker compose up -d`, confirm the API reports the older version (e.g., via a `/api/health` version field if present, or image digest).
- **Watchtower test**: push a patch release, do NOT run the Action's deploy step (simulate failure), wait one poll cycle (5 min), confirm Watchtower pulled API/Caddy and restarted. Note: UI assets will NOT refresh in this scenario — confirmed expected behavior (Watchtower can't run the publisher).
- **Cert renewal smoke test**: `docker exec caddy caddy list-certificates` shows expiry > 30 days out. Don't wait for actual renewal in CI; trust Caddy's well-tested renewal logic.
- **Data persistence test**: create a user + submit a job, run `docker compose down && docker compose up -d && docker compose run --rm ui-publisher`, confirm user, job, AND cert still exist (cert persists via `caddy_data` named volume).

## Out of Scope
- Multi-VPS / HA deployment.
- Postgres migration.
- Automated DB backups (noted as open question).
- Blue-green or canary deploys.
- Secret management beyond a hand-managed `.env` file.
- Container image vulnerability scanning in CI.
- Multi-arch images (only `linux/amd64` built).
- Custom Caddy modules / non-default ACME providers (e.g., ZeroSSL, DNS-01 challenge for wildcards).

## Open Questions
- ~~Final domain name and DNS provider~~ — **Resolved**: `app.kuule.cc` → `37.114.41.69`, A record already in place.
- Should the API expose a `GET /api/version` returning the image tag, for smoke checks? (Nice-to-have, not required.)
- Backup cadence for `/opt/linkedin-apply/data/` (SQLite + generated PDFs) AND the `caddy_data` volume — cron `sqlite3 .backup` + `docker run ... tar` of caddy volume, copied offsite? Defer to a follow-up.
- Does the UI code currently use relative URLs for API calls? Confirm during implementation; if it hardcodes `http://localhost:8000` anywhere, that's a small but required code change.

## References
- `Dockerfile` — existing API image.
- `docker-compose.yml` — existing local dev compose (kept).
- `ui/package.json` — confirms SvelteKit + `@sveltejs/adapter-static` + `vite build`.
- CLAUDE.md § "Configuration" — enumerates required env vars that must land in `/opt/linkedin-apply/.env` (note: `APP_URL` should now be `https://<domain>` for magic-link callbacks).
- Caddy docs: https://caddyserver.com/docs/
- Caddy automatic HTTPS: https://caddyserver.com/docs/automatic-https
- Watchtower docs: https://containrrr.dev/watchtower/
- GHCR docs: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
