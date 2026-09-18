"""Governed output tables (specification section 11).

Three schemas in one SQLite database: ``RAW`` counts as landed, ``GRAPH`` for
resolved nodes and edges, ``RECO`` for canonical metrics, candidates, scores,
evidence and feedback. Everything in RECO carries ``run_id`` and ``as_of_date``;
nothing is updated in place.

Two guardrails are enforced here rather than by policy text:

* Propose-only. No engine path can write a status past Proposed; only a
  ``REVIEW_DECISION`` row written by an authenticated reviewer moves it.
* Evidence required. A score row without at least one evidence row fails the
  check and the run is not published.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import ENGINE_MAX_STATUS, STATUS_ORDER, ScoreWeights
from .models import Candidate, ReviewDecision, RunManifest

SCHEMA = """
CREATE TABLE IF NOT EXISTS RUN (
    run_id TEXT PRIMARY KEY, mode TEXT, industry TEXT, catalog TEXT, as_of_date TEXT,
    started_at TEXT, finished_at TEXT, weight_version TEXT, parser_version TEXT,
    generation_id TEXT, synthetic INTEGER, published INTEGER, stats TEXT,
    quality_gates TEXT, warnings TEXT, extract_ids TEXT, agent_log TEXT, label TEXT
);
CREATE TABLE IF NOT EXISTS GRAPH_NODE_REPORT (
    run_id TEXT, report_id TEXT, name TEXT, tool TEXT, package TEXT, owner TEXT,
    business_unit TEXT, run_count_12m INTEGER, distinct_users INTEGER, last_run TEXT,
    disposition TEXT, complexity REAL, scheduled INTEGER, folder_path TEXT,
    decision_critical INTEGER,
    PRIMARY KEY (run_id, report_id)
);
CREATE TABLE IF NOT EXISTS GRAPH_NODE_KPI (
    run_id TEXT, kpi_id TEXT, report_id TEXT, label TEXT, tool TEXT, aggregation TEXT,
    expression TEXT, fingerprint TEXT, filter_fp TEXT, parse_status TEXT, grain TEXT,
    measure_scope TEXT, parse_note TEXT,
    PRIMARY KEY (run_id, kpi_id)
);
CREATE TABLE IF NOT EXISTS GRAPH_NODE_COLUMN (
    run_id TEXT, column_fqn TEXT, table_fqn TEXT, system TEXT, data_type TEXT,
    pk_flag INTEGER, sensitivity TEXT, pii_flag INTEGER, business_term TEXT,
    definition TEXT, steward_id TEXT, domain TEXT,
    PRIMARY KEY (run_id, column_fqn)
);
CREATE TABLE IF NOT EXISTS GRAPH_NODE_TABLE (
    run_id TEXT, table_fqn TEXT, system TEXT, sor_flag INTEGER, lifecycle_status TEXT,
    inferred_grain TEXT, grain_source TEXT, domain TEXT, row_count INTEGER,
    measure_count INTEGER, sunset_date TEXT, successor_system TEXT,
    PRIMARY KEY (run_id, table_fqn)
);
CREATE TABLE IF NOT EXISTS GRAPH_EDGE_KPI_COLUMN (
    run_id TEXT, kpi_id TEXT, column_fqn TEXT, role TEXT, er_rule TEXT, confidence REAL,
    raw_reference TEXT
);
CREATE TABLE IF NOT EXISTS GRAPH_ER_QUARANTINE (
    run_id TEXT, kpi_id TEXT, raw_reference TEXT, reason_code TEXT, detail TEXT, role TEXT
);
CREATE TABLE IF NOT EXISTS KPI_CANONICAL (
    run_id TEXT, metric_id TEXT, canonical_name TEXT, definition TEXT, fingerprint TEXT,
    grain TEXT, aggregation TEXT, steward_id TEXT, steward_source TEXT, name_status TEXT,
    domain TEXT, sub_domain TEXT, usage_weight REAL, report_count INTEGER,
    consumer_breadth INTEGER, variant_count INTEGER, opaque INTEGER, tools TEXT,
    operand_columns TEXT, source_tables TEXT, kpi_ids TEXT, labels TEXT,
    PRIMARY KEY (run_id, metric_id)
);
CREATE TABLE IF NOT EXISTS KPI_VARIANT (
    run_id TEXT, kpi_id TEXT, metric_id TEXT, tier TEXT, filter_fp TEXT,
    filter_expression TEXT, variant_label TEXT
);
CREATE TABLE IF NOT EXISTS KPI_CONFLICT (
    run_id TEXT, conflict_id TEXT, label TEXT, metric_id_a TEXT, metric_id_b TEXT,
    usage_weight_a REAL, usage_weight_b REAL, difference_summary TEXT, pattern TEXT,
    resolution_status TEXT, steward_id TEXT, similarity REAL,
    semantic_model_decision TEXT, reports_a TEXT, reports_b TEXT,
    expression_a TEXT, expression_b TEXT,
    PRIMARY KEY (run_id, conflict_id)
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE (
    run_id TEXT, candidate_id TEXT, proposed_name TEXT, purpose TEXT, archetype TEXT,
    tier TEXT, grain TEXT, domain TEXT, sub_domain TEXT, owner_candidate TEXT,
    steward_candidate TEXT, status TEXT, archetype_confidence REAL,
    archetype_runner_up TEXT, tier_confidence REAL, name_status TEXT,
    parent_candidate_id TEXT, origin TEXT, as_of_date TEXT, payload TEXT,
    PRIMARY KEY (run_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_METRIC (run_id TEXT, candidate_id TEXT, metric_id TEXT);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_SOURCE (
    run_id TEXT, candidate_id TEXT, table_fqn TEXT, system TEXT, share_of_metrics REAL,
    sor_flag INTEGER, lifecycle_status TEXT
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_CONSUMER (
    run_id TEXT, candidate_id TEXT, business_unit TEXT, users INTEGER,
    report_count INTEGER, scheduled_share REAL, cadence TEXT
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_REPORT (
    run_id TEXT, candidate_id TEXT, report_id TEXT, coverage REAL, disposition TEXT,
    users INTEGER, last_run TEXT
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_SCORE (
    run_id TEXT, candidate_id TEXT, weight_version TEXT, demand REAL, consolidation REAL,
    feasibility REAL, risk REAL, composite REAL, gate_results TEXT, features TEXT,
    PRIMARY KEY (run_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_EVIDENCE (
    run_id TEXT, candidate_id TEXT, feature TEXT, evidence_type TEXT, evidence_id TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_CRITIQUE (
    run_id TEXT, candidate_id TEXT, criterion TEXT, finding TEXT, severity TEXT
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_NARRATIVE (
    run_id TEXT, candidate_id TEXT, purpose TEXT, value_hypothesis TEXT,
    decisions TEXT, status TEXT,
    PRIMARY KEY (run_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS REVIEW_DECISION (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, candidate_id TEXT,
    decision TEXT, reason_code TEXT, reviewer TEXT, decided_at TEXT,
    field_overridden TEXT, new_value TEXT, target_candidate_id TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS SCORE_WEIGHT (
    weight_version TEXT, dimension TEXT, feature TEXT, weight REAL, effective_from TEXT,
    note TEXT, approved_by TEXT
);
CREATE INDEX IF NOT EXISTS IX_CAND_RUN ON DP_CANDIDATE (run_id, status);
CREATE INDEX IF NOT EXISTS IX_EVIDENCE ON DP_CANDIDATE_EVIDENCE (run_id, candidate_id);
CREATE INDEX IF NOT EXISTS IX_VARIANT ON KPI_VARIANT (run_id, metric_id);
"""


class ProposeOnlyError(PermissionError):
    """Raised when anything but a reviewer decision tries to move a status."""


class EvidenceMissingError(ValueError):
    """Raised when a score row would be written without evidence behind it."""


def _json(value: Any) -> str:
    def default(obj):
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        if isinstance(obj, (_dt.date, _dt.datetime)):
            return obj.isoformat()
        return str(obj)
    return json.dumps(value, default=default)


class Store:
    """SQLite-backed RECO tables. One database can hold many runs."""

    def __init__(self, path: str | Path = "data/engine.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    # -- lifecycle ------------------------------------------------------
    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- writes ---------------------------------------------------------
    def save_run(self, manifest: RunManifest, label: str = "") -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO RUN VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (manifest.run_id, manifest.mode, manifest.industry, manifest.catalog,
             manifest.as_of_date, manifest.started_at, manifest.finished_at,
             manifest.weight_version, manifest.parser_version, manifest.generation_id,
             int(manifest.synthetic), int(manifest.published), _json(manifest.stats),
             _json(manifest.quality_gates), _json(manifest.warnings),
             _json(manifest.extract_ids), _json(manifest.agent_log), label),
        )
        self.connection.commit()

    def save_weights(self, weights: ScoreWeights) -> None:
        self.connection.execute("DELETE FROM SCORE_WEIGHT WHERE weight_version = ?",
                                (weights.weight_version,))
        for row in weights.rows():
            self.connection.execute(
                "INSERT INTO SCORE_WEIGHT VALUES (?,?,?,?,?,?,?)",
                (row["weight_version"], row["dimension"], row["feature"], row["weight"],
                 row["effective_from"], weights.note, weights.approved_by))
        self.connection.commit()

    def save_graph(self, run_id: str, graph) -> None:
        cur = self.connection
        for report in graph.reports.values():
            cur.execute("INSERT OR REPLACE INTO GRAPH_NODE_REPORT VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, report.report_id, report.report_name, report.tool,
                         report.semantic_container, report.owner, report.business_unit,
                         report.run_count_12m, report.distinct_users_12m,
                         report.last_run_date.isoformat() if report.last_run_date else "",
                         report.disposition, report.complexity_score,
                         int(report.schedule_flag), report.folder_path,
                         int(report.decision_critical)))
        for kpi in graph.kpis.values():
            cur.execute("INSERT OR REPLACE INTO GRAPH_NODE_KPI VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, kpi.kpi_id, kpi.report_id, kpi.label, kpi.tool,
                         kpi.aggregation, kpi.expression, kpi.fingerprint, kpi.filter_fp,
                         kpi.parse_status, kpi.grain, kpi.measure_scope, kpi.parse_note))
        for column in graph.columns.values():
            cur.execute("INSERT OR REPLACE INTO GRAPH_NODE_COLUMN VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, column.column_fqn, column.table_fqn, column.system,
                         column.data_type, int(column.pk_flag), column.sensitivity,
                         int(column.pii_flag), column.business_term, column.definition,
                         column.steward_id, column.domain))
        for table in graph.tables.values():
            cur.execute("INSERT OR REPLACE INTO GRAPH_NODE_TABLE VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, table.table_fqn, table.system, int(table.sor_flag),
                         table.lifecycle_status, table.inferred_grain, table.grain_source,
                         table.domain, table.row_count, table.measure_count,
                         table.sunset_date, table.successor_system))
        cur.executemany("INSERT INTO GRAPH_EDGE_KPI_COLUMN VALUES (?,?,?,?,?,?,?)",
                        [(run_id, e.kpi_id, e.column_fqn, e.role, e.er_rule, e.confidence,
                          e.raw_reference) for e in graph.edges_kpi_column])
        cur.executemany("INSERT INTO GRAPH_ER_QUARANTINE VALUES (?,?,?,?,?,?)",
                        [(run_id, q.kpi_id, q.raw_reference, q.reason_code, q.detail, q.role)
                         for q in graph.quarantine])
        cur.commit()

    def save_canonicalization(self, run_id: str, result) -> None:
        cur = self.connection
        for metric in result.metrics.values():
            cur.execute(
                "INSERT OR REPLACE INTO KPI_CANONICAL VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, metric.metric_id, metric.canonical_name, metric.definition,
                 metric.fingerprint, metric.grain, metric.aggregation, metric.steward_id,
                 metric.steward_source, metric.name_status, metric.domain, metric.sub_domain,
                 metric.usage_weight, metric.report_count, metric.consumer_breadth,
                 metric.variant_count, int(metric.opaque), _json(metric.tools),
                 _json(metric.operand_columns), _json(metric.source_tables),
                 _json(metric.kpi_ids), _json(metric.labels)))
        cur.executemany("INSERT INTO KPI_VARIANT VALUES (?,?,?,?,?,?,?)",
                        [(run_id, v.kpi_id, v.metric_id, v.tier, v.filter_fp,
                          v.filter_expression, v.variant_label) for v in result.variants])
        for conflict in result.conflicts:
            cur.execute("INSERT OR REPLACE INTO KPI_CONFLICT VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, conflict.conflict_id, conflict.label, conflict.metric_id_a,
                         conflict.metric_id_b, conflict.usage_weight_a, conflict.usage_weight_b,
                         conflict.difference_summary, conflict.pattern,
                         conflict.resolution_status, conflict.steward_id, conflict.similarity,
                         conflict.semantic_model_decision, _json(conflict.reports_a),
                         _json(conflict.reports_b), conflict.expression_a,
                         conflict.expression_b))
        cur.commit()

    def save_candidates(self, run_id: str, candidates: Iterable[Candidate]) -> None:
        """Write candidates, their scores and evidence. Propose-only is enforced here."""
        candidates = list(candidates)
        for candidate in candidates:
            if STATUS_ORDER.index(candidate.status) > STATUS_ORDER.index(ENGINE_MAX_STATUS):
                raise ProposeOnlyError(
                    f"{candidate.candidate_id}: the engine cannot write status "
                    f"'{candidate.status}'; only a reviewer decision can move a candidate "
                    f"past {ENGINE_MAX_STATUS}")
            if candidate.score is not None and not candidate.evidence:
                raise EvidenceMissingError(
                    f"{candidate.candidate_id}: a score row must carry at least one "
                    "evidence row")
        cur = self.connection
        for candidate in candidates:
            payload = _candidate_payload(candidate)
            cur.execute("INSERT OR REPLACE INTO DP_CANDIDATE VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_id, candidate.candidate_id, candidate.proposed_name,
                         candidate.purpose, candidate.archetype, candidate.tier,
                         candidate.grain, candidate.domain, candidate.sub_domain,
                         candidate.owner_candidate, candidate.steward_candidate,
                         candidate.status, candidate.archetype_confidence,
                         candidate.archetype_runner_up, candidate.tier_confidence,
                         candidate.name_status, candidate.parent_candidate_id,
                         candidate.origin, candidate.as_of_date, _json(payload)))
            cur.executemany("INSERT INTO DP_CANDIDATE_METRIC VALUES (?,?,?)",
                            [(run_id, candidate.candidate_id, m) for m in candidate.metric_ids])
            cur.executemany("INSERT INTO DP_CANDIDATE_SOURCE VALUES (?,?,?,?,?,?,?)",
                            [(run_id, candidate.candidate_id, s.table_fqn, s.system,
                              s.share_of_metrics, int(s.sor_flag), s.lifecycle_status)
                             for s in candidate.sources])
            cur.executemany("INSERT INTO DP_CANDIDATE_CONSUMER VALUES (?,?,?,?,?,?,?)",
                            [(run_id, candidate.candidate_id, c.business_unit, c.users,
                              c.report_count, c.scheduled_share, c.cadence)
                             for c in candidate.consumers])
            cur.executemany("INSERT INTO DP_CANDIDATE_REPORT VALUES (?,?,?,?,?,?,?)",
                            [(run_id, candidate.candidate_id, r.report_id, r.coverage,
                              r.disposition, r.users, r.last_run) for r in candidate.reports])
            if candidate.score:
                cur.execute("INSERT OR REPLACE INTO DP_CANDIDATE_SCORE VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (run_id, candidate.candidate_id, candidate.score.weight_version,
                             candidate.score.demand, candidate.score.consolidation,
                             candidate.score.feasibility, candidate.score.risk,
                             candidate.score.composite, _json(candidate.score.gates),
                             _json(candidate.score.features)))
            cur.executemany("INSERT INTO DP_CANDIDATE_EVIDENCE VALUES (?,?,?,?,?,?)",
                            [(run_id, e.candidate_id, e.feature, e.evidence_type,
                              e.evidence_id, e.detail) for e in candidate.evidence])
            cur.executemany("INSERT INTO DP_CANDIDATE_CRITIQUE VALUES (?,?,?,?,?)",
                            [(run_id, f.candidate_id, f.criterion, f.finding, f.severity)
                             for f in candidate.critique])
            cur.execute("INSERT OR REPLACE INTO DP_CANDIDATE_NARRATIVE VALUES (?,?,?,?,?,?)",
                        (run_id, candidate.candidate_id, candidate.purpose,
                         candidate.narrative.get("value_hypothesis", ""),
                         _json([asdict(d) for d in candidate.decisions_drafted]),
                         "AI_DRAFT"))
        cur.commit()

    # -- reviewer -------------------------------------------------------
    def record_decision(self, decision: ReviewDecision) -> ReviewDecision:
        """The only path that can move a candidate past Proposed."""
        if not decision.reviewer:
            raise ProposeOnlyError("a review decision must name an authenticated reviewer")
        if decision.decision not in ("Accept", "Reject", "Merge", "Split", "Defer", "Override"):
            raise ValueError(f"unknown decision '{decision.decision}'")
        decision.decided_at = decision.decided_at or _dt.datetime.now().isoformat(timespec="seconds")
        self.connection.execute(
            "INSERT INTO REVIEW_DECISION (run_id, candidate_id, decision, reason_code, "
            "reviewer, decided_at, field_overridden, new_value, target_candidate_id, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (decision.run_id, decision.candidate_id, decision.decision, decision.reason_code,
             decision.reviewer, decision.decided_at, decision.field_overridden,
             decision.new_value, decision.target_candidate_id, decision.note))
        status = {
            "Accept": "Accepted", "Reject": "Rejected", "Merge": "Merged",
            "Split": "Proposed", "Defer": "Deferred",
        }.get(decision.decision)
        if status:
            self.connection.execute(
                "UPDATE DP_CANDIDATE SET status = ? WHERE run_id = ? AND candidate_id = ?",
                (status, decision.run_id, decision.candidate_id))
        if decision.decision == "Override" and decision.field_overridden:
            allowed = {"archetype", "tier", "proposed_name", "grain", "owner_candidate",
                       "steward_candidate", "name_status", "domain"}
            if decision.field_overridden in allowed:
                self.connection.execute(
                    f"UPDATE DP_CANDIDATE SET {decision.field_overridden} = ? "
                    "WHERE run_id = ? AND candidate_id = ?",
                    (decision.new_value, decision.run_id, decision.candidate_id))
        self.connection.commit()
        return decision

    def resolve_conflict(self, run_id: str, conflict_id: str, status: str,
                         reviewer: str, note: str = "") -> None:
        if not reviewer:
            raise ProposeOnlyError("resolving a conflict requires an authenticated steward")
        self.connection.execute(
            "UPDATE KPI_CONFLICT SET resolution_status = ? WHERE run_id = ? AND conflict_id = ?",
            (status, run_id, conflict_id))
        self.connection.commit()

    def accept_metric_name(self, run_id: str, metric_id: str, reviewer: str,
                           new_name: str = "") -> None:
        if not reviewer:
            raise ProposeOnlyError("accepting an AI-drafted name requires a steward")
        if new_name:
            self.connection.execute(
                "UPDATE KPI_CANONICAL SET canonical_name = ?, name_status = 'ACCEPTED' "
                "WHERE run_id = ? AND metric_id = ?", (new_name, run_id, metric_id))
        else:
            self.connection.execute(
                "UPDATE KPI_CANONICAL SET name_status = 'ACCEPTED' "
                "WHERE run_id = ? AND metric_id = ?", (run_id, metric_id))
        self.connection.commit()

    # -- reads ----------------------------------------------------------
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        rows = self.connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def runs(self, limit: int = 50) -> list[dict]:
        rows = self.query("SELECT * FROM RUN ORDER BY started_at DESC LIMIT ?", (limit,))
        for row in rows:
            for key in ("stats", "quality_gates", "warnings", "extract_ids", "agent_log"):
                row[key] = json.loads(row[key] or "null")
        return rows

    def run(self, run_id: str) -> dict | None:
        rows = self.query("SELECT * FROM RUN WHERE run_id = ?", (run_id,))
        if not rows:
            return None
        row = rows[0]
        for key in ("stats", "quality_gates", "warnings", "extract_ids", "agent_log"):
            row[key] = json.loads(row[key] or "null")
        return row

    def latest_run_id(self) -> str | None:
        rows = self.query("SELECT run_id FROM RUN ORDER BY started_at DESC LIMIT 1")
        return rows[0]["run_id"] if rows else None

    def candidates(self, run_id: str, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM DP_CANDIDATE WHERE run_id = ?"
        params: tuple = (run_id,)
        if status:
            sql += " AND status = ?"
            params = (run_id, status)
        rows = self.query(sql, params)
        for row in rows:
            row["payload"] = json.loads(row["payload"] or "{}")
        return rows

    def candidate(self, run_id: str, candidate_id: str) -> dict | None:
        rows = self.candidates(run_id)
        for row in rows:
            if row["candidate_id"] == candidate_id:
                return row
        return None

    def metrics(self, run_id: str) -> list[dict]:
        rows = self.query("SELECT * FROM KPI_CANONICAL WHERE run_id = ?", (run_id,))
        for row in rows:
            for key in ("tools", "operand_columns", "source_tables", "kpi_ids", "labels"):
                row[key] = json.loads(row[key] or "[]")
        return rows

    def conflicts(self, run_id: str) -> list[dict]:
        rows = self.query("SELECT * FROM KPI_CONFLICT WHERE run_id = ? "
                          "ORDER BY (usage_weight_a + usage_weight_b) DESC", (run_id,))
        for row in rows:
            row["reports_a"] = json.loads(row["reports_a"] or "[]")
            row["reports_b"] = json.loads(row["reports_b"] or "[]")
        return rows

    def evidence(self, run_id: str, candidate_id: str, feature: str | None = None) -> list[dict]:
        sql = "SELECT * FROM DP_CANDIDATE_EVIDENCE WHERE run_id = ? AND candidate_id = ?"
        params: tuple = (run_id, candidate_id)
        if feature:
            sql += " AND feature = ?"
            params = (run_id, candidate_id, feature)
        return self.query(sql, params)

    def decisions(self, run_id: str | None = None) -> list[dict]:
        if run_id:
            return self.query("SELECT * FROM REVIEW_DECISION WHERE run_id = ? "
                              "ORDER BY decided_at DESC", (run_id,))
        return self.query("SELECT * FROM REVIEW_DECISION ORDER BY decided_at DESC")

    def weights(self, weight_version: str | None = None) -> list[dict]:
        if weight_version:
            return self.query("SELECT * FROM SCORE_WEIGHT WHERE weight_version = ?",
                              (weight_version,))
        return self.query("SELECT * FROM SCORE_WEIGHT ORDER BY effective_from DESC")

    def publish(self, run_id: str) -> dict:
        """Mark a run published, after checking that every score row has evidence."""
        orphans = self.query(
            "SELECT s.candidate_id FROM DP_CANDIDATE_SCORE s "
            "WHERE s.run_id = ? AND NOT EXISTS ("
            "  SELECT 1 FROM DP_CANDIDATE_EVIDENCE e "
            "  WHERE e.run_id = s.run_id AND e.candidate_id = s.candidate_id)", (run_id,))
        if orphans:
            raise EvidenceMissingError(
                "scores without evidence cannot be published: "
                + ", ".join(o["candidate_id"] for o in orphans))
        self.connection.execute("UPDATE RUN SET published = 1 WHERE run_id = ?", (run_id,))
        self.connection.commit()
        return {"run_id": run_id, "published": True}


def _candidate_payload(candidate: Candidate) -> dict:
    payload = asdict(candidate)
    payload["grain_ambiguity"] = getattr(candidate, "_grain_ambiguity", 0.0)
    payload["usage_weight"] = getattr(candidate, "_usage_weight", 0.0)
    payload["classification_rationale"] = getattr(candidate, "_classification_rationale", {})
    payload["reuse_communities"] = getattr(candidate, "_reuse_communities", 0)
    return payload
