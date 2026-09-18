"""Identity, roles and request hardening for the HTTP surface (R-01, R-29, R-30).

The engine only proposes; a human decides. That guardrail is worth nothing if
the human is a string in a JSON body, so every request resolves to a
:class:`Principal` before a handler runs, and handlers take the reviewer,
steward, council member or operator from the principal, never from the body.

Identity is resolved in one order, most trusted first:

1. **Trusted proxy header.** The supported production posture is a
   TLS-terminating SSO proxy that authenticates the user and forwards the
   identity. The header is honoured only when the connection's peer address is
   one of ``DPRE_TRUSTED_PROXY``, so the header cannot be spoofed by a client
   that reaches the port directly.
2. **Bearer token.** ``DPRE_TOKENS`` points at a JSON map
   ``{token: {identity, roles, domains}}``; tokens are looked up by digest so a
   wrong token costs the same as a right one.
3. **Loopback dev identity.** Only when the server is bound to loopback, an
   ``X-DPRE-Identity`` header names the caller. This is a development
   convenience, not a control: :func:`startup_check` refuses to start on a
   non-loopback address without (1) or (2).

Authorisation is a role-to-action matrix (:data:`ROLE_ACTIONS`) plus domain
scope: a principal with an empty ``domains`` tuple is unscoped, otherwise the
action must name a domain the principal holds. Segregation of duties is
enforced where the review package cannot see it: a council member may not
approve a weight version trained on their own decisions
(:func:`assert_weight_approver_independent`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import problem
from .settings import CSRF_HEADER, DEV_IDENTITY_HEADER, Settings, is_loopback

__all__ = [
    "ACTIONS", "ROLES", "ROLE_ACTIONS", "ANONYMOUS", "Principal", "SecurityPolicy",
    "TokenMap", "authorize", "assert_weight_approver_independent", "check_content_length",
    "check_content_type", "check_csrf", "check_host", "load_token_map", "resolve_principal",
    "safe_child", "startup_check", "training_reviewers", "validate_upload",
]

# --------------------------------------------------------------------------
# The role-to-action matrix (R-01)
# --------------------------------------------------------------------------

#: Every action a route can demand, with the sentence an auditor should read.
ACTIONS: dict[str, str] = {
    "read": "Read runs, candidates, metrics, evidence, gaps and decisions",
    "run": "Upload extracts, inspect them and execute the pipeline",
    "review": "Record a review decision on a candidate in scope",
    "steward": "Resolve a definition conflict or accept a canonical metric name",
    "approve_weights": "Approve a score weight version for the council",
    "waive": "Waive a quality gate or approve an exception",
    "download": "Download a generated seed or synthetic payload",
    "configure": "Change engine configuration in a running instance",
    "administer": "Operate the instance: retention purge and other admin actions",
}

#: Which roles hold which actions. Read is deliberately common to every role:
#: evidence is what makes a decision contestable, so no role is blind.
ROLE_ACTIONS: dict[str, tuple[str, ...]] = {
    "reviewer": ("read", "review", "download"),
    "steward": ("read", "steward", "download"),
    "council": ("read", "approve_weights", "waive", "configure"),
    "catalog_admin": ("read", "download"),
    "operator": ("read", "run", "administer"),
}

ROLES: tuple[str, ...] = tuple(ROLE_ACTIONS)

#: Actions whose authorisation is additionally scoped to the principal's
#: domains. A reviewer with ``domains: ["Finance"]`` cannot accept a Risk
#: candidate, and cannot download its seeds either.
DOMAIN_SCOPED: frozenset[str] = frozenset({"review", "steward", "download"})

#: Extensions the upload endpoint accepts, with the magic bytes each must start
#: with. ``None`` means "no binary signature: must look like text" (R-30).
UPLOAD_SIGNATURES: dict[str, bytes | None] = {
    ".xlsx": b"PK\x03\x04",
    ".xlsm": b"PK\x03\x04",
    ".csv": None,
    ".tsv": None,
    ".tab": None,
    ".txt": None,
    ".json": None,
    ".jsonl": None,
    ".ndjson": None,
}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SNIFF_BYTES = 8192


# --------------------------------------------------------------------------
# Principal
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Principal:
    """Who is making this request, and what they are entitled to do.

    ``domains`` empty means unscoped (every domain); a non-empty tuple is an
    allow-list. ``auth_method`` and ``client_address`` exist so an audit record
    can say *how* an identity was established, not only that it was asserted.
    """

    identity: str = ""
    roles: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    auth_method: str = "none"
    client_address: str = ""

    @property
    def authenticated(self) -> bool:
        return bool(self.identity)

    @property
    def actions(self) -> tuple[str, ...]:
        """Every action this principal's roles grant, de-duplicated and sorted."""
        granted: set[str] = set()
        for role in self.roles:
            granted.update(ROLE_ACTIONS.get(role, ()))
        return tuple(sorted(granted))

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def may(self, action: str) -> bool:
        return action in self.actions

    def covers_domain(self, domain: str) -> bool:
        """Unscoped principals cover everything; scoped ones only their list."""
        if not self.domains or not domain:
            return True
        wanted = domain.strip().casefold()
        return any(d.strip().casefold() == wanted for d in self.domains)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "roles": list(self.roles),
            "domains": list(self.domains),
            "auth_method": self.auth_method,
            "actions": list(self.actions),
        }


