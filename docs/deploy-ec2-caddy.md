# Deploying on AWS EC2 behind Caddy

One EC2 instance running two containers: **Caddy** (TLS from Let's Encrypt,
sign-in, identity headers) and **the engine** (no published port, trusts only
Caddy). The ready-to-use files are in `deploy/ec2-caddy/`; this page walks
through them. `docs/deployment.md` covers the engine's configuration in general.

```
browser ──HTTPS──▶ Caddy :443 (basic_auth, sets X-Forwarded-User/-Roles)
                        │  docker network 172.30.0.0/24
                        ▼
                   dpre :8000 (DPRE_TRUSTED_PROXY=172.30.0.10)
                        │
                   volume dpre-data → /var/lib/dpre (database, uploads)
```

Why it is shaped this way: the engine refuses to bind a public address unless
something authenticates the user (`docs/security.md`). Caddy does that with
`basic_auth` and forwards the signed-in name and roles; the engine honours those
headers only from Caddy's fixed address and ignores anything a client sends.

## 1. Launch the instance

| Setting | Value |
| --- | --- |
| AMI | Ubuntu Server 24.04 LTS (x86_64 or arm64 both work) |
| Type | `t3.small` (2 GiB) is enough; `t3.medium` for large client estates |
| Storage | 20 GiB gp3, **encrypted** (it holds client metadata) |
| Security group | inbound 22 from your IP only; 80 and 443 from `0.0.0.0/0` (and `::/0`). Nothing on 8000. |
| Elastic IP | allocate and associate, so DNS survives a stop/start |

Then point DNS at it: an `A` record, e.g. `dpre.example.com → <Elastic IP>`.
Wait until `dig +short dpre.example.com` returns the address; Caddy needs it to
obtain the certificate.

## 2. Install Docker

```bash
ssh ubuntu@dpre.example.com
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && exit     # log back in to pick up the group
ssh ubuntu@dpre.example.com
docker compose version
```

## 3. Get the code

```bash
sudo mkdir -p /opt/dpre && sudo chown ubuntu: /opt/dpre
git clone https://github.com/cloudkatasani/data-product-recommendation-engine.git /opt/dpre
cd /opt/dpre/deploy/ec2-caddy
```

The repository is private: use a read-only deploy key
(`ssh-keygen -t ed25519`, add the public key under *Settings → Deploy keys*,
clone with the `git@github.com:` URL) or a fine-grained token with read access.

## 4. Configure

```bash
cp .env.example .env
cp Caddyfile.example Caddyfile
nano .env            # DPRE_DOMAIN=dpre.example.com, ACME_EMAIL=you@example.com
```

Create a password hash for each user and paste it into `Caddyfile`:

```bash
docker run --rm caddy:2 caddy hash-password --plaintext 'a-long-password'
```

In `Caddyfile`, edit three blocks:

- `basic_auth` — one line per user: `name <hash>`.
- `map … {dpre_roles}` — the roles each user gets: `reviewer`, `steward`,
  `operator` (upload and run), `catalog_admin`, `council` (weight approval and
  waivers). Give `council` to someone who does **not** review candidates; the
  engine enforces that segregation. A user not listed gets `default`.
- `map … {dpre_domains}` — leave `""` for access to every business domain, or
  list domains (`"Finance,Operations"`) to scope a reviewer.

`Caddyfile` and `.env` are git-ignored in that folder, so a later `git pull`
never overwrites them.

## 5. Start

```bash
docker compose up -d --build
docker compose ps                    # both "running"; dpre becomes "healthy"
docker compose logs -f caddy         # look for "certificate obtained successfully"
```

## 6. Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://dpre.example.com/api/v1/whoami   # 401
curl -s -u alice:'a-long-password' https://dpre.example.com/api/v1/whoami
# {"principal": {"identity": "alice", "roles": [...], "auth_method": "trusted_proxy", ...}}
curl -s -u alice:'a-long-password' https://dpre.example.com/api/v1/ready
```

Then open `https://dpre.example.com`, sign in, and run an automated pack
(Start → Automated → an industry) as a user with the `operator` role.

If every request returns `DPRE-HTTP-001`, `DPRE_DOMAIN` in `.env` does not match
the name in the browser. If you are signed in but the app says the request has
no identity, the engine is not seeing Caddy at `172.30.0.10`: check
`docker compose config` shows both fixed addresses.

## Operating it

| Task | How |
| --- | --- |
| Add/remove a user, change roles | Edit `Caddyfile`, then `docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile` |
| Upgrade | `git pull && docker compose up -d --build` (the database migrates itself; back up first) |
| Logs | `docker compose logs -f dpre` — one JSON line per request with `identity` and `request_id` |
| Restart on reboot | Automatic (`restart: unless-stopped`, Docker starts at boot) |
| Retention purge | `POST /api/v1/admin/purge` as an operator — see `docs/deployment.md` |

### Backups

All state is the `dpre-data` volume. Two layers:

1. **EBS snapshots** of the root volume — Data Lifecycle Manager, daily, keep 14.
2. **A consistent copy of the database** (safe while the engine runs):

```bash
docker compose exec dpre python -c "import sqlite3; \
s=sqlite3.connect('/var/lib/dpre/private/engine.db'); \
d=sqlite3.connect('/var/lib/dpre/private/engine.backup.db'); s.backup(d); d.close()"
docker compose cp dpre:/var/lib/dpre/private/engine.backup.db ./engine-$(date +%F).db
# then e.g. aws s3 cp ./engine-$(date +%F).db s3://<bucket>/dpre/ --sse aws:kms
```

## Variations

- **Real single sign-on instead of passwords.** Replace `basic_auth` with
  Caddy's `forward_auth` to an `oauth2-proxy` container, and set
  `X-Forwarded-User` / `X-Forwarded-Roles` from the headers it returns
  (`copy_headers`). The engine side does not change.
- **Machine clients with bearer tokens.** `basic_auth` uses the `Authorization`
  header, so tokens cannot share the same site block. Serve them on a second
  hostname without `basic_auth` that passes `Authorization` through, and set
  `DPRE_TOKENS` (`docs/deployment.md`).
- **Restrict who can reach it at all.** Narrow the security group's 443 rule to
  your office or VPN range. Keep port 80 open to everyone so Let's Encrypt can
  renew the certificate, or switch Caddy to the DNS challenge.
