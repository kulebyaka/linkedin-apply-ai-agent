# Deployment Guide

Operational reference for deploying and managing the LinkedIn Apply AI Agent on the production VPS.

For the design rationale and rollout plan, see [`docs/plans/vps-deployment.md`](plans/vps-deployment.md).

## VPS

| Item | Value |
|------|-------|
| Host (IP) | `37.114.41.69` |
| SSH user | `root` |
| Hostname | `panel` |
| OS | Debian GNU/Linux 13 (trixie) |
| Kernel | 6.12.86-1 (amd64) |
| Architecture | x86_64 |
| RAM | 5.8 GiB |
| Disk | 45 GB (`/dev/sda1`) |
| Docker | 26.1.5+dfsg1 |
| Docker Compose | 2.26.1 (plugin) |
| Host key (ED25519) fingerprint | `SHA256:P1OeBhSI9cza2jOqMZDx1li3748+UMXk1YLGOoYdbk4` |

## SSH Access

### Key

A dedicated ed25519 keypair is used for this VPS — do not reuse it for other hosts.

| File | Path |
|------|------|
| Private key | `~/.ssh/id_ed25519_vps` |
| Public key | `~/.ssh/id_ed25519_vps.pub` |
| Comment | `kiril.elizarov@gmail.com` |

Public key (registered with the VPS provider as `mac`):

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMMygS8UaNajOGxNwD9giw9uN708e674GUj8Tj4DHFCP kiril.elizarov@gmail.com
```

### Connect

Direct:

```bash
ssh -i ~/.ssh/id_ed25519_vps root@37.114.41.69
```

Or via SSH config alias — add this to `~/.ssh/config`:

```sshconfig
Host vps
    HostName 37.114.41.69
    User root
    IdentityFile ~/.ssh/id_ed25519_vps
    IdentitiesOnly yes
