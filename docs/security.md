# Security

What this engine holds, who can reach it, what it refuses, and what a client's
privacy officer needs to know before it touches a production extract. It is
written to be read by a security architect at a client, not only by a developer.

Companion documents: `docs/deployment.md` (how to run it), `docs/api.md` (the
contract the controls are enforced on).

---

## 1. Threat model

### 1.1 Assets

| Asset | Where it lives | Why it matters |
| --- | --- | --- |
| Client metadata extracts (Cognos, Power BI, Collibra, Alation) | `workspace/private/uploads/`, transiently | Names every report, KPI, table and column of the estate, including columns flagged PII (`tax_identifier`, `customer_email`). A map of where the client's sensitive data lives. |
| The lineage graph and canonical metric register | `workspace/private/engine.db` | Same content, resolved and indexed. `GRAPH_NODE_COLUMN` carries sensitivity and PII flags. |
| Steward, report-owner and reviewer identities | `engine.db`, seeds, communications drafts | **Personal data** under GDPR: named employees with roles and workload. |
| The decision ledger (`REVIEW_DECISION`, weight approvals, waivers) | `engine.db`, hash-chained | The evidence that a human, not the engine, accepted each candidate. This is the control the asset is sold on. |
| Generated DPF seeds | `workspace/seeds/<run>/<candidate>/` | Charters, attribute registers, semantic models. Derived from the above. |
| Score weights and their approvals | `engine.db` | Determines what the backlog recommends; tampering changes advice given to a CDO. |

### 1.2 Actors

| Actor | Motivation |
| --- | --- |
| Reviewer, steward, council member, catalog admin, engine operator | Legitimate users, scoped by role and domain. |
| A curious insider on the same network | Read another domain's candidates, or accept in someone else's name. |
| A hostile web page a reviewer visits while the engine runs | Drive the loopback instance from the reviewer's browser (CSRF, DNS rebinding) and exfiltrate the estate. |
| An unauthenticated network peer | Reach an exposed port and read or run everything. |
| A malicious uploader | Denial of service through a decompression bomb, or code paths reached by a mistyped file. |
| A compromised reverse proxy | Assert any identity. Explicitly in scope as a trust boundary, not a defence. |

### 1.3 Trust boundaries

```
  browser ──┬── (1) ── TLS-terminating SSO proxy ──┬── (2) ── dpre HTTP surface
            │                                       │
   hostile  │                                       ├── (3) ── workspace/private (0700)
    page ───┘                                       └── (4) ── workspace/seeds, synthetic
```

1. **Browser → proxy.** Outside the engine's control. TLS and authentication
   belong here. The engine speaks plain HTTP and says so.
2. **Proxy → engine.** The engine trusts the identity header *only* from peer
   addresses in `DPRE_TRUSTED_PROXY`. Everything else on this boundary —
   `Host`, `Content-Type`, `Content-Length`, `X-DPRE-Request`, the role matrix —
   is validated, because a request that reaches the port is not a request from
   the proxy.
3. **Engine → private state.** The database, the uploads and the upload registry
   live under `workspace/private` at mode 0700/0600 and are reachable by **no**
   route. There is no path parameter anywhere in the API.
4. **Engine → served artifacts.** Seeds and synthetic workbooks are served only
   through routes keyed by run, candidate, seed name or industry, each composed
   server-side and confined by `security.safe_child`.

### 1.4 Threats and the control that answers each

