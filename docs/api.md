# API contract

The HTTP surface of the Data Product Recommendation Engine. The machine-readable
version of this document is generated from the router table itself and served at
`GET /api/v1/openapi.json` (OpenAPI 3.1), so it cannot drift from the code.

The engine only proposes. No route advances a candidate past `Proposed`; only
`POST /api/v1/runs/{run_id}/review` does, and the reviewer it records is the
authenticated principal, never a name in the request body.

## Versioning

| Prefix | Status |
| --- | --- |
| `/api/v1/...` | Canonical. Code against this. |
| `/api/...` | Alias for the same handlers, retained for one release so the shipped browser application keeps working. It will be withdrawn; it is not in the OpenAPI document. |

`GET /api/v1/version` returns `engine_version`, `parser_version`, `weight_version`
and `api_version`.

## Authentication

Every request resolves to a principal before a handler runs. Sources are tried in
this order, and the first one that yields an identity wins.

1. **Trusted proxy header.** `X-Forwarded-User` (name configurable) is honoured
   only when the connection's peer address is listed in `DPRE_TRUSTED_PROXY`.
   `X-Forwarded-Roles` and `X-Forwarded-Groups` carry roles and domain scope.
   This is the supported production posture.
2. **Bearer token.** `Authorization: Bearer <token>`, looked up by digest in the
   JSON map at `DPRE_TOKENS`: `{"<token>": {"identity": "...", "roles": [...],
   "domains": [...]}}`. An entry without an identity or a known role is dropped
   at load time rather than trusted.
3. **Loopback dev identity.** `X-DPRE-Identity: <name>`, honoured **only** when
   the server is bound to a loopback address. It is a development convenience,
   not a control; see `docs/security.md`.

A non-loopback bind with neither (1) nor (2) configured refuses to start.

`GET /api/v1/whoami` returns what the server believes about the caller.

### Roles and actions

| role | actions |
| --- | --- |
| `reviewer` | `read`, `review`, `download` |
| `steward` | `read`, `steward`, `download` |
| `council` | `read`, `approve_weights`, `waive`, `configure` |
| `catalog_admin` | `read`, `download` |
| `operator` | `read`, `run`, `administer` |

`review`, `steward` and `download` are **domain scoped**: a principal whose
`domains` list is non-empty may only act on candidates in those domains. An empty
`domains` list means every domain.

Segregation of duties: a council member whose own Accept/Reject decisions are in
the training set of a weight proposal cannot approve that proposal
(`DPRE-AUTH-004`).

### Required headers

| Header | When | Why |
| --- | --- | --- |
| `X-DPRE-Request: 1` | every `POST` | A cross-origin form cannot set a custom header, so requiring one defeats CSRF and DNS-rebinding writes. |
| `Content-Type: application/json` | every JSON `POST` | A body of another media type is refused with `415`, never silently treated as `{}`. |
| `Host` | every request | Validated against the bound address and `DPRE_ALLOWED_HOSTS`. |

Every response carries `X-Request-Id`; quote it to the operator when reporting a
`500`, whose detail exists only in the server log.

## Errors

Refusals are RFC 9457 problem documents, `Content-Type: application/problem+json`:

```json
{
  "type": "https://dpre.invalid/problems/dpre-auth-002",
  "title": "Role does not permit this action",
  "status": 403,
  "code": "DPRE-AUTH-002",
  "detail": "'approve_weights' requires one of: council.",
  "instance": "/api/v1/weights/approve",
  "request_id": "9f2c1d4a7b0e5c83",
  "error": "Role does not permit this action"
}
```

`code` is the stable contract; `title` and `detail` are prose and may be
reworded. `error` repeats the title for the current browser application and is
**deprecated**. A `5xx` never echoes an exception, a stack frame or a server
path.

