"""HTTP API and browser application for the recommendation engine.

Two entry paths, as the engine is chartered: Manual, where a user supplies
Cognos, Power BI, Collibra or Alation extracts, and Automated, where the engine
generates a synthetic pack for an industry. Both land in the same pipeline, so
the review surface, the seeds and the conversational agent behave identically.
"""
from __future__ import annotations

import datetime as _dt
import json
import mimetypes
import re
import traceback
import urllib.parse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from ..chat import ConversationalAgent, SUGGESTED_QUESTIONS, describe as describe_view
from ..config import EngineConfig, ScoreWeights
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

STATIC = Path(__file__).parent / "static"


class Workspace:
    """Where a running instance keeps its database, uploads, seeds and workbooks."""

    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root)
        self.uploads = self.root / "uploads"
        self.seeds = self.root / "seeds"
        self.synthetic = self.root / "synthetic"
        for path in (self.root, self.uploads, self.seeds, self.synthetic):
            path.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.root / "engine.db")
        self.config = EngineConfig()
        self.last_run_id: str | None = self.store.latest_run_id()

    def contains(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root.resolve())
            return True
        except ValueError:
            return False


class ApiError(Exception):
    def __init__(self, status: int, message: str, detail: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


def build_router(workspace: Workspace) -> list[tuple[str, str, Callable]]:
    """Return ``(method, path pattern, handler)`` triples."""
    ws = workspace
    store = ws.store

    # ---- reference data ------------------------------------------------
    def health(_req, _match, _body):
        return {"status": "ok", "runs": len(store.runs(limit=500)),
                "latest_run": store.latest_run_id()}

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
        from .multipart import parse
        parts = parse(req["raw_body"], req["headers"].get("content-type", ""))
        saved = []
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        for part in parts:
            if not part.is_file or not part.content:
                continue
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(part.filename).name)
            target = ws.uploads / f"{stamp}-{safe}"
            target.write_bytes(part.content)
            try:
                info = inspect_file(target)
            except Exception as exc:                       # unreadable upload
                info = {"file": target.name, "path": str(target), "tables": [],
                        "error": f"{type(exc).__name__}: {exc}"}
            info["size_bytes"] = len(part.content)
            saved.append(info)
        if not saved:
            raise ApiError(400, "No file was received",
                           "Attach one or more Cognos, Power BI, Collibra or Alation extracts.")
        return {"files": saved}

    def inspect(_req, _match, body):
        path = Path(body.get("path", ""))
        if not ws.contains(path):
            raise ApiError(400, "That path is outside the workspace")
        return inspect_file(path)

    def run_manual(_req, _match, body):
        specs = []
        for entry in body.get("sources", []):
            path = Path(entry.get("path", ""))
            if not ws.contains(path):
                raise ApiError(400, f"Path outside the workspace: {path}")
            specs.append(SourceSpec(
                path=str(path), schema_key=entry.get("schema_key", ""),
                sheet=entry.get("sheet") or None, mapping=entry.get("mapping") or {},
                label=entry.get("label", "")))
        if not specs:
            raise ApiError(400, "No inputs were selected",
                           "Bind at least the KPI lineage extract to an input type.")
        as_of = _parse_date(body.get("as_of"))
        ingest = ingest_manual(specs, as_of=as_of,
                              catalog_preference=body.get("catalog"))
        if not ingest.ok and not body.get("force"):
            return {"ok": False, "ingest": ingest.to_dict(),
                    "message": "Ingestion failed validation; fix the errors or re-run with force."}
        return _execute(ingest, body, label=body.get("label", "manual run"))

    # ---- automated mode ------------------------------------------------
    def run_automated(_req, _match, body):
        industry = body.get("industry", "generic")
        if industry not in INDUSTRY_KEYS:
            raise ApiError(400, f"Unknown industry '{industry}'",
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
        industry = match.group("industry")
        if industry not in INDUSTRY_KEYS:
            raise ApiError(404, f"Unknown industry '{industry}'")
        pack = generate_pack(industry)
        target = ws.synthetic / f"synthetic_pack_{industry}.xlsx"
        write_pack_workbook(pack, target)
        return {"path": str(target), "download": f"/api/download?path={urllib.parse.quote(str(target))}",
                "manifest": pack.bundle.manifest,
                "planted_defects": pack.bundle.planted_defects}

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
    def runs(_req, _match, _body):
        return {"runs": store.runs()}

    def run_detail(_req, match, _body):
        run_id = match.group("run_id")
        run = store.run(run_id)
        if run is None:
            raise ApiError(404, f"Run {run_id} not found")
        return {"run": run}

    def run_candidates(_req, match, _body):
        run_id = match.group("run_id")
        rows = store.candidates(run_id)
        rows.sort(key=lambda r: -(r["payload"].get("score", {}) or {}).get("composite", 0))
        return {"run_id": run_id, "candidates": [_row_summary(r) for r in rows]}

    def candidate_detail(_req, match, _body):
        run_id, candidate_id = match.group("run_id"), match.group("candidate_id")
        row = store.candidate(run_id, candidate_id)
        if row is None:
            raise ApiError(404, f"Candidate {candidate_id} not found in run {run_id}")
        payload = row["payload"]
        metric_ids = set(payload.get("metric_ids", []))
        metrics = [m for m in store.metrics(run_id) if m["metric_id"] in metric_ids]
        conflicts = [c for c in store.conflicts(run_id)
                     if c["conflict_id"] in payload.get("conflicts", [])]
        seeds_dir = ws.seeds / run_id / candidate_id
        seed_files = ([{"name": p.name, "size": p.stat().st_size,
                        "download": f"/api/download?path={urllib.parse.quote(str(p))}"}
                       for p in sorted(seeds_dir.glob("*"))] if seeds_dir.exists() else [])
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

    def run_conflicts(_req, match, _body):
        return {"conflicts": store.conflicts(match.group("run_id"))}

    def run_metrics(_req, match, _body):
        metrics = store.metrics(match.group("run_id"))
        metrics.sort(key=lambda m: -m["usage_weight"])
        return {"metrics": metrics}

    def run_portfolio(_req, match, _body):
        run_id = match.group("run_id")
        run = store.run(run_id)
        if run is None:
            raise ApiError(404, f"Run {run_id} not found")
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

    def run_gaps(_req, match, _body):
        run_id = match.group("run_id")
        quarantine = store.query(
            "SELECT reason_code, COUNT(*) AS rows_affected FROM GRAPH_ER_QUARANTINE "
            "WHERE run_id = ? GROUP BY reason_code ORDER BY rows_affected DESC", (run_id,))
        examples = store.query(
            "SELECT kpi_id, raw_reference, reason_code, detail FROM GRAPH_ER_QUARANTINE "
            "WHERE run_id = ? LIMIT 200", (run_id,))
        undefined = store.query(
            "SELECT column_fqn, table_fqn, domain FROM GRAPH_NODE_COLUMN "
            "WHERE run_id = ? AND (business_term IS NULL OR business_term = '') LIMIT 200",
            (run_id,))
        stewardless = store.query(
            "SELECT metric_id, canonical_name, domain FROM KPI_CANONICAL "
            "WHERE run_id = ? AND (steward_id IS NULL OR steward_id = '')", (run_id,))
        return {"quarantine": quarantine, "examples": examples,
                "columns_without_definition": undefined,
                "metrics_without_steward": stewardless}

    def run_agents(_req, match, _body):
        run = store.run(match.group("run_id"))
        if run is None:
            raise ApiError(404, "Run not found")
        return {"agents": run.get("agent_log", []), "quality_gates": run.get("quality_gates", []),
                "warnings": run.get("warnings", [])}

    # ---- review --------------------------------------------------------
    def post_review(_req, match, body):
        run_id = match.group("run_id")
        reviewer = (body.get("reviewer") or "").strip()
        if not reviewer:
            raise ApiError(400, "A review decision must name a reviewer",
                           "Propose-only means acceptance must be attributable.")
        outcome = review(
            store, run_id, body.get("candidate_id", ""), body.get("decision", ""), reviewer,
            reason_code=body.get("reason_code", ""), note=body.get("note", ""),
            target_candidate_id=body.get("target_candidate_id", ""),
            field_overridden=body.get("field_overridden", ""),
            new_value=body.get("new_value", ""), split_by=body.get("split_by", "grain"))
        return {"outcome": outcome.to_dict()}

    def resolve_conflict(_req, match, body):
        store.resolve_conflict(match.group("run_id"), match.group("conflict_id"),
                               body.get("status", "RESOLVED"),
                               body.get("reviewer", ""), body.get("note", ""))
        return {"conflict_id": match.group("conflict_id"), "status": body.get("status", "RESOLVED")}

    def accept_name(_req, match, body):
        store.accept_metric_name(match.group("run_id"), match.group("metric_id"),
                                 body.get("reviewer", ""), body.get("name", ""))
        return {"metric_id": match.group("metric_id"), "name_status": "ACCEPTED"}

    def mark_critical(_req, match, body):
        return mark_decision_critical(store, match.group("run_id"), body.get("report_id", ""),
                                      body.get("reviewer", ""), body.get("note", ""))

    def feedback(_req, match, _body):
        return feedback_report(store, ws.config.weights)

    def approve(_req, _match, body):
        proposal = reestimate_weights(store, ws.config.weights)
        approver = (body.get("approver") or "").strip()
        if not approver:
            raise ApiError(400, "A weight version must be approved by a named council member")
        if proposal.sample_size < 1:
            raise ApiError(400, "There are no reviewer decisions to learn from yet")
        weights = approve_weights(store, proposal, approver)
        ws.config.weights = weights
        return {"weights": weights.to_dict(), "proposal": proposal.to_dict()}

    def decisions(_req, match, _body):
        return {"decisions": store.decisions(match.group("run_id"))}

    # ---- chat ----------------------------------------------------------
    def chat(_req, _match, body):
        run_id = body.get("run_id") or store.latest_run_id()
        agent = ConversationalAgent(store, run_id)
        return agent.ask(body.get("question", "")).to_dict()

    return [
        ("GET", r"^/api/health$", health),
        ("GET", r"^/api/industries$", industries),
        ("GET", r"^/api/schemas$", schemas),
        ("GET", r"^/api/seed-index$", seed_index),
        ("GET", r"^/api/semantic-view$", semantic_view),
        ("GET", r"^/api/reason-codes$", reason_codes),
        ("GET", r"^/api/config$", config_view),
        ("POST", r"^/api/config$", config_update),
        ("POST", r"^/api/upload$", upload),
        ("POST", r"^/api/inspect$", inspect),
        ("POST", r"^/api/run/manual$", run_manual),
        ("POST", r"^/api/run/automated$", run_automated),
        ("GET", r"^/api/synthetic/(?P<industry>[a-z_]+)/workbook$", synthetic_workbook),
        ("GET", r"^/api/runs$", runs),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)$", run_detail),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)/candidates$", run_candidates),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)/candidates/(?P<candidate_id>[^/]+)$", candidate_detail),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)/conflicts$", run_conflicts),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)/metrics$", run_metrics),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)/portfolio$", run_portfolio),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)/gaps$", run_gaps),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)/agents$", run_agents),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)/decisions$", decisions),
        ("GET", r"^/api/runs/(?P<run_id>[^/]+)/feedback$", feedback),
        ("POST", r"^/api/runs/(?P<run_id>[^/]+)/review$", post_review),
        ("POST", r"^/api/runs/(?P<run_id>[^/]+)/conflicts/(?P<conflict_id>[^/]+)/resolve$", resolve_conflict),
        ("POST", r"^/api/runs/(?P<run_id>[^/]+)/metrics/(?P<metric_id>[^/]+)/accept-name$", accept_name),
        ("POST", r"^/api/runs/(?P<run_id>[^/]+)/reports/decision-critical$", mark_critical),
        ("POST", r"^/api/weights/approve$", approve),
        ("POST", r"^/api/chat$", chat),
    ]


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
    }


