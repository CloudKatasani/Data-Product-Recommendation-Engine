# Deployment

How to run the engine, configure it and operate it. The security rationale for
each posture is in `docs/security.md`; the routes are in `docs/api.md`.

## What you are deploying

A single Python process with **no runtime dependencies**. It serves a browser
application and a JSON API from one port and keeps its state in one workspace
directory. There is no message broker, no cache and no external database.

Requirements: Python 3.11, 3.12 or 3.13; a writable workspace volume; a
TLS-terminating reverse proxy for anything but local development.

## Quick start (development)

```bash
python -m pip install -e ".[test]"
dpre serve --host 127.0.0.1 --port 8000 --workspace ./data
# open http://127.0.0.1:8000
```

The server prints the identity sources it accepted. On loopback that is
`loopback_dev_identity`: send `X-DPRE-Identity: your.name` and the engine treats
you as that person with the roles in `DPRE_DEV_ROLES`.

A non-loopback `--host` **refuses to start** without `DPRE_TOKENS` or
`DPRE_TRUSTED_PROXY`:

```
RuntimeError: refusing to bind 0.0.0.0: a non-loopback address needs an identity
source. Set DPRE_TOKENS to a bearer-token map, or DPRE_TRUSTED_PROXY to the
address of the SSO proxy that forwards the user's identity.
```

## Configuration

Everything is read from `DPRE_*` environment variables by
`dpre/server/settings.py`. `.env.example` is the annotated template; copy it,
never commit the copy.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DPRE_WORKSPACE` | `data` | Root of all state. `private/` inside it holds the database and uploads. |
| `DPRE_DB` | `$DPRE_WORKSPACE/private/engine.db` | Database path override. |
| `DPRE_HOST` / `DPRE_PORT` | `127.0.0.1` / `8000` | Bind address. |
| `DPRE_ALLOWED_HOSTS` | — | Extra values accepted in the `Host` header, comma separated. |
| `DPRE_TOKENS` | — | JSON map `{token: {identity, roles, domains}}`. |
| `DPRE_TRUSTED_PROXY` | — | Peer addresses whose identity header is honoured. |
| `DPRE_PROXY_IDENTITY_HEADER` | `X-Forwarded-User` | Identity header name. |
| `DPRE_PROXY_ROLES_HEADER` | `X-Forwarded-Roles` | Roles header name. |
| `DPRE_PROXY_DOMAINS_HEADER` | `X-Forwarded-Groups` | Domain-scope header name. |
| `DPRE_DEV_ROLES` | `reviewer,steward,operator,catalog_admin` | Roles a loopback dev identity receives. |
| `DPRE_MAX_UPLOAD_MB` | `64` | Request body cap, checked before reading. |
| `DPRE_REQUIRE_CSRF_HEADER` | `true` | Require `X-DPRE-Request` on state-changing requests. |
| `DPRE_RETENTION_UPLOAD_DAYS` | `30` | Upload retention when uploads are kept. |
| `DPRE_RETENTION_SEED_DAYS` | `365` | Seed-pack retention. |
| `DPRE_RETENTION_RUN_DAYS` | `365` | Synthetic-workbook retention. |
| `DPRE_KEEP_UPLOADS` | `false` | Keep extracts after a successful ingestion. |
| `DPRE_LOG_LEVEL` / `DPRE_LOG_FORMAT` | `INFO` / `json` | Logging. |
| `DPRE_AI_MODEL` | — | Optional model-assisted drafting; drafts stay `AI_DRAFT` either way. |

## The workspace

```
<workspace>/
├── private/            # 0700 - never served
│   ├── engine.db       # 0600 - runs, graph, candidates, ledgers
│   ├── uploads/        # 0700 - client extracts, 0600 each, opaque names
│   └── uploads.json    # 0600 - upload id -> file
├── seeds/<run>/<candidate>/   # served only via the seed route
└── synthetic/                 # served only via the workbook route
```

An instance started against a pre-hardening workspace moves a legacy
`engine.db` (and its `-wal`/`-shm` files) into `private/` on first start.

Back up the whole workspace; it is the only state. Encrypt the volume.

## Container

```bash
docker build -t dpre:1.0.0 .
docker run --rm \
  -p 127.0.0.1:8000:8000 \
  -v dpre-data:/var/lib/dpre \
  -e DPRE_TRUSTED_PROXY=10.0.0.9 \
  -e DPRE_ALLOWED_HOSTS=dpre.client.example \
  dpre:1.0.0