| code | status | meaning |
| --- | --- | --- |
| `DPRE-AUTH-001` | 401 | No identity on the request. |
| `DPRE-AUTH-002` | 403 | The principal's roles do not hold the action. |
| `DPRE-AUTH-003` | 403 | The resource's domain is outside the principal's scope. |
| `DPRE-AUTH-004` | 403 | Segregation of duties: the approver trained the proposal. |
| `DPRE-AUTH-005` | 401 | The credential presented is not accepted. |
| `DPRE-HTTP-001` | 400 | `Host` is not an address this instance answers to. |
| `DPRE-HTTP-002` | 403 | `X-DPRE-Request` missing on a state-changing request. |
| `DPRE-HTTP-003` | 415 | The body's media type is not what the route parses. |
| `DPRE-HTTP-004` | 413 | The body exceeds `DPRE_MAX_UPLOAD_MB`. |
| `DPRE-HTTP-005` | 400 | The body is not a JSON object. |
| `DPRE-HTTP-006` | 404 | No such route. |
| `DPRE-HTTP-007` | 400 | `limit` or `offset` is not a bounded whole number. |
| `DPRE-INPUT-001` | 400 | A parameter is invalid (including an unsafe name). |
| `DPRE-INPUT-002` | 404 | The run, candidate, upload or seed does not exist. |
| `DPRE-UPLOAD-001` | 400 | No file part in the multipart body. |
| `DPRE-UPLOAD-002` | 415 | Extension not allow-listed, or the bytes do not match it. |
| `DPRE-UPLOAD-003` | 400 | The file could not be parsed into records. |
| `DPRE-UPLOAD-004` | 413 | The workbook expands past the decompression bounds. |
| `DPRE-RUN-001` | 400 | Ingestion failed validation (re-post with `"force": true` to override). |
| `DPRE-RUN-002` | 400 | Unknown industry. |
| `DPRE-REVIEW-001` | 403 | The store refused the decision: propose-only, a gate or a transition. |
| `DPRE-REVIEW-002` | 400 | The decision or its reason code is not valid. |
| `DPRE-WEIGHTS-001` | 400 | The weight proposal cannot be approved yet. |
| `DPRE-ADMIN-001` | 400 | An administrative action was refused. |
| `DPRE-READY-001` | 503 | The database is not answering or its schema is incomplete. |
| `DPRE-INTERNAL-001` | 500 | Unexpected failure; the detail is in the server log under `request_id`. |

## Pagination

List routes accept `limit` (1–500, default 50; 200 on `gaps`) and `offset`
(default 0), and return a `page` envelope beside the list:

```json
{ "candidates": [...], "page": { "limit": 50, "offset": 0, "total": 213, "returned": 50 } }
```

Paged routes: `runs`, `runs/{run_id}/candidates`, `runs/{run_id}/conflicts`,
`runs/{run_id}/metrics`, `runs/{run_id}/gaps`, `runs/{run_id}/decisions`.

## Files

No route accepts a filesystem path, and no route returns one.

* **Uploads.** `POST /api/v1/upload` (multipart) stores each extract under a
  server-generated `upload_id` and returns that id with the detected tables. The
  client posts the id back to `POST /api/v1/uploads/{upload_id}/inspect` and to
  `run/manual` (`{"sources": [{"upload_id": "...", "schema_key": "...", "sheet":
  "...", "mapping": {...}}]}`). Uploaded extracts are deleted after a successful
  ingestion unless `DPRE_KEEP_UPLOADS=true`.
* **Downloads.** `GET /api/v1/runs/{run_id}/candidates/{candidate_id}/seeds/{name}`
  and `GET /api/v1/synthetic/{industry}/workbook/download`. Both compose their
  path server-side under `seeds/` or `synthetic/` only. `name` must match
  `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`.

## Routes

`action` is the entry in the role matrix the route demands; `_public_` routes
resolve no principal.