def _parse_date(value: Any) -> _dt.date | None:
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "DataProductRecommendationEngine/1.0"
    workspace: Workspace
    routes: list[tuple[str, str, Callable]]

    def log_message(self, fmt: str, *args) -> None:       # quieter console
        if self.path.startswith("/api/") and not self.path.startswith("/api/health"):
            super().log_message(fmt, *args)

    # -- plumbing -------------------------------------------------------
    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        if content_type == "application/json":
            body = json.dumps(payload, cls=EngineEncoder).encode("utf-8")
        else:
            body = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _error(self, status: int, message: str, detail: str = "") -> None:
        self._send(status, {"error": message, "detail": detail})

    def do_GET(self) -> None:                             # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/download":
            return self._download(parsed)
        if parsed.path.startswith("/api/"):
            return self._dispatch("GET", parsed, b"")
        return self._static(parsed.path)

    def do_POST(self) -> None:                            # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        return self._dispatch("POST", parsed, raw)

    def _dispatch(self, method: str, parsed, raw: bytes) -> None:
        for route_method, pattern, handler in self.routes:
            if route_method != method:
                continue
            match = re.match(pattern, parsed.path)
            if not match:
                continue
            content_type = self.headers.get("content-type", "")
            body: dict = {}
            if raw and "application/json" in content_type:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    return self._error(400, "Request body is not valid JSON")
            request = {
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "raw_body": raw,
                "query": urllib.parse.parse_qs(parsed.query),
            }
            try:
                return self._send(200, handler(request, match, body))
            except ApiError as exc:
                return self._error(exc.status, exc.message, exc.detail)
            except (KeyError, FileNotFoundError) as exc:
                return self._error(404, str(exc))
            except PermissionError as exc:
                return self._error(403, str(exc))
            except ValueError as exc:
                return self._error(400, str(exc))
            except Exception as exc:                      # noqa: BLE001
                traceback.print_exc()
                return self._error(500, f"{type(exc).__name__}: {exc}")
        self._error(404, f"No route for {method} {parsed.path}")

    def _download(self, parsed) -> None:
        params = urllib.parse.parse_qs(parsed.query)
        raw = (params.get("path") or [""])[0]
        path = Path(raw)
        if not raw or not self.workspace.contains(path) or not path.is_file():
            return self._error(404, "File not found in the workspace")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC / name).resolve()
        try:
            target.relative_to(STATIC.resolve())
        except ValueError:
            return self._error(403, "Forbidden")
        if not target.is_file():
            target = STATIC / "index.html"
        content_type = mimetypes.guess_type(target.name)[0] or "text/plain"
        self._send(200, target.read_bytes(), content_type + "; charset=utf-8")


def serve(host: str = "127.0.0.1", port: int = 8000, root: str | Path = "data",
          quiet: bool = False) -> None:
    workspace = Workspace(root)
    Handler.workspace = workspace
    Handler.routes = build_router(workspace)
    httpd = ThreadingHTTPServer((host, port), Handler)
    if not quiet:
        print(f"Data Product Recommendation Engine on http://{host}:{port}")
        print(f"  workspace: {workspace.root.resolve()}")
        print("  Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        workspace.store.close()
