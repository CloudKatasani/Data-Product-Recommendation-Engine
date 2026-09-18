"""The API error model: RFC 9457 problem documents with stable codes (R-31).

A client integration team codes against codes, not prose. Every refusal the
HTTP surface can make is one entry in :data:`PROBLEMS`, with a fixed code, a
fixed status and a fixed title; the variable part is ``detail``, which is
written for a human and never carries an exception string, a stack frame or a
server path (R-29). A 5xx carries only a correlation id: the traceback goes to
the operator's log, and the reviewer gets an id to quote.

The document keeps the legacy ``error``/``detail`` keys alongside the RFC 9457
members so the browser application, which reads ``error``, keeps working for
the one release in which ``/api/`` remains an alias for ``/api/v1/``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONTENT_TYPE = "application/problem+json"

#: Base for the ``type`` member. It is a documentation anchor, not a network
#: location: docs/api.md carries the section each code links to.
TYPE_BASE = "https://dpre.invalid/problems/"


@dataclass(frozen=True)
class ProblemType:
    """One stable, documented failure mode of the API."""

    code: str
    status: int
    title: str

    @property
    def type_uri(self) -> str:
        return TYPE_BASE + self.code.lower()


def _problems(*entries: ProblemType) -> dict[str, ProblemType]:
    return {entry.code: entry for entry in entries}


PROBLEMS: dict[str, ProblemType] = _problems(
    # -- identity and authorisation (R-01) ------------------------------
    ProblemType("DPRE-AUTH-001", 401, "Unauthenticated"),
    ProblemType("DPRE-AUTH-002", 403, "Role does not permit this action"),
    ProblemType("DPRE-AUTH-003", 403, "Domain outside this principal's scope"),
    ProblemType("DPRE-AUTH-004", 403, "Segregation of duties"),
    ProblemType("DPRE-AUTH-005", 401, "Credential is not accepted"),
    # -- transport hardening (R-29, R-30) -------------------------------
    ProblemType("DPRE-HTTP-001", 400, "Host header is not allowed"),
    ProblemType("DPRE-HTTP-002", 403, "Missing request header"),
    ProblemType("DPRE-HTTP-003", 415, "Unsupported media type"),
    ProblemType("DPRE-HTTP-004", 413, "Payload too large"),
    ProblemType("DPRE-HTTP-005", 400, "Request body is not valid JSON"),
    ProblemType("DPRE-HTTP-006", 404, "No such route"),
    ProblemType("DPRE-HTTP-007", 400, "Invalid query parameter"),
    # -- inputs ----------------------------------------------------------
    ProblemType("DPRE-INPUT-001", 400, "Invalid parameter"),
    ProblemType("DPRE-INPUT-002", 404, "Resource not found"),
    ProblemType("DPRE-UPLOAD-001", 400, "No file was received"),
    ProblemType("DPRE-UPLOAD-002", 415, "File type is not accepted"),
    ProblemType("DPRE-UPLOAD-003", 400, "File could not be read"),
    ProblemType("DPRE-UPLOAD-004", 413, "File expands beyond the allowed bounds"),
    # -- engine semantics ------------------------------------------------
    ProblemType("DPRE-RUN-001", 400, "Ingestion failed validation"),
    ProblemType("DPRE-RUN-002", 400, "Unknown industry"),
    ProblemType("DPRE-REVIEW-001", 403, "Review decision refused"),
    ProblemType("DPRE-REVIEW-002", 400, "Review decision is not valid"),
    ProblemType("DPRE-WEIGHTS-001", 400, "Weight proposal cannot be approved"),
    ProblemType("DPRE-ADMIN-001", 400, "Administrative action refused"),
    ProblemType("DPRE-READY-001", 503, "Instance is not ready"),
    # -- catch-all -------------------------------------------------------
    ProblemType("DPRE-INTERNAL-001", 500, "Internal error"),
)

#: Exception classes the engine raises, mapped to the code they surface as.
#: Everything else becomes DPRE-INTERNAL-001 with a correlation id only.
DEFAULT_CODE_BY_STATUS = {
    400: "DPRE-INPUT-001",
    401: "DPRE-AUTH-001",
    403: "DPRE-AUTH-002",
    404: "DPRE-INPUT-002",
    413: "DPRE-HTTP-004",
    415: "DPRE-HTTP-003",
    500: "DPRE-INTERNAL-001",
}


class ProblemError(Exception):
    """An error that already knows how it should look on the wire."""

    def __init__(self, code: str, detail: str = "", *, status: int | None = None,
                 title: str = "", **extra: Any) -> None:
        problem = PROBLEMS.get(code)
        self.code = code if problem else "DPRE-INTERNAL-001"
        resolved = problem or PROBLEMS["DPRE-INTERNAL-001"]
        self.status = status or resolved.status
        self.title = title or resolved.title
        self.detail = detail
        self.type_uri = resolved.type_uri
        self.extra = {k: v for k, v in extra.items() if v not in (None, "")}
        super().__init__(f"{self.code}: {self.title}")

    def document(self, *, instance: str = "", request_id: str = "") -> dict[str, Any]:
        return problem_document(self.code, self.detail, status=self.status, title=self.title,
                                instance=instance, request_id=request_id, **self.extra)


def problem(code: str, detail: str = "", **extra: Any) -> ProblemError:
    """Build a :class:`ProblemError`; ``raise problem(...)`` reads as English."""
    return ProblemError(code, detail, **extra)


def problem_document(code: str, detail: str = "", *, status: int | None = None,
                     title: str = "", instance: str = "", request_id: str = "",
                     **extra: Any) -> dict[str, Any]:
    """An RFC 9457 problem document, plus the legacy keys the browser reads."""
    spec = PROBLEMS.get(code) or PROBLEMS["DPRE-INTERNAL-001"]
    resolved_title = title or spec.title
    document: dict[str, Any] = {
        "type": spec.type_uri,
        "title": resolved_title,
        "status": status or spec.status,
        "code": spec.code,
        "detail": detail,
        # Legacy members: the browser application reads `error` and `detail`.
        "error": resolved_title,
    }
    if instance:
        document["instance"] = instance
    if request_id:
        document["request_id"] = request_id
    for key, value in extra.items():
        if value not in (None, ""):
            document[key] = value
    return document


def status_for(code: str) -> int:
    spec = PROBLEMS.get(code)
    return spec.status if spec else 500


def code_for_status(status: int) -> str:
    """The code a legacy ``ApiError(status, ...)`` degrades to."""
    return DEFAULT_CODE_BY_STATUS.get(status, "DPRE-INTERNAL-001")


def catalogue() -> list[dict[str, Any]]:
    """Every problem type, for docs/api.md and the OpenAPI description."""
    return [{"code": p.code, "status": p.status, "title": p.title, "type": p.type_uri}
            for p in sorted(PROBLEMS.values(), key=lambda p: p.code)]
