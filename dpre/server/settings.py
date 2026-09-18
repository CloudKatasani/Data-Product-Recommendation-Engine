"""Runtime settings and structured logging for a deployed instance (R-28).

A deployment is configured by environment, never by editing code: the
workspace, the bound address, the identity sources and the limits that bound an
untrusted request all resolve from ``DPRE_*`` variables, so development, test
and production differ only in their environment. ``.env.example`` documents
every variable and ``docs/deployment.md`` the postures they describe.

Logging lives here rather than beside the handler because a log line and the
response that produced it must carry the same correlation id: the handler puts
``request_id`` on every event and echoes it in ``X-Request-Id`` (R-29), so a
reviewer's complaint can be traced to a line in the operator's log without the
operator ever returning a traceback to the browser.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

#: Header a browser must send on every state-changing POST (R-29). A custom
#: header cannot be set by a cross-origin form post, so requiring it defeats
#: both classic CSRF and DNS rebinding against the loopback posture.
CSRF_HEADER = "X-DPRE-Request"

#: Header carrying the developer's chosen identity; honoured on loopback only.
DEV_IDENTITY_HEADER = "X-DPRE-Identity"

#: Response header carrying the correlation id of a request.
REQUEST_ID_HEADER = "X-Request-Id"

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1", ""})

#: Roles a loopback dev identity receives. ``council`` is deliberately absent:
#: weight approval and waivers are the segregation-of-duties sensitive actions,
#: so even in development they need a real token (docs/security.md).
DEFAULT_DEV_ROLES = ("reviewer", "steward", "operator", "catalog_admin")

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def is_loopback(host: str) -> bool:
    """True when ``host`` names this machine only.

    A non-loopback bind is what turns an unauthenticated port into an exposed
    one, so this single predicate gates dev identities and the refusal to start
    without a configured identity source.
    """
    return (host or "").strip().strip("[]").lower() in LOOPBACK_HOSTS


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    text = value.strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return default


def _as_int(value: str | None, default: int, *, minimum: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _as_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.replace(";", ",").split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """Everything a running instance needs to know about its environment.

    Frozen so a request cannot reconfigure the process; ``with_overrides``
    returns a copy for tests and for CLI flags that beat the environment.
    """

    workspace: Path = Path("data")
    db_path: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    ai_model: str = ""
    log_level: str = "INFO"
    log_format: str = "json"
    tokens_path: Path | None = None
    trusted_proxies: tuple[str, ...] = ()
    proxy_identity_header: str = "X-Forwarded-User"
    proxy_roles_header: str = "X-Forwarded-Roles"
    proxy_domains_header: str = "X-Forwarded-Groups"
    allowed_hosts: tuple[str, ...] = ()
    max_upload_mb: int = 64
    dev_roles: tuple[str, ...] = DEFAULT_DEV_ROLES
    retention_run_days: int = 365
    retention_upload_days: int = 30
    retention_seed_days: int = 365
    keep_uploads_after_ingest: bool = False
    require_csrf_header: bool = True

    # -- derived ---------------------------------------------------------
    @property
    def private_dir(self) -> Path:
        """Directory that is never served: database, uploads, upload registry."""
        return Path(self.workspace) / "private"

    @property
    def database(self) -> Path:
        return Path(self.db_path) if self.db_path else self.private_dir / "engine.db"

    @property
    def max_upload_bytes(self) -> int:
        return max(1, self.max_upload_mb) * 1024 * 1024

    @property
    def is_loopback_bind(self) -> bool:
        return is_loopback(self.host)

    @property
    def dev_identity_allowed(self) -> bool:
        """A caller may name themselves only on a loopback bind (R-01)."""
        return self.is_loopback_bind

    def with_overrides(self, **changes: Any) -> "Settings":
        clean = {k: v for k, v in changes.items() if v is not None}
        if "workspace" in clean:
            clean["workspace"] = Path(clean["workspace"])
        if "db_path" in clean:
            clean["db_path"] = Path(clean["db_path"])
        if "tokens_path" in clean:
            clean["tokens_path"] = Path(clean["tokens_path"])
        return replace(self, **clean)

    def to_public_dict(self) -> dict[str, Any]:
        """Settings safe to show an operator: paths and switches, no secrets."""
        return {
            "workspace": str(self.workspace),
            "database": str(self.database),
            "host": self.host,
            "port": self.port,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "identity_sources": self.identity_sources(),
            "allowed_hosts": list(self.allowed_hosts),
            "max_upload_mb": self.max_upload_mb,
            "retention_days": {
                "runs": self.retention_run_days,
                "uploads": self.retention_upload_days,
                "seeds": self.retention_seed_days,
            },
            "keep_uploads_after_ingest": self.keep_uploads_after_ingest,
            "require_csrf_header": self.require_csrf_header,
        }

    def identity_sources(self) -> list[str]:
        """Which identity mechanisms this configuration actually enables."""
        sources: list[str] = []
        if self.trusted_proxies:
            sources.append("trusted_proxy")
        if self.tokens_path:
            sources.append("bearer_token")
        if self.dev_identity_allowed:
            sources.append("loopback_dev_identity")
        return sources

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: Any) -> "Settings":
        """Read ``DPRE_*`` variables, then apply explicit overrides (CLI flags)."""
        src: Mapping[str, str] = os.environ if env is None else env
        base = cls(
            workspace=Path(src.get("DPRE_WORKSPACE") or "data"),
            db_path=Path(src["DPRE_DB"]) if src.get("DPRE_DB") else None,
            host=(src.get("DPRE_HOST") or "127.0.0.1").strip(),
            port=_as_int(src.get("DPRE_PORT"), 8000, minimum=1),
            ai_model=(src.get("DPRE_AI_MODEL") or "").strip(),
            log_level=(src.get("DPRE_LOG_LEVEL") or "INFO").strip().upper(),
            log_format=(src.get("DPRE_LOG_FORMAT") or "json").strip().lower(),
            tokens_path=Path(src["DPRE_TOKENS"]) if src.get("DPRE_TOKENS") else None,
            trusted_proxies=_as_tuple(src.get("DPRE_TRUSTED_PROXY")),
            proxy_identity_header=(src.get("DPRE_PROXY_IDENTITY_HEADER")
                                   or "X-Forwarded-User").strip(),
            proxy_roles_header=(src.get("DPRE_PROXY_ROLES_HEADER")
                                or "X-Forwarded-Roles").strip(),
            proxy_domains_header=(src.get("DPRE_PROXY_DOMAINS_HEADER")
                                  or "X-Forwarded-Groups").strip(),
            allowed_hosts=_as_tuple(src.get("DPRE_ALLOWED_HOSTS")),
            max_upload_mb=_as_int(src.get("DPRE_MAX_UPLOAD_MB"), 64, minimum=1),
            dev_roles=_as_tuple(src.get("DPRE_DEV_ROLES")) or DEFAULT_DEV_ROLES,
            retention_run_days=_as_int(src.get("DPRE_RETENTION_RUN_DAYS"), 365),
            retention_upload_days=_as_int(src.get("DPRE_RETENTION_UPLOAD_DAYS"), 30),
            retention_seed_days=_as_int(src.get("DPRE_RETENTION_SEED_DAYS"), 365),
            keep_uploads_after_ingest=_as_bool(src.get("DPRE_KEEP_UPLOADS"), False),
            require_csrf_header=_as_bool(src.get("DPRE_REQUIRE_CSRF_HEADER"), True),
        )
        return base.with_overrides(**overrides)


# --------------------------------------------------------------------------
# Structured logging (R-28)
# --------------------------------------------------------------------------

LOGGER_NAME = "dpre.server"

_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))


class JsonFormatter(logging.Formatter):
    """One JSON object per line on stderr, so a log shipper needs no regexes."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in ("event", "message", "asctime", "taskName"):
                continue
            payload[key] = value if _jsonable(value) else str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


class TextFormatter(logging.Formatter):
    """A readable line for a developer's terminal; the fields are identical."""

    def format(self, record: logging.LogRecord) -> str:
        extras = " ".join(
            f"{k}={v}" for k, v in sorted(record.__dict__.items())
            if k not in _RESERVED and k not in ("event", "message", "asctime", "taskName")
        )
        event = getattr(record, "event", record.getMessage())
        return f"{record.levelname:<7} {event} {extras}".rstrip()


def _jsonable(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, type(None), list, dict, tuple))


def configure_logging(settings: Settings, *, stream=None) -> logging.Logger:
    """Attach exactly one stderr handler to the engine logger and return it.

    Idempotent: re-configuring (a test, a reload) replaces the handler rather
    than adding a second one, so a line is never emitted twice.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, settings.log_level, logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonFormatter() if settings.log_format == "json" else TextFormatter())
    logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    """The engine logger, configured or not: importing must never emit output."""
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Log a named event with structured fields (never an f-string message)."""
    logger.log(level, event, extra={"event": event, **fields})