```

The image is `python:3.11-slim`, runs as uid 10001 (`dpre`), declares
`/var/lib/dpre` as a volume and carries a `HEALTHCHECK` against
`/api/v1/health`. It binds `0.0.0.0`, so it will not start until an identity
source is configured — by design.

## Reverse proxy

Minimal nginx in front of `oauth2-proxy` (or any IdP-aware proxy):

```nginx
server {
  listen 443 ssl http2;
  server_name dpre.client.example;

  client_max_body_size 64m;          # match DPRE_MAX_UPLOAD_MB

  location / {
    auth_request /oauth2/auth;
    auth_request_set $user  $upstream_http_x_auth_request_user;
    auth_request_set $roles $upstream_http_x_auth_request_groups;

    # Strip anything the client tried to assert, then set our own.
    proxy_set_header X-Forwarded-User   $user;
    proxy_set_header X-Forwarded-Roles  $roles;
    proxy_set_header X-Forwarded-Groups $roles;

    proxy_set_header Host $host;      # must be in DPRE_ALLOWED_HOSTS
    proxy_pass http://dpre:8000;
    proxy_read_timeout 600s;          # a full-estate run is synchronous
  }

  location /api/v1/run/ { limit_req zone=runs burst=2 nodelay; proxy_pass http://dpre:8000; }
}
```

Two details that matter: the proxy must **strip** inbound
`X-Forwarded-User`/`-Roles`/`-Groups` before setting its own, and `Host` must be
a value the engine accepts, or every request is refused with `DPRE-HTTP-001`.

## Health, readiness and version

| Route | Use |
| --- | --- |
| `GET /api/v1/health` | Liveness. No database work, safe at any frequency. |
| `GET /api/v1/ready` | Readiness. Pings the database, checks the schema, reports the identity sources. `503` with `DPRE-READY-001` if not ready. |
| `GET /api/v1/version` | Engine, parser, weight and API versions. |

Kubernetes:

```yaml
livenessProbe:  { httpGet: { path: /api/v1/health, port: 8000 }, periodSeconds: 30 }
readinessProbe: { httpGet: { path: /api/v1/ready,  port: 8000 }, periodSeconds: 10 }
```

## Logging

One JSON object per line on stderr. Every request emits `http_request`:

```json
{"ts":"2026-09-18T09:14:02+0000","level":"INFO","logger":"dpre.server",
 "event":"http_request","request_id":"9f2c1d4a7b0e5c83","method":"POST",
 "path":"/api/v1/runs/RUN.../review","status":200,"duration_ms":41.2,
 "identity":"priya.silva","auth_method":"trusted_proxy","client":"10.0.0.9"}
```

The same `request_id` is returned in `X-Request-Id` and appears in the body of
every problem document, so a reviewer's screenshot leads straight to the line.
An unhandled failure logs `unhandled_exception` with the traceback **server-side
only**; the client sees `DPRE-INTERNAL-001` and the id.

Set `DPRE_LOG_FORMAT=text` for a readable developer terminal.

## Routine operations

| Task | Command |
| --- | --- |
| Apply retention | `POST /api/v1/admin/purge` with `{"as_of": "2026-09-18"}` (operator role). Dry run first with `{"dry_run": true}`. |
| Rotate tokens | Edit `DPRE_TOKENS` and save; the map is reloaded on the next request that presents a bearer token. No restart. |
| Back up | Stop writes or snapshot the volume; copy `private/engine.db` together with its `-wal` file. |
| Verify the audit chain | `dpre audit` (see `docs/usage.md`). |

## Upgrading

1. Read the release notes for schema changes; the database migrates itself on
   first open and records the version (`/api/v1/ready` reports it).
2. Back up the workspace volume.
3. Deploy the new image; readiness gates the rollout.
4. Client integrations pin `/api/v1`. The unversioned `/api` alias is retained
   for one release only.

## Continuous integration

`.github/workflows/ci.yml` runs on every push: the suite on Python 3.11, 3.12
and 3.13 against the **installed** package, `compileall` plus `pyflakes` when it
is available, a check that the runtime dependency list is still empty, and one
end-to-end automated run. A release is a green run of that workflow plus a tag.
