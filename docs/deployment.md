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
| A     | `api` | `37.114.41.69`  | 300 | API (FastAPI)                        |

### Verification

After changes, propagation typically takes 5–60 minutes. Confirm with:

```bash
dig +short kuule.cc
dig +short www.kuule.cc
dig +short app.kuule.cc
dig +short api.kuule.cc
```

All four should resolve to `37.114.41.69` (the `www` CNAME resolves transitively through the apex A record).

### TLS / reverse proxy

Live as of 2026-05-16. Caddy 2 (alpine) terminates TLS on `:443` and reverse-proxies same-origin: UI at `/`, API at `/api/*`. Let's Encrypt cert is auto-issued and renewed via HTTP-01 on `:80` (which Caddy also serves as a 308 redirect to HTTPS). Certs persist in the `caddy_data` named docker volume — back it up alongside `/opt/linkedin-apply/data/` or expect a re-issue on volume loss.

Active config:

- VPS `.env` → `APP_URL=https://app.kuule.cc`, `CORS_ORIGINS=["https://app.kuule.cc"]`
- UI build → `VITE_API_BASE_URL=""` (baked into the image), so the client uses relative `/api/*` URLs
- Caddy config rendered from `Caddyfile` at deploy time using GH repo variables `APP_DOMAIN` and `ACME_EMAIL`

## Stack layout on the VPS

```
/opt/linkedin-apply/
├── docker-compose.yml   # rendered from docker-compose.prod.yml at each deploy
├── Caddyfile            # rendered from repo template at each deploy
├── .env                 # hand-managed, chmod 600 — never written by CI
├── data/                # SQLite + generated PDFs (bind mount → /app/data)
└── logs/                # API logs (bind mount → /app/logs)
                         # caddy_data, caddy_config, ui_static live as named docker volumes
```

Services (all on the `linkedin-apply` docker network):

| Container | Image | Role |
|---|---|---|
| `linkedin-apply-caddy` | `caddy:2-alpine` | TLS + reverse proxy; only container exposed on host (`:80`, `:443`) |
| `linkedin-apply-api` | `ghcr.io/kulebyaka/linkedin-apply-ai-agent-api:<tag>` | FastAPI, internal port `8000` only |
| `linkedin-apply-ui-publisher` | `ghcr.io/kulebyaka/linkedin-apply-ai-agent-ui:<tag>` | One-shot: copies `/build` into the `ui_static` volume on each deploy |
| `linkedin-apply-watchtower` | `containrrr/watchtower` | Label-scoped polling for `:latest` updates on api+caddy |

A separate host-wide watchtower already runs from `/home/admin/services/watchtower/` — our stack ships its own label-scoped watchtower (`WATCHTOWER_LABEL_ENABLE=true`) so the two do not conflict.

## Deploys

Trigger from anywhere with the `gh` CLI:

```bash
# Cut a release (normal flow)
gh release create v0.1.1 --generate-notes

# Or redeploy a specific tag on demand (ad-hoc / rollback)
gh workflow run release.yml --ref master -f tag=v0.1.0
```

The workflow builds linux/amd64 images for both api and ui, pushes them to GHCR with `:<tag>` and `:latest`, scps the rendered `docker-compose.yml` + `Caddyfile`, then SSHes to the VPS and runs `compose pull && up -d --remove-orphans && run --rm ui-publisher`. Watchtower is a safety net only — UI artifact refresh requires the publisher step, which only the workflow runs.

## Manual operations on the VPS

```bash
ssh vps   # or: ssh -i ~/.ssh/id_ed25519_vps root@37.114.41.69

cd /opt/linkedin-apply
docker compose ps                          # service status
docker compose logs -f api                 # tail API logs
docker compose logs -f caddy               # cert renewal + access logs
docker exec linkedin-apply-caddy caddy list-certificates   # cert expiry

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
| variable | `ACME_EMAIL` | Maintainer email for Let's Encrypt registration |
