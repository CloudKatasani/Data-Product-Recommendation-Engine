"""HTTP API and browser application for the recommendation engine.

Two entry paths, as the engine is chartered: Manual, where a user supplies
Cognos, Power BI, Collibra or Alation extracts, and Automated, where the engine
generates a synthetic pack for an industry. Both land in the same pipeline, so
the review surface, the seeds and the conversational agent behave identically.

The surface is hardened rather than trusted (R-01, R-05, R-29, R-30, R-31):

* Every request resolves a :class:`~dpre.server.security.Principal` before a
  handler runs. A reviewer, steward, council member or operator is *who the
  request authenticated as*, never a name in the body.
* Nothing is served by caller-supplied path. The database, the uploads and the
  upload registry live under ``workspace/private`` (0700) and are never served;
  seeds and synthetic workbooks are reachable only through routes keyed by run,
  candidate, seed name or industry.
* Routes are versioned under ``/api/v1`` with ``/api`` kept as an alias for one
  release, list routes page, and every refusal is an RFC 9457 problem document
  with a stable code.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import threading
import traceback
import urllib.parse
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .. import __version__
from ..chat import ConversationalAgent, SUGGESTED_QUESTIONS, describe as describe_view
from ..config import PARSER_VERSION, EngineConfig, ScoreWeights
from ..ingest import (
    RECOMMENDED_INPUTS, REQUIRED_INPUTS, SCHEMAS, SourceSpec, ingest_automated, ingest_manual,
    inspect_file,
)
from ..models import Candidate
from ..pipeline import run_pipeline
from ..review import approve_weights, feedback_report, mark_decision_critical, reestimate_weights
from ..review.workflow import REASON_CODES, review
from ..seeds import SEED_INDEX, write_seeds
from ..store import Store
from ..synth import INDUSTRY_KEYS, generate_pack, list_industries
from ..synth.workbook import write_pack_workbook
from ..util.jsonio import EngineEncoder
from ..util.xlsx import WorkbookTooLarge
from . import errors, openapi
from .errors import ProblemError, problem
from .security import (
    ANONYMOUS, Principal, SecurityPolicy, assert_weight_approver_independent, authorize,
    check_content_length, check_content_type, check_csrf, check_host, resolve_principal,
    safe_child, startup_check, validate_upload,
)
from .settings import (
    REQUEST_ID_HEADER, Settings, configure_logging, get_logger, log_event,
)

STATIC = Path(__file__).parent / "static"

#: Headers every response carries. A default-src 'self' policy means a future
#: innerHTML mistake in the browser application cannot load anything remote.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
        "object-src 'none'; img-src 'self' data:"),
}

MAX_PAGE_LIMIT = 500
DEFAULT_PAGE_LIMIT = 50


# --------------------------------------------------------------------------
# Workspace
# --------------------------------------------------------------------------

class UploadRegistry:
    """Opaque ids for uploaded extracts, so a client never handles a path (R-05).

    The registry is a small JSON file inside ``private/``: the client posts back
    an id, the server resolves the id to the file it wrote. Ids are random, so
    an upload is not guessable from a timestamp and a file name.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _write(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        _chmod(self.path, 0o600)

    def add(self, *, upload_id: str, stored_name: str, original_name: str, size: int,
            digest: str, uploaded_at: str, uploaded_by: str) -> dict:
        entry = {"upload_id": upload_id, "stored_name": stored_name,
                 "original_name": original_name, "size_bytes": size, "sha256": digest,
                 "uploaded_at": uploaded_at, "uploaded_by": uploaded_by}
        with self._lock:
            data = self._read()
            data[upload_id] = entry
            self._write(data)
        return entry

    def get(self, upload_id: str) -> dict | None:
        return self._read().get(upload_id)

    def entries(self) -> list[dict]:
        return sorted(self._read().values(), key=lambda e: e.get("uploaded_at", ""))

    def remove(self, upload_id: str) -> dict | None:
        with self._lock:
            data = self._read()
            entry = data.pop(upload_id, None)
            if entry is not None:
                self._write(data)
        return entry


def _chmod(path: Path, mode: int) -> None:
    """Best-effort permission tightening; a filesystem that refuses is not fatal."""
    try:
        os.chmod(path, mode)
    except (OSError, NotImplementedError):                 # pragma: no cover - platform
        pass


class Workspace:
    """Where a running instance keeps its database, uploads, seeds and workbooks.

    ``private/`` (0700) holds the database, the uploads and the upload registry
    and is never served; ``seeds/`` and ``synthetic/`` hold generated artifacts
    that named routes may serve (R-05).
    """

    def __init__(self, root: str | Path = "data", settings: Settings | None = None) -> None:
        self.root = Path(root)
        self.settings = (settings or Settings()).with_overrides(workspace=self.root)
        self.private = self.settings.private_dir
        self.uploads = self.private / "uploads"
        self.seeds = self.root / "seeds"
        self.synthetic = self.root / "synthetic"
        for path in (self.root, self.seeds, self.synthetic):
            path.mkdir(parents=True, exist_ok=True)
        for path in (self.private, self.uploads):
            path.mkdir(parents=True, exist_ok=True)
            _chmod(path, 0o700)
        self._adopt_legacy_database()
        self.store = Store(self.settings.database)
        _chmod(self.settings.database, 0o600)
        self.registry = UploadRegistry(self.private / "uploads.json")
        self.config = EngineConfig()
        self.last_run_id: str | None = self.store.latest_run_id()

    def _adopt_legacy_database(self) -> None:
        """Move a pre-hardening ``engine.db`` out of the served root (R-05)."""
        target = self.settings.database
        legacy = self.root / "engine.db"
        if target.exists() or not legacy.is_file() or legacy.resolve() == target.resolve():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(legacy) + suffix)
            if source.is_file():
                shutil.move(str(source), str(Path(str(target) + suffix)))

    def contains(self, path: Path) -> bool:
        """Legacy confinement check. No route serves by path any more (R-05)."""
        try:
            path.resolve().relative_to(self.root.resolve())
            return True
        except ValueError:
            return False

    def seed_dir(self, run_id: str, candidate_id: str) -> Path:
        return safe_child(safe_child(self.seeds, run_id), candidate_id)

    def close(self) -> None:
        self.store.close()


