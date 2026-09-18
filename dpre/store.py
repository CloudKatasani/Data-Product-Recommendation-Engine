"""Governed output tables (specification section 11).

Three schemas in one SQLite database: ``RAW`` counts as landed, ``GRAPH`` for
resolved nodes and edges, ``RECO`` for canonical metrics, candidates, scores,
evidence and feedback. Everything in RECO carries ``run_id`` and ``as_of_date``;
nothing is updated in place: a reviewer's change is a new ledger row and the
prior state is snapshotted into a history table.

Guardrails enforced here rather than by policy text:

* Propose-only. No engine path can write a status past Proposed; only a
  ``REVIEW_DECISION`` row written by an authenticated reviewer moves it, along
  the transitions in ``dpre.governance.transitions``.
* Evidence required. A score row without at least one evidence row fails the
  check and the run is not published; a run without candidates never is.
* Gates at acceptance. Accept is refused on Blocked; on Exploratory it needs a
  recorded consumer confirmation or a named exception with a second approver.
* Tamper evidence. ``REVIEW_DECISION`` is hash-chained and, like every ledger,
  protected by triggers that refuse UPDATE and DELETE; ``verify_audit_chain``
  detects anything done around the application.
* Weight governance. ``SCORE_WEIGHT`` is append-only and immutable per version;
  approvals are a separate ledger, and every configuration change is recorded.
* One writer at a time. A re-entrant lock guards the shared connection, the
  database runs in WAL mode with a busy timeout, and ``persist_run`` writes a
  whole run in one ``BEGIN IMMEDIATE .. COMMIT``.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from .config import (
    DIMENSION_WEIGHTS, ENGINE_MAX_STATUS, FEATURE_WEIGHTS, STATUS_ORDER, ScoreWeights,
)
from .governance import ledger as _ledger
from .governance.identity import lineage_id as _lineage_id
from .governance.reasons import DECISIONS, validate_override, validate_reason
from .governance.transitions import REVIEWED_STATUSES, TransitionError, next_status
from .models import Candidate, ReviewDecision, RunManifest

SCHEMA_VERSION = 2
INITIAL_WEIGHT_VERSION = "v1.0-initial"
INITIAL_WEIGHT_EFFECTIVE_FROM = "2026-01-01"
INITIAL_WEIGHT_NOTE = "pending council approval (D-04)"

CONFLICT_STATUSES = ("OPEN", "RESOLVED_A", "RESOLVED_B", "RENAMED", "DEFERRED")
DEFINITION_STATUSES = ("Draft", "Proposed", "Certified", "Deprecated")
BENEFIT_EVENT_TYPES = ("report_retired", "charter_approved", "conflict_resolved")

SCHEMA = """
CREATE TABLE IF NOT EXISTS SCHEMA_VERSION (
    version INTEGER PRIMARY KEY, applied_at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS RUN (
    run_id TEXT PRIMARY KEY, mode TEXT, industry TEXT, catalog TEXT, as_of_date TEXT,
    started_at TEXT, finished_at TEXT, weight_version TEXT, parser_version TEXT,
    generation_id TEXT, synthetic INTEGER, published INTEGER, stats TEXT,
    quality_gates TEXT, warnings TEXT, extract_ids TEXT, agent_log TEXT, label TEXT,
    weight_hash TEXT
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
    definition_status TEXT,
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
    lineage_id TEXT,
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
    field_overridden TEXT, new_value TEXT, target_candidate_id TEXT, note TEXT,
    subject_type TEXT, previous_status TEXT, new_status TEXT, previous_value TEXT,
    row_hash TEXT, prev_hash TEXT, engagement_id TEXT, actor_role TEXT,
    gate_waived TEXT, waiver_reason TEXT, second_approver TEXT,
    usable_without_rework INTEGER, rework_needed TEXT, lineage_id TEXT
);
CREATE TABLE IF NOT EXISTS SCORE_WEIGHT (
    weight_version TEXT, dimension TEXT, feature TEXT, weight REAL, effective_from TEXT,
    note TEXT, approved_by TEXT
);
CREATE INDEX IF NOT EXISTS IX_CAND_RUN ON DP_CANDIDATE (run_id, status);
CREATE INDEX IF NOT EXISTS IX_EVIDENCE ON DP_CANDIDATE_EVIDENCE (run_id, candidate_id);
CREATE INDEX IF NOT EXISTS IX_VARIANT ON KPI_VARIANT (run_id, metric_id);
CREATE INDEX IF NOT EXISTS IX_DECISION_RUN ON REVIEW_DECISION (run_id, candidate_id);
"""

# Columns added since schema version 1, applied to an existing database with
# ALTER TABLE guarded by PRAGMA table_info (R-15: an upgrade must not break).
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("RUN", "weight_hash", "TEXT"),
    ("KPI_CANONICAL", "definition_status", "TEXT"),
    ("DP_CANDIDATE", "lineage_id", "TEXT"),
    ("REVIEW_DECISION", "subject_type", "TEXT"),
    ("REVIEW_DECISION", "previous_status", "TEXT"),
    ("REVIEW_DECISION", "new_status", "TEXT"),
    ("REVIEW_DECISION", "previous_value", "TEXT"),
    ("REVIEW_DECISION", "row_hash", "TEXT"),
    ("REVIEW_DECISION", "prev_hash", "TEXT"),
    ("REVIEW_DECISION", "engagement_id", "TEXT"),
    ("REVIEW_DECISION", "actor_role", "TEXT"),
    ("REVIEW_DECISION", "gate_waived", "TEXT"),
    ("REVIEW_DECISION", "waiver_reason", "TEXT"),
    ("REVIEW_DECISION", "second_approver", "TEXT"),
    ("REVIEW_DECISION", "usable_without_rework", "INTEGER"),
    ("REVIEW_DECISION", "rework_needed", "TEXT"),
    ("REVIEW_DECISION", "lineage_id", "TEXT"),
)

# The fields that go into a REVIEW_DECISION row hash, in a fixed order.
DECISION_HASH_FIELDS = (
    "decision_id", "run_id", "candidate_id", "subject_type", "decision", "reason_code",
    "reviewer", "actor_role", "decided_at", "field_overridden", "previous_value",
    "new_value", "previous_status", "new_status", "target_candidate_id", "note",
    "engagement_id", "gate_waived", "waiver_reason", "second_approver",
    "usable_without_rework", "rework_needed", "lineage_id",
)

STATUS_AFTER = {
    "Accept": "Accepted", "AcceptWithException": "Accepted", "Reject": "Rejected",
    "Merge": "Merged", "Defer": "Deferred",
}


class ProposeOnlyError(PermissionError):
    """Raised when anything but a reviewer decision tries to move a status."""


class EvidenceMissingError(ValueError):
    """Raised when a score row would be written without evidence behind it."""


class GateError(PermissionError):
    """Raised when Accept is attempted on a candidate a hard gate still caps."""


class WeightVersionError(ValueError):
    """Raised when a weight version would be altered after it exists."""


class RunExistsError(ValueError):
    """Raised when a run id is persisted twice; runs are never replaced."""


class PublishError(ValueError):
    """Raised when a run cannot be published (no candidates, or no evidence)."""


def _json(value: Any) -> str:
    def default(obj):
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        if isinstance(obj, (_dt.date, _dt.datetime)):
            return obj.isoformat()
        return str(obj)
    return json.dumps(value, default=default)


def weight_vector_hash(rows: Iterable[dict]) -> str:
    """Content hash of a weight vector: (dimension, feature, weight) only.

    Effective dates, notes and approvers are provenance, not content, so two
    saves of the same numbers hash alike and a RUN can prove which numbers
    scored it (R-03).
    """
    material = sorted((r["dimension"], r["feature"], round(float(r["weight"]), 9)) for r in rows)
    return hashlib.sha256(_ledger.canonical_json(material).encode("utf-8")).hexdigest()


class Store:
    """SQLite-backed RECO tables. One database can hold many runs."""

    def __init__(self, path: str | Path = "data/engine.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        # isolation_level=None: the module never opens implicit transactions, so
        # every write happens inside an explicit BEGIN IMMEDIATE .. COMMIT below.
        self.connection = sqlite3.connect(str(self.path), check_same_thread=False,
                                          timeout=30.0, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SCHEMA)
        self._migrate()
        _ledger.ensure_schema(self.connection)
        self._seed_initial_weights()

    # -- lifecycle ------------------------------------------------------
    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One writer at a time; nested use joins the outer transaction."""
        with self._lock:
            if self._depth == 0:
                self.connection.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.connection
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self.connection.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if self._depth == 0:
                    self.connection.execute("COMMIT")

    # -- schema ---------------------------------------------------------
    def _columns(self, table: str) -> set[str]:
        return {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}

    def _migrate(self) -> None:
        """Bring a database written by an older engine up to this schema."""
        sealed_needed = "row_hash" not in self._columns("REVIEW_DECISION")
        for table, column, kind in MIGRATIONS:
            if column not in self._columns(table):
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        if sealed_needed:
            sealed = self._seal_legacy_decisions()
            note = f"schema v{SCHEMA_VERSION}: governance ledgers; {sealed} legacy decisions sealed"
        else:
            note = f"schema v{SCHEMA_VERSION}"
        current = self.connection.execute("SELECT MAX(version) AS v FROM SCHEMA_VERSION").fetchone()
        if not current or (current["v"] or 0) < SCHEMA_VERSION:
            self.connection.execute(
                "INSERT INTO SCHEMA_VERSION (version, applied_at, note) VALUES (?,?,?)",
                (SCHEMA_VERSION, _ledger.utc_now(), note))

    def _seal_legacy_decisions(self) -> int:
        """Hash-chain rows written before the chain existed, once, at upgrade.

        This runs before the append-only triggers are created, and the count
        is recorded in SCHEMA_VERSION so the seal is itself auditable.
        """
        rows = self.connection.execute(
            "SELECT * FROM REVIEW_DECISION ORDER BY decision_id").fetchall()
        prev = ""
        for row in rows:
            fields = dict(row)
            fields.setdefault("subject_type", "candidate")
            fields["subject_type"] = fields["subject_type"] or "candidate"
            fields["actor_role"] = fields.get("actor_role") or "legacy"
            fields["new_status"] = fields.get("new_status") or STATUS_AFTER.get(fields["decision"])
            hashed = {k: fields.get(k) for k in DECISION_HASH_FIELDS}
            digest = _ledger.row_hash(hashed, prev)
            self.connection.execute(
                "UPDATE REVIEW_DECISION SET subject_type = ?, actor_role = ?, new_status = ?, "
                "row_hash = ?, prev_hash = ? WHERE decision_id = ?",
                (fields["subject_type"], fields["actor_role"], fields["new_status"], digest, prev,
                 row["decision_id"]))
            prev = digest
        return len(rows)

    def schema_version(self) -> int:
        row = self.connection.execute("SELECT MAX(version) AS v FROM SCHEMA_VERSION").fetchone()
        return int(row["v"] or 0) if row else 0

    def _seed_initial_weights(self) -> None:
        """Ship v1.0-initial unapproved: no council has approved it yet (D-04)."""
        exists = self.connection.execute(
            "SELECT 1 FROM SCORE_WEIGHT WHERE weight_version = ? LIMIT 1",
            (INITIAL_WEIGHT_VERSION,)).fetchone()
        if exists:
            return
        baseline = ScoreWeights(weight_version=INITIAL_WEIGHT_VERSION,
                                effective_from=INITIAL_WEIGHT_EFFECTIVE_FROM,
                                dimensions=dict(DIMENSION_WEIGHTS),
                                features={k: dict(v) for k, v in FEATURE_WEIGHTS.items()},
                                note=INITIAL_WEIGHT_NOTE, approved_by="")
        self.save_weights(baseline)

    # -- run persistence ------------------------------------------------
    def persist_run(self, manifest: RunManifest, graph, canonical,
                    candidates: Iterable[Candidate], label: str = "",
                    weights: ScoreWeights | None = None,
                    publish: bool | None = None) -> dict:
        """Write a whole run in one transaction, publishing last (R-04).

        ``publish`` defaults to the manifest's verdict; a run with no candidates
        is never published, whatever the manifest says.
        """
        candidates = list(candidates)
        wanted = manifest.published if publish is None else publish
        with self.transaction():
            if weights is not None:
                self.save_weights(weights)
            self.save_run(manifest, label)
            self.save_graph(manifest.run_id, graph)
            self.save_canonicalization(manifest.run_id, canonical)
            self.save_candidates(manifest.run_id, candidates)
            published = False
            note = ""
            if wanted and candidates:
                published = bool(self.publish(manifest.run_id)["published"])
            elif wanted:
                note = "not published: the run produced no candidates"
        return {"run_id": manifest.run_id, "published": published,
                "candidates": len(candidates), "note": note}

    def save_run(self, manifest: RunManifest, label: str = "") -> None:
        """Insert the RUN row with published = 0; only ``publish`` flips it."""
        rows = self.weights(manifest.weight_version)
        weight_hash = weight_vector_hash(rows) if rows else ""
        with self.transaction() as cur:
            try:
                cur.execute(
                    "INSERT INTO RUN (run_id, mode, industry, catalog, as_of_date, started_at, "
                    "finished_at, weight_version, parser_version, generation_id, synthetic, "
                    "published, stats, quality_gates, warnings, extract_ids, agent_log, label, "
                    "weight_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (manifest.run_id, manifest.mode, manifest.industry, manifest.catalog,
                     manifest.as_of_date, manifest.started_at, manifest.finished_at,
                     manifest.weight_version, manifest.parser_version, manifest.generation_id,
                     int(manifest.synthetic), 0, _json(manifest.stats),
                     _json(manifest.quality_gates), _json(manifest.warnings),
                     _json(manifest.extract_ids), _json(manifest.agent_log), label,
                     weight_hash))
            except sqlite3.IntegrityError as exc:
                raise RunExistsError(f"run {manifest.run_id} already exists; runs are never "
                                     "replaced") from exc

    def save_graph(self, run_id: str, graph) -> None:
        with self.transaction() as cur:
            cur.executemany(
                "INSERT OR REPLACE INTO GRAPH_NODE_REPORT (run_id, report_id, name, tool, package, "
                "owner, business_unit, run_count_12m, distinct_users, last_run, disposition, "
                "complexity, scheduled, folder_path, decision_critical) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, r.report_id, r.report_name, r.tool, r.semantic_container, r.owner,
                  r.business_unit, r.run_count_12m, r.distinct_users_12m,
                  r.last_run_date.isoformat() if r.last_run_date else "", r.disposition,
                  r.complexity_score, int(r.schedule_flag), r.folder_path,
                  int(r.decision_critical)) for r in graph.reports.values()])
            cur.executemany(
                "INSERT OR REPLACE INTO GRAPH_NODE_KPI (run_id, kpi_id, report_id, label, tool, "
                "aggregation, expression, fingerprint, filter_fp, parse_status, grain, "
                "measure_scope, parse_note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, k.kpi_id, k.report_id, k.label, k.tool, k.aggregation, k.expression,
                  k.fingerprint, k.filter_fp, k.parse_status, k.grain, k.measure_scope,
                  k.parse_note) for k in graph.kpis.values()])
            cur.executemany(
                "INSERT OR REPLACE INTO GRAPH_NODE_COLUMN (run_id, column_fqn, table_fqn, system, "
                "data_type, pk_flag, sensitivity, pii_flag, business_term, definition, "
                "steward_id, domain) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, c.column_fqn, c.table_fqn, c.system, c.data_type, int(c.pk_flag),
                  c.sensitivity, int(c.pii_flag), c.business_term, c.definition, c.steward_id,
                  c.domain) for c in graph.columns.values()])
            cur.executemany(
                "INSERT OR REPLACE INTO GRAPH_NODE_TABLE (run_id, table_fqn, system, sor_flag, "
                "lifecycle_status, inferred_grain, grain_source, domain, row_count, "
                "measure_count, sunset_date, successor_system) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, t.table_fqn, t.system, int(t.sor_flag), t.lifecycle_status,
                  t.inferred_grain, t.grain_source, t.domain, t.row_count, t.measure_count,
                  t.sunset_date, t.successor_system) for t in graph.tables.values()])
            cur.executemany(
                "INSERT INTO GRAPH_EDGE_KPI_COLUMN (run_id, kpi_id, column_fqn, role, er_rule, "
                "confidence, raw_reference) VALUES (?,?,?,?,?,?,?)",
                [(run_id, e.kpi_id, e.column_fqn, e.role, e.er_rule, e.confidence,
                  e.raw_reference) for e in graph.edges_kpi_column])
            cur.executemany(
                "INSERT INTO GRAPH_ER_QUARANTINE (run_id, kpi_id, raw_reference, reason_code, "
                "detail, role) VALUES (?,?,?,?,?,?)",
                [(run_id, q.kpi_id, q.raw_reference, q.reason_code, q.detail, q.role)
                 for q in graph.quarantine])

    def save_canonicalization(self, run_id: str, result) -> None:
        with self.transaction() as cur:
            cur.executemany(
                "INSERT OR REPLACE INTO KPI_CANONICAL (run_id, metric_id, canonical_name, "
                "definition, fingerprint, grain, aggregation, steward_id, steward_source, "
                "name_status, domain, sub_domain, usage_weight, report_count, consumer_breadth, "
                "variant_count, opaque, tools, operand_columns, source_tables, kpi_ids, labels, "
                "definition_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, m.metric_id, m.canonical_name, m.definition, m.fingerprint, m.grain,
                  m.aggregation, m.steward_id, m.steward_source, m.name_status, m.domain,
                  m.sub_domain, m.usage_weight, m.report_count, m.consumer_breadth,
                  m.variant_count, int(m.opaque), _json(m.tools), _json(m.operand_columns),
                  _json(m.source_tables), _json(m.kpi_ids), _json(m.labels), "Draft")
                 for m in result.metrics.values()])
            cur.executemany(
                "INSERT INTO KPI_VARIANT (run_id, kpi_id, metric_id, tier, filter_fp, "
                "filter_expression, variant_label) VALUES (?,?,?,?,?,?,?)",
                [(run_id, v.kpi_id, v.metric_id, v.tier, v.filter_fp, v.filter_expression,
                  v.variant_label) for v in result.variants])
            cur.executemany(
                "INSERT OR REPLACE INTO KPI_CONFLICT (run_id, conflict_id, label, metric_id_a, "
                "metric_id_b, usage_weight_a, usage_weight_b, difference_summary, pattern, "
                "resolution_status, steward_id, similarity, semantic_model_decision, reports_a, "
                "reports_b, expression_a, expression_b) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, c.conflict_id, c.label, c.metric_id_a, c.metric_id_b, c.usage_weight_a,
                  c.usage_weight_b, c.difference_summary, c.pattern, c.resolution_status,
                  c.steward_id, c.similarity, c.semantic_model_decision, _json(c.reports_a),
                  _json(c.reports_b), c.expression_a, c.expression_b)
                 for c in result.conflicts])

    def save_candidates(self, run_id: str, candidates: Iterable[Candidate]) -> None:
        """Write candidates, their scores and evidence. Propose-only is enforced here.

        The run-independent ``lineage_id`` (R-09) is computed from the metric
        fingerprints already saved in KPI_CANONICAL for this run, so the same
        metric set and grain gets the same id in every run.
        """
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
        fingerprints = self._fingerprints(run_id)
        with self.transaction() as cur:
            for candidate in candidates:
                payload = _candidate_payload(candidate)
                lineage = _lineage_id([fingerprints.get(m, m) for m in candidate.metric_ids],
                                      candidate.grain)
                payload["lineage_id"] = lineage
                cur.execute(
                    "INSERT INTO DP_CANDIDATE (run_id, candidate_id, proposed_name, purpose, "
                    "archetype, tier, grain, domain, sub_domain, owner_candidate, "
                    "steward_candidate, status, archetype_confidence, archetype_runner_up, "
                    "tier_confidence, name_status, parent_candidate_id, origin, as_of_date, "
                    "payload, lineage_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, candidate.candidate_id, candidate.proposed_name, candidate.purpose,
                     candidate.archetype, candidate.tier, candidate.grain, candidate.domain,
                     candidate.sub_domain, candidate.owner_candidate,
                     candidate.steward_candidate, candidate.status,
                     candidate.archetype_confidence, candidate.archetype_runner_up,
                     candidate.tier_confidence, candidate.name_status,
                     candidate.parent_candidate_id, candidate.origin, candidate.as_of_date,
                     _json(payload), lineage))
                cur.executemany(
                    "INSERT INTO DP_CANDIDATE_METRIC (run_id, candidate_id, metric_id) "
                    "VALUES (?,?,?)",
                    [(run_id, candidate.candidate_id, m) for m in candidate.metric_ids])
                cur.executemany(
                    "INSERT INTO DP_CANDIDATE_SOURCE (run_id, candidate_id, table_fqn, system, "
                    "share_of_metrics, sor_flag, lifecycle_status) VALUES (?,?,?,?,?,?,?)",
                    [(run_id, candidate.candidate_id, s.table_fqn, s.system, s.share_of_metrics,
                      int(s.sor_flag), s.lifecycle_status) for s in candidate.sources])
                cur.executemany(
                    "INSERT INTO DP_CANDIDATE_CONSUMER (run_id, candidate_id, business_unit, "
                    "users, report_count, scheduled_share, cadence) VALUES (?,?,?,?,?,?,?)",
                    [(run_id, candidate.candidate_id, c.business_unit, c.users, c.report_count,
                      c.scheduled_share, c.cadence) for c in candidate.consumers])
                cur.executemany(
                    "INSERT INTO DP_CANDIDATE_REPORT (run_id, candidate_id, report_id, coverage, "
                    "disposition, users, last_run) VALUES (?,?,?,?,?,?,?)",
                    [(run_id, candidate.candidate_id, r.report_id, r.coverage, r.disposition,
                      r.users, r.last_run) for r in candidate.reports])
                if candidate.score:
                    cur.execute(
                        "INSERT INTO DP_CANDIDATE_SCORE (run_id, candidate_id, weight_version, "
                        "demand, consolidation, feasibility, risk, composite, gate_results, "
                        "features) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (run_id, candidate.candidate_id, candidate.score.weight_version,
                         candidate.score.demand, candidate.score.consolidation,
                         candidate.score.feasibility, candidate.score.risk,
                         candidate.score.composite, _json(candidate.score.gates),
                         _json(candidate.score.features)))
                cur.executemany(
                    "INSERT INTO DP_CANDIDATE_EVIDENCE (run_id, candidate_id, feature, "
                    "evidence_type, evidence_id, detail) VALUES (?,?,?,?,?,?)",
                    [(run_id, e.candidate_id, e.feature, e.evidence_type, e.evidence_id,
                      e.detail) for e in candidate.evidence])
                cur.executemany(
                    "INSERT INTO DP_CANDIDATE_CRITIQUE (run_id, candidate_id, criterion, finding, "
                    "severity) VALUES (?,?,?,?,?)",
                    [(run_id, f.candidate_id, f.criterion, f.finding, f.severity)
                     for f in candidate.critique])
                cur.execute(
                    "INSERT INTO DP_CANDIDATE_NARRATIVE (run_id, candidate_id, purpose, "
                    "value_hypothesis, decisions, status) VALUES (?,?,?,?,?,?)",
                    (run_id, candidate.candidate_id, candidate.purpose,
                     candidate.narrative.get("value_hypothesis", ""),
                     _json([asdict(d) for d in candidate.decisions_drafted]), "AI_DRAFT"))

    def _fingerprints(self, run_id: str) -> dict[str, str]:
        return {row["metric_id"]: row["fingerprint"] for row in self.query(
            "SELECT metric_id, fingerprint FROM KPI_CANONICAL WHERE run_id = ?", (run_id,))}

    def _kpi_ids(self, run_id: str) -> dict[str, list[str]]:
        """The extract-level KPI ids behind each metric: stable when a calculation changes."""
        return {row["metric_id"]: json.loads(row["kpi_ids"] or "[]") for row in self.query(
            "SELECT metric_id, kpi_ids FROM KPI_CANONICAL WHERE run_id = ?", (run_id,))}

    def publish(self, run_id: str) -> dict:
        """Mark a run published: it must hold candidates and evidence behind every score."""
        with self.transaction() as cur:
            if not self.run(run_id):
                raise PublishError(f"run {run_id} does not exist")
            count = cur.execute("SELECT COUNT(*) AS n FROM DP_CANDIDATE WHERE run_id = ?",
                                (run_id,)).fetchone()["n"]
            if not count:
                raise PublishError(f"run {run_id} holds no candidates and cannot be published")
            orphans = self.query(
                "SELECT s.candidate_id FROM DP_CANDIDATE_SCORE s "
                "WHERE s.run_id = ? AND NOT EXISTS ("
                "  SELECT 1 FROM DP_CANDIDATE_EVIDENCE e "
                "  WHERE e.run_id = s.run_id AND e.candidate_id = s.candidate_id)", (run_id,))
            if orphans:
                raise EvidenceMissingError(
                    "scores without evidence cannot be published: "
                    + ", ".join(o["candidate_id"] for o in orphans))
            cur.execute("UPDATE RUN SET published = 1 WHERE run_id = ?", (run_id,))
        return {"run_id": run_id, "published": True}

    # -- weights (R-03) -------------------------------------------------
    def save_weights(self, weights: ScoreWeights, approved_by: str = "",
                     note: str | None = None) -> dict:
        """Append a weight version; an existing version can never change.

        Saving the same numbers again is a no-op, so the pipeline may record
        the vector that scored a run. Approval is a separate ledger row written
        by ``approve_weight_version``; ``approved_by`` here is only the label on
        the vector rows and is empty unless the approval path passes it.
        """
        new_rows = weights.rows()
        with self.transaction() as cur:
            existing = self.weights(weights.weight_version)
            if existing:
                if weight_vector_hash(existing) != weight_vector_hash(new_rows):
                    raise WeightVersionError(
                        f"weight version {weights.weight_version} already exists with "
                        "different values; weights are immutable per version, propose a "
                        "new version instead")
                return {"weight_version": weights.weight_version, "created": False}
            cur.executemany(
                "INSERT INTO SCORE_WEIGHT (weight_version, dimension, feature, weight, "
                "effective_from, note, approved_by) VALUES (?,?,?,?,?,?,?)",
                [(row["weight_version"], row["dimension"], row["feature"], row["weight"],
                  row["effective_from"], weights.note if note is None else note, approved_by)
                 for row in new_rows])
        return {"weight_version": weights.weight_version, "created": True}

    def approve_weight_version(self, weight_version: str, approver: str, note: str = "",
                               at: str | None = None) -> dict:
        """A council member approves a stored version; recorded, never edited."""
        if not approver:
            raise PermissionError("a weight version must be approved by a named council member")
        with self.transaction() as cur:
            rows = self.weights(weight_version)
            if not rows:
                raise WeightVersionError(f"weight version {weight_version} is not stored")
            before = self.current_weights()
            approved_at = _ledger.normalize_timestamp(at)
            cur.execute(
                "INSERT INTO WEIGHT_APPROVAL (weight_version, approved_by, approved_at, note) "
                "VALUES (?,?,?,?)", (weight_version, approver, approved_at, note))
            after = self.current_weights()
            self.record_config_change(
                approver, "weights.weight_version", before.weight_version, weight_version,
                note=note or "weight version approved", at=approved_at)
            if before.dimensions != after.dimensions:
                self.record_config_change(
                    approver, "weights.dimensions", _json(before.dimensions),
                    _json(after.dimensions), note=f"approved with {weight_version}",
                    at=approved_at)
        return {"weight_version": weight_version, "approved_by": approver,
                "approved_at": approved_at, "weight_hash": weight_vector_hash(rows)}

    def current_weights(self) -> ScoreWeights:
        """The latest approved version, or the unapproved v1.0-initial baseline."""
        approval = self.query(
            "SELECT * FROM WEIGHT_APPROVAL ORDER BY approval_id DESC LIMIT 1")
        if approval:
            row = approval[0]
            return _weights_from_rows(self.weights(row["weight_version"]),
                                      row["weight_version"], row["approved_by"], row["note"])
        rows = self.weights(INITIAL_WEIGHT_VERSION)
        return _weights_from_rows(rows, INITIAL_WEIGHT_VERSION, "", INITIAL_WEIGHT_NOTE)

    def weight_versions(self) -> list[dict]:
        versions = self.query(
            "SELECT weight_version, MIN(effective_from) AS effective_from, MIN(note) AS note, "
            "COUNT(*) AS rows FROM SCORE_WEIGHT GROUP BY weight_version "
            "ORDER BY MIN(rowid)")
        approvals = {row["weight_version"]: row for row in self.query(
            "SELECT * FROM WEIGHT_APPROVAL ORDER BY approval_id")}
        out = []
        for version in versions:
            approval = approvals.get(version["weight_version"])
            out.append({
                **version,
                "weight_hash": weight_vector_hash(self.weights(version["weight_version"])),
                "approved": approval is not None,
                "approved_by": approval["approved_by"] if approval else "",
                "approved_at": approval["approved_at"] if approval else "",
            })
        return out

    def record_config_change(self, actor: str, field: str, before: Any, after: Any,
                             note: str = "", run_id: str = "", at: str | None = None) -> dict:
        """CONFIG_CHANGE ledger: who changed which knob, from what, to what (R-03)."""
        if not actor:
            raise PermissionError("a configuration change must name who made it")
        row = {
            "changed_at": _ledger.normalize_timestamp(at), "actor": actor, "field": field,
            "before": before if isinstance(before, str) or before is None else _json(before),
            "after": after if isinstance(after, str) or after is None else _json(after),
            "note": note, "run_id": run_id,
        }
        with self.transaction() as cur:
            cursor = cur.execute(
                "INSERT INTO CONFIG_CHANGE (changed_at, actor, field, before, after, note, run_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (row["changed_at"], actor, field, row["before"], row["after"], note, run_id))
            row["change_id"] = cursor.lastrowid
        return row

    def config_changes(self, field: str | None = None) -> list[dict]:
        if field:
            return self.query("SELECT * FROM CONFIG_CHANGE WHERE field = ? ORDER BY change_id",
                              (field,))
        return self.query("SELECT * FROM CONFIG_CHANGE ORDER BY change_id")

    # -- reviewer -------------------------------------------------------
    def record_decision(self, decision: ReviewDecision, *, actor_role: str = "reviewer",
                        engagement_id: str = "", subject_type: str = "candidate",
                        gate_waived: str = "", waiver_reason: str = "",
                        second_approver: str = "", usable_without_rework: bool | None = None,
                        rework_needed: list[str] | None = None, carried_status: str = "",
                        value: dict | None = None, previous_value: str | None = None) -> dict:
        """The only path that can move a candidate past Proposed.

        Validates the reviewer, the decision, the reason code (R-43), the gate
        (R-02) and the transition (R-15), then appends one hash-chained
        REVIEW_DECISION row, a status-history row and, where the decision
        implies one, a waiver or a benefit plan. Returns the stored row.
        """
        if not decision.reviewer:
            raise ProposeOnlyError("a review decision must name an authenticated reviewer")
        if decision.decision not in DECISIONS:
            raise ValueError(f"unknown decision '{decision.decision}'; expected one of "
                             + ", ".join(DECISIONS))
        reason = validate_reason(decision.decision, decision.reason_code, decision.note)
        with self.transaction() as cur:
            candidate = None
            previous_status = None
            new_status = None
            lineage = ""
            gate_note = ""
            if subject_type == "candidate":
                candidate = self.candidate(decision.run_id, decision.candidate_id)
                if candidate is None:
                    raise KeyError(f"candidate {decision.candidate_id} not found in run "
                                   f"{decision.run_id}")
                previous_status = candidate["status"]
                lineage = candidate.get("lineage_id") or ""
                if decision.decision in ("Accept", "AcceptWithException"):
                    gate_note = self._check_accept_gates(
                        candidate, decision.decision, gate_waived, waiver_reason,
                        second_approver, decision.reviewer)
                if decision.decision == "Reverse":
                    self._check_reversal_actor(decision)
                if decision.decision == "Override":
                    field, new_value = validate_override(decision.field_overridden,
                                                         decision.new_value)
                    decision.field_overridden, decision.new_value = field, new_value
                    if previous_value is None:
                        previous_value = candidate.get(field)
                new_status = next_status(previous_status, decision.decision, carried_status)
                if decision.target_candidate_id and \
                        self.candidate(decision.run_id, decision.target_candidate_id) is None:
                    raise KeyError(f"target candidate {decision.target_candidate_id} not found "
                                   f"in run {decision.run_id}")
            elif subject_type == "confirmation":
                linked = self.candidate(decision.run_id, decision.candidate_id)
                lineage = (linked or {}).get("lineage_id") or ""
            decision.reason_code = reason
            decision.decided_at = _ledger.normalize_timestamp(decision.decided_at)
            if gate_note:
                decision.note = (decision.note + "; " if decision.note else "") + gate_note

            row = self._append_decision(cur, decision, subject_type=subject_type,
                                        actor_role=actor_role, engagement_id=engagement_id,
                                        previous_status=previous_status,
                                        new_status=new_status or previous_status,
                                        previous_value=previous_value, gate_waived=gate_waived,
                                        waiver_reason=waiver_reason,
                                        second_approver=second_approver,
                                        usable_without_rework=usable_without_rework,
                                        rework_needed=rework_needed, lineage_id=lineage)
            if candidate is None:
                return row

            if new_status and new_status != previous_status:
                cur.execute("UPDATE DP_CANDIDATE SET status = ? WHERE run_id = ? AND candidate_id = ?",
                            (new_status, decision.run_id, decision.candidate_id))
                cur.execute(
                    "INSERT INTO DP_CANDIDATE_STATUS_HISTORY (run_id, candidate_id, from_status, "
                    "to_status, decision_id, actor, at) VALUES (?,?,?,?,?,?,?)",
                    (decision.run_id, decision.candidate_id, previous_status, new_status,
                     row["decision_id"], decision.reviewer, decision.decided_at))
            if decision.decision == "Override":
                cur.execute(
                    f"UPDATE DP_CANDIDATE SET {decision.field_overridden} = ? "
                    "WHERE run_id = ? AND candidate_id = ?",
                    (decision.new_value, decision.run_id, decision.candidate_id))
            if decision.decision == "AcceptWithException":
                cur.execute(
                    "INSERT INTO GATE_WAIVER (run_id, candidate_id, lineage_id, gate_waived, "
                    "waiver_reason, reviewer, second_approver, decision_id, granted_at, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'OPEN')",
                    (decision.run_id, decision.candidate_id, lineage, gate_waived, waiver_reason,
                     decision.reviewer, second_approver, row["decision_id"], decision.decided_at))
            if new_status == "Accepted":
                self._write_benefit_plan(cur, candidate, row, value)
            if decision.decision == "Reverse" and previous_status == "Accepted":
                cur.execute(
                    "UPDATE GATE_WAIVER SET status = 'CLOSED', closed_at = ?, closed_by = ?, "
                    "close_note = ? WHERE run_id = ? AND candidate_id = ? AND status = 'OPEN'",
                    (decision.decided_at, decision.reviewer,
                     f"acceptance reversed by decision {row['decision_id']}",
                     decision.run_id, decision.candidate_id))
            return row

    def _append_decision(self, cur: sqlite3.Connection, decision: ReviewDecision, *,
                         subject_type: str, actor_role: str, engagement_id: str,
                         previous_status: str | None, new_status: str | None,
                         previous_value: str | None, gate_waived: str, waiver_reason: str,
                         second_approver: str, usable_without_rework: bool | None,
                         rework_needed: list[str] | None, lineage_id: str) -> dict:
        """Append one hash-chained row. Caller holds the transaction."""
        last = cur.execute(
            "SELECT decision_id, row_hash FROM REVIEW_DECISION ORDER BY decision_id DESC LIMIT 1"
        ).fetchone()
        seq = cur.execute("SELECT seq FROM sqlite_sequence WHERE name = 'REVIEW_DECISION'"
                          ).fetchone()
        next_id = max((last["decision_id"] if last else 0), (seq["seq"] if seq else 0)) + 1
        prev_hash = (last["row_hash"] or "") if last else ""
        fields = {
            "decision_id": next_id, "run_id": decision.run_id or "",
            "candidate_id": decision.candidate_id or "", "subject_type": subject_type,
            "decision": decision.decision, "reason_code": decision.reason_code or "",
            "reviewer": decision.reviewer, "actor_role": actor_role or "reviewer",
            "decided_at": decision.decided_at, "field_overridden": decision.field_overridden or "",
            "previous_value": None if previous_value is None else str(previous_value),
            "new_value": decision.new_value or "", "previous_status": previous_status,
            "new_status": new_status, "target_candidate_id": decision.target_candidate_id or "",
            "note": decision.note or "", "engagement_id": engagement_id or "",
            "gate_waived": gate_waived or "", "waiver_reason": waiver_reason or "",
            "second_approver": second_approver or "",
            "usable_without_rework": (None if usable_without_rework is None
                                      else int(bool(usable_without_rework))),
            "rework_needed": _json(list(rework_needed)) if rework_needed else "",
            "lineage_id": lineage_id or "",
        }
        digest = _ledger.row_hash(fields, prev_hash)
        columns = list(DECISION_HASH_FIELDS) + ["row_hash", "prev_hash"]
        values = [fields[c] for c in DECISION_HASH_FIELDS] + [digest, prev_hash]
        cur.execute(
            f"INSERT INTO REVIEW_DECISION ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})", values)
        fields["row_hash"], fields["prev_hash"] = digest, prev_hash
        return fields

    def _check_accept_gates(self, candidate: dict, decision: str, gate_waived: str,
                            waiver_reason: str, second_approver: str, reviewer: str) -> str:
        """Gates the weights cannot override (spec 8.3) bind at acceptance (R-02)."""
        failed = self._failed_gates(candidate)
        status = candidate["status"]
        if status == "Blocked" or "G4" in failed:
            detail = failed.get("G4") or "status Blocked by a hard gate"
            raise GateError(f"{candidate['candidate_id']} is Blocked (G4 sunset source: {detail}); "
                            "a Blocked candidate cannot be Accepted; Reject, Defer or wait for "
                            "a successor mapping in the catalog")
        if status not in ("Exploratory", "Proposed", "Deferred"):
            return ""
        if status == "Exploratory" and not failed:
            failed = {"G1": "gate results not recorded; Exploratory treated as an unconfirmed "
                            "consumer (G1)"}
        confirmed = self.confirmations_for(candidate.get("lineage_id") or "",
                                           candidate["run_id"], candidate["candidate_id"])
        if decision == "Accept":
            if not failed:
                return ""
            if set(failed) == {"G1"} and confirmed:
                who = confirmed[-1]
                return (f"G1 satisfied by consumer confirmation #{who['confirmation_id']} "
                        f"({who['business_unit']}, confirmed by {who['confirmed_by']})")
            raise GateError(
                f"{candidate['candidate_id']} is {status} with failed gate(s) "
                + "; ".join(f"{g}: {d}" for g, d in failed.items())
                + ". Accept needs a recorded consumer confirmation (G1 only) or decision "
                  "AcceptWithException naming gate_waived, waiver_reason and a second approver")
        # AcceptWithException
        if not failed:
            raise TransitionError(f"{candidate['candidate_id']} has no failed gate to waive; "
                                  "use Accept")
        waived = {g.strip() for g in (gate_waived or "").split(",") if g.strip()}
        unknown = waived - set(failed)
        if unknown:
            raise GateError(f"gate(s) {', '.join(sorted(unknown))} did not fail for "
                            f"{candidate['candidate_id']}; failed: {', '.join(sorted(failed))}")
        uncovered = {g for g in failed if g not in waived and not (g == "G1" and confirmed)}
        if uncovered:
            raise GateError("every failed gate must be waived or confirmed; still open: "
                            + ", ".join(f"{g}: {failed[g]}" for g in sorted(uncovered)))
        if not (waiver_reason or "").strip():
            raise GateError("an exception must carry a waiver_reason")
        if not (second_approver or "").strip():
            raise GateError("an exception must name a second approver")
        if second_approver.strip() == reviewer.strip():
            raise GateError("the second approver must be a different person from the reviewer")
        return f"gate(s) {', '.join(sorted(waived))} waived; second approver {second_approver}"

    def _failed_gates(self, candidate: dict) -> dict[str, str]:
        rows = self.query("SELECT gate_results FROM DP_CANDIDATE_SCORE WHERE run_id = ? "
                          "AND candidate_id = ?", (candidate["run_id"], candidate["candidate_id"]))
        gates = json.loads(rows[0]["gate_results"] or "[]") if rows else []
        return {g["gate"]: g.get("detail", "") for g in gates if not g.get("passed")}

    def _check_reversal_actor(self, decision: ReviewDecision) -> None:
        rows = self.query(
            "SELECT reviewer, decision FROM REVIEW_DECISION WHERE run_id = ? AND candidate_id = ? "
            "AND new_status IS NOT previous_status AND subject_type = 'candidate' "
            "ORDER BY decision_id DESC LIMIT 1", (decision.run_id, decision.candidate_id))
        if not rows:
            raise TransitionError("nothing to reverse: no prior status change is on file")
        if rows[0]["reviewer"] == decision.reviewer:
            raise TransitionError(
                f"a reversal needs a different actor from {rows[0]['reviewer']}, who made the "
                f"{rows[0]['decision']} decision being reversed")

    def _write_benefit_plan(self, cur: sqlite3.Connection, candidate: dict, row: dict,
                            value: dict | None) -> None:
        """What was promised at Accept, from the card's value row (R-09)."""
        payload = candidate.get("payload") or {}
        reports = payload.get("reports") or []
        retirable = [r for r in reports
                     if str(r.get("disposition", "")).lower() in ("retire", "merge")]
        plan = {
            "reports_retired": len(retirable),
            "conflicts_resolved": len(payload.get("conflicts") or []),
            "users_served": sum(int(c.get("users", 0)) for c in payload.get("consumers") or []),
            "usage_weight": float(payload.get("usage_weight") or 0.0),
        }
        if value:
            for key in ("reports_retired", "conflicts_resolved", "users_served", "usage_weight"):
                if key in value and value[key] is not None:
                    plan[key] = value[key]
        cur.execute(
            "INSERT INTO BENEFIT_PLAN (lineage_id, run_id, candidate_id, decision_id, planned_at, "
            "planned_by, reports_retired, conflicts_resolved, users_served, usage_weight, "
            "value_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (candidate.get("lineage_id") or "", candidate["run_id"], candidate["candidate_id"],
             row["decision_id"], row["decided_at"], row["reviewer"], int(plan["reports_retired"]),
             int(plan["conflicts_resolved"]), int(plan["users_served"]),
             float(plan["usage_weight"]), _json(value or {})))

    # -- audit ----------------------------------------------------------
    def verify_audit_chain(self, run_id: str | None = None) -> dict:
        """Recompute every REVIEW_DECISION hash and the links between rows.

        Returns ``{ok, rows, first_break, orphans}``: ``first_break`` names the
        first row whose content or link does not verify; ``orphans`` are
        candidates past Proposed with no decision row behind them.
        """
        rows = self.query("SELECT * FROM REVIEW_DECISION ORDER BY decision_id")
        expected_prev = ""
        checked = 0
        first_break = None
        for row in rows:
            in_scope = run_id is None or row["run_id"] == run_id
            if in_scope:
                checked += 1
            fields = {k: row.get(k) for k in DECISION_HASH_FIELDS}
            reason = ""
            if not row.get("row_hash"):
                reason = "row not hashed: written outside Store.record_decision"
            elif (row.get("prev_hash") or "") != expected_prev:
                reason = "chain broken: prev_hash does not match the previous row"
            elif _ledger.row_hash(fields, row["prev_hash"] or "") != row["row_hash"]:
                reason = "content altered: row_hash does not match the stored fields"
            if reason and first_break is None and in_scope:
                first_break = {"decision_id": row["decision_id"], "run_id": row["run_id"],
                               "candidate_id": row["candidate_id"], "reason": reason}
            expected_prev = row.get("row_hash") or expected_prev
        orphans = self.orphan_statuses(run_id)
        return {"ok": first_break is None and not orphans, "rows": checked,
                "first_break": first_break, "orphans": orphans}

    def orphan_statuses(self, run_id: str | None = None) -> list[dict]:
        """Candidates whose status is past Proposed with no decision behind it."""
        sql = ("SELECT c.run_id, c.candidate_id, c.status FROM DP_CANDIDATE c "
               "WHERE c.status IN ({}) AND NOT EXISTS (SELECT 1 FROM REVIEW_DECISION d "
               "WHERE d.run_id = c.run_id AND d.candidate_id = c.candidate_id "
               "AND d.new_status = c.status)").format(
            ", ".join("?" for _ in REVIEWED_STATUSES))
        params: tuple = tuple(REVIEWED_STATUSES)
        if run_id:
            sql += " AND c.run_id = ?"
            params += (run_id,)
        return self.query(sql + " ORDER BY c.run_id, c.candidate_id", params)

    def status_history(self, run_id: str, candidate_id: str) -> list[dict]:
        return self.query(
            "SELECT * FROM DP_CANDIDATE_STATUS_HISTORY WHERE run_id = ? AND candidate_id = ? "
            "ORDER BY history_id", (run_id, candidate_id))

    def open_waivers(self, run_id: str | None = None) -> list[dict]:
        """The exceptions view: every gate waiver still in force (R-02)."""
        sql = ("SELECT w.*, c.proposed_name, c.status FROM GATE_WAIVER w "
               "LEFT JOIN DP_CANDIDATE c ON c.run_id = w.run_id AND c.candidate_id = w.candidate_id "
               "WHERE w.status = 'OPEN'")
        params: tuple = ()
        if run_id:
            sql += " AND w.run_id = ?"
            params = (run_id,)
        return self.query(sql + " ORDER BY w.waiver_id", params)

    # -- run-independent overrides (R-07) ---------------------------------
    def record_report_override(self, report_id: str, field: str, value: str, actor: str,
                               note: str = "", run_id: str = "", decision_id: int | None = None,
                               at: str | None = None) -> dict:
        if not actor:
            raise ProposeOnlyError("a report override requires a reviewer")
        stamped = _ledger.normalize_timestamp(at)
        with self.transaction() as cur:
            cursor = cur.execute(
                "INSERT INTO REPORT_OVERRIDE (report_id, field, value, actor, at, note, run_id, "
                "decision_id) VALUES (?,?,?,?,?,?,?,?)",
                (report_id, field, str(value), actor, stamped, note, run_id, decision_id))
        return {"override_id": cursor.lastrowid, "report_id": report_id, "field": field,
                "value": str(value), "actor": actor, "at": stamped, "note": note}

    def report_overrides(self, field: str | None = None) -> list[dict]:
        """Latest value per (report_id, field); applied on every run at graph build."""
        rows = self.query("SELECT * FROM REPORT_OVERRIDE ORDER BY override_id")
        latest: dict[tuple[str, str], dict] = {}
        for row in rows:
            if field and row["field"] != field:
                continue
            latest[(row["report_id"], row["field"])] = row
        return list(latest.values())

    def record_consumer_confirmation(self, lineage_id: str, run_id: str, candidate_id: str,
                                     business_unit: str, blocked_decision: str,
                                     latency_tolerance: str, consequence: str,
                                     confirmed_by: str, decision_id: int | None = None,
                                     note: str = "", at: str | None = None) -> dict:
        if not confirmed_by:
            raise ProposeOnlyError("a consumer confirmation requires a reviewer")
        for name, text in (("business_unit", business_unit),
                           ("blocked_decision", blocked_decision),
                           ("latency_tolerance", latency_tolerance),
                           ("consequence", consequence)):
            if not (text or "").strip():
                raise ValueError(f"a consumer confirmation needs {name}: the three fields the "
                                 "specification reserves for the human (section 9.2)")
        stamped = _ledger.normalize_timestamp(at)
        with self.transaction() as cur:
            cursor = cur.execute(
                "INSERT INTO CANDIDATE_CONSUMER_CONFIRMATION (lineage_id, run_id, candidate_id, "
                "business_unit, blocked_decision, latency_tolerance, consequence, confirmed_by, "
                "confirmed_at, decision_id, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (lineage_id, run_id, candidate_id, business_unit, blocked_decision,
                 latency_tolerance, consequence, confirmed_by, stamped, decision_id, note))
            confirmation_id = cursor.lastrowid
            self._confirm_narrative(cur, run_id, candidate_id, business_unit, blocked_decision,
                                    latency_tolerance, consequence, confirmed_by, stamped)
        return {"confirmation_id": confirmation_id, "lineage_id": lineage_id,
                "business_unit": business_unit, "confirmed_by": confirmed_by,
                "confirmed_at": stamped}

    def _confirm_narrative(self, cur: sqlite3.Connection, run_id: str, candidate_id: str,
                           business_unit: str, blocked_decision: str, latency_tolerance: str,
                           consequence: str, confirmed_by: str, at: str) -> None:
        """The AI_DRAFT decision-register entry becomes CONFIRMED for that unit."""
        row = cur.execute("SELECT decisions FROM DP_CANDIDATE_NARRATIVE WHERE run_id = ? AND "
                          "candidate_id = ?", (run_id, candidate_id)).fetchone()
        if not row:
            return
        drafts = json.loads(row["decisions"] or "[]")
        for draft in drafts:
            if draft.get("business_unit") == business_unit:
                draft.update({"status": "CONFIRMED", "blocked_decision": blocked_decision,
                              "latency_tolerance": latency_tolerance,
                              "consequence": consequence, "confirmed_by": confirmed_by,
                              "confirmed_at": at})
        status = "CONFIRMED" if drafts and all(d.get("status") == "CONFIRMED" for d in drafts) \
            else "PARTIALLY_CONFIRMED" if any(d.get("status") == "CONFIRMED" for d in drafts) \
            else "AI_DRAFT"
        cur.execute("UPDATE DP_CANDIDATE_NARRATIVE SET decisions = ?, status = ? "
                    "WHERE run_id = ? AND candidate_id = ?",
                    (_json(drafts), status, run_id, candidate_id))

    def consumer_confirmations(self) -> list[dict]:
        """Every confirmation, keyed by lineage id, for the pipeline to apply at G1."""
        return self.query("SELECT * FROM CANDIDATE_CONSUMER_CONFIRMATION ORDER BY confirmation_id")

    def confirmations_for(self, lineage_id: str, run_id: str = "",
                          candidate_id: str = "") -> list[dict]:
        return self.query(
            "SELECT * FROM CANDIDATE_CONSUMER_CONFIRMATION WHERE (lineage_id = ? AND ? != '') "
            "OR (run_id = ? AND candidate_id = ?) ORDER BY confirmation_id",
            (lineage_id, lineage_id, run_id, candidate_id))

    # -- conflicts and names (R-14, R-57) -----------------------------------
    def resolve_conflict(self, run_id: str, conflict_id: str, status: str, reviewer: str,
                         note: str = "", authoritative_metric_id: str = "",
                         decided_at: str | None = None) -> dict:
        """Adjudicate a conflict: ledger row, REVIEW_DECISION row, then the run's view."""
        if not reviewer:
            raise ProposeOnlyError("resolving a conflict requires an authenticated steward")
        if status not in CONFLICT_STATUSES:
            raise ValueError(f"'{status}' is not a conflict status; expected one of "
                             + ", ".join(CONFLICT_STATUSES))
        conflict = self.query("SELECT * FROM KPI_CONFLICT WHERE run_id = ? AND conflict_id = ?",
                              (run_id, conflict_id))
        if not conflict:
            raise KeyError(f"conflict {conflict_id} not found in run {run_id}")
        conflict = conflict[0]
        if status.startswith("RESOLVED_"):
            if not (note or "").strip():
                raise ValueError(f"{status} requires a rationale in the note")
            side = conflict["metric_id_a"] if status == "RESOLVED_A" else conflict["metric_id_b"]
            if authoritative_metric_id and authoritative_metric_id != side:
                raise ValueError(f"{status} names metric {side} as authoritative, not "
                                 f"{authoritative_metric_id}")
            authoritative_metric_id = side
        fingerprints = self._fingerprints(run_id)
        fp_a = fingerprints.get(conflict["metric_id_a"], conflict["metric_id_a"])
        fp_b = fingerprints.get(conflict["metric_id_b"], conflict["metric_id_b"])
        key = conflict_key(fp_a, fp_b)
        kpis = self._kpi_ids(run_id)
        with self.transaction() as cur:
            row = self.record_decision(
                ReviewDecision(candidate_id=conflict_id, decision="Override",
                               reason_code="conflict_resolved", reviewer=reviewer,
                               decided_at=decided_at or "",
                               field_overridden="resolution_status",
                               new_value=status, run_id=run_id,
                               note=note or f"conflict {conflict_id} set to {status}"),
                subject_type="conflict", actor_role="steward",
                previous_value=conflict["resolution_status"])
            cur.execute(
                "INSERT INTO CONFLICT_DECISION (conflict_key, label, fingerprint_a, fingerprint_b, "
                "status, authoritative_metric_id, rationale, steward, decided_at, run_id, "
                "conflict_id, decision_id, kpi_ids_a, kpi_ids_b) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, conflict["label"], fp_a, fp_b, status, authoritative_metric_id, note,
                 reviewer, row["decided_at"], run_id, conflict_id, row["decision_id"],
                 _json(kpis.get(conflict["metric_id_a"], [])),
                 _json(kpis.get(conflict["metric_id_b"], []))))
            cur.execute("UPDATE KPI_CONFLICT SET resolution_status = ?, steward_id = ? "
                        "WHERE run_id = ? AND conflict_id = ?",
                        (status, reviewer, run_id, conflict_id))
        return {"conflict_id": conflict_id, "conflict_key": key, "status": status,
                "authoritative_metric_id": authoritative_metric_id, "steward": reviewer,
                "decided_at": row["decided_at"], "decision_id": row["decision_id"]}

    def conflict_decisions(self, conflict_key: str | None = None) -> list[dict]:
        if conflict_key:
            return self.query("SELECT * FROM CONFLICT_DECISION WHERE conflict_key = ? "
                              "ORDER BY conflict_decision_id", (conflict_key,))
        return self.query("SELECT * FROM CONFLICT_DECISION ORDER BY conflict_decision_id")

    def accept_metric_name(self, run_id: str, metric_id: str, reviewer: str,
                           new_name: str = "", definition_status: str = "Proposed",
                           note: str = "", decided_at: str | None = None) -> dict:
        """Accept or rename an AI-drafted metric name; the prior name is kept in history."""
        if not reviewer:
            raise ProposeOnlyError("accepting an AI-drafted name requires a steward")
        if definition_status not in DEFINITION_STATUSES:
            raise ValueError(f"'{definition_status}' is not a definition status; expected one of "
                             + ", ".join(DEFINITION_STATUSES))
        rows = self.query("SELECT * FROM KPI_CANONICAL WHERE run_id = ? AND metric_id = ?",
                          (run_id, metric_id))
        if not rows:
            raise KeyError(f"metric {metric_id} not found in run {run_id}")
        metric = rows[0]
        name = (new_name or "").strip() or metric["canonical_name"]
        if len(name) > 200:
            raise ValueError("a metric name is capped at 200 characters")
        change = "renamed" if name != metric["canonical_name"] else "accepted"
        with self.transaction() as cur:
            row = self.record_decision(
                ReviewDecision(candidate_id=metric_id, decision="Override",
                               reason_code="wrong_name" if change == "renamed" else "name_accepted",
                               reviewer=reviewer,
                               decided_at=decided_at or "", field_overridden="canonical_name",
                               new_value=name, run_id=run_id,
                               note=note or f"metric name {change}; definition {definition_status}"),
                subject_type="metric", actor_role="steward",
                previous_value=metric["canonical_name"])
            cur.execute(
                "INSERT INTO KPI_CANONICAL_HISTORY (run_id, metric_id, fingerprint, canonical_name, "
                "name_status, definition_status, change, changed_at, changed_by, decision_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, metric_id, metric["fingerprint"], metric["canonical_name"],
                 metric["name_status"], metric.get("definition_status") or "Draft",
                 f"before {change}", row["decided_at"], reviewer, row["decision_id"]))
            cur.execute(
                "INSERT INTO METRIC_NAME_DECISION (fingerprint, metric_id, previous_name, new_name, "
                "definition_status, steward, decided_at, run_id, decision_id, kpi_ids) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (metric["fingerprint"], metric_id, metric["canonical_name"], name,
                 definition_status, reviewer, row["decided_at"], run_id, row["decision_id"],
                 metric.get("kpi_ids") or "[]"))
            cur.execute(
                "UPDATE KPI_CANONICAL SET canonical_name = ?, name_status = 'ACCEPTED', "
                "definition_status = ? WHERE run_id = ? AND metric_id = ?",
                (name, definition_status, run_id, metric_id))
        return {"metric_id": metric_id, "fingerprint": metric["fingerprint"],
                "previous_name": metric["canonical_name"], "new_name": name,
                "name_status": "ACCEPTED", "definition_status": definition_status,
                "steward": reviewer, "decided_at": row["decided_at"],
                "decision_id": row["decision_id"]}

    def metric_name_decisions(self, fingerprint: str | None = None) -> list[dict]:
        if fingerprint:
            return self.query("SELECT * FROM METRIC_NAME_DECISION WHERE fingerprint = ? "
                              "ORDER BY name_decision_id", (fingerprint,))
        return self.query("SELECT * FROM METRIC_NAME_DECISION ORDER BY name_decision_id")

    def metric_history(self, run_id: str, metric_id: str) -> list[dict]:
        return self.query("SELECT * FROM KPI_CANONICAL_HISTORY WHERE run_id = ? AND metric_id = ? "
                          "ORDER BY history_id", (run_id, metric_id))

    # -- candidate edits by a reviewer (Merge / Split) -----------------------
    def update_candidate_payload(self, run_id: str, candidate_id: str, payload: dict,
                                 grain: str | None = None, reason: str = "",
                                 decision_id: int | None = None, actor: str = "",
                                 at: str | None = None) -> None:
        """Snapshot the pre-change payload, then replace it (R-15)."""
        with self.transaction() as cur:
            current = self.candidate(run_id, candidate_id)
            if current is None:
                raise KeyError(f"candidate {candidate_id} not found in run {run_id}")
            cur.execute(
                "INSERT INTO DP_CANDIDATE_PAYLOAD_HISTORY (run_id, candidate_id, payload, grain, "
                "reason, decision_id, at) VALUES (?,?,?,?,?,?,?)",
                (run_id, candidate_id, _json(current["payload"]), current["grain"], reason,
                 decision_id, _ledger.normalize_timestamp(at)))
            if grain is None:
                cur.execute("UPDATE DP_CANDIDATE SET payload = ? WHERE run_id = ? AND candidate_id = ?",
                            (_json(payload), run_id, candidate_id))
            else:
                cur.execute("UPDATE DP_CANDIDATE SET payload = ?, grain = ? "
                            "WHERE run_id = ? AND candidate_id = ?",
                            (_json(payload), grain, run_id, candidate_id))

    def add_candidate_metrics(self, run_id: str, candidate_id: str, metric_ids: Iterable[str]) -> None:
        with self.transaction() as cur:
            cur.executemany("INSERT INTO DP_CANDIDATE_METRIC (run_id, candidate_id, metric_id) "
                            "VALUES (?,?,?)", [(run_id, candidate_id, m) for m in metric_ids])

    def create_candidate_row(self, run_id: str, row: dict) -> None:
        """Insert a reviewer-created (split) candidate; status is forced to Proposed."""
        if STATUS_ORDER.index(row.get("status", "Proposed")) > STATUS_ORDER.index(ENGINE_MAX_STATUS):
            raise ProposeOnlyError("a reviewer-created candidate starts at Proposed")
        payload = dict(row.get("payload") or {})
        fingerprints = self._fingerprints(run_id)
        lineage = _lineage_id([fingerprints.get(m, m) for m in payload.get("metric_ids", [])],
                              row.get("grain", ""))
        payload["lineage_id"] = lineage
        columns = ("run_id", "candidate_id", "proposed_name", "purpose", "archetype", "tier",
                   "grain", "domain", "sub_domain", "owner_candidate", "steward_candidate",
                   "status", "archetype_confidence", "archetype_runner_up", "tier_confidence",
                   "name_status", "parent_candidate_id", "origin", "as_of_date", "payload",
                   "lineage_id")
        values = {**row, "run_id": run_id, "payload": _json(payload), "lineage_id": lineage,
                  "status": row.get("status", "Proposed")}
        with self.transaction() as cur:
            cur.execute(
                f"INSERT INTO DP_CANDIDATE ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                [values.get(c, "") for c in columns])
            cur.executemany("INSERT INTO DP_CANDIDATE_METRIC (run_id, candidate_id, metric_id) "
                            "VALUES (?,?,?)",
                            [(run_id, row["candidate_id"], m) for m in payload.get("metric_ids", [])])

    def payload_history(self, run_id: str, candidate_id: str) -> list[dict]:
        rows = self.query("SELECT * FROM DP_CANDIDATE_PAYLOAD_HISTORY WHERE run_id = ? AND "
                          "candidate_id = ? ORDER BY history_id", (run_id, candidate_id))
        for row in rows:
            row["payload"] = json.loads(row["payload"] or "{}")
        return rows

    # -- reads ----------------------------------------------------------
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
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

    def previous_run_id(self, run_id: str) -> str | None:
        """The published run that precedes ``run_id`` for the same industry and mode."""
        current = self.run(run_id)
        if current is None:
            return None
        rows = self.query(
            "SELECT run_id FROM RUN WHERE run_id != ? AND industry = ? AND mode = ? "
            "AND started_at <= ? ORDER BY started_at DESC LIMIT 1",
            (run_id, current["industry"], current["mode"], current["started_at"]))
        return rows[0]["run_id"] if rows else None

    def candidates(self, run_id: str, status: str | None = None) -> list[dict]:
        sql = "SELECT * FROM DP_CANDIDATE WHERE run_id = ?"
        params: tuple = (run_id,)
        if status:
            sql += " AND status = ?"
            params = (run_id, status)
        rows = self.query(sql + " ORDER BY rowid", params)
        for row in rows:
            row["payload"] = json.loads(row["payload"] or "{}")
        return rows

    def candidate(self, run_id: str, candidate_id: str) -> dict | None:
        rows = self.query("SELECT * FROM DP_CANDIDATE WHERE run_id = ? AND candidate_id = ?",
                          (run_id, candidate_id))
        if not rows:
            return None
        row = rows[0]
        row["payload"] = json.loads(row["payload"] or "{}")
        return row

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
                              "ORDER BY decision_id DESC", (run_id,))
        return self.query("SELECT * FROM REVIEW_DECISION ORDER BY decision_id DESC")

    def weights(self, weight_version: str | None = None) -> list[dict]:
        if weight_version:
            return self.query("SELECT * FROM SCORE_WEIGHT WHERE weight_version = ? ORDER BY rowid",
                              (weight_version,))
        return self.query("SELECT * FROM SCORE_WEIGHT ORDER BY effective_from DESC, rowid")

    def run_delta(self, run_id: str) -> list[dict]:
        return self.query("SELECT * FROM RUN_DELTA WHERE run_id = ? ORDER BY rowid", (run_id,))


