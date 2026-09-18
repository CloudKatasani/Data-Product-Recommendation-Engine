"""An OpenAPI 3.1 document generated from the router table (R-31).

A hand-written contract drifts from the code it describes within a release, so
the document is derived: every route registered on the router carries the path
template, the action it demands and whether it pages, and this module turns
that table into the document served at ``/api/v1/openapi.json``.

Schemas are deliberately generic objects with descriptions rather than a full
model of every payload: the payloads are the specification's own artifacts and
change with it, while the contract a client integration team codes against is
the route set, the auth scheme, the error model and the pagination envelope.
Those are exact here.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from . import errors
from .security import ACTIONS, ROLE_ACTIONS
from .settings import CSRF_HEADER, DEV_IDENTITY_HEADER

_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[^}]+)?\}")

#: Pagination is one envelope everywhere, so a client writes the loop once.
PAGE_PARAMETERS = [
    {"name": "limit", "in": "query", "required": False,
     "description": "Maximum rows to return (1-500).",
     "schema": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50}},
    {"name": "offset", "in": "query", "required": False,
     "description": "Rows to skip; use with the `page.total` of the previous response.",
     "schema": {"type": "integer", "minimum": 0, "default": 0}},
]


def path_parameters(template: str) -> list[str]:
    """Parameter names in a path template, in order of appearance."""
    return _PARAM.findall(template or "")


def public_path(template: str) -> str:
    """Strip an inline pattern from ``{industry:[a-z_]+}`` for the document."""
    return _PARAM.sub(lambda m: "{" + m.group(1) + "}", template or "")


def _operation_id(method: str, template: str) -> str:
    parts = [p for p in public_path(template).strip("/").split("/") if p]
    words: list[str] = []
    for part in parts:
        if part.startswith("{"):
            words.append("by-" + part.strip("{}"))
        else:
            words.append(part)
    slug = "-".join(words[2:] or words)  # drop the "api/v1" prefix
    return method.lower() + "-" + re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")


def _security_description() -> str:
    lines = ["Every `/api/v1` route except `health`, `ready`, `version` and this document "
             "resolves a principal and checks the role-to-action matrix.", "",
             "| role | actions |", "| --- | --- |"]
    lines += [f"| `{role}` | {', '.join(f'`{a}`' for a in actions)} |"
              for role, actions in ROLE_ACTIONS.items()]
    lines += ["", "Actions:", ""]
    lines += [f"- `{action}` — {text}" for action, text in ACTIONS.items()]
    return "\n".join(lines)


def _error_description() -> str:
    lines = ["Errors are RFC 9457 problem documents (`application/problem+json`) with a "
             "stable `code`. The `error` member repeats the title for the browser "
             "application and is deprecated.", "",
             "| code | status | title |", "| --- | --- | --- |"]
    lines += [f"| `{p['code']}` | {p['status']} | {p['title']} |" for p in errors.catalogue()]
    return "\n".join(lines)


def _components() -> dict[str, Any]:
    return {
        "securitySchemes": {
            "bearerToken": {
                "type": "http", "scheme": "bearer",
                "description": "A token from the DPRE_TOKENS map; carries identity, roles "
                               "and domain scope.",
            },
            "trustedProxy": {
                "type": "apiKey", "in": "header", "name": "X-Forwarded-User",
                "description": "Honoured only when the connection's peer address is one of "
                               "DPRE_TRUSTED_PROXY. The supported production posture.",
            },
            "devIdentity": {
                "type": "apiKey", "in": "header", "name": DEV_IDENTITY_HEADER,
                "description": "Development only: honoured when the server is bound to "
                               "loopback. Not a control.",
            },
        },
        "parameters": {
            "csrf": {
                "name": CSRF_HEADER, "in": "header", "required": True,
                "description": "Required on every state-changing request; defeats CSRF and "
                               "DNS rebinding.",
                "schema": {"type": "string", "enum": ["1"]},
            },
        },
        "schemas": {
            "Problem": {
                "type": "object",
                "description": "RFC 9457 problem document.",
                "required": ["type", "title", "status", "code"],
                "properties": {
                    "type": {"type": "string", "format": "uri"},
                    "title": {"type": "string"},
                    "status": {"type": "integer"},
                    "code": {"type": "string", "examples": ["DPRE-REVIEW-001"]},
                    "detail": {"type": "string"},
                    "instance": {"type": "string"},
                    "request_id": {"type": "string",
                                   "description": "Correlation id, echoed in X-Request-Id."},
                    "error": {"type": "string", "deprecated": True},
                },
            },
            "Page": {
                "type": "object",
                "description": "Pagination envelope returned beside every list.",
                "required": ["limit", "offset", "total", "returned"],
                "properties": {
                    "limit": {"type": "integer"}, "offset": {"type": "integer"},
                    "total": {"type": "integer"}, "returned": {"type": "integer"},
                },
            },
            "Envelope": {
                "type": "object",
                "description": "Route-specific payload; see docs/api.md for the members.",
                "additionalProperties": True,
            },
        },
        "responses": {
            "Problem": {
                "description": "Refused. See the `code` member.",
                "content": {errors.CONTENT_TYPE: {
                    "schema": {"$ref": "#/components/schemas/Problem"}}},
            },
        },
    }


def _responses_for(route: Any) -> dict[str, Any]:
    if getattr(route, "produces", "json") == "binary":
        ok = {"description": "The file.",
              "content": {"application/octet-stream": {
                  "schema": {"type": "string", "format": "binary"}}}}
    else:
        schema: dict[str, Any] = {"$ref": "#/components/schemas/Envelope"}
        if getattr(route, "paginated", False):
            schema = {"allOf": [{"$ref": "#/components/schemas/Envelope"},
                                {"type": "object",
                                 "properties": {"page": {"$ref": "#/components/schemas/Page"}}}]}
        ok = {"description": "Success.", "content": {"application/json": {"schema": schema}}}
    responses: dict[str, Any] = {"200": ok}
    if getattr(route, "action", ""):
        responses["401"] = {"$ref": "#/components/responses/Problem"}
        responses["403"] = {"$ref": "#/components/responses/Problem"}
    responses["400"] = {"$ref": "#/components/responses/Problem"}
    responses["404"] = {"$ref": "#/components/responses/Problem"}
    if route.method == "POST":
        responses["413"] = {"$ref": "#/components/responses/Problem"}
        responses["415"] = {"$ref": "#/components/responses/Problem"}
    responses["500"] = {"$ref": "#/components/responses/Problem"}
    return responses


def _request_body(route: Any) -> dict[str, Any] | None:
    if route.method != "POST":
        return None
    media = getattr(route, "request_media", "json")
    if media == "multipart":
        return {"required": True, "content": {"multipart/form-data": {"schema": {
            "type": "object",
            "properties": {"files": {"type": "array",
                                     "items": {"type": "string", "format": "binary"}}},
        }}}}
    return {"required": False, "content": {"application/json": {
        "schema": {"$ref": "#/components/schemas/Envelope"}}}}


def _operation(route: Any) -> dict[str, Any]:
    template = public_path(route.path)
    parameters: list[dict[str, Any]] = [
        {"name": name, "in": "path", "required": True,
         "description": f"`{name}` of the resource.", "schema": {"type": "string"}}
        for name in path_parameters(route.path)
    ]
    if getattr(route, "paginated", False):
        parameters += PAGE_PARAMETERS
    if route.method == "POST":
        parameters.append({"$ref": "#/components/parameters/csrf"})
    action = getattr(route, "action", "")
    description = getattr(route, "description", "") or ""
    if action:
        roles = [r for r, acts in ROLE_ACTIONS.items() if action in acts]
        description = (description + f"\n\nRequires the `{action}` action "
                       f"(roles: {', '.join(roles)}).").strip()
    operation: dict[str, Any] = {
        "operationId": _operation_id(route.method, route.path),
        "summary": getattr(route, "summary", "") or template,
        "description": description,
        "tags": [getattr(route, "tag", "") or "engine"],
        "responses": _responses_for(route),
    }
    if parameters:
        operation["parameters"] = parameters
    body = _request_body(route)
    if body:
        operation["requestBody"] = body
    if not action:
        operation["security"] = []
    return operation


def build_document(routes: Iterable[Any], *, version: str, title: str = "",
                   servers: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Turn the router table into an OpenAPI 3.1 document.

    Only the canonical ``/api/v1`` routes are documented; the unversioned
    ``/api`` aliases exist for one release for the browser application and are
    described in prose rather than duplicated here.
    """
    paths: dict[str, dict[str, Any]] = {}
    tags: dict[str, str] = {}
    for route in routes:
        template = public_path(getattr(route, "path", ""))
        if not template.startswith("/api/v1"):
            continue
        paths.setdefault(template, {})[route.method.lower()] = _operation(route)
        tag = getattr(route, "tag", "") or "engine"
        tags.setdefault(tag, f"{tag.replace('-', ' ').capitalize()} routes.")
    return {
        "openapi": "3.1.0",
        "info": {
            "title": title or "Data Product Recommendation Engine API",
            "version": version,
            "summary": "Ranked, evidence-backed data product candidates. The engine "
                       "proposes; a human decides.",
            "description": (
                "The engine never advances a candidate past `Proposed` on its own: only a "
                "reviewer decision does, and the reviewer is the authenticated principal, "
                "never a name in the body.\n\n"
                "## Authentication\n\n" + _security_description()
                + "\n\n## Errors\n\n" + _error_description()
                + "\n\n## Versioning\n\nRoutes are served under `/api/v1`. The unversioned "
                  "`/api` prefix is an alias for one release and will be withdrawn."),
            "license": {"name": "Proprietary"},
        },
        "servers": servers or [{"url": "/", "description": "This instance"}],
        "security": [{"bearerToken": []}, {"trustedProxy": []}, {"devIdentity": []}],
        "tags": [{"name": name, "description": text} for name, text in sorted(tags.items())],
        "paths": dict(sorted(paths.items())),
        "components": _components(),
    }