class ApiError(Exception):
    """Legacy error shape, kept so packages wired in later keep working.

    New code raises :func:`dpre.server.errors.problem` with a stable code; an
    ``ApiError`` degrades to the default code for its status.
    """

    def __init__(self, status: int, message: str, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail

    def as_problem(self) -> ProblemError:
        return ProblemError(errors.code_for_status(self.status), self.detail or self.message,
                            status=self.status, title=self.message)


@dataclass(frozen=True)
class FileResponse:
    """A handler's way of saying "send this file", without touching the socket."""

    path: Path
    filename: str = ""
    content_type: str = ""

    def resolved_type(self) -> str:
        return (self.content_type
                or mimetypes.guess_type(self.filename or self.path.name)[0]
                or "application/octet-stream")


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Route:
    """One route: how to match it, who may call it, how to document it.

    Iterating a route yields ``(method, pattern, handler)``, so code written
    against the old table of triples keeps working while the extra members feed
    authorisation (``action``/``roles``) and the OpenAPI document.
    """

    method: str
    path: str
    handler: Callable
    pattern: str = ""
    action: str = ""
    roles: tuple[str, ...] = ()
    summary: str = ""
    description: str = ""
    tag: str = "engine"
    paginated: bool = False
    request_media: str = "json"
    produces: str = "json"

    def __iter__(self) -> Iterator[Any]:
        return iter((self.method, self.pattern, self.handler))

    def __getitem__(self, index: int) -> Any:
        return (self.method, self.pattern, self.handler)[index]

    def __len__(self) -> int:
        return 3


class Router(list):
    """The route table, with :meth:`register` as the extension point.

    It *is* a list of :class:`Route`, so ``for method, pattern, handler in
    router`` still reads the way it always did; an integration step adds routes
    with ``router.register(...)`` rather than editing this module.
    """

    def register(self, method: str, path: str, handler: Callable, *, action: str = "",
                 roles: Iterable[str] = (), summary: str = "", description: str = "",
                 tag: str = "engine", paginated: bool = False, request_media: str = "json",
                 produces: str = "json") -> Route:
        route = Route(method=method.upper(), path=path, handler=handler,
                      pattern=compile_path(path), action=action, roles=tuple(roles),
                      summary=summary, description=description, tag=tag, paginated=paginated,
                      request_media=request_media, produces=produces)
        self.append(route)
        return route


def compile_path(path: str) -> str:
    """Turn ``/api/v1/runs/{run_id}`` into a regex matching ``/api`` too.

    ``{name}`` matches one segment; ``{name:regex}`` constrains it. The
    unversioned alias exists for one release so the shipped browser application
    keeps working while clients move to ``/api/v1`` (R-31).
    """
    versioned = path.startswith("/api/v1")
    body = path[len("/api/v1"):] if versioned else path
    out: list[str] = []
    for part in re.split(r"(\{[^}]+\})", body):
        if part.startswith("{") and part.endswith("}"):
            name, _, spec = part[1:-1].partition(":")
            out.append(f"(?P<{name}>{spec or '[^/]+'})")
        elif part:
            out.append(re.escape(part))
    prefix = r"^/api(?:/v1)?" if versioned else "^"
    return prefix + "".join(out) + "$"


def _page_params(request: dict, default_limit: int = DEFAULT_PAGE_LIMIT) -> tuple[int, int]:
    """``limit`` and ``offset`` from the query string, bounded and validated."""
    query = request.get("query") or {}

    def one(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = (query.get(name) or [None])[0]
        if raw in (None, ""):
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise problem("DPRE-HTTP-007", f"'{name}' must be a whole number.") from None
        if value < minimum or value > maximum:
            raise problem("DPRE-HTTP-007",
                          f"'{name}' must be between {minimum} and {maximum}.")
        return value

    return one("limit", default_limit, 1, MAX_PAGE_LIMIT), one("offset", 0, 0, 10 ** 9)


def paginate(request: dict, rows: list, default_limit: int = DEFAULT_PAGE_LIMIT
             ) -> tuple[list, dict]:
    """Slice ``rows`` and describe the slice, one envelope for every list route."""
    limit, offset = _page_params(request, default_limit)
    window = rows[offset:offset + limit]
    return window, {"limit": limit, "offset": offset, "total": len(rows),
                    "returned": len(window)}


# --------------------------------------------------------------------------
# Retention (R-26)
# --------------------------------------------------------------------------

def purge(workspace: Workspace, settings: Settings | None = None,
          as_of: _dt.date | None = None, dry_run: bool = False) -> dict:
    """Delete workspace artifacts past their retention window.

    Ledger rows are deliberately *not* deleted: ``REVIEW_DECISION`` and the
    other ledgers are hash-chained evidence that a human, not the engine, moved
    a candidate, and destroying them would destroy the control the engine is
    sold on. Retention here covers the files: uploaded client extracts, the
    generated seed packs of old runs and synthetic workbooks. A legal-hold or
    ledger-retention decision belongs to the client's records policy and is
    documented in docs/security.md.

    ``as_of`` is explicit so a purge is reproducible and testable.
    """
    config = settings or workspace.settings
    today = as_of or _dt.date.today()
    report: dict[str, Any] = {"as_of": today.isoformat(), "dry_run": dry_run,
                              "uploads": [], "seeds": [], "synthetic": [],
                              "retention_days": {"uploads": config.retention_upload_days,
                                                 "seeds": config.retention_seed_days,
                                                 "runs": config.retention_run_days}}
    cutoffs = {
        "uploads": today - _dt.timedelta(days=max(0, config.retention_upload_days)),
        "seeds": today - _dt.timedelta(days=max(0, config.retention_seed_days)),
        "synthetic": today - _dt.timedelta(days=max(0, config.retention_run_days)),
    }

    for entry in workspace.registry.entries():
        stamp = _parse_date(entry.get("uploaded_at")) or today
        if stamp > cutoffs["uploads"]:
            continue
        report["uploads"].append(entry["upload_id"])
        if not dry_run:
            _delete_upload(workspace, entry["upload_id"])

    for directory, bucket in ((workspace.seeds, "seeds"), (workspace.synthetic, "synthetic")):
        if not directory.exists():
            continue
        for child in sorted(directory.iterdir()):
            modified = _dt.date.fromtimestamp(child.stat().st_mtime)
            if modified > cutoffs[bucket]:
                continue
            report[bucket].append(child.name)
            if not dry_run:
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()

    report["deleted"] = sum(len(report[k]) for k in ("uploads", "seeds", "synthetic"))
    return report


def _delete_upload(workspace: Workspace, upload_id: str) -> None:
    entry = workspace.registry.remove(upload_id)
    if not entry:
        return
    stored = workspace.uploads / entry["stored_name"]
    try:
        if stored.is_file() and workspace.uploads.resolve() in stored.resolve().parents:
            stored.unlink()
    except OSError:                                        # pragma: no cover - platform
        pass


# --------------------------------------------------------------------------
# Router construction
# --------------------------------------------------------------------------

def build_router(workspace: Workspace) -> Router:
    """Return the route table. ``router.register(...)`` adds to it."""
    ws = workspace
    store = ws.store
    router = Router()

    # ---- liveness, readiness, contract ---------------------------------
    def health(_req, _match, _body):
        """Liveness only: no database work, so a probe cannot be the load."""
        return {"status": "ok", "service": "dpre", "version": __version__}

    def ready(_req, _match, _body):
        checks: dict[str, Any] = {}
        try:
            store.query("SELECT 1 AS ok")
            checks["database"] = "ok"
        except Exception:                                  # noqa: BLE001 - reported, not raised
            checks["database"] = "unavailable"
        tables = {row["name"] for row in store.query(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        required = {"RUN", "DP_CANDIDATE", "REVIEW_DECISION", "SCORE_WEIGHT"}
        missing = sorted(required - tables)
        checks["schema_version"] = store.schema_version()
        checks["missing_tables"] = missing
        checks["identity_sources"] = ws.settings.identity_sources()
        if checks["database"] != "ok" or missing:
            raise ProblemError("DPRE-READY-001",
                               "The database is not answering or its schema is incomplete.",
                               checks=checks)
        return {"status": "ready", "checks": checks}

    def version(_req, _match, _body):
        return {"engine_version": __version__, "parser_version": PARSER_VERSION,
                "weight_version": ws.config.weights.weight_version,
                "api_version": "v1"}

    def openapi_document(_req, _match, _body):
        return openapi.build_document(router, version=__version__)

    def whoami(req, _match, _body):
        """What the server believes about the caller: shown in the top bar."""
        return {"principal": req["principal"].to_dict()}

    # ---- reference data ------------------------------------------------
    def industries(_req, _match, _body):
        return {"industries": list_industries()}

    def schemas(_req, _match, _body):
        return {
            "schemas": [s.to_dict() for s in SCHEMAS.values()],
            "required": list(REQUIRED_INPUTS),
            "recommended": list(RECOMMENDED_INPUTS),
        }

    def seed_index(_req, _match, _body):
        return {"seeds": list(SEED_INDEX)}

    def semantic_view(_req, _match, _body):
        return {"queries": describe_view(), "suggested_questions": list(SUGGESTED_QUESTIONS)}

    def reason_codes(_req, _match, _body):
        return {"reason_codes": {k: list(v) for k, v in REASON_CODES.items()}}

    def config_view(_req, _match, _body):
        return {"config": ws.config.to_dict()}

    def config_update(_req, _match, body):
        weights = body.get("weights")
        if weights:
            ws.config.weights = ScoreWeights.from_dict(weights)
        cluster = body.get("cluster") or {}
        for key in ("min_similarity", "same_domain_bonus", "resolution"):
            if key in cluster:
                setattr(ws.config.cluster, key, float(cluster[key]))
        for key in ("min_metrics", "min_business_units"):
            if key in cluster:
                setattr(ws.config.cluster, key, int(cluster[key]))
        if "keep_counts_toward_consolidation" in body:
            ws.config.keep_counts_toward_consolidation = bool(
                body["keep_counts_toward_consolidation"])
        return {"config": ws.config.to_dict()}

    # ---- manual mode ---------------------------------------------------
    def upload(req, _match, _body):
        """Store each extract under an opaque id; never hand back a path (R-05)."""
        from .multipart import parse
        parts = parse(req["raw_body"], req["headers"].get("content-type", ""),
                      max_bytes=ws.settings.max_upload_bytes)
        saved = []
        for part in parts:
            if not part.is_file or not part.content:
                continue
            name = validate_upload(part.filename, part.content)
            upload_id = secrets.token_hex(16)
            stored_name = upload_id + Path(name).suffix.lower()
            target = ws.uploads / stored_name
            target.write_bytes(part.content)
            _chmod(target, 0o600)
            entry = ws.registry.add(
                upload_id=upload_id, stored_name=stored_name, original_name=name,
                size=len(part.content),
                digest=hashlib.sha256(part.content).hexdigest(),
                uploaded_at=req["received_at"], uploaded_by=req["principal"].identity)
            try:
                saved.append(_describe_upload(target, entry))
            except ProblemError:
                _delete_upload(ws, upload_id)              # keep nothing we refused
                raise
        if not saved:
            raise problem("DPRE-UPLOAD-001",
                          "Attach one or more Cognos, Power BI, Collibra or Alation extracts.")
        return {"files": saved}

    def _describe_upload(target: Path, entry: dict) -> dict:
        try:
            info = inspect_file(target)
        except WorkbookTooLarge as exc:                    # R-30: a decompression bomb
            raise problem("DPRE-UPLOAD-004", str(exc)) from None
        except Exception as exc:                           # noqa: BLE001 - unreadable upload
            info = {"tables": [], "error": _safe_detail(f"{type(exc).__name__}: {exc}")}
        info.pop("path", None)                             # never leak a server path
        info["file"] = entry["original_name"]
        info["upload_id"] = entry["upload_id"]
        info["size_bytes"] = entry["size_bytes"]
        info["sha256"] = entry["sha256"]
        return info

    def _upload_path(upload_id: str) -> tuple[Path, dict]:
        entry = ws.registry.get(str(upload_id or "").strip())
        if not entry:
            raise problem("DPRE-INPUT-002",
                          "That upload id is not known to this instance. Upload the extract "
                          "again; uploads are deleted after ingestion by default.")
        path = safe_child(ws.uploads, entry["stored_name"])
        if not path.is_file():
            raise problem("DPRE-INPUT-002", "The uploaded file is no longer on disk.")
        return path, entry

    def inspect_upload(_req, match, _body):
        path, entry = _upload_path(match.group("upload_id"))
        return _describe_upload(path, entry)

    def run_manual(req, _match, body):
        specs, used_ids = [], []
        for entry in body.get("sources", []):
            path, record = _upload_path(entry.get("upload_id", ""))
            used_ids.append(record["upload_id"])
            specs.append(SourceSpec(
                path=str(path), schema_key=entry.get("schema_key", ""),
                sheet=entry.get("sheet") or None, mapping=entry.get("mapping") or {},
                label=entry.get("label", "") or record["original_name"]))
        if not specs:
            raise problem("DPRE-RUN-001",
                          "Bind at least the KPI lineage extract to an input type.")
        as_of = _parse_date(body.get("as_of"))
        ingest = ingest_manual(specs, as_of=as_of, catalog_preference=body.get("catalog"))
        if not ingest.ok and not body.get("force"):
            return {"ok": False, "ingest": ingest.to_dict(),
                    "message": "Ingestion failed validation; fix the errors or re-run with force."}
        result = _execute(ingest, body, label=body.get("label", "manual run"))
        if not ws.settings.keep_uploads_after_ingest:
            for upload_id in dict.fromkeys(used_ids):      # R-26: keep no client data
                _delete_upload(ws, upload_id)
            result["uploads_deleted"] = sorted(set(used_ids))
        return result

    # ---- automated mode ------------------------------------------------
    def run_automated(_req, _match, body):
        industry = body.get("industry", "generic")
        if industry not in INDUSTRY_KEYS:
            raise problem("DPRE-RUN-002",
                          "Known industries: " + ", ".join(INDUSTRY_KEYS))
        as_of = _parse_date(body.get("as_of"))
        seed = body.get("seed")
        workbook = None
        if body.get("save_workbook", True):
            workbook = ws.synthetic / f"synthetic_pack_{industry}.xlsx"
        ingest = ingest_automated(industry, seed=int(seed) if seed else None, as_of=as_of,
                                  catalog=body.get("catalog", "collibra"),
                                  workbook_path=workbook)
        return _execute(ingest, body, label=body.get("label", f"automated: {industry}"))

    def synthetic_workbook(_req, match, _body):
        industry = _industry(match)
        pack = generate_pack(industry)
        target = ws.synthetic / f"synthetic_pack_{industry}.xlsx"
        write_pack_workbook(pack, target)
        return {"industry": industry,
                "download": f"/api/v1/synthetic/{industry}/workbook/download",
                "manifest": pack.bundle.manifest,
                "planted_defects": pack.bundle.planted_defects}

    def synthetic_workbook_download(_req, match, _body):
        industry = _industry(match)
        target = ws.synthetic / f"synthetic_pack_{industry}.xlsx"
        if not target.is_file():
            write_pack_workbook(generate_pack(industry), target)
        return FileResponse(target, filename=target.name)

    def _industry(match) -> str:
        industry = match.group("industry")
        if industry not in INDUSTRY_KEYS:
            raise problem("DPRE-RUN-002", "Known industries: " + ", ".join(INDUSTRY_KEYS))
        return industry

    def _execute(ingest, body: dict, label: str) -> dict:
        previous = ws.last_run_id
        result = run_pipeline(ingest, config=ws.config, store=store,
                              previous_run_id=previous, label=label)
        ws.last_run_id = result.run_id
        write_seeds_for_run(result)
        return {
            "ok": True,
            "run_id": result.run_id,
            "ingest": ingest.to_dict(),
            "summary": result.summary(),
            "candidates": [_candidate_row(c) for c in result.ranked()],
            "portfolio": result.portfolio,
        }

    def write_seeds_for_run(result) -> None:
        base = ws.seeds / result.run_id
        for candidate in result.candidates:
            write_seeds(candidate, result.canonical, result.graph, base,
                        result.manifest.catalog)

    # ---- run reads -----------------------------------------------------
    def runs(req, _match, _body):
        rows = store.runs(limit=MAX_PAGE_LIMIT)
        window, page = paginate(req, rows)
        return {"runs": window, "page": page}

    def run_detail(_req, match, _body):
        run_id = match.group("run_id")
        run = store.run(run_id)
        if run is None:
            raise problem("DPRE-INPUT-002", f"Run {run_id} is not in this database.")
        return {"run": run}

    def run_candidates(req, match, _body):
        run_id = match.group("run_id")
        rows = store.candidates(run_id)
        rows.sort(key=lambda r: -(r["payload"].get("score", {}) or {}).get("composite", 0))
        window, page = paginate(req, rows)
        return {"run_id": run_id, "candidates": [_row_summary(r) for r in window], "page": page}

    def candidate_detail(req, match, _body):
        run_id, candidate_id = match.group("run_id"), match.group("candidate_id")
        row = store.candidate(run_id, candidate_id)
        if row is None:
            raise problem("DPRE-INPUT-002",
                          f"Candidate {candidate_id} is not in run {run_id}.")
        payload = row["payload"]
        metric_ids = set(payload.get("metric_ids", []))
        metrics = [m for m in store.metrics(run_id) if m["metric_id"] in metric_ids]
        conflicts = [c for c in store.conflicts(run_id)
                     if c["conflict_id"] in payload.get("conflicts", [])]
        seeds_dir = ws.seeds / run_id / candidate_id
        seed_files = ([{"name": p.name, "size": p.stat().st_size,
                        "download": (f"/api/v1/runs/{urllib.parse.quote(run_id)}/candidates/"
                                     f"{urllib.parse.quote(candidate_id)}/seeds/{p.name}")}
                       for p in sorted(seeds_dir.glob("*")) if p.is_file()]
                      if seeds_dir.is_dir() else [])
        return {
            "run_id": run_id,
            "candidate": payload,
            "status": row["status"],
            "metrics": sorted(metrics, key=lambda m: -m["usage_weight"]),
            "conflicts": conflicts,
            "evidence": store.evidence(run_id, candidate_id),
            "seeds": seed_files,
            "decisions": [d for d in store.decisions(run_id)
                          if d["candidate_id"] == candidate_id],
        }

    def candidate_seed(req, match, _body):
        """Serve one seed file, keyed by run, candidate and name only (R-05)."""
        run_id, candidate_id = match.group("run_id"), match.group("candidate_id")
        row = store.candidate(run_id, candidate_id)
        if row is None:
            raise problem("DPRE-INPUT-002",
                          f"Candidate {candidate_id} is not in run {run_id}.")
        authorize(req["principal"], "download", row.get("domain") or "")
        target = safe_child(ws.seed_dir(run_id, candidate_id), match.group("name"))
        if not target.is_file():
            raise problem("DPRE-INPUT-002", "That seed has not been generated for this run.")
        return FileResponse(target, filename=target.name)

    def run_conflicts(req, match, _body):
        window, page = paginate(req, store.conflicts(match.group("run_id")), MAX_PAGE_LIMIT)
        return {"conflicts": window, "page": page}

    def run_metrics(req, match, _body):
        metrics = store.metrics(match.group("run_id"))
        metrics.sort(key=lambda m: -m["usage_weight"])
        window, page = paginate(req, metrics)
        return {"metrics": window, "page": page}

    def run_portfolio(_req, match, _body):
        run_id = match.group("run_id")
        run = store.run(run_id)
        if run is None:
            raise problem("DPRE-INPUT-002", f"Run {run_id} is not in this database.")
        rows = store.candidates(run_id)
        coverage = []
        covered: set[str] = set()
        metrics = {m["metric_id"]: m for m in store.metrics(run_id)}
        total = sum(m["usage_weight"] for m in metrics.values()) or 1.0
        ranked = sorted(rows, key=lambda r: -(r["payload"].get("score", {}) or {}).get("composite", 0))
        for index, row in enumerate(ranked, start=1):
            covered.update(row["payload"].get("metric_ids", []))
            weight = sum(metrics[m]["usage_weight"] for m in covered if m in metrics)
            coverage.append({"n": index, "candidate": row["proposed_name"],
                             "candidate_id": row["candidate_id"],
                             "cumulative_coverage": round(weight / total, 4),
                             "composite": (row["payload"].get("score", {}) or {}).get("composite", 0)})
        retirement = []
        for row in ranked:
            reports = row["payload"].get("reports", [])
            retirement.append({
                "candidate_id": row["candidate_id"], "candidate": row["proposed_name"],
                "status": row["status"],
                "fully_covered": sum(1 for r in reports if r["coverage"] >= 1.0),
                "partially_covered": sum(1 for r in reports if 0 < r["coverage"] < 1.0),
                "users_affected": sum(r["users"] for r in reports if r["coverage"] > 0),
                "by_disposition": _count_by(reports, "disposition"),
            })
        heat: dict[str, dict] = {}
        for conflict in store.conflicts(run_id):
            bucket = heat.setdefault(conflict["label"], {
                "label": conflict["label"], "competing_definitions": set(), "conflicts": 0,
                "usage_at_stake": 0.0, "patterns": set(), "open": 0})
            bucket["competing_definitions"].add(conflict["metric_id_a"])
            bucket["competing_definitions"].add(conflict["metric_id_b"])
            bucket["conflicts"] += 1
            bucket["usage_at_stake"] = max(
                bucket["usage_at_stake"],
                conflict["usage_weight_a"] + conflict["usage_weight_b"])
            bucket["patterns"].add(conflict["pattern"])
            bucket["open"] += 1 if conflict["resolution_status"] == "OPEN" else 0
        heat_rows = [{**v, "competing_definitions": len(v["competing_definitions"]),
                      "patterns": sorted(v["patterns"])} for v in heat.values()]
        heat_rows.sort(key=lambda r: -r["usage_at_stake"])
        return {
            "coverage_curve": coverage,
            "retirement_map": retirement,
            "conflict_heat_map": heat_rows,
            "status_mix": _count_by(rows, "status"),
            "archetype_mix": _count_by(rows, "archetype"),
            "tier_mix": _count_by(rows, "tier"),
            "domain_mix": _count_by(rows, "domain"),
            "stats": run.get("stats", {}),
        }

    def run_gaps(req, match, _body):
        run_id = match.group("run_id")
        limit, offset = _page_params(req, 200)
        quarantine = store.query(
            "SELECT reason_code, COUNT(*) AS rows_affected FROM GRAPH_ER_QUARANTINE "
            "WHERE run_id = ? GROUP BY reason_code ORDER BY rows_affected DESC", (run_id,))
        total = sum(row["rows_affected"] for row in quarantine)
        examples = store.query(
            "SELECT kpi_id, raw_reference, reason_code, detail FROM GRAPH_ER_QUARANTINE "
            "WHERE run_id = ? ORDER BY kpi_id LIMIT ? OFFSET ?", (run_id, limit, offset))
        undefined = store.query(
            "SELECT column_fqn, table_fqn, domain FROM GRAPH_NODE_COLUMN "
            "WHERE run_id = ? AND (business_term IS NULL OR business_term = '') "
            "ORDER BY column_fqn LIMIT ? OFFSET ?", (run_id, limit, offset))
        stewardless = store.query(
            "SELECT metric_id, canonical_name, domain FROM KPI_CANONICAL "
            "WHERE run_id = ? AND (steward_id IS NULL OR steward_id = '') "
            "ORDER BY metric_id LIMIT ? OFFSET ?", (run_id, limit, offset))
        return {"quarantine": quarantine, "examples": examples,
                "columns_without_definition": undefined,
                "metrics_without_steward": stewardless,
                "page": {"limit": limit, "offset": offset, "total": total,
                         "returned": len(examples)}}

    def run_agents(_req, match, _body):
        run = store.run(match.group("run_id"))
        if run is None:
            raise problem("DPRE-INPUT-002", "Run not found.")
        return {"agents": run.get("agent_log", []), "quality_gates": run.get("quality_gates", []),
                "warnings": run.get("warnings", [])}

    # ---- review --------------------------------------------------------
    def post_review(req, match, body):
        """The reviewer is the principal. A ``reviewer`` in the body is ignored."""
        run_id = match.group("run_id")
        principal: Principal = req["principal"]
        candidate_id = body.get("candidate_id", "")
        row = store.candidate(run_id, candidate_id)
        if row is None:
            raise problem("DPRE-INPUT-002",
                          f"Candidate {candidate_id} is not in run {run_id}.")
        authorize(principal, "review", row.get("domain") or "")
        outcome = review(
            store, run_id, candidate_id, body.get("decision", ""), principal.identity,
            reason_code=body.get("reason_code", ""), note=body.get("note", ""),
            target_candidate_id=body.get("target_candidate_id", ""),
            field_overridden=body.get("field_overridden", ""),
            new_value=body.get("new_value", ""), split_by=body.get("split_by", "grain"))
        return {"outcome": outcome.to_dict(), "reviewer": principal.identity,
                "auth_method": principal.auth_method}

    def resolve_conflict(req, match, body):
        principal: Principal = req["principal"]
        status = str(body.get("status") or "").strip()
        if not status:
            raise problem("DPRE-INPUT-001",
                          "A conflict resolution must name the status it moves to.")
        store.resolve_conflict(match.group("run_id"), match.group("conflict_id"), status,
                               principal.identity, body.get("note", ""))
        return {"conflict_id": match.group("conflict_id"), "status": status,
                "reviewer": principal.identity}

    def accept_name(req, match, body):
        principal: Principal = req["principal"]
        store.accept_metric_name(match.group("run_id"), match.group("metric_id"),
                                 principal.identity, body.get("name", ""))
        return {"metric_id": match.group("metric_id"), "name_status": "ACCEPTED",
                "reviewer": principal.identity}

    def mark_critical(req, match, body):
        return mark_decision_critical(store, match.group("run_id"), body.get("report_id", ""),
                                      req["principal"].identity, body.get("note", ""))

    # ---- programme and governance --------------------------------------
    #
    # The review session decides one candidate at a time. Everything below
    # answers the questions asked between sessions - what does this cost, what
    # ships when, what is on the risk log, can the chain be trusted - from the
    # governed tables, without composing SQL at the surface.

    def run_value(_req, match, _body):
        from ..value.model import load_values
        run_id = match.group("run_id")
        rows = load_values(store.connection, run_id)
        assumptions = store.query(
            "SELECT * FROM VALUE_ASSUMPTION ORDER BY effective_from DESC LIMIT 1")
        return {"run_id": run_id, "values": rows,
                "assumptions": assumptions[0] if assumptions else {}}

    def run_effort(_req, match, _body):
        from ..programme.effort import load_efforts
        run_id = match.group("run_id")
        return {"run_id": run_id, "efforts": load_efforts(store.connection, run_id)}

    def run_waves(_req, match, _body):
        from ..programme.waves import load_waves
        run_id = match.group("run_id")
        plan = load_waves(store.connection, run_id)
        names = {row["candidate_id"]: row["proposed_name"] for row in store.candidates(run_id)}
        for bucket in ("waves", "unscheduled"):
            for row in plan[bucket]:
                row["proposed_name"] = names.get(row["candidate_id"], row["candidate_id"])
        return {"run_id": run_id, **plan}

    def run_dependencies(req, match, _body):
        from ..programme.dependencies import load_dependencies
        run_id = match.group("run_id")
        candidate_id = (req["query"].get("candidate_id") or [None])[0]
        return {"run_id": run_id,
                "dependencies": load_dependencies(store.connection, run_id, candidate_id)}

    def run_raid(req, match, _body):
        from ..programme.raid import load_raid
        run_id = match.group("run_id")
        raid_type = (req["query"].get("type") or [None])[0]
        window, page = paginate(req, load_raid(store.connection, run_id, raid_type))
        return {"run_id": run_id, "raid": window, "page": page}

    def run_status(req, match, _body):
        from ..programme.status import status_report
        run_id = match.group("run_id")
        previous = (req["query"].get("previous_run_id") or [None])[0] \
            or store.previous_run_id(run_id)
        return status_report(store, run_id, previous)

    def run_sensitivity(_req, match, _body):
        from ..score.sensitivity import load_sensitivity
        run_id = match.group("run_id")
        return {"run_id": run_id, "sensitivity": load_sensitivity(store.connection, run_id)}

    def run_benchmark(_req, match, _body):
        from ..portfolio.benchmark import load_benchmark
        run_id = match.group("run_id")
        return {"run_id": run_id, "benchmark": load_benchmark(store.connection, run_id)}

    def run_delta(_req, match, _body):
        run_id = match.group("run_id")
        return {"run_id": run_id, "previous_run_id": store.previous_run_id(run_id),
                "rows": store.run_delta(run_id)}

    def candidate_history(_req, match, _body):
        run_id, candidate_id = match.group("run_id"), match.group("candidate_id")
        return {"run_id": run_id, "candidate_id": candidate_id,
                "status": store.status_history(run_id, candidate_id),
                "payload": store.payload_history(run_id, candidate_id)}

    def confirm_consumer_route(req, match, body):
        """The human half of gate G1 (specification section 9.2).

        Four facts a machine cannot supply: which business unit, which decision
        the data blocks, how fresh it has to be, and what happens without it.
        The record is keyed by lineage, so it survives into the next run.
        """
        from ..review.workflow import confirm_consumer
        principal: Principal = req["principal"]
        run_id, candidate_id = match.group("run_id"), match.group("candidate_id")
        row = store.candidate(run_id, candidate_id)
        if row is None:
            raise problem("DPRE-INPUT-002",
                          f"Candidate {candidate_id} is not in run {run_id}.")
        authorize(principal, "review", row.get("domain") or "")
        outcome = confirm_consumer(
            store, run_id, candidate_id, body.get("business_unit", ""),
            body.get("blocked_decision", ""), body.get("latency_tolerance", ""),
            body.get("consequence", ""), principal.identity, note=body.get("note", ""))
        return {"outcome": outcome.to_dict(), "confirmed_by": principal.identity}

    def exceptions(req, _match, _body):
        run_id = (req["query"].get("run") or [None])[0]
        return {"waivers": store.open_waivers(run_id)}

    def audit(req, _match, _body):
        from ..governance import audit_report
        return audit_report(store, (req["query"].get("run") or [None])[0])

    def transitions(_req, _match, _body):
        from ..governance import transitions_table
        return {"transitions": transitions_table()}

    def weights_view(_req, _match, _body):
        return {"current": store.current_weights().to_dict(),
                "versions": store.weight_versions()}

    def benefits(_req, _match, _body):
        from ..governance import realisation_view
        return {"realisation": realisation_view(store)}

    def benefit_event(req, _match, body):
        from ..governance import record_benefit_event
        principal: Principal = req["principal"]
        return record_benefit_event(
            store, body.get("lineage_id", ""), body.get("event_type", ""),
            body.get("subject_id", ""), body.get("occurred_at", ""),
            principal.identity, note=body.get("note", ""))

    # ---- the Assessor's findings ---------------------------------------

    def run_quality(_req, match, _body):
        """Input data quality for one run, by input and DQ dimension."""
        from ..quality.dq import load_dq_scorecard
        run_id = match.group("run_id")
        return {"run_id": run_id, "rules": load_dq_scorecard(store.connection, run_id)}

    def run_detection(_req, match, _body):
        """Planted against detected. Empty on a client estate, which is honest."""
        from ..quality.detection import load_detection_scorecard
        run_id = match.group("run_id")
        rows = load_detection_scorecard(store.connection, run_id)
        planted = sum(r["planted"] for r in rows)
        detected = sum(r["detected"] for r in rows)
        return {"run_id": run_id, "rows": rows, "planted": planted, "detected": detected,
                "recall": round(detected / planted, 4) if planted else None,
                "note": "" if rows else "nothing was planted in this estate"}

    def run_remediation(req, match, _body):
        from ..quality.remediation import load_remediation_plan
        run_id = match.group("run_id")
        rows = load_remediation_plan(store.connection, run_id)
        priority = (req["query"].get("priority") or [None])[0]
        if priority:
            rows = [row for row in rows if row["priority"] == priority]
        window, page = paginate(req, rows)
        return {"run_id": run_id, "units": window, "page": page}

    def run_stewardship(req, match, _body):
        from ..quality.stewardship import load_stewardship_requests
        run_id = match.group("run_id")
        window, page = paginate(req, load_stewardship_requests(store.connection, run_id))
        return {"run_id": run_id, "requests": window, "page": page}

    def bias(_req, _match, _body):
        """What the ranking is blind to, with the mitigation in the code."""
        from ..quality.bias import bias_register, bias_summary
        rows = bias_register()
        return {"biases": rows, "summary": bias_summary(rows)}

    def feedback(_req, _match, _body):
        return feedback_report(store, ws.config.weights)

    def approve(req, _match, _body):
        """Council approval. The approver is the principal and must be independent."""
        principal: Principal = req["principal"]
        assert_weight_approver_independent(store, principal.identity)
        proposal = reestimate_weights(store, ws.config.weights)
        if proposal.sample_size < 1:
            raise problem("DPRE-WEIGHTS-001",
                          "There are no reviewer decisions to learn from yet.")
        weights = approve_weights(store, proposal, principal.identity)
        ws.config.weights = weights
        return {"weights": weights.to_dict(), "proposal": proposal.to_dict(),
                "approver": principal.identity}

    def decisions(req, match, _body):
        window, page = paginate(req, store.decisions(match.group("run_id")))
        return {"decisions": window, "page": page}

    # ---- chat ----------------------------------------------------------
    def chat(_req, _match, body):
        run_id = body.get("run_id") or store.latest_run_id()
        agent = ConversationalAgent(store, run_id)
        return agent.ask(body.get("question", "")).to_dict()

    # ---- administration -------------------------------------------------
    def admin_purge(_req, _match, body):
        return purge(ws, ws.settings, as_of=_parse_date(body.get("as_of")),
                     dry_run=bool(body.get("dry_run")))

    # ---- the table -------------------------------------------------------
    add = router.register
    add("GET", "/api/v1/health", health, tag="operations",
        summary="Liveness probe")
    add("GET", "/api/v1/ready", ready, tag="operations",
        summary="Readiness: database ping and schema check")
    add("GET", "/api/v1/version", version, tag="operations",
        summary="Engine, parser and weight versions")
    add("GET", "/api/v1/openapi.json", openapi_document, tag="operations",
        summary="This API contract")
    add("GET", "/api/v1/whoami", whoami, action="read", tag="operations",
        summary="The principal the server resolved for this request")

    add("GET", "/api/v1/industries", industries, action="read", tag="reference",
        summary="Synthetic industries")
    add("GET", "/api/v1/schemas", schemas, action="read", tag="reference",
        summary="Accepted extract schemas and their required fields")
    add("GET", "/api/v1/seed-index", seed_index, action="read", tag="reference",
        summary="DPF stages the engine seeds")
    add("GET", "/api/v1/semantic-view", semantic_view, action="read", tag="reference",
        summary="Named queries the chat surface may select")
    add("GET", "/api/v1/reason-codes", reason_codes, action="read", tag="reference",
        summary="Valid reason codes per decision")
    add("GET", "/api/v1/config", config_view, action="read", tag="reference",
        summary="Effective engine configuration")
    add("POST", "/api/v1/config", config_update, action="configure", tag="reference",
        summary="Change weights and clustering configuration")

    add("POST", "/api/v1/upload", upload, action="run", tag="manual",
        request_media="multipart", summary="Upload extracts; returns opaque upload ids")
    add("POST", "/api/v1/uploads/{upload_id}/inspect", inspect_upload, action="run",
        tag="manual", summary="Describe an uploaded extract and its auto-mapping")
    add("POST", "/api/v1/run/manual", run_manual, action="run", tag="manual",
        summary="Run the pipeline over uploaded extracts")

    add("POST", "/api/v1/run/automated", run_automated, action="run", tag="automated",
        summary="Generate a synthetic pack and run the pipeline")
    add("GET", "/api/v1/synthetic/{industry:[a-z_]+}/workbook", synthetic_workbook,
        action="run", tag="automated", summary="Generate the synthetic workbook for an industry")
    add("GET", "/api/v1/synthetic/{industry:[a-z_]+}/workbook/download",
        synthetic_workbook_download, action="download", tag="automated", produces="binary",
        summary="Download the synthetic workbook")

    add("GET", "/api/v1/runs", runs, action="read", tag="runs", paginated=True,
        summary="Runs, newest first")
    add("GET", "/api/v1/runs/{run_id}", run_detail, action="read", tag="runs",
        summary="One run and its manifest")
    add("GET", "/api/v1/runs/{run_id}/candidates", run_candidates, action="read", tag="runs",
        paginated=True, summary="Candidates of a run, ranked by composite score")
    add("GET", "/api/v1/runs/{run_id}/candidates/{candidate_id}", candidate_detail,
        action="read", tag="runs", summary="One candidate with evidence, metrics and seeds")
    add("GET", "/api/v1/runs/{run_id}/candidates/{candidate_id}/seeds/{name}", candidate_seed,
        action="download", tag="runs", produces="binary",
        summary="Download one generated seed artifact")
    add("GET", "/api/v1/runs/{run_id}/conflicts", run_conflicts, action="read", tag="runs",
        paginated=True, summary="Definition conflicts of a run")
    add("GET", "/api/v1/runs/{run_id}/metrics", run_metrics, action="read", tag="runs",
        paginated=True, summary="Canonical metrics of a run")
    add("GET", "/api/v1/runs/{run_id}/portfolio", run_portfolio, action="read", tag="runs",
        summary="Coverage curve, retirement map and conflict heat map")
    add("GET", "/api/v1/runs/{run_id}/gaps", run_gaps, action="read", tag="runs",
        paginated=True, summary="Quarantined lineage, undefined columns, stewardless metrics")
    add("GET", "/api/v1/runs/{run_id}/agents", run_agents, action="read", tag="runs",
        summary="Agent log and quality gates of a run")
    add("GET", "/api/v1/runs/{run_id}/decisions", decisions, action="read", tag="review",
        paginated=True, summary="Review decisions recorded against a run")
    add("GET", "/api/v1/runs/{run_id}/feedback", feedback, action="read", tag="review",
        summary="Feedback loop report and the current weight proposal")

    add("POST", "/api/v1/runs/{run_id}/review", post_review, action="review", tag="review",
        summary="Record a review decision (reviewer = the authenticated principal)")
    add("POST", "/api/v1/runs/{run_id}/conflicts/{conflict_id}/resolve", resolve_conflict,
        action="steward", tag="review", summary="Adjudicate a definition conflict")
    add("POST", "/api/v1/runs/{run_id}/metrics/{metric_id}/accept-name", accept_name,
        action="steward", tag="review", summary="Accept a drafted canonical metric name")
    add("POST", "/api/v1/runs/{run_id}/reports/decision-critical", mark_critical,
        action="review", tag="review", summary="Mark a report as decision-critical")
    add("POST", "/api/v1/weights/approve", approve, action="approve_weights", tag="review",
        summary="Council approval of a proposed weight version")

    add("GET", "/api/v1/runs/{run_id}/value", run_value, action="read", tag="programme",
        summary="Money behind each candidate, with the assumption version")
    add("GET", "/api/v1/runs/{run_id}/effort", run_effort, action="read", tag="programme",
        summary="Build effort and t-shirt size per candidate, with its drivers")
    add("GET", "/api/v1/runs/{run_id}/waves", run_waves, action="read", tag="programme",
        summary="The delivery sequence and what could not be scheduled")
    add("GET", "/api/v1/runs/{run_id}/dependencies", run_dependencies, action="read",
        tag="programme", summary="What each candidate waits on")
    add("GET", "/api/v1/runs/{run_id}/raid", run_raid, action="read", tag="programme",
        paginated=True, summary="Risks, assumptions, issues and dependencies")
    add("GET", "/api/v1/runs/{run_id}/status", run_status, action="read", tag="programme",
        summary="Status against the section 14.2 success measures")
    add("GET", "/api/v1/runs/{run_id}/sensitivity", run_sensitivity, action="read",
        tag="programme", summary="How far each rank moves under a weight perturbation")
    add("GET", "/api/v1/runs/{run_id}/benchmark", run_benchmark, action="read",
        tag="programme", summary="This estate against the reference bands")
    add("GET", "/api/v1/runs/{run_id}/delta", run_delta, action="read", tag="runs",
        summary="What changed since the previous run")
    add("GET", "/api/v1/runs/{run_id}/candidates/{candidate_id}/history", candidate_history,
        action="read", tag="review", summary="Every status and payload change, with its cause")
    add("POST", "/api/v1/runs/{run_id}/candidates/{candidate_id}/confirm-consumer",
        confirm_consumer_route, action="review", tag="review",
        summary="Record the named consumer that clears gate G1")
    add("GET", "/api/v1/runs/{run_id}/quality", run_quality, action="read", tag="assessment",
        summary="Data quality of the extracts this run was fed")
    add("GET", "/api/v1/runs/{run_id}/detection", run_detection, action="read",
        tag="assessment", summary="Planted defects against what the run detected")
    add("GET", "/api/v1/runs/{run_id}/remediation", run_remediation, action="read",
        tag="assessment", paginated=True,
        summary="Lineage and catalog gaps as ranked units with an owner")
    add("GET", "/api/v1/runs/{run_id}/stewardship", run_stewardship, action="read",
        tag="assessment", paginated=True,
        summary="Metrics awaiting a steward, with the question to ask")
    add("GET", "/api/v1/bias", bias, action="read", tag="assessment",
        summary="The documented bias assessment for the ranking")

    add("GET", "/api/v1/exceptions", exceptions, action="read", tag="governance",
        summary="Gate waivers still in force")
    add("GET", "/api/v1/audit", audit, action="read", tag="governance",
        summary="Hash-chain verification and the exceptions an auditor would ask about")
    add("GET", "/api/v1/transitions", transitions, action="read", tag="governance",
        summary="The candidate status state machine")
    add("GET", "/api/v1/weights", weights_view, action="read", tag="governance",
        summary="The weight vector in force and every stored version")
    add("GET", "/api/v1/benefits", benefits, action="read", tag="governance",
        summary="Planned against realised benefit for every accepted lineage")
    add("POST", "/api/v1/benefits/events", benefit_event, action="review", tag="governance",
        summary="Record a realisation event against an accepted lineage")

    add("POST", "/api/v1/chat", chat, action="read", tag="chat",
        summary="Ask the conversational surface a named-query question")

    add("POST", "/api/v1/admin/purge", admin_purge, action="administer", tag="operations",
        summary="Delete workspace artifacts past their retention window")
    return router


# --------------------------------------------------------------------------

def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = row.get(key) or "Unassigned"
        out[value] = out.get(value, 0) + 1
    return out


def _candidate_row(candidate: Candidate) -> dict:
    score = candidate.score
    return {
        "candidate_id": candidate.candidate_id,
        "proposed_name": candidate.proposed_name,
        "purpose": candidate.purpose,
        "archetype": candidate.archetype,
        "archetype_confidence": candidate.archetype_confidence,
        "archetype_runner_up": candidate.archetype_runner_up,
        "tier": candidate.tier,
        "grain": candidate.grain,
        "domain": candidate.domain,
        "status": candidate.status,
        "origin": candidate.origin,
        "metrics": len(candidate.metric_ids),
        "consumers": len(candidate.consumers),
        "users": sum(c.users for c in candidate.consumers),
        "reports_retirable": sum(1 for r in candidate.reports if r.coverage >= 1.0),
        "conflicts": len(candidate.conflicts),
        "gaps": len(candidate.gaps),
        "name_status": candidate.name_status,
        "composite": score.composite if score else 0.0,
        "demand": score.demand if score else 0.0,
        "consolidation": score.consolidation if score else 0.0,
        "feasibility": score.feasibility if score else 0.0,
        "risk": score.risk if score else 0.0,
        "gates": [asdict(g) for g in (score.gates if score else [])],
        "critique": [asdict(f) for f in candidate.critique],
    }


def _row_summary(row: dict) -> dict:
    payload = row["payload"]
    score = payload.get("score") or {}
    return {
        "candidate_id": row["candidate_id"],
        "proposed_name": row["proposed_name"],
        "purpose": row["purpose"],
        "archetype": row["archetype"],
        "archetype_confidence": row["archetype_confidence"],
        "archetype_runner_up": row["archetype_runner_up"],
        "tier": row["tier"],
        "grain": row["grain"],
        "domain": row["domain"],
        "status": row["status"],
        "origin": row["origin"],
        "metrics": len(payload.get("metric_ids", [])),
        "consumers": len(payload.get("consumers", [])),
        "users": sum(c.get("users", 0) for c in payload.get("consumers", [])),
        "reports_retirable": sum(1 for r in payload.get("reports", []) if r.get("coverage", 0) >= 1.0),
        "conflicts": len(payload.get("conflicts", [])),
        "gaps": len(payload.get("gaps", [])),
        "name_status": row["name_status"],
        "composite": score.get("composite", 0.0),
        "demand": score.get("demand", 0.0),
        "consolidation": score.get("consolidation", 0.0),
        "feasibility": score.get("feasibility", 0.0),
        "risk": score.get("risk", 0.0),
        "gates": score.get("gates", []),
        "critique": payload.get("critique", []),
        # Programme figures, written by the Programme step. A backlog that shows
        # only a score asks a board to sequence work it cannot price.
        "size": (payload.get("effort") or {}).get("size", ""),
        "build_weeks": (payload.get("effort") or {}).get("total_weeks", 0.0),
        "annual_benefit": (payload.get("value") or {}).get("attributed_annual_benefit", 0.0),
        "payback_months": (payload.get("value") or {}).get("payback_months"),
        "currency": (payload.get("value") or {}).get("currency", ""),
        "wave": payload.get("wave"),
        "rank_low": (payload.get("rank_range") or {}).get("rank_low"),
        "rank_high": (payload.get("rank_range") or {}).get("rank_high"),
        "consumer_confirmed": bool(payload.get("consumer_confirmed", False)),
        "lineage_id": payload.get("lineage_id", ""),
    }


def _parse_date(value: Any) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


_PATHISH = re.compile(r"(?<![\w])/(?!api/)(?:[\w.\-]+/)+[\w.\-]*")


def _safe_detail(text: str, limit: int = 400) -> str:
    """Strip absolute server paths out of a message before a client sees it (R-29)."""
    return _PATHISH.sub("<path>", str(text))[:limit]


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    """One request: validate the envelope, resolve the principal, dispatch."""

    server_version = "dpre"
    sys_version = ""                                       # never advertise Python (R-29)
    protocol_version = "HTTP/1.1"
    workspace: Workspace
    routes: list
    policy: SecurityPolicy | None = None
    settings: Settings | None = None

    # -- per-request state ----------------------------------------------
    request_id: str = ""
    principal: Principal = ANONYMOUS
    _head_only: bool = False

    def log_message(self, fmt: str, *args) -> None:
        """Silence the stdlib access log; structured events replace it (R-28)."""

    def version_string(self) -> str:
        """A neutral ``Server`` header: never the Python build (R-29)."""
        return self.server_version

    # -- configuration ---------------------------------------------------
    def _settings(self) -> Settings:
        if self.settings is not None:
            return self.settings
        return getattr(self.workspace, "settings", None) or Settings()

    def _policy(self) -> SecurityPolicy:
        """Build a policy from the *bound* address when one was not supplied.

        A test or an embedder that only sets ``Handler.workspace`` and
        ``Handler.routes`` still gets Host validation and a dev identity that
        are correct for the socket actually listening.
        """
        if self.policy is not None:
            return self.policy
        address = getattr(self.server, "server_address", ("127.0.0.1", 0))
        settings = self._settings()
        type(self).policy = SecurityPolicy.from_settings(
            settings, bound_host=str(address[0]), bound_port=int(address[1]))
        return type(self).policy

    def _logger(self) -> logging.Logger:
        return get_logger()

    # -- plumbing -------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {k.lower(): v for k, v in self.headers.items()}

    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        if content_type.startswith("application/json") or content_type == errors.CONTENT_TYPE:
            body = json.dumps(payload, cls=EngineEncoder).encode("utf-8")
        else:
            body = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(REQUEST_ID_HEADER, self.request_id)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        if self._head_only:
            return
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, response: FileResponse) -> None:
        data = response.path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", response.resolved_type())
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(REQUEST_ID_HEADER, self.request_id)
        name = re.sub(r'[^A-Za-z0-9._-]', "_", response.filename or response.path.name)
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        for header, value in SECURITY_HEADERS.items():
            self.send_header(header, value)
        self.end_headers()
        if self._head_only:
            return
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _problem(self, exc: ProblemError, path: str = "") -> None:
        document = exc.document(instance=path or self.path, request_id=self.request_id)
        document["detail"] = _safe_detail(document.get("detail", ""))
        self._send(exc.status, document, errors.CONTENT_TYPE)

    def _error(self, status: int, message: str, detail: str = "") -> None:
        """Legacy shape, still used by anything that raises :class:`ApiError`."""
        self._problem(ProblemError(errors.code_for_status(status), detail or message,
                                   status=status, title=message))

    # -- entry points ----------------------------------------------------
    def do_GET(self) -> None:                             # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:                            # noqa: N802
        self._head_only = True
        try:
            self._handle("GET")
        finally:
            self._head_only = False

    def do_POST(self) -> None:                            # noqa: N802
        self._handle("POST")

    def do_OPTIONS(self) -> None:                         # noqa: N802
        """204 with no Access-Control-Allow-Origin: this API is same-origin only."""
        self.request_id = self.request_id or secrets.token_hex(8)
        self.send_response(204)
        self.send_header("Allow", "GET, HEAD, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.send_header(REQUEST_ID_HEADER, self.request_id)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()

    def _handle(self, method: str) -> None:
        started = _dt.datetime.now(_dt.timezone.utc)
        self.request_id = secrets.token_hex(8)
        self.principal = ANONYMOUS
        parsed = urllib.parse.urlparse(self.path)
        policy = self._policy()
        raw = b""
        status = 200
        try:
            check_host(self.headers.get("host", ""), policy)
            self.principal = resolve_principal(
                self._headers(), self.client_address[0] if self.client_address else "", policy)
            check_csrf(method, self._headers(), policy)
            if method == "POST":
                length = check_content_length(self.headers.get("content-length"),
                                              self._settings())
                raw = self.rfile.read(length) if length else b""
        except ProblemError as exc:
            status = exc.status
            self._problem(exc, parsed.path)
            return self._log_request(method, parsed.path, status, started)
        try:
            if parsed.path.startswith("/api/"):
                status = self._dispatch(method, parsed, raw)
            elif method == "GET":
                status = self._static(parsed.path)
            else:
                raise problem("DPRE-HTTP-006", f"No route for {method} {parsed.path}")
        except ProblemError as exc:
            status = exc.status
            self._problem(exc, parsed.path)
        self._log_request(method, parsed.path, status, started)

    def _log_request(self, method: str, path: str, status: int,
                     started: _dt.datetime) -> None:
        elapsed = (_dt.datetime.now(_dt.timezone.utc) - started).total_seconds()
        log_event(self._logger(), logging.INFO, "http_request",
                  request_id=self.request_id, method=method, path=path, status=status,
                  duration_ms=round(elapsed * 1000, 1),
                  identity=self.principal.identity, auth_method=self.principal.auth_method,
                  client=self.client_address[0] if self.client_address else "")

    # -- dispatch --------------------------------------------------------
    def _dispatch(self, method: str, parsed, raw: bytes) -> int:
        """Match the route table, authorise, then run the handler."""
        for route in self.routes:
            route_method, pattern, handler = (route.method, route.pattern, route.handler) \
                if isinstance(route, Route) else route
            if route_method != method:
                continue
            match = re.match(pattern, parsed.path)
            if not match:
                continue
            # Authorise before looking at the body: an unauthenticated caller
            # should learn nothing about what the route parses.
            self._authorize(route)
            body: dict = {}
            expected = getattr(route, "request_media", "json")
            if method == "POST":
                check_content_type(self.headers.get("content-type", ""), expected)
                if raw and expected == "json":
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        raise problem("DPRE-HTTP-005",
                                      "The body could not be parsed as JSON.") from None
                    if not isinstance(body, dict):
                        raise problem("DPRE-HTTP-005", "The body must be a JSON object.")
            request = {
                "headers": self._headers(),
                "raw_body": raw,
                "query": urllib.parse.parse_qs(parsed.query),
                "principal": self.principal,
                "request_id": self.request_id,
                "received_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                "method": method,
                "path": parsed.path,
            }
            return self._run(handler, request, match, body, parsed)
        raise problem("DPRE-HTTP-006", f"No route for {method} {parsed.path}")

    def _authorize(self, route: Any) -> None:
        """Role check at the boundary; a handler re-checks with a domain."""
        roles = tuple(getattr(route, "roles", ()) or ())
        action = getattr(route, "action", "")
        dev_identity = self._policy().dev_identity_allowed
        if roles:
            if not self.principal.authenticated:
                raise problem("DPRE-AUTH-001",
                              "Sign in before you do this." if dev_identity
                              else "This request carries no identity.",
                              sign_in=bool(dev_identity))
            if not any(self.principal.has_role(role) for role in roles):
                raise problem("DPRE-AUTH-002",
                              "This route requires one of: " + ", ".join(roles) + ".")
            return
        authorize(self.principal, action, dev_identity=dev_identity)

    def _run(self, handler: Callable, request: dict, match, body: dict, parsed) -> int:
        from ..store import ProposeOnlyError
        try:
            result = handler(request, match, body)
        except ProblemError as exc:
            self._problem(exc, parsed.path)
            return exc.status
        except ApiError as exc:
            self._problem(exc.as_problem(), parsed.path)
            return exc.status
        except ProposeOnlyError as exc:
            self._problem(ProblemError("DPRE-REVIEW-001", _safe_detail(str(exc))), parsed.path)
            return 403
        except PermissionError as exc:
            self._problem(ProblemError("DPRE-REVIEW-001", _safe_detail(str(exc))), parsed.path)
            return 403
        except KeyError as exc:
            self._problem(ProblemError("DPRE-INPUT-002", _safe_detail(str(exc))), parsed.path)
            return 404
        except FileNotFoundError:
            self._problem(ProblemError("DPRE-INPUT-002",
                                       "The requested resource does not exist."), parsed.path)
            return 404
        except ValueError as exc:
            self._problem(ProblemError("DPRE-INPUT-001", _safe_detail(str(exc))), parsed.path)
            return 400
        except Exception:                                  # noqa: BLE001
            log_event(self._logger(), logging.ERROR, "unhandled_exception",
                      request_id=self.request_id, path=parsed.path,
                      traceback=traceback.format_exc())
            self._problem(ProblemError(
                "DPRE-INTERNAL-001",
                "The engine failed to complete this request. Quote the request id to the "
                "operator; the detail is in the server log only."), parsed.path)
            return 500
        if isinstance(result, FileResponse):
            self._send_file(result)
            return 200
        self._send(200, result)
        return 200

    # -- static ----------------------------------------------------------
    def _static(self, path: str) -> int:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        try:
            target = (STATIC / name).resolve()
            target.relative_to(STATIC.resolve())
        except (ValueError, OSError):
            raise problem("DPRE-HTTP-006", "No such asset.") from None
        if not target.is_file():
            target = STATIC / "index.html"
        content_type = mimetypes.guess_type(target.name)[0] or "text/plain"
        self._send(200, target.read_bytes(), content_type + "; charset=utf-8")
        return 200


# --------------------------------------------------------------------------
# Serving
# --------------------------------------------------------------------------

def serve(host: str = "127.0.0.1", port: int = 8000, root: str | Path = "data",
          quiet: bool = False, settings: Settings | None = None) -> None:
    """Start the application, refusing an unauthenticated public bind (R-28)."""
    resolved = (settings or Settings.from_env()).with_overrides(
        workspace=Path(root), host=host, port=port)
    warnings = startup_check(resolved)
    configure_logging(resolved)
    workspace = Workspace(root, settings=resolved)
    Handler.workspace = workspace
    Handler.routes = build_router(workspace)
    Handler.settings = resolved
    Handler.policy = SecurityPolicy.from_settings(resolved, bound_host=host, bound_port=port)
    httpd = ThreadingHTTPServer((host, port), Handler)
    log_event(get_logger(), logging.INFO, "server_started", host=host, port=port,
              workspace=str(workspace.root.resolve()),
              identity_sources=resolved.identity_sources(), version=__version__)
    if not quiet:
        print(f"Data Product Recommendation Engine on http://{host}:{port}")
        print(f"  workspace: {workspace.root.resolve()}")
        print(f"  identity:  {', '.join(resolved.identity_sources()) or 'none'}")
        for warning in warnings:
            print(f"  warning:   {warning}")
        print("  Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        workspace.store.close()
