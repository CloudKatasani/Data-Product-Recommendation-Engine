"""The decision register for D-01..D-08, with the honest enforcement status (R-25).

Specification section 15.2 lists eight open decisions and a proposed owner for
each. ``docs/implementation-map.md`` said they were "exposed as configuration";
that overstated it. What is true today, checked against the code:

* D-01, D-02, D-03 and D-04 are ``EngineConfig`` fields or a weight version,
  so a run records the position it took; nobody had recorded who decided it.
* D-05 is a fact of the build (the Power BI adapter exists), not a decision
  anyone took about the programme.
* D-06 is written to ``EngineConfig.to_dict`` and read nowhere: the critic's
  S9-privacy finding is fixed at severity ``minor`` whatever the threshold.
* D-07 is assumed: ``dpre/seeds/catalog_payload.py`` writes a payload for the
  catalog named on the run with no record that the catalog admin agreed the
  asset type.
* D-08 is partly enforced by the HTTP role matrix (``dpre/server/security.py``
  ``ROLE_ACTIONS`` and ``DOMAIN_SCOPED``), which is a deployment fact rather
  than a decision with an owner.

This module gives each decision a register row: the position the engine is
running on (read from the configuration, live), who owns it, whether the
position is enforced, recorded only, assumed or not addressed, and a ledger
(``DECISION_LOG``, append-only) for the moment a named person takes the
decision with a rationale. A run snapshots the register so the executive pack
can say "this ranking assumed D-03 = yes, decided by nobody yet".
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from ..config import EngineConfig

ENFORCED = "enforced"
RECORDED = "recorded only"
ASSUMED = "assumed"
NOT_ADDRESSED = "not addressed"
ENFORCEMENT_LEVELS = (ENFORCED, RECORDED, ASSUMED, NOT_ADDRESSED)

STATUS_DEFAULT = "engine default"
STATUS_DECIDED = "decided"

SCHEMA = """
CREATE TABLE IF NOT EXISTS DECISION_LOG (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT NOT NULL, position TEXT NOT NULL,
    decided_by TEXT NOT NULL, decided_at TEXT NOT NULL, rationale TEXT NOT NULL,
    run_id TEXT, engagement_id TEXT
);
CREATE TABLE IF NOT EXISTS DECISION_REGISTER (
    run_id TEXT NOT NULL, ref TEXT NOT NULL, title TEXT, position TEXT, status TEXT,
    decided_by TEXT, decided_at TEXT, rationale TEXT, owner TEXT, enforcement TEXT,
    implemented_in TEXT, snapshot_at TEXT,
    PRIMARY KEY (run_id, ref)
);
CREATE TRIGGER IF NOT EXISTS TRG_DECISION_LOG_NO_UPDATE BEFORE UPDATE ON DECISION_LOG
BEGIN SELECT RAISE(ABORT, 'DECISION_LOG is append-only'); END;
CREATE TRIGGER IF NOT EXISTS TRG_DECISION_LOG_NO_DELETE BEFORE DELETE ON DECISION_LOG
BEGIN SELECT RAISE(ABORT, 'DECISION_LOG is append-only'); END;
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


class DecisionError(ValueError):
    """Raised for an unknown decision ref or an unattributed decision."""


@dataclass(frozen=True)
class DecisionSpec:
    ref: str
    title: str
    why_it_matters: str
    proposed_owner: str
    implemented_in: str
    enforcement: str
    enforcement_note: str
    assumption_keys: tuple[str, ...] = ()


