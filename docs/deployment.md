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

Not yet configured — the VPS currently serves plain HTTP on `:80` (UI) and `:8000` (API). TLS via Caddy + Let's Encrypt is a planned follow-up; see [`docs/plans/vps-deployment.md`](plans/vps-deployment.md) "Out of scope".

When TLS lands, update:

- GitHub repo variable `VITE_API_URL` → `https://api.kuule.cc`
- VPS `.env` `APP_URL` → `https://app.kuule.cc` (so magic links use the correct callback)
