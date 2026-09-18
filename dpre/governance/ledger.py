"""Run-independent governance ledgers and the audit-chain primitives.

The RECO tables in ``dpre.store`` are keyed by run. What a steward, a reviewer
or a council decides must outlive the run it was made on (R-07, R-09, R-14),
so those decisions live in the tables defined here and are re-applied to each
new run by ``dpre.governance``. Every ledger is append-only: SQLite triggers
refuse UPDATE and DELETE, and ``REVIEW_DECISION`` rows are hash-chained so a
change made around the application is detectable, not merely disallowed
(R-15).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS REPORT_OVERRIDE (
    override_id INTEGER PRIMARY KEY AUTOINCREMENT, report_id TEXT NOT NULL,
    field TEXT NOT NULL, value TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL,
    note TEXT, run_id TEXT, decision_id INTEGER
);
CREATE TABLE IF NOT EXISTS CANDIDATE_CONSUMER_CONFIRMATION (
    confirmation_id INTEGER PRIMARY KEY AUTOINCREMENT, lineage_id TEXT NOT NULL,
    run_id TEXT, candidate_id TEXT, business_unit TEXT NOT NULL,
    blocked_decision TEXT NOT NULL, latency_tolerance TEXT NOT NULL,
    consequence TEXT NOT NULL, confirmed_by TEXT NOT NULL, confirmed_at TEXT NOT NULL,
    decision_id INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS GATE_WAIVER (
    waiver_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, candidate_id TEXT,
    lineage_id TEXT, gate_waived TEXT NOT NULL, waiver_reason TEXT NOT NULL,
    reviewer TEXT NOT NULL, second_approver TEXT NOT NULL, decision_id INTEGER,
    granted_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN',
    closed_at TEXT, closed_by TEXT, close_note TEXT
);
CREATE TABLE IF NOT EXISTS CONFIG_CHANGE (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT, changed_at TEXT NOT NULL,
    actor TEXT NOT NULL, field TEXT NOT NULL, before TEXT, after TEXT, note TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS WEIGHT_APPROVAL (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT, weight_version TEXT NOT NULL,
    approved_by TEXT NOT NULL, approved_at TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS RUN_DELTA (
    run_id TEXT NOT NULL, previous_run_id TEXT NOT NULL, previous_candidate_id TEXT,
    candidate_id TEXT, lineage_id TEXT, jaccard REAL, change TEXT NOT NULL,
    status_carried TEXT, detail TEXT, computed_at TEXT
);
CREATE TABLE IF NOT EXISTS BENEFIT_PLAN (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT, lineage_id TEXT NOT NULL, run_id TEXT,
    candidate_id TEXT, decision_id INTEGER, planned_at TEXT NOT NULL, planned_by TEXT,
    reports_retired INTEGER, conflicts_resolved INTEGER, users_served INTEGER,
    usage_weight REAL, value_json TEXT
);
CREATE TABLE IF NOT EXISTS BENEFIT_ACTUAL (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, lineage_id TEXT NOT NULL,
    event_type TEXT NOT NULL, subject_id TEXT, occurred_at TEXT NOT NULL,
    confirmed_by TEXT NOT NULL, note TEXT, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS CONFLICT_DECISION (
    conflict_decision_id INTEGER PRIMARY KEY AUTOINCREMENT, conflict_key TEXT NOT NULL,
    label TEXT, fingerprint_a TEXT, fingerprint_b TEXT, status TEXT NOT NULL,
    authoritative_metric_id TEXT, rationale TEXT, steward TEXT NOT NULL,
    decided_at TEXT NOT NULL, run_id TEXT, conflict_id TEXT, decision_id INTEGER,
    kpi_ids_a TEXT, kpi_ids_b TEXT
);
CREATE TABLE IF NOT EXISTS METRIC_NAME_DECISION (
    name_decision_id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL,
    metric_id TEXT, previous_name TEXT, new_name TEXT NOT NULL,
    definition_status TEXT NOT NULL, steward TEXT NOT NULL, decided_at TEXT NOT NULL,
    run_id TEXT, decision_id INTEGER, kpi_ids TEXT
);
CREATE TABLE IF NOT EXISTS KPI_CANONICAL_HISTORY (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    metric_id TEXT NOT NULL, fingerprint TEXT, canonical_name TEXT, name_status TEXT,
    definition_status TEXT, change TEXT NOT NULL, changed_at TEXT NOT NULL,
    changed_by TEXT, decision_id INTEGER
);
CREATE TABLE IF NOT EXISTS GOVERNANCE_SEED (
    run_id TEXT NOT NULL, subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
    ledger_key TEXT, applied_status TEXT, outcome TEXT NOT NULL, detail TEXT,
    seeded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_STATUS_HISTORY (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL, from_status TEXT, to_status TEXT NOT NULL,
    decision_id INTEGER, actor TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_PAYLOAD_HISTORY (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL, payload TEXT NOT NULL, grain TEXT, reason TEXT,
    decision_id INTEGER, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS IX_CONFIRMATION_LINEAGE ON CANDIDATE_CONSUMER_CONFIRMATION (lineage_id);
CREATE INDEX IF NOT EXISTS IX_REPORT_OVERRIDE ON REPORT_OVERRIDE (report_id, field);
CREATE INDEX IF NOT EXISTS IX_CONFLICT_DECISION ON CONFLICT_DECISION (conflict_key);
CREATE INDEX IF NOT EXISTS IX_NAME_DECISION ON METRIC_NAME_DECISION (fingerprint);
CREATE INDEX IF NOT EXISTS IX_STATUS_HISTORY ON DP_CANDIDATE_STATUS_HISTORY (run_id, candidate_id);
CREATE INDEX IF NOT EXISTS IX_RUN_DELTA ON RUN_DELTA (run_id);
"""