DECISIONS: tuple[DecisionSpec, ...] = (
    DecisionSpec(
        "D-01", "Usage window: 12 or 24 months, and how seasonal reports are treated",
        "Changes demand scores for annual and rate-case reports", "Data product council",
        "dpre/config.py::EngineConfig.usage_window_months, recency_half_life_months",
        RECORDED,
        "The window is recorded on the run; the demand features read the 12-month extract "
        "columns (run_count_12m, distinct_users_12m) whatever the field says. Seasonal "
        "reports are handled only through the decision-critical override (section 15.1).",
        ("usage_window_months", "recency_half_life_months")),
    DecisionSpec(
        "D-02", "Minimum community size before a candidate is Proposed rather than Exploratory",
        "Controls backlog length and reviewer load", "Data product council",
        "dpre/config.py::ClusterConfig.min_metrics, min_business_units",
        ENFORCED,
        "Applied in dpre/cluster/generator.py::generate_candidates; changeable through "
        "POST /api/v1/config, and the change is written to CONFIG_CHANGE when the server "
        "records it.",
        ("cluster.min_metrics", "cluster.min_business_units")),
    DecisionSpec(
        "D-03", "Whether 'Keep' reports count toward consolidation at all",
        "A Keep report replaced by a product is a win; one left untouched is not",
        "Rationalization lead",
        "dpre/config.py::EngineConfig.keep_counts_toward_consolidation, DISPOSITION_WEIGHT['keep']",
        ENFORCED,
        "dpre/score/scorer.py zeroes a Keep report's contribution when the flag is false and "
        "otherwise counts it at the disposition weight (0.5).",
        ("keep_counts_toward_consolidation", "disposition_weight.keep")),
    DecisionSpec(
        "D-04", "Initial score weights, and who approves a change to them",
        "Determines the first ranking; must be visible and contestable", "Data product council",
        "dpre/config.py::ScoreWeights; dpre/store.py::Store.approve_weight_version",
        ENFORCED,
        "The initial version ships unapproved (store note 'pending council approval (D-04)'); "
        "versions are immutable, approval is a separate ledger row, and an approver may not "
        "have trained the proposal (dpre/server/security.py::assert_weight_approver_independent).",
        ("weights.dimensions.demand", "weights.dimensions.consolidation",
         "weights.dimensions.feasibility", "weights.dimensions.risk")),
    DecisionSpec(
        "D-05", "Whether Power BI lineage is a Phase 3 adapter or a separate programme",
        "The estate is mixed; one graph needs both", "Programme sponsor",
        "dpre/ingest/adapters/powerbi.py; dpre/graph/builder.py (one KPI node, many report edges)",
        ASSUMED,
        "The build treats Power BI as a first-release adapter into one graph. That is an "
        "engineering position, not a programme decision; the sponsor still has to take it.",
        ()),
    DecisionSpec(
        "D-06", "Sensitivity threshold that forces privacy review before Proposed",
        "Aligns with DPF Stage 9 veto", "Privacy officer",
        "dpre/config.py::EngineConfig.sensitivity_review_threshold",
        RECORDED,
        "Written to EngineConfig.to_dict and read by nothing. The critic raises S9-privacy at "
        "severity 'minor' when any PII attribute is in scope (dpre/narrate/critic.py), "
        "regardless of the threshold. Enforcement would be a status cap in "
        "dpre/score/scorer.py::_apply_gates or a blocker in the critic.",
        ("sensitivity_review_threshold", "sensitivity_rank.restricted")),
    DecisionSpec(
        "D-07", "Catalog asset type and attributes for a Proposed data product",
        "The registration payload cannot be built without it", "Catalog admin",
        "dpre/seeds/catalog_payload.py::catalog_payload",
        ASSUMED,
        "The payload is written in a fixed shape for the catalog named on the run "
        "(collibra or alation) with no record that the catalog admin agreed the asset type "
        "or its attributes; import is blocked while any name is AI_DRAFT.",
        ()),
    DecisionSpec(
        "D-08", "Which reviewer role may Accept, per domain",
        "Propose-only means nothing if acceptance is undefined", "Programme sponsor",
        "dpre/server/security.py::ROLE_ACTIONS, DOMAIN_SCOPED; "
        "dpre/engagement/model.py::reviewer_may (engagement roster)",
        ENFORCED,
        "Over HTTP the 'review' action needs the reviewer role and a domain in the principal's "
        "scope. The engagement roster (ENGAGEMENT_REVIEWER) records who holds which role for "
        "which domains on this engagement; the library path (dpre/review/workflow.py::review) "
        "still trusts the reviewer string it is given.",
        ("engine_max_status",)),
)


def _position(spec: DecisionSpec, config: EngineConfig, catalog: str) -> str:
    """What the engine is running on for each decision, read live."""
    cluster = config.cluster
    weights = config.weights
    if spec.ref == "D-01":
        return (f"{config.usage_window_months} months, recency half-life "
                f"{config.recency_half_life_months} months")
    if spec.ref == "D-02":
        return (f"at least {cluster.min_metrics} canonical metrics and "
                f"{cluster.min_business_units} business units")
    if spec.ref == "D-03":
        return ("yes - Keep reports count at the disposition weight"
                if config.keep_counts_toward_consolidation
                else "no - Keep reports are excluded from consolidation")
    if spec.ref == "D-04":
        approved = weights.approved_by or "nobody yet"
        return f"weight version {weights.weight_version}, approval recorded for: {approved}"
    if spec.ref == "D-05":
        return "one graph over Cognos and Power BI (adapter in the first release)"
    if spec.ref == "D-06":
        return f"privacy review at or above {config.sensitivity_review_threshold} (recorded only)"
    if spec.ref == "D-07":
        return f"payload shaped for {catalog}; asset type not confirmed by the catalog admin"
    if spec.ref == "D-08":
        return "HTTP: role 'reviewer' with domain scope; library: any named reviewer string"
    return ""


