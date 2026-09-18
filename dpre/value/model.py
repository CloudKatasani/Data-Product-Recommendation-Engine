"""The value model: money and hours behind every candidate (review finding R-08).

Five annual benefit components, each traceable to the reports, conflicts or
packages that produce it, minus a one-off build cost from the effort model;
then payback and a three-year NPV. Two figures are carried for each candidate:

* ``standalone``: what this candidate releases if it is the only one built;
* ``attributed``: its share once every fully covered report is counted once,
  for its primary candidate (portfolio/views.py). Portfolio totals sum the
  attributed figures, so the estate number cannot double-count.

Nothing here reads the clock: everything comes from the run and the rate card.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import Candidate, EvidenceRow, KnowledgeGraph
from ..portfolio.views import attribute_reports, is_hold, is_retirable, package_impact
from .assumptions import ValueAssumptions

VALUE_MODEL_VERSION = "value-1.0"

SCHEMA = """
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_VALUE (
    run_id TEXT, candidate_id TEXT, assumption_version TEXT, model_version TEXT,
    currency TEXT, components TEXT, gross_annual_benefit REAL,
    attributed_annual_benefit REAL, build_cost REAL, payback_months REAL, npv_3y REAL,
    evidence TEXT, basis TEXT,
    PRIMARY KEY (run_id, candidate_id)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass
class ValueComponent:
    name: str                        # report_retirement | licence | infrastructure | ...
    annual_benefit: float            # standalone
    attributed_benefit: float        # after primary-candidate attribution
    driver_count: int
    unit: str
    basis: str
    items: list[str] = field(default_factory=list)


@dataclass
class CandidateValue:
    candidate_id: str
    assumption_version: str
    currency: str
    components: list[ValueComponent]
    gross_annual_benefit: float
    attributed_annual_benefit: float
    build_cost: float
    build_basis: str
    payback_months: float | None
    npv_3y: float
    evidence: list[EvidenceRow]
    basis: str
    model_version: str = VALUE_MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["components"] = [asdict(c) for c in self.components]
        payload["evidence"] = [asdict(e) for e in self.evidence]
        return payload


# --------------------------------------------------------------------------

def compute_value(candidate: Candidate, graph: KnowledgeGraph, conflicts_open: dict[str, Any],
                  assumptions: ValueAssumptions, effort: Any = None,
                  attribution: dict[str, str] | None = None,
                  conflict_attribution: dict[str, str] | None = None) -> CandidateValue:
    """Value of one candidate. ``effort`` is an EffortEstimate or None.

    ``conflicts_open`` maps conflict_id to the MetricConflict for every OPEN
    conflict in the run; ``attribution`` and ``conflict_attribution`` map a
    report or conflict to its primary candidate (defaults: everything is primary,
    which is the standalone view).
    """
    evidence: list[EvidenceRow] = []
    components = [
        _report_retirement(candidate, graph, assumptions, attribution, evidence),
        _licence(candidate, graph, assumptions, attribution, evidence),
        _infrastructure(candidate, graph, assumptions, attribution, evidence),
        _reconciliation(candidate, conflicts_open, assumptions, conflict_attribution, evidence),
        _mis_decision(candidate, graph, conflicts_open, assumptions, attribution, evidence),
    ]
    gross = round(sum(c.annual_benefit for c in components), 2)
    attributed = round(sum(c.attributed_benefit for c in components), 2)
    build_cost, build_basis = _build_cost(effort, assumptions)
    # Payback is undefined without a build cost or without a benefit; the row says
    # so rather than printing a zero that reads as "free".
    payback = (round(12.0 * build_cost / attributed, 1)
               if attributed > 0 and build_cost > 0 else None)
    npv = _npv(attributed, build_cost, assumptions)
    if build_cost > 0:
        evidence.append(EvidenceRow(candidate.candidate_id, "value:build_cost", "effort",
                                    getattr(effort, "size", "n/a"), build_basis))
    return CandidateValue(
        candidate_id=candidate.candidate_id,
        assumption_version=assumptions.version,
        currency=assumptions.currency,
        components=components,
        gross_annual_benefit=gross,
        attributed_annual_benefit=attributed,
        build_cost=round(build_cost, 2),
        build_basis=build_basis,
        payback_months=payback,
        npv_3y=npv,
        evidence=evidence,
        basis=assumptions.basis,
    )


def _primary(attribution: dict[str, str] | None, key: str, candidate_id: str) -> bool:
    return attribution is None or attribution.get(key) == candidate_id


def _report_retirement(candidate, graph, assumptions, attribution, evidence) -> ValueComponent:
    """Maintenance hours released by reports that actually go away."""
    standalone = attributed = 0.0
    count = 0
    items: list[str] = []
    for report in candidate.reports:
        node = graph.reports.get(report.report_id)
        hold = is_hold(node, report.report_name)
        if not is_retirable(report, hold):
            continue
        band = assumptions.complexity_band(node.complexity_score if node else 0.0)
        hours = assumptions.maintenance_hours_per_report.get(band, 0.0)
        share = assumptions.disposition_realisation.get((report.disposition or "keep").lower(), 0.0)
        benefit = hours * assumptions.loaded_hourly_rate * share
        standalone += benefit
        primary = _primary(attribution, report.report_id, candidate.candidate_id)
        if primary:
            attributed += benefit
            count += 1
            items.append(report.report_id)
            evidence.append(EvidenceRow(
                candidate.candidate_id, "value:report_retirement", "report", report.report_id,
                f"{report.report_name}: {band} complexity, {hours:.0f} h/yr x "
                f"{assumptions.loaded_hourly_rate:.0f} x {share:.0%} ({report.disposition})"))
    return ValueComponent(
        "report_retirement", round(standalone, 2), round(attributed, 2), count, "reports",
        "maintenance hours per report by complexity band x loaded rate x disposition "
        "realisation; Keep and regulatory/decision-critical reports excluded", items)


def _licence(candidate, graph, assumptions, attribution, evidence) -> ValueComponent:
    standalone = attributed = 0.0
    count = 0
    items: list[str] = []
    for report in candidate.reports:
        node = graph.reports.get(report.report_id)
        if not is_retirable(report, is_hold(node, report.report_name)):
            continue
        tool = (node.tool if node else "cognos").lower()
        share = assumptions.disposition_realisation.get((report.disposition or "keep").lower(), 0.0)
        benefit = assumptions.licence_cost_per_report_per_year.get(tool, 0.0) * share
        standalone += benefit
        if _primary(attribution, report.report_id, candidate.candidate_id):
            attributed += benefit
            count += 1
            items.append(report.report_id)
            evidence.append(EvidenceRow(
                candidate.candidate_id, "value:licence", "report", report.report_id,
                f"{tool} licence and refresh {benefit:,.0f}/yr"))
    return ValueComponent("licence", round(standalone, 2), round(attributed, 2), count,
                          "reports", "licence and refresh cost per retired report by tool", items)


def _infrastructure(candidate, graph, assumptions, attribution, evidence) -> ValueComponent:
    """A package released only when every report in it goes; partial packages earn nothing."""
    own = package_impact(candidate, graph, attribution or {})
    standalone_pkgs = package_impact(candidate, graph,
                                     {r.report_id: candidate.candidate_id for r in candidate.reports})
    rate = assumptions.infra_cost_per_package_per_year
    for package in own["fully_retirable"]:
        evidence.append(EvidenceRow(candidate.candidate_id, "value:infrastructure", "package",
                                    package, f"package emptied: {rate:,.0f}/yr"))
    return ValueComponent(
        "infrastructure", round(rate * len(standalone_pkgs["fully_retirable"]), 2),
        round(rate * len(own["fully_retirable"]), 2), len(own["fully_retirable"]), "packages",
        "platform cost per package or semantic model fully emptied; partially affected "
        "packages earn nothing", own["fully_retirable"])


def _reconciliation(candidate, conflicts_open, assumptions, conflict_attribution,
                    evidence) -> ValueComponent:
    per_conflict = (assumptions.reconciliation_hours_per_conflict_per_period
                    * assumptions.periods_per_year * assumptions.loaded_hourly_rate)
    open_here = [c for c in candidate.conflicts if c in conflicts_open]
    attributed_ids = [c for c in open_here
                      if _primary(conflict_attribution, c, candidate.candidate_id)]
    for conflict_id in attributed_ids:
        conflict = conflicts_open[conflict_id]
        evidence.append(EvidenceRow(
            candidate.candidate_id, "value:reconciliation", "conflict", conflict_id,
            f"{conflict.label} ({conflict.pattern}): "
            f"{assumptions.reconciliation_hours_per_conflict_per_period:.0f} h x "
            f"{assumptions.periods_per_year} periods"))
    return ValueComponent(
        "conflict_reconciliation", round(per_conflict * len(open_here), 2),
        round(per_conflict * len(attributed_ids), 2), len(attributed_ids), "conflicts",
        "hours per open conflict per period x periods x loaded rate", attributed_ids)


def _mis_decision(candidate, graph, conflicts_open, assumptions, attribution,
                  evidence) -> ValueComponent:
    """Cost avoided where a regulatory or decision-critical report reads a conflicted number.

    Only reports whose metrics are on one side of an OPEN conflict qualify: the
    exposure is a competing definition reaching a filing, not the filing itself.
    """
    conflicted_reports: set[str] = set()
    for conflict in conflicts_open.values():
        conflicted_reports.update(conflict.reports_a)
        conflicted_reports.update(conflict.reports_b)
    standalone = attributed = 0.0
    count = 0
    items: list[str] = []
    for report in candidate.reports:
        if report.coverage < 1.0 or report.report_id not in conflicted_reports:
            continue
        node = graph.reports.get(report.report_id)
        if not is_hold(node, report.report_name):
            continue
        band = "regulatory" if is_hold(None, report.report_name) else "financial"
        benefit = assumptions.mis_decision_cost_band.get(band, 0.0)
        standalone += benefit
        if _primary(attribution, report.report_id, candidate.candidate_id):
            attributed += benefit
            count += 1
            items.append(report.report_id)
            evidence.append(EvidenceRow(
                candidate.candidate_id, "value:mis_decision", "report", report.report_id,
                f"{report.report_name}: {band} band, reads a metric under open conflict"))
    return ValueComponent(
        "mis_decision_avoidance", round(standalone, 2), round(attributed, 2), count, "reports",
        "expected annual cost of a mis-stated number by band, for regulatory or "
        "decision-critical reports that read a conflicted metric", items)


def _build_cost(effort: Any, assumptions: ValueAssumptions) -> tuple[float, str]:
    if effort is None:
        return 0.0, "no effort estimate supplied"
    if assumptions.build_cost_method == "size":
        cost = assumptions.build_cost_by_size.get(effort.size, 0.0)
        return cost, f"T-shirt {effort.size} at {cost:,.0f}"
    cost = effort.points * assumptions.build_cost_per_effort_point
    return cost, (f"{effort.points} effort points x {assumptions.build_cost_per_effort_point:,.0f} "
                  f"per point (T-shirt {effort.size})")


def _npv(annual_benefit: float, build_cost: float, assumptions: ValueAssumptions) -> float:
    npv = -build_cost
    for year in range(1, assumptions.horizon_years + 1):
        realised = annual_benefit * (assumptions.ramp_year1_share if year == 1 else 1.0)
        npv += realised / ((1.0 + assumptions.discount_rate) ** year)
    return round(npv, 2)


# --------------------------------------------------------------------------

def attribute_conflicts(candidates: list[Candidate]) -> dict[str, str]:
    """One primary candidate per conflict: the highest composite carrying it."""
    best: dict[str, tuple[float, str]] = {}
    for candidate in sorted(candidates, key=lambda c: (-(c.score.composite if c.score else 0.0),
                                                       c.candidate_id)):
        for conflict_id in candidate.conflicts:
            best.setdefault(conflict_id, ((candidate.score.composite if candidate.score else 0.0),
                                          candidate.candidate_id))
    return {k: v[1] for k, v in best.items()}


def value_for_run(result: Any, assumptions: ValueAssumptions | None = None,
                  efforts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Value per candidate plus de-duplicated portfolio totals.

    ``result`` is a RunResult (candidates, graph, canonical). ``efforts`` maps
    candidate_id to an EffortEstimate; without it the build cost is zero and
    payback is undefined, which the row says.
    """
    assumptions = assumptions or ValueAssumptions()
    efforts = efforts or {}
    candidates: list[Candidate] = result.candidates
    graph: KnowledgeGraph = result.graph
    conflicts_open = {c.conflict_id: c for c in result.canonical.conflicts
                      if c.resolution_status == "OPEN"}
    attribution = attribute_reports(candidates)
    conflict_attribution = attribute_conflicts(candidates)

    per_candidate: dict[str, CandidateValue] = {}
    for candidate in candidates:
        per_candidate[candidate.candidate_id] = compute_value(
            candidate, graph, conflicts_open, assumptions, efforts.get(candidate.candidate_id),
            attribution, conflict_attribution)

    totals: dict[str, float] = {}
    for value in per_candidate.values():
        for component in value.components:
            totals[component.name] = round(totals.get(component.name, 0.0)
                                           + component.attributed_benefit, 2)
    attributed_total = round(sum(v.attributed_annual_benefit for v in per_candidate.values()), 2)
    standalone_total = round(sum(v.gross_annual_benefit for v in per_candidate.values()), 2)
    build_total = round(sum(v.build_cost for v in per_candidate.values()), 2)
    proposed = [v for c, v in per_candidate.items()
                if next(x for x in candidates if x.candidate_id == c).status == "Proposed"]
    return {
        "assumption_version": assumptions.version,
        "model_version": VALUE_MODEL_VERSION,
        "currency": assumptions.currency,
        "basis": assumptions.basis,
        "candidates": {cid: v.to_dict() for cid, v in per_candidate.items()},
        "portfolio": {
            "attributed_annual_benefit": attributed_total,
            "standalone_sum_annual_benefit": standalone_total,
            "double_count_removed": round(standalone_total - attributed_total, 2),
            "by_component": totals,
            "build_cost": build_total,
            "npv_3y": round(sum(v.npv_3y for v in per_candidate.values()), 2),
            "proposed_annual_benefit": round(sum(v.attributed_annual_benefit for v in proposed), 2),
            "reports_counted_once": len(attribution),
            "conflicts_counted_once": len(conflict_attribution),
        },
    }


def save_values(connection: sqlite3.Connection, run_id: str,
                values: dict[str, CandidateValue] | dict[str, dict]) -> None:
    ensure_schema(connection)
    for candidate_id, value in values.items():
        payload = value.to_dict() if isinstance(value, CandidateValue) else value
        connection.execute(
            "INSERT OR REPLACE INTO DP_CANDIDATE_VALUE VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, candidate_id, payload["assumption_version"], payload["model_version"],
             payload["currency"], json.dumps(payload["components"]),
             payload["gross_annual_benefit"], payload["attributed_annual_benefit"],
             payload["build_cost"], payload["payback_months"], payload["npv_3y"],
             json.dumps(payload["evidence"]), payload["basis"]))
    connection.commit()


def load_values(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM DP_CANDIDATE_VALUE WHERE run_id = ? ORDER BY attributed_annual_benefit DESC",
        (run_id,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["components"] = json.loads(item["components"] or "[]")
        item["evidence"] = json.loads(item["evidence"] or "[]")
        out.append(item)
    return out


def value_sentence(value: CandidateValue) -> str:
    """The narrator's value hypothesis, generated from the value row (R-08)."""
    parts = {c.name: c for c in value.components}
    retire = parts["report_retirement"]
    reconcile = parts["conflict_reconciliation"]
    payback = (f"payback {value.payback_months:.0f} months" if value.payback_months
               else "payback not computable without an effort estimate")
    return (f"Releases an estimated {value.currency} {value.attributed_annual_benefit:,.0f} a year "
            f"({value.basis}; assumptions {value.assumption_version}): "
            f"{retire.driver_count} reports retired, {reconcile.driver_count} open conflicts "
            f"no longer reconciled by hand, {parts['infrastructure'].driver_count} packages "
            f"emptied; build {value.currency} {value.build_cost:,.0f}, {payback}, "
            f"3-year NPV {value.currency} {value.npv_3y:,.0f}.")
