"""Effort model: a T-shirt size and Stage 1-6 weeks from named drivers (R-10).

Scoring says which candidate is worth most; this says what each one costs to
open. Every driver is a count the run already holds, each converts to points
through a published rate, and the rows behind each driver are kept as evidence
so a delivery lead can argue with the number rather than with the engine.
Nothing here reads the clock.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import Candidate, EvidenceRow, KnowledgeGraph

EFFORT_VERSION = "effort-1.0"

# Points per unit of each driver. One point is roughly one loaded team-day.
DRIVER_RATES: dict[str, float] = {
    "metric_count": 1.0,               # per canonical metric to model and certify
    "source_systems": 4.0,             # per distinct source system to onboard
    "open_conflicts": 1.5,             # per adjudication to run with a steward
    "pii_attributes": 1.0,             # per PII column through Stage 9 review
    "attributes_without_definition": 0.5,  # per definition to backfill
    "quarantined_lineage_rows": 0.5,   # per lineage row to trace by hand
    "opaque_metrics": 2.0,             # per manual definition to write
    "cross_tool_spread": 6.0,          # once, when Cognos and Power BI both feed it
    "report_complexity": 10.0,         # x mean complexity_score of covered reports
    "grain_ambiguity": 12.0,           # x grain ambiguity share (0..1)
    "readiness_gap": 8.0,              # x (1 - readiness) from the critic
}
SIZE_BANDS = (("XS", 12.0), ("S", 24.0), ("M", 45.0), ("L", 75.0), ("XL", float("inf")))

# Indicative Stage 1-6 weeks by size; the drivers then stretch specific stages.
BASE_STAGE_WEEKS: dict[str, dict[int, float]] = {
    "XS": {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1},
    "S":  {1: 1, 2: 1, 3: 2, 4: 1, 5: 2, 6: 2},
    "M":  {1: 2, 2: 2, 3: 3, 4: 2, 5: 3, 6: 3},
    "L":  {1: 2, 2: 3, 3: 4, 4: 3, 5: 4, 6: 5},
    "XL": {1: 3, 2: 4, 3: 6, 4: 4, 5: 6, 6: 7},
}
STAGE_NAMES = {1: "Consumption Discovery", 2: "Charter", 3: "Source Discovery",
               4: "Design", 5: "Attribute Register", 6: "Semantic Model"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_EFFORT (
    run_id TEXT, candidate_id TEXT, effort_version TEXT, points REAL, size TEXT,
    total_weeks REAL, stage_weeks TEXT, drivers TEXT, evidence TEXT,
    PRIMARY KEY (run_id, candidate_id)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass
class EffortDriver:
    name: str
    value: float
    rate: float
    points: float
    detail: str


@dataclass
class EffortEstimate:
    candidate_id: str
    effort_version: str
    points: float
    size: str
    stage_weeks: dict[int, float]
    total_weeks: float
    drivers: list[EffortDriver] = field(default_factory=list)
    evidence: list[EvidenceRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "effort_version": self.effort_version,
            "points": self.points,
            "size": self.size,
            "stage_weeks": {str(k): v for k, v in self.stage_weeks.items()},
            "total_weeks": self.total_weeks,
            "drivers": [asdict(d) for d in self.drivers],
            "evidence": [asdict(e) for e in self.evidence],
        }


def size_for(points: float) -> str:
    for size, ceiling in SIZE_BANDS:
        if points <= ceiling:
            return size
    return "XL"


def estimate_effort(candidate: Candidate, result: Any, graph: KnowledgeGraph | None = None,
                    canonical: Any = None) -> EffortEstimate:
    """Effort for one candidate from a RunResult (or a graph plus canonicalization)."""
    graph = graph or result.graph
    canonical = canonical or result.canonical
    metrics = [canonical.metrics[m] for m in candidate.metric_ids if m in canonical.metrics]
    open_ids = {c.conflict_id for c in canonical.conflicts if c.resolution_status == "OPEN"}
    drivers: list[EffortDriver] = []
    evidence: list[EvidenceRow] = []
    cid = candidate.candidate_id

    def driver(name: str, value: float, detail: str) -> None:
        rate = DRIVER_RATES[name]
        drivers.append(EffortDriver(name, round(float(value), 4), rate,
                                    round(float(value) * rate, 2), detail))

    driver("metric_count", len(metrics), f"{len(metrics)} canonical metrics to certify")
    for metric in metrics[:6]:
        evidence.append(EvidenceRow(cid, "effort:metric_count", "metric", metric.metric_id,
                                    metric.canonical_name))

    systems = sorted({s.system for s in candidate.sources if s.system})
    driver("source_systems", len(systems), "source systems: " + ", ".join(systems))
    for system in systems:
        evidence.append(EvidenceRow(cid, "effort:source_systems", "system", system,
                                    "distinct source system to onboard"))

    open_conflicts = [c for c in candidate.conflicts if c in open_ids]
    driver("open_conflicts", len(open_conflicts),
           f"{len(open_conflicts)} open conflicts for a steward to adjudicate")
    for conflict_id in open_conflicts[:6]:
        evidence.append(EvidenceRow(cid, "effort:open_conflicts", "conflict", conflict_id,
                                    "adjudication session with the steward"))

    pii = [a for a in candidate.attributes if a.pii_flag]
    driver("pii_attributes", len(pii), f"{len(pii)} PII attributes through Stage 9 review")
    for attribute in pii[:6]:
        evidence.append(EvidenceRow(cid, "effort:pii_attributes", "column", attribute.column_fqn,
                                    f"{attribute.sensitivity}, PII"))

    undefined = [a for a in candidate.attributes if a.role == "operand" and not a.definition]
    driver("attributes_without_definition", len(undefined),
           f"{len(undefined)} operand columns with no catalog definition")
    for attribute in undefined[:6]:
        evidence.append(EvidenceRow(cid, "effort:attributes_without_definition", "column",
                                    attribute.column_fqn, "definition to backfill"))

    kpi_ids = {k for m in metrics for k in m.kpi_ids}
    quarantined = [q for q in graph.quarantine if q.kpi_id in kpi_ids]
    driver("quarantined_lineage_rows", len(quarantined),
           f"{len(quarantined)} lineage rows quarantined")
    for row in quarantined[:6]:
        evidence.append(EvidenceRow(cid, "effort:quarantined_lineage_rows", "kpi", row.kpi_id,
                                    f"{row.reason_code}: {row.raw_reference}"))

    opaque = [m for m in metrics if m.opaque]
    driver("opaque_metrics", len(opaque), f"{len(opaque)} opaque calculations to define by hand")
    for metric in opaque[:6]:
        evidence.append(EvidenceRow(cid, "effort:opaque_metrics", "metric", metric.metric_id,
                                    metric.canonical_name))

    tools = sorted({t for m in metrics for t in m.tools})
    driver("cross_tool_spread", 1 if len(tools) > 1 else 0, "tools: " + ", ".join(tools))

    covered = [graph.reports[r.report_id] for r in candidate.reports
               if r.coverage >= 1.0 and r.report_id in graph.reports]
    mean_complexity = (sum(r.complexity_score for r in covered) / len(covered)) if covered else 0.0
    driver("report_complexity", round(mean_complexity, 4),
           f"mean complexity_score {mean_complexity:.2f} over {len(covered)} fully covered reports")
    for report in sorted(covered, key=lambda r: -r.complexity_score)[:4]:
        evidence.append(EvidenceRow(cid, "effort:report_complexity", "report", report.report_id,
                                    f"complexity {report.complexity_score:.2f}"))

    ambiguity = float(getattr(candidate, "_grain_ambiguity", 0.0))
    driver("grain_ambiguity", ambiguity,
           f"grain ambiguity {ambiguity:.2f}"
           + ("" if ambiguity == 0 else "; metrics evaluate at more than one grain"))

    readiness = float(getattr(candidate, "_readiness", 0.0))
    driver("readiness_gap", round(1.0 - readiness / 100.0, 4),
           f"DPF readiness {readiness:.0f}% from the critic's checklist")

    points = round(sum(d.points for d in drivers), 1)
    size = size_for(points)
    stage_weeks = _stage_weeks(size, drivers)
    return EffortEstimate(
        candidate_id=cid, effort_version=EFFORT_VERSION, points=points, size=size,
        stage_weeks=stage_weeks, total_weeks=round(sum(stage_weeks.values()), 1),
        drivers=drivers, evidence=evidence,
    )


def _stage_weeks(size: str, drivers: list[EffortDriver]) -> dict[int, float]:
    """Base weeks by size, stretched where a driver lands on a specific stage."""
    weeks = {k: float(v) for k, v in BASE_STAGE_WEEKS[size].items()}
    by_name = {d.name: d for d in drivers}
    # Stage 3 grows with systems to onboard and lineage to trace by hand.
    weeks[3] += 0.5 * max(0, by_name["source_systems"].value - 1)
    weeks[3] += 0.1 * by_name["quarantined_lineage_rows"].value
    # Stage 5 grows with definitions to backfill.
    weeks[5] += 0.1 * by_name["attributes_without_definition"].value
    # Stage 6 grows with adjudications and opaque definitions; one steward session
    # settles about three conflicts a week.
    weeks[6] += by_name["open_conflicts"].value / 3.0
    weeks[6] += 0.5 * by_name["opaque_metrics"].value
    # Stage 1 grows with unconfirmed consumers.
    weeks[1] += 2.0 * by_name["readiness_gap"].value
    return {k: round(v, 1) for k, v in weeks.items()}


def effort_for_run(result: Any) -> dict[str, EffortEstimate]:
    return {c.candidate_id: estimate_effort(c, result) for c in result.candidates}


def save_efforts(connection: sqlite3.Connection, run_id: str,
                 efforts: dict[str, EffortEstimate]) -> None:
    ensure_schema(connection)
    for candidate_id, effort in efforts.items():
        payload = effort.to_dict()
        connection.execute(
            "INSERT OR REPLACE INTO DP_CANDIDATE_EFFORT VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, candidate_id, effort.effort_version, effort.points, effort.size,
             effort.total_weeks, json.dumps(payload["stage_weeks"]),
             json.dumps(payload["drivers"]), json.dumps(payload["evidence"])))
    connection.commit()


def load_efforts(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM DP_CANDIDATE_EFFORT WHERE run_id = ? "
                              "ORDER BY points DESC", (run_id,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        for key in ("stage_weeks", "drivers", "evidence"):
            item[key] = json.loads(item[key] or "{}")
        out.append(item)
    return out