def decision_register(config: EngineConfig | None = None, catalog: str = "collibra",
                      connection: sqlite3.Connection | None = None) -> list[dict]:
    """One row per decision: engine position, owner, enforcement and any decision taken."""
    config = config or EngineConfig()
    taken = decisions_taken(connection) if connection is not None else {}
    rows = []
    for spec in DECISIONS:
        row = asdict(spec)
        row["assumption_keys"] = list(spec.assumption_keys)
        row["engine_position"] = _position(spec, config, catalog)
        log = taken.get(spec.ref)
        if log:
            row.update(status=STATUS_DECIDED, position=log["position"],
                       decided_by=log["decided_by"], decided_at=log["decided_at"],
                       rationale=log["rationale"])
        else:
            row.update(status=STATUS_DEFAULT, position=row["engine_position"], decided_by="",
                       decided_at="", rationale="")
        rows.append(row)
    return rows


def decide(connection: sqlite3.Connection, ref: str, position: str, decided_by: str,
           rationale: str, decided_at: str | None = None, run_id: str = "",
           engagement_id: str = "") -> dict:
    """Record that a named person took decision ``ref``; append-only."""
    if ref not in {d.ref for d in DECISIONS}:
        raise DecisionError(f"unknown decision '{ref}'; expected one of "
                            + ", ".join(d.ref for d in DECISIONS))
    if not (decided_by or "").strip():
        raise DecisionError(f"{ref} must name who decided it")
    if not (rationale or "").strip():
        raise DecisionError(f"{ref} needs a rationale a council member can read")
    if not (position or "").strip():
        raise DecisionError(f"{ref} needs the position that was decided")
    ensure_schema(connection)
    at = decided_at or _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    cursor = connection.execute(
        "INSERT INTO DECISION_LOG (ref, position, decided_by, decided_at, rationale, run_id, "
        "engagement_id) VALUES (?,?,?,?,?,?,?)",
        (ref, position.strip(), decided_by.strip(), at, rationale.strip(), run_id, engagement_id))
    connection.commit()
    return {"log_id": cursor.lastrowid, "ref": ref, "position": position.strip(),
            "decided_by": decided_by.strip(), "decided_at": at, "rationale": rationale.strip(),
            "run_id": run_id, "engagement_id": engagement_id}


def decisions_taken(connection: sqlite3.Connection) -> dict[str, dict]:
    """Latest logged decision per ref."""
    ensure_schema(connection)
    cursor = connection.execute("SELECT * FROM DECISION_LOG ORDER BY log_id")
    names = [c[0] for c in cursor.description]
    latest: dict[str, dict] = {}
    for row in cursor.fetchall():
        record = dict(zip(names, row))
        latest[record["ref"]] = record
    return latest


def decision_log(connection: sqlite3.Connection, ref: str | None = None) -> list[dict]:
    ensure_schema(connection)
    if ref:
        cursor = connection.execute("SELECT * FROM DECISION_LOG WHERE ref = ? ORDER BY log_id",
                                    (ref,))
    else:
        cursor = connection.execute("SELECT * FROM DECISION_LOG ORDER BY log_id")
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def snapshot_decisions(connection: sqlite3.Connection, run_id: str,
                       config: EngineConfig | None = None, catalog: str = "collibra",
                       snapshot_at: str = "") -> list[dict]:
    """Persist the register as it stood for a run (DECISION_REGISTER)."""
    ensure_schema(connection)
    rows = decision_register(config, catalog, connection)
    connection.executemany(
        "INSERT OR REPLACE INTO DECISION_REGISTER VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(run_id, r["ref"], r["title"], r["position"], r["status"], r["decided_by"],
          r["decided_at"], r["rationale"], r["proposed_owner"], r["enforcement"],
          r["implemented_in"], snapshot_at) for r in rows])
    connection.commit()
    return rows


def decisions_for_run(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    ensure_schema(connection)
    cursor = connection.execute("SELECT * FROM DECISION_REGISTER WHERE run_id = ? ORDER BY ref",
                                (run_id,))
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def open_decisions(rows: list[dict]) -> list[dict]:
    """Rows still on the engine default: what the council has not yet taken."""
    return [r for r in rows if r["status"] != STATUS_DECIDED]


def render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["| # | Decision | Engine position | Enforcement | Owner | Status | Implemented in |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        status = r["status"]
        if r.get("decided_by"):
            status += f" by {r['decided_by']} on {r['decided_at']}"
        lines.append(f"| {r['ref']} | {r['title']} | {r['position']} | {r['enforcement']} | "
                     f"{r['proposed_owner']} | {status} | `{r['implemented_in']}` |")
    lines.append("")
    for r in rows:
        lines.append(f"**{r['ref']}** - {r['enforcement_note']}")
        lines.append("")
    return "\n".join(lines)