| # | Threat | Control | Evidence |
| --- | --- | --- | --- |
| T1 | Anyone asserts any reviewer name | Identity resolved per request from proxy header, bearer token or (loopback only) dev header; handlers read `principal.identity` and ignore any `reviewer`/`approver` in the body | `tests/test_security.py::test_identity_is_taken_from_the_header_and_the_body_is_ignored`, `tests/test_server.py::test_review_identity_comes_from_the_principal_not_the_body` |
| T2 | A reviewer acts outside their domain | Role-to-action matrix plus domain scope on `review`, `steward`, `download` | `test_domain_scope_bounds_review_and_download` |
| T3 | A council member approves weights trained on their own decisions | `assert_weight_approver_independent` compares the approver against the Accept/Reject identities in the training set | `test_a_weight_approver_may_not_have_trained_the_proposal` |
| T4 | Exfiltration of the database or a client extract | `/api/download?path=` removed; database and uploads moved to `private/`; downloads keyed by resource; opaque upload ids | `test_the_database_and_uploads_are_private`, `test_the_path_download_route_is_gone` |
| T5 | Path traversal in a download | `safe_child` rejects `..`, absolute paths and unsafe characters, then re-checks the resolved parent | `test_path_traversal_cannot_escape_a_directory` |
| T6 | CSRF / DNS rebinding from a hostile page | `Host` validated against the bound address and `DPRE_ALLOWED_HOSTS`; `X-DPRE-Request` required on every state-changing request; no CORS headers, `OPTIONS` answers 204 with no ACAO | `test_a_wrong_host_header_is_refused`, `test_a_post_without_the_request_header_is_refused`, `test_options_is_204_without_cors_and_head_carries_no_body` |
| T7 | Memory exhaustion via upload | `Content-Length` capped before a byte is read (`DPRE_MAX_UPLOAD_MB`, default 64); multipart parser bounded by size and part count | `test_an_oversized_body_is_refused_before_it_is_read`, `test_the_multipart_parser_is_bounded` |
| T8 | Decompression bomb in an `.xlsx` | Per-member decompressed-size ceiling, compression-ratio ceiling, archive-wide byte budget, bounded read, rows-per-sheet cap | `test_a_workbook_that_expands_too_far_is_refused`, `test_an_expansion_workbook_is_refused_at_the_upload_endpoint` |
| T9 | A renamed executable reaching a parser | Extension allow-list plus magic-byte check | `test_upload_extensions_are_allow_listed_and_magic_checked` |
| T10 | Information disclosure through errors | RFC 9457 problems with fixed titles; absolute paths scrubbed; `5xx` returns only a correlation id; `Server: dpre`, no Python version | `test_a_server_path_never_reaches_the_client`, `test_responses_do_not_advertise_the_runtime` |
| T11 | Script injection in the browser application | `Content-Security-Policy: default-src 'self'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer` on every response | `tests/test_server.py::test_static_application_is_served` |
| T12 | An unauthenticated public deployment | `startup_check` refuses a non-loopback bind without a token map or a trusted proxy | `test_a_public_bind_without_an_identity_source_refuses_to_start` |
| T13 | Client data kept forever | Uploads deleted after successful ingestion; retention windows with `purge` | `test_purge_deletes_past_the_retention_window_and_keeps_the_ledger` |

### 1.5 Explicitly out of scope

* **TLS.** The process speaks plain HTTP. Terminate TLS in front of it.
* **Encryption at rest.** Provide it at the volume (LUKS, EBS/Managed Disk
  encryption). The engine sets file modes, not cipher suites.
* **A compromised trusted proxy.** If the proxy is owned, identity is owned.
* **Rate limiting.** The run and upload routes are expensive; put them behind the
  proxy's rate limiter.
* **Secret management.** `DPRE_TOKENS` is a file the platform must protect and
  rotate like any other secret.

---

## 2. Supported deployment postures

Only two postures are supported. Anything else is unreviewed.

### Posture A — loopback development

One person, one machine. `DPRE_HOST=127.0.0.1`. The caller names themselves with
`X-DPRE-Identity` and receives `DPRE_DEV_ROLES`, which by default is
`reviewer,steward,operator,catalog_admin`.

`council` is deliberately **not** in that default: weight approval and waivers
are the segregation-of-duties sensitive actions, so even in development they
require a real bearer token. A dev instance that tries to approve weights gets
`DPRE-AUTH-002`, which is the same refusal a production reviewer would get.

This posture is **not** a control environment. Decisions recorded here carry an
`auth_method` of `loopback_dev` and should not be presented as evidence.

### Posture B — behind a TLS-terminating SSO proxy

The reviewed production posture.

```
client browser ──TLS──> nginx / Envoy / oauth2-proxy ──HTTP──> dpre (container)
                         authenticates against the client's IdP
                         sets X-Forwarded-User / -Roles / -Groups
                         strips those headers from the inbound request
```

Requirements:

* `DPRE_TRUSTED_PROXY` lists the proxy's peer address. Nothing else may set the
  identity header; a request that reaches the port directly establishes nothing.
* The proxy **must strip** `X-Forwarded-User`, `X-Forwarded-Roles` and
  `X-Forwarded-Groups` from inbound requests before setting its own.
* `DPRE_ALLOWED_HOSTS` names the public hostname.
* The engine's port is reachable only from the proxy (network policy, security
  group or a container network).
* Roles and domains come from IdP group membership, mapped at the proxy.

A machine client (a scheduler, a Collibra import job) uses a bearer token from
`DPRE_TOKENS` instead, scoped to `operator` or `read` only.

### Hardening checklist

- [ ] `DPRE_HOST` is loopback, or an identity source is configured (enforced).
- [ ] TLS terminated in front; the engine's port is not routable from user networks.
- [ ] `DPRE_ALLOWED_HOSTS` set to the public hostname.
- [ ] Proxy strips inbound identity headers.
- [ ] `DPRE_TOKENS` (if used) is 0600, owned by the service account, outside the workspace, and rotated.
- [ ] Workspace volume is encrypted and backed up with the client's records.
- [ ] `DPRE_MAX_UPLOAD_MB` matches the proxy's body limit.
- [ ] Rate limiting on `/api/v1/run/*` and `/api/v1/upload` at the proxy.
- [ ] Structured logs shipped; `request_id` retained for the incident window.
- [ ] `/api/v1/ready` wired to the orchestrator's readiness probe.
- [ ] Retention windows agreed with the client and `admin/purge` scheduled.
- [ ] Container runs as a non-root user (the shipped image does).