# Ledgers an auditor must be able to trust: no row is ever changed or removed.
# GATE_WAIVER is closed by a status flip, so it is not in this list; its
# opening and closing are both recorded in REVIEW_DECISION.
APPEND_ONLY_TABLES = (
    "REVIEW_DECISION", "SCORE_WEIGHT", "CONFIG_CHANGE", "WEIGHT_APPROVAL",
    "DP_CANDIDATE_STATUS_HISTORY", "DP_CANDIDATE_PAYLOAD_HISTORY", "CONFLICT_DECISION",
    "METRIC_NAME_DECISION", "KPI_CANONICAL_HISTORY", "BENEFIT_ACTUAL", "BENEFIT_PLAN",
    "CANDIDATE_CONSUMER_CONFIRMATION", "REPORT_OVERRIDE",
)


def append_only_triggers() -> list[str]:
    statements = []
    for table in APPEND_ONLY_TABLES:
        for verb in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER IF NOT EXISTS TRG_{table}_NO_{verb} BEFORE {verb} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{table} is append-only: {verb} is refused'); END")
    return statements


def append_only_triggers_sql() -> str:
    return ";\n".join(append_only_triggers()) + ";"


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the governance ledgers and their append-only triggers (idempotent).

    Statements run one by one rather than through ``executescript``, which
    would COMMIT a transaction the caller has open (``Store.transaction``).
    """
    for statement in SCHEMA.split(";"):
        if statement.strip():
            connection.execute(statement)
    for statement in append_only_triggers():
        connection.execute(statement)


# --------------------------------------------------------------------------
# Time and hashing
# --------------------------------------------------------------------------

def utc_now() -> str:
    """Timezone-aware UTC ISO timestamp; the only wall-clock read in the ledgers."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def normalize_timestamp(value: str | None) -> str:
    """Accept a caller-supplied timestamp (tests, replays) or stamp now in UTC."""
    if not value:
        return utc_now()
    parsed = _dt.datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc).isoformat(timespec="seconds")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def row_hash(fields: dict[str, Any], prev_hash: str) -> str:
    """SHA-256 over the row's governed fields chained to the previous row's hash."""
    material = prev_hash + "|" + canonical_json(fields)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def stable_key(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()