| method | path | action | paged | summary |
| --- | --- | --- | --- | --- |
| `GET` | `/api/v1/health` | _public_ | — | Liveness probe |
| `GET` | `/api/v1/ready` | _public_ | — | Readiness: database ping and schema check |
| `GET` | `/api/v1/version` | _public_ | — | Engine, parser and weight versions |
| `GET` | `/api/v1/openapi.json` | _public_ | — | This API contract |
| `GET` | `/api/v1/whoami` | read | — | The principal the server resolved for this request |
| `GET` | `/api/v1/industries` | read | — | Synthetic industries |
| `GET` | `/api/v1/schemas` | read | — | Accepted extract schemas and their required fields |
| `GET` | `/api/v1/seed-index` | read | — | DPF stages the engine seeds |
| `GET` | `/api/v1/semantic-view` | read | — | Named queries the chat surface may select |
| `GET` | `/api/v1/reason-codes` | read | — | Valid reason codes per decision |
| `GET` | `/api/v1/config` | read | — | Effective engine configuration |
| `POST` | `/api/v1/config` | configure | — | Change weights and clustering configuration |
| `POST` | `/api/v1/upload` | run | — | Upload extracts; returns opaque upload ids |
| `POST` | `/api/v1/uploads/{upload_id}/inspect` | run | — | Describe an uploaded extract and its auto-mapping |
| `POST` | `/api/v1/run/manual` | run | — | Run the pipeline over uploaded extracts |
| `POST` | `/api/v1/run/automated` | run | — | Generate a synthetic pack and run the pipeline |
| `GET` | `/api/v1/synthetic/{industry}/workbook` | run | — | Generate the synthetic workbook for an industry |
| `GET` | `/api/v1/synthetic/{industry}/workbook/download` | download | — | Download the synthetic workbook |
| `GET` | `/api/v1/runs` | read | yes | Runs, newest first |
| `GET` | `/api/v1/runs/{run_id}` | read | — | One run and its manifest |
| `GET` | `/api/v1/runs/{run_id}/candidates` | read | yes | Candidates of a run, ranked by composite score |
| `GET` | `/api/v1/runs/{run_id}/candidates/{candidate_id}` | read | — | One candidate with evidence, metrics and seeds |
| `GET` | `/api/v1/runs/{run_id}/candidates/{candidate_id}/seeds/{name}` | download | — | Download one generated seed artifact |
| `GET` | `/api/v1/runs/{run_id}/conflicts` | read | yes | Definition conflicts of a run |
| `GET` | `/api/v1/runs/{run_id}/metrics` | read | yes | Canonical metrics of a run |
| `GET` | `/api/v1/runs/{run_id}/portfolio` | read | — | Coverage curve, retirement map and conflict heat map |
| `GET` | `/api/v1/runs/{run_id}/gaps` | read | yes | Quarantined lineage, undefined columns, stewardless metrics |
| `GET` | `/api/v1/runs/{run_id}/agents` | read | — | Agent log and quality gates of a run |
| `GET` | `/api/v1/runs/{run_id}/decisions` | read | yes | Review decisions recorded against a run |
| `GET` | `/api/v1/runs/{run_id}/feedback` | read | — | Feedback loop report and the current weight proposal |
| `POST` | `/api/v1/runs/{run_id}/review` | review | — | Record a review decision (reviewer = the authenticated principal) |
| `POST` | `/api/v1/runs/{run_id}/conflicts/{conflict_id}/resolve` | steward | — | Adjudicate a definition conflict |
| `POST` | `/api/v1/runs/{run_id}/metrics/{metric_id}/accept-name` | steward | — | Accept a drafted canonical metric name |
| `POST` | `/api/v1/runs/{run_id}/reports/decision-critical` | review | — | Mark a report as decision-critical |
| `POST` | `/api/v1/weights/approve` | approve_weights | — | Council approval of a proposed weight version |
| `POST` | `/api/v1/chat` | read | — | Ask the conversational surface a named-query question |
| `POST` | `/api/v1/admin/purge` | administer | — | Delete workspace artifacts past their retention window |

### Notes on individual routes

* `POST /api/v1/runs/{run_id}/review` — body: `candidate_id`, `decision`
  (`Accept`, `Reject`, `Merge`, `Split`, `Defer`, `Override`), `reason_code`,
  `note`, and for `Merge`/`Split`/`Override` the relevant target or field. A
  `reviewer` member in the body is **ignored**. Refusals from the propose-only
  gates surface as `DPRE-REVIEW-001`.
* `POST /api/v1/weights/approve` — takes no approver: it is the principal. The
  proposal is re-estimated server-side, must beat the majority-class baseline,
  and the approver must not appear in its training rows.
* `POST /api/v1/admin/purge` — body `{"as_of": "YYYY-MM-DD", "dry_run": true}`.
  `as_of` keeps the result reproducible; `dry_run` reports without deleting.
* `POST /api/v1/chat` — the agent selects a **named query** from the semantic
  view. It never composes SQL, and every answer carries citations.

## Known limits of this release

* Runs execute inside the request. A full-estate manual run can exceed a
  corporate proxy's idle timeout; a `202 Accepted` plus `/api/v1/jobs/{id}` is
  the intended follow-up and is not in this release.
* Pagination is `limit`/`offset`. A cursor form can be added without changing
  the `page` envelope's other members.
* There is no per-client rate limiting: place the run and upload routes behind
  the proxy's rate limiter (`docs/deployment.md`).