---

## 3. Data handling and retention

### 3.1 What is stored, and where

| Data | Location | Mode | Retention |
| --- | --- | --- | --- |
| Uploaded extracts | `workspace/private/uploads/<opaque-id><ext>` | 0600 | Deleted immediately after a successful ingestion (`DPRE_KEEP_UPLOADS=false`, the default); otherwise `DPRE_RETENTION_UPLOAD_DAYS` (30). |
| Upload registry | `workspace/private/uploads.json` | 0600 | Entry removed with the file. |
| Database | `workspace/private/engine.db` | 0600 | See 3.2. |
| Seeds | `workspace/seeds/<run>/<candidate>/` | default | `DPRE_RETENTION_SEED_DAYS` (365). |
| Synthetic workbooks | `workspace/synthetic/` | default | `DPRE_RETENTION_RUN_DAYS` (365). Synthetic only: no client data. |
| Logs | stderr, JSON | — | The platform's log retention. Contains identities, paths of API routes and request ids; **not** extract contents. |

`POST /api/v1/admin/purge` (operator role) applies these windows;
`{"dry_run": true}` reports without deleting, and `as_of` makes the result
reproducible. Schedule it daily.

### 3.2 Why the ledger is not purged

`purge` deletes files. It does **not** delete `REVIEW_DECISION`, weight
approvals, waivers or status history. Those rows are hash-chained and are the
evidence that a named human moved each candidate past `Proposed`; deleting them
would destroy the control the engine exists to provide, and `verify_audit_chain`
would report the gap.

Ledger retention is therefore a **records-management decision for the client**,
not an engine setting. Two facts to take into that decision:

* The ledger contains employee identities (personal data) but no customer data —
  only *names of* columns and reports.
* An erasure request against a steward or reviewer identity cannot be satisfied
  by deleting ledger rows without breaking the chain. The intended remedy is
  pseudonymisation at the identity source (map the person to a stable opaque
  principal id at the proxy) agreed **before** go-live. This is an open item, and
  it is the one privacy question this design does not close on its own.

### 3.3 Minimisation

* The engine ingests **metadata**, never rows of customer data. It reads column
  names, types, sensitivity flags and usage counts.
* Extracts are deleted after ingestion by default, so the client's file does not
  outlive the run it fed.
* No route returns a filesystem path, so nothing about the host's layout leaks
  into a client's browser or logs.
* Error details are scrubbed of absolute paths before they leave the process.

---

## 4. Privacy impact assessment — outline

For the client's DPO to complete. The engine supplies the facts; the client
supplies purpose, lawful basis and sign-off.

1. **Description of processing.** Metadata about a reporting estate, plus the
   names of employees who own reports, steward metrics and review candidates.
   Purpose: rationalising reports into governed data products.
2. **Necessity and proportionality.** Steward and owner identities are necessary
   to route adjudication and notification; without them the engine cannot say
   *who* must decide. Alternative considered: pseudonymous principal ids from the
   IdP, which the trusted-proxy posture already supports (§3.2).
3. **Personal data inventory.**
   | Element | Source | Category | Where it lands |
   | --- | --- | --- | --- |
   | Report owner name/email | Cognos, Power BI extract | Employee contact | `GRAPH_NODE_REPORT.owner`, owner-notification drafts |
   | Steward id | Collibra, Alation extract | Employee identifier | `KPI_CANONICAL.steward_id`, steward-adjudication drafts |
   | Reviewer / approver identity | Authenticated principal | Employee identifier | `REVIEW_DECISION`, weight approvals, status history |
   | Distinct-user counts per report | Usage extract | Aggregate, not identifying | `GRAPH_NODE_REPORT` |
   | Column names flagged PII | Catalog extract | *About* personal data, not personal data itself | `GRAPH_NODE_COLUMN` |
4. **Lawful basis.** Legitimate interest (IT estate rationalisation) is the usual
   basis for the employee data; the client confirms.
5. **Data subjects' rights.** Access and rectification: the identities come from
   the client's own catalog and IdP and are corrected there, then re-ingested.
   Erasure: see §3.2.
6. **Risks and mitigations.** Section 1.4 is the input; the residual risks are
   §1.5 plus ledger erasure (§3.2).
7. **Retention.** Section 3.1 for files; a client decision for the ledger.
8. **International transfer.** Determined by where the client runs the container;
   the engine makes no outbound network call and contacts no third party.
9. **Sign-off.** DPO, CDO and the engagement partner.

---

## 5. Reporting a vulnerability

Report to the engagement's security contact with the `request_id` of the
affected request if there is one. Do not open a ticket containing a client
extract or a database file.