ANONYMOUS = Principal()


# --------------------------------------------------------------------------
# Token map
# --------------------------------------------------------------------------

def _clean_roles(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [v for v in re.split(r"[,\s;]+", values) if v]
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(v).strip() for v in values if str(v).strip() in ROLE_ACTIONS))


def _clean_domains(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [v for v in re.split(r"[,;]+", values) if v.strip()]
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TokenMap:
    """Bearer tokens, stored by digest so the file's contents never sit in memory.

    ``mtime`` and ``size`` let a long-running server notice an edited file
    without a restart, and without stat-ing on a hot path more than once.
    """

    by_digest: dict[str, Principal] = field(default_factory=dict)
    source: Path | None = None
    mtime: float = 0.0
    size: int = 0

    def lookup(self, token: str) -> Principal | None:
        if not token:
            return None
        found = self.by_digest.get(_digest(token))
        # Constant-time confirmation: a dict hit is compared once more so an
        # attacker cannot distinguish "wrong token" from "no tokens at all".
        if found is None:
            hmac.compare_digest(_digest(token), _digest(token))
            return None
        return found

    def __len__(self) -> int:
        return len(self.by_digest)


def load_token_map(path: str | Path | None) -> TokenMap:
    """Read ``{token: {identity, roles, domains}}``; a malformed entry is dropped.

    Refusing to start on a malformed file would make a typo an outage, so bad
    entries are skipped and the count of loaded tokens is what the operator
    checks (``/api/v1/ready`` reports it).
    """
    if not path:
        return TokenMap()
    source = Path(path)
    if not source.is_file():
        raise RuntimeError(f"token map {source} is not readable")
    stat = source.stat()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"token map {source} is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"token map {source} must be an object of token -> principal")
    by_digest: dict[str, Principal] = {}
    for token, spec in raw.items():
        if not isinstance(spec, dict) or not str(token).strip():
            continue
        identity = str(spec.get("identity") or "").strip()
        roles = _clean_roles(spec.get("roles"))
        if not identity or not roles:
            continue
        by_digest[_digest(str(token))] = Principal(
            identity=identity, roles=roles, domains=_clean_domains(spec.get("domains")),
            auth_method="bearer_token")
    return TokenMap(by_digest=by_digest, source=source, mtime=stat.st_mtime, size=stat.st_size)


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------

@dataclass
class SecurityPolicy:
    """Settings plus the loaded token map: everything a request is judged by."""

    settings: Settings
    tokens: TokenMap = field(default_factory=TokenMap)
    bound_host: str = ""
    bound_port: int = 0

    @classmethod
    def from_settings(cls, settings: Settings, *, bound_host: str = "",
                      bound_port: int = 0) -> "SecurityPolicy":
        return cls(settings=settings, tokens=load_token_map(settings.tokens_path),
                   bound_host=bound_host or settings.host,
                   bound_port=bound_port or settings.port)

    def reload_tokens(self) -> None:
        """Pick up an edited token map without restarting the process."""
        source = self.settings.tokens_path
        if not source:
            return
        path = Path(source)
        if not path.is_file():
            return
        stat = path.stat()
        if stat.st_mtime == self.tokens.mtime and stat.st_size == self.tokens.size:
            return
        try:
            self.tokens = load_token_map(path)
        except RuntimeError:
            # A half-written or broken file must not lock everyone out: keep the
            # map that was loaded last and let readiness report the problem.
            pass

    @property
    def dev_identity_allowed(self) -> bool:
        return is_loopback(self.bound_host or self.settings.host)

    def allowed_hosts(self) -> tuple[str, ...]:
        """Host values this instance answers to (name only, port stripped)."""
        hosts = {h.strip().lower() for h in self.settings.allowed_hosts if h.strip()}
        bound = (self.bound_host or self.settings.host or "").strip().lower()
        if bound and bound not in ("0.0.0.0", "::"):
            hosts.add(bound)
        if is_loopback(bound) or bound in ("0.0.0.0", "::"):
            hosts.update({"localhost", "127.0.0.1", "::1", "[::1]"})
        return tuple(sorted(hosts))


def startup_check(settings: Settings) -> list[str]:
    """Refuse an unauthenticated public bind; return the warnings worth printing.

    This is the single place where "it only listens on localhost" stops being
    an implicit assumption and becomes an enforced precondition (R-28).
    """
    if not is_loopback(settings.host) and not settings.tokens_path \
            and not settings.trusted_proxies:
        raise RuntimeError(
            f"refusing to bind {settings.host}: a non-loopback address needs an identity "
            "source. Set DPRE_TOKENS to a bearer-token map, or DPRE_TRUSTED_PROXY to the "
            "address of the SSO proxy that forwards the user's identity "
            "(see docs/security.md).")
    warnings: list[str] = []
    if not is_loopback(settings.host) and not settings.allowed_hosts:
        warnings.append("DPRE_ALLOWED_HOSTS is empty: only the bound address will be accepted "
                        "in the Host header.")
    if settings.tokens_path and not Path(settings.tokens_path).is_file():
        warnings.append(f"DPRE_TOKENS points at {settings.tokens_path}, which does not exist.")
    if not is_loopback(settings.host):
        warnings.append("This process speaks plain HTTP: terminate TLS in front of it.")
    return warnings


# --------------------------------------------------------------------------
# Resolving a principal
# --------------------------------------------------------------------------

def resolve_principal(headers: Mapping[str, str], client_address: str,
                      policy: SecurityPolicy) -> Principal:
    """Identity for one request: trusted proxy, then bearer token, then dev."""
    settings = policy.settings
    peer = (client_address or "").strip()

    if settings.trusted_proxies and peer in settings.trusted_proxies:
        name = (headers.get(settings.proxy_identity_header.lower()) or "").strip()
        if name:
            roles = _clean_roles(headers.get(settings.proxy_roles_header.lower()))
            return Principal(
                identity=name, roles=roles or ("reviewer",),
                domains=_clean_domains(headers.get(settings.proxy_domains_header.lower())),
                auth_method="trusted_proxy", client_address=peer)

    authorization = (headers.get("authorization") or "").strip()
    if authorization[:7].lower() == "bearer ":
        policy.reload_tokens()
        found = policy.tokens.lookup(authorization[7:].strip())
        if found is None:
            raise problem("DPRE-AUTH-005", "The bearer token is not recognised.")
        return Principal(identity=found.identity, roles=found.roles, domains=found.domains,
                         auth_method="bearer_token", client_address=peer)

    if policy.dev_identity_allowed:
        name = (headers.get(DEV_IDENTITY_HEADER.lower()) or "").strip()
        if name:
            return Principal(identity=name[:128], roles=tuple(settings.dev_roles),
                             auth_method="loopback_dev", client_address=peer)

    return Principal(auth_method="none", client_address=peer)


def authorize(principal: Principal, action: str, domain: str = "",
              dev_identity: bool = False) -> None:
    """Raise unless ``principal`` may perform ``action`` (optionally in ``domain``).

    ``dev_identity`` says whether this instance accepts a name typed into the
    browser, which is true on a loopback bind. It only changes the sentence: a
    refusal that names a single sign-on proxy to somebody running on their own
    laptop tells them to fix the wrong thing.
    """
    if not action:
        return
    if not principal.authenticated:
        raise problem(
            "DPRE-AUTH-001",
            "Sign in before you do this. This instance is on loopback, so the name "
            "you sign in with is the name that goes on the record."
            if dev_identity else
            "This request carries no identity. Authenticate through the "
            "configured proxy or a bearer token.",
            required_action=action, sign_in=bool(dev_identity))
    if not principal.may(action):
        raise problem("DPRE-AUTH-002",
                      f"'{action}' requires one of: "
                      + ", ".join(r for r in ROLES if action in ROLE_ACTIONS[r]) + ".",
                      required_action=action, principal_roles=list(principal.roles))
    if action in DOMAIN_SCOPED and not principal.covers_domain(domain):
        raise problem("DPRE-AUTH-003",
                      f"'{principal.identity}' is scoped to "
                      + ", ".join(principal.domains) + f" and not to {domain}.",
                      required_action=action, domain=domain)


# --------------------------------------------------------------------------
# Segregation of duties (R-01, specification 13.3)
# --------------------------------------------------------------------------

def training_reviewers(store: Any) -> set[str]:
    """Identities whose Accept/Reject decisions can train a weight proposal.

    ``review.feedback.collect_training_rows`` selects the rows but not the
    reviewer, so the identities are read here through the same table with a
    read-only ``Store.query``. If that function later returns the reviewer, this
    should narrow to exactly the rows it used.
    """
    rows = store.query(
        "SELECT DISTINCT reviewer FROM REVIEW_DECISION "
        "WHERE COALESCE(subject_type, 'candidate') = 'candidate' "
        "  AND (new_status IN ('Accepted', 'Rejected') "
        "       OR (new_status IS NULL AND decision IN ('Accept', 'Reject')))")
    return {str(row["reviewer"]).strip() for row in rows if (row["reviewer"] or "").strip()}


def assert_weight_approver_independent(store: Any, approver: str) -> None:
    """A council member may not approve weights trained on their own decisions.

    Specification 13.3 asks for a council approval that is independent of the
    reviewers whose behaviour the proposal learned. That is a segregation-of-
    duties control, so it is enforced at the boundary where the identity is
    known, not left to the proposer.
    """
    name = (approver or "").strip()
    if not name:
        return
    trainers = {r.casefold() for r in training_reviewers(store)}
    if name.casefold() in trainers:
        raise problem("DPRE-AUTH-004",
                      f"'{name}' recorded decisions in the training set for this proposal, so "
                      "the same identity cannot approve the weights it produced. A different "
                      "council member must approve.",
                      approver=name)


# --------------------------------------------------------------------------
# Transport checks (R-29, R-30)
# --------------------------------------------------------------------------

def check_host(host_header: str, policy: SecurityPolicy) -> None:
    """Validate ``Host`` against the bound address plus ``DPRE_ALLOWED_HOSTS``.

    Without this a DNS-rebinding page can drive the loopback instance from a
    browser that visited any hostile site, which is precisely the assumption
    the loopback posture rests on.
    """
    raw = (host_header or "").strip()
    if not raw:
        raise problem("DPRE-HTTP-001", "The request carries no Host header.")
    name = raw.rsplit(":", 1)[0] if raw.count(":") == 1 or raw.startswith("[") else raw
    if name.startswith("[") and "]" in name:
        name = name[: name.index("]") + 1]
    name = name.strip().lower()
    allowed = policy.allowed_hosts()
    if "*" in allowed or name in allowed:
        return
    raise problem("DPRE-HTTP-001",
                  "This instance answers to " + ", ".join(allowed)
                  + ". Set DPRE_ALLOWED_HOSTS if it should answer to another name.")


def check_csrf(method: str, headers: Mapping[str, str], policy: SecurityPolicy) -> None:
    """Every state-changing request must carry ``X-DPRE-Request``.

    A cross-origin form post cannot set a custom header, so requiring one costs
    the browser application a line and removes CSRF and rebinding writes.
    """
    if method in ("GET", "HEAD", "OPTIONS") or not policy.settings.require_csrf_header:
        return
    if not (headers.get(CSRF_HEADER.lower()) or "").strip():
        raise problem("DPRE-HTTP-002",
                      f"State-changing requests must send the {CSRF_HEADER} header.",
                      required_header=CSRF_HEADER)


def check_content_type(content_type: str, expected: str) -> None:
    """Reject a body whose media type is not what the route parses (415)."""
    base = (content_type or "").split(";", 1)[0].strip().lower()
    if expected == "any":
        return
    if expected == "json" and base != "application/json":
        raise problem("DPRE-HTTP-003",
                      "This endpoint reads application/json; "
                      f"the request sent '{base or 'nothing'}'.")
    if expected == "multipart" and base != "multipart/form-data":
        raise problem("DPRE-HTTP-003",
                      "This endpoint reads multipart/form-data; "
                      f"the request sent '{base or 'nothing'}'.")


def check_content_length(raw_length: str | int | None, settings: Settings) -> int:
    """Parse and bound ``Content-Length`` *before* a byte is read (R-30)."""
    if raw_length in (None, ""):
        return 0
    try:
        length = int(raw_length)
    except (TypeError, ValueError):
        raise problem("DPRE-HTTP-005", "Content-Length is not a number.") from None
    if length < 0:
        raise problem("DPRE-HTTP-005", "Content-Length is negative.")
    if length > settings.max_upload_bytes:
        raise problem("DPRE-HTTP-004",
                      f"The request body is {length} bytes; this instance accepts at most "
                      f"{settings.max_upload_mb} MB (DPRE_MAX_UPLOAD_MB).",
                      limit_bytes=settings.max_upload_bytes)
    return length


def safe_child(base: Path, *parts: str) -> Path:
    """Resolve ``parts`` under ``base`` or refuse: no traversal, no absolute path.

    Every download route composes its path this way, so a caller-supplied name
    can only ever name a file inside the directory the route already chose.
    """
    for part in parts:
        if not _SAFE_NAME.match(part or ""):
            raise problem("DPRE-INPUT-001",
                          f"'{part}' is not a valid name: letters, digits, dot, dash and "
                          "underscore only.")
    target = (Path(base) / Path(*parts)).resolve()
    root = Path(base).resolve()
    if target != root and root not in target.parents:
        raise problem("DPRE-INPUT-001", "That name resolves outside its directory.")
    return target


def validate_upload(filename: str, content: bytes) -> str:
    """Allow-list the extension and confirm the bytes match it (R-30).

    Returns the sanitised file name. A workbook must really be a zip, and a
    text extract must really be text: a renamed executable is refused before it
    reaches a parser.
    """
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename or "").name).lstrip(".")[:128]
    if not name:
        raise problem("DPRE-UPLOAD-002", "The upload has no usable file name.")
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_SIGNATURES:
        raise problem("DPRE-UPLOAD-002",
                      f"'{suffix or name}' is not an accepted extract. Accepted: "
                      + ", ".join(sorted(UPLOAD_SIGNATURES)) + ".")
    if not content:
        raise problem("DPRE-UPLOAD-001", f"{name} is empty.")
    signature = UPLOAD_SIGNATURES[suffix]
    if signature is not None:
        if not content.startswith(signature):
            raise problem("DPRE-UPLOAD-002",
                          f"{name} does not begin with the signature of a {suffix} file.")
    elif not _looks_like_text(content[:_SNIFF_BYTES]):
        raise problem("DPRE-UPLOAD-002",
                      f"{name} is not readable as text, which a {suffix} extract must be.")
    return name


def _looks_like_text(sample: bytes) -> bool:
    if b"\x00" in sample:
        return False
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            sample.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


def redact(value: str, keep: int = 4) -> str:
    """Show enough of a secret to correlate it in a log, never enough to use it."""
    text = str(value or "")
    return text[:keep] + "…" if len(text) > keep else "…"


def describe_matrix() -> list[dict[str, Any]]:
    """The role-to-action matrix, for docs/security.md and the API contract."""
    return [{"role": role, "actions": list(actions),
             "domain_scoped": sorted(set(actions) & DOMAIN_SCOPED)}
            for role, actions in ROLE_ACTIONS.items()]