def conflict_key(fingerprint_a: str, fingerprint_b: str) -> str:
    """Run-independent identity of a conflict: the two fingerprints, order-free."""
    return _ledger.stable_key(*sorted((fingerprint_a or "", fingerprint_b or "")))


def _weights_from_rows(rows: list[dict], version: str, approved_by: str,
                       note: str | None) -> ScoreWeights:
    dimensions: dict[str, float] = {}
    features: dict[str, dict[str, float]] = {}
    effective_from = INITIAL_WEIGHT_EFFECTIVE_FROM
    for row in rows:
        effective_from = row.get("effective_from") or effective_from
        if row["feature"] == "__composite__":
            dimensions[row["dimension"]] = float(row["weight"])
        else:
            features.setdefault(row["dimension"], {})[row["feature"]] = float(row["weight"])
    if not dimensions:
        dimensions = dict(DIMENSION_WEIGHTS)
    if not features:
        features = {k: dict(v) for k, v in FEATURE_WEIGHTS.items()}
    return ScoreWeights(weight_version=version, effective_from=effective_from,
                        dimensions=dimensions, features=features,
                        note=note or (rows[0].get("note") if rows else "") or "",
                        approved_by=approved_by or "")


def _candidate_payload(candidate: Candidate) -> dict:
    payload = asdict(candidate)
    payload["grain_ambiguity"] = getattr(candidate, "_grain_ambiguity", 0.0)
    payload["usage_weight"] = getattr(candidate, "_usage_weight", 0.0)
    payload["classification_rationale"] = getattr(candidate, "_classification_rationale", {})
    payload["reuse_communities"] = getattr(candidate, "_reuse_communities", 0)
    return payload