```

Then: `ssh vps`.

### Regenerating the key

If the private key is lost or compromised:

```bash
ssh-keygen -t ed25519 -C "kiril.elizarov@gmail.com" -f ~/.ssh/id_ed25519_vps -N ""
```

Add the new `~/.ssh/id_ed25519_vps.pub` contents to the VPS provider's SSH key list, then re-run `ssh-copy-id` or paste into `~/.ssh/authorized_keys` on the box.

### Host key changed warning

If `ssh` reports `REMOTE HOST IDENTIFICATION HAS CHANGED`, the VPS was likely rebuilt or the provider recycled the IP. After confirming with the provider:

```bash
ssh-keygen -R 37.114.41.69
ssh -i ~/.ssh/id_ed25519_vps root@37.114.41.69
```

Then verify the new fingerprint against the provider's console before trusting it.

## Domain

The production domain is `kuule.cc`, registered separately from the VPS provider. DNS is managed at the registrar.

### DNS records

| Type  | Name  | Content         | TTL | Purpose                              |
|-------|-------|-----------------|-----|--------------------------------------|
| A     | `@`   | `37.114.41.69`  | 300 | Apex — anchors `www` CNAME, redirect target |
| CNAME | `www` | `kuule.cc`      | 300 | Conventional alias to apex           |
| A     | `app` | `37.114.41.69`  | 300 | UI (SvelteKit static via nginx)      |
| —     | `api` | —              | —   | No DNS record; API is reached at `app.kuule.cc/api/*` |

### Verification

After changes, propagation typically takes 5–60 minutes. Confirm with:

```bash
dig +short kuule.cc
dig +short www.kuule.cc
dig +short app.kuule.cc
```

The apex, `www`, and `app` resolve to `37.114.41.69` (the `www` CNAME resolves through the apex A record).

### TLS / reverse proxy

The shared host Caddy under `/opt/caddy` owns ports 80/443 and imports `/opt/caddy/sites/*.caddy`. This repo owns only `/opt/caddy/sites/linkedin-apply.caddy`. The API joins the external `ingress` network as `linkedin-api`; the UI is served from the existing `linkedin-apply_ui_static` volume. The shared proxy reuses `linkedin-apply_caddy_data` and `linkedin-apply_caddy_config` so existing certificates survive the move. Back up certificate data alongside `/opt/linkedin-apply/data/` or expect a re-issue on volume loss.

Active config:

- VPS `.env` → `APP_URL=https://app.kuule.cc`, `CORS_ORIGINS=["https://app.kuule.cc"]`
- UI build → `VITE_API_BASE_URL=""` (baked into the image), so the client uses relative `/api/*` URLs
- This repo's site file is rendered from `Caddyfile` at release time using `APP_DOMAIN`; the shared Caddy base config and ACME email are host-managed.

### VPS `.env` essentials

The API container is `env_file: .env`, and that file is **hand-managed and never written by CI** — a
new setting means you must SSH in and add it. Settings that must not be left at their defaults in
production:

| Key | Why |
|---|---|
| `REPO_TYPE=sqlite` | The default is `memory` — every job would be lost on each redeploy |
| `JWT_SECRET` | Must be a real 32+ char random value; the placeholder is rejected at signing time |
| `DEV_AUTH_BYPASS=false` | The app **refuses to start** if this is true with a non-localhost `APP_URL` |
| `RESEND_API_KEY`, `RESEND_FROM` | No magic links without them; `RESEND_FROM` must be a verified domain |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / … | At least one, matching `PRIMARY_LLM_PROVIDER` |
| `LINKEDIN_SEARCH_SCHEDULE_ENABLED=true` | Otherwise no browser launches and no scraping happens |
| `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` | Needed for the scraper session |
| `ADMIN_ALERT_EMAIL` | Empty means silent failures — this is how you learn the LinkedIn session died |
| `SEED_JOBS_FROM_FILE=false` | `true` replaces scraping entirely with fixture replay |

`./data` and `./logs` are bind-mounted into the container, so the SQLite DB, generated PDFs, the
model-catalog cache, and the admin-alert cooldown state all persist across deploys under
`/opt/linkedin-apply/data/`. Back that directory up alongside the shared Caddy certificate volume.

## Stack layout on the VPS

```
/opt/linkedin-apply/
├── docker-compose.yml   # rendered from docker-compose.prod.yml at each deploy
├── Caddyfile            # saved pre-migration config; not used by releases
├── .env                 # hand-managed, chmod 600 — never written by CI
├── data/                # SQLite + generated PDFs (bind mount → /app/data)
└── logs/                # API logs (bind mount → /app/logs)
                         # ui_static lives as a named docker volume

/opt/caddy/compose.yaml                # shared proxy, owned by the host
/opt/caddy/Caddyfile                   # imports sites/*.caddy
/opt/caddy/sites/linkedin-apply.caddy # this repo's site file
```

Services (the API also joins the external `ingress` network):

| Container | Image | Role |
|---|---|---|
| `shared-caddy` | `caddy:2-alpine` | Host-level TLS and reverse proxy, managed under `/opt/caddy` |
| `linkedin-apply-api` | `ghcr.io/kulebyaka/linkedin-apply-ai-agent-api:<tag>` | FastAPI, internal port `8000` only |
| `linkedin-apply-ui-publisher` | `ghcr.io/kulebyaka/linkedin-apply-ai-agent-ui:<tag>` | One-shot: copies `/build` into the `ui_static` volume on each deploy |
| `linkedin-apply-watchtower` | `containrrr/watchtower` | Label-scoped polling for the API; shared Caddy is excluded |

A separate host-wide watchtower already runs from `/home/admin/services/watchtower/` — our stack ships its own label-scoped watchtower (`WATCHTOWER_LABEL_ENABLE=true`) so the two do not conflict.

## Deploys

Trigger from anywhere with the `gh` CLI:

```bash
# Cut a release (normal flow)
gh release create v0.1.1 --generate-notes

# Or redeploy a specific tag on demand (ad-hoc / rollback)
gh workflow run release.yml --ref master -f tag=v0.1.0
```

The workflow builds linux/amd64 images for both api and ui, pushes them to GHCR with `:<tag>` and `:latest`, copies the rendered compose and this app's Caddy site file, then SSHes to the VPS and, in order:

1. Refreshes GHCR auth **best-effort** — persistent creds live in `/root/.docker/config.json` after the first successful login, so a missing or invalid `GHCR_PULL_TOKEN` doesn't fail the deploy.
2. `docker compose pull`.
3. **Deletes `data/model_catalog_cache.json`.** `load_catalog` serves a <24h disk cache verbatim without re-parsing, so a cache written by the outgoing image would mask this release's catalog/filter changes for up to 24 hours. Removing it forces a clean refetch on startup.
4. `docker compose up -d --remove-orphans`, then `run --rm ui-publisher`, then validates and reloads shared Caddy. A failed validation or reload fails the release.
5. **Prunes old release images.** `docker image prune -f` alone only drops dangling layers and leaves the previous version-tagged image (~1.9 GB each) behind — which is what fills the 45 GB disk over successive releases. The step explicitly `docker rmi`s every tag of *our two* images except the just-deployed tag and `:latest`, scoped so other projects sharing the box are untouched, and only then runs `image prune -f`.

Watchtower is a safety net only — UI artifact refresh requires the publisher step, which only the workflow runs.

## Manual operations on the VPS

```bash
ssh vps   # or: ssh -i ~/.ssh/id_ed25519_vps root@37.114.41.69

cd /opt/linkedin-apply
docker compose ps                          # service status
docker compose logs -f api                 # tail API logs
docker compose -f /opt/caddy/compose.yaml logs -f caddy  # certs + access logs
docker exec shared-caddy caddy list-certificates         # cert expiry

# Rollback to a previous image tag (edit the two `__VERSION__`-pinned image lines)
sed -i 's|api:v0.1.1|api:v0.1.0|; s|ui:v0.1.1|ui:v0.1.0|' docker-compose.yml
docker compose pull && docker compose up -d && docker compose run --rm ui-publisher
```

## Required GitHub secrets and variables

| Kind | Name | Purpose |
|---|---|---|
| secret | `VPS_HOST` | VPS IP (`37.114.41.69`) |
| secret | `VPS_USER` | SSH user (`root` for now — see hardening note in the plan) |
| secret | `VPS_SSH_KEY` | Contents of `~/.ssh/id_ed25519_vps` |
| secret | `GHCR_PULL_TOKEN` | Classic PAT with `read:packages` for `kulebyaka` — VPS `docker login` to GHCR |
| variable | `APP_DOMAIN` | `app.kuule.cc` |
The shared Caddy ACME email is set in `/opt/caddy/Caddyfile`, not by this repo's releases.
