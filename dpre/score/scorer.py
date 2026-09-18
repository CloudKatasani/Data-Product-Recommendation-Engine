"""Scoring (specification section 8).

Four dimensions scored 0-100, combined into a composite, constrained by hard
gates that no weight can override. Every score exposes its features and the
graph rows behind them; a score row without evidence is invalid and is not
published.

Null rule: every feature resolves to a number, never NULL. Missing inputs
coalesce to the value that lowers the score, so a metadata gap can never make a
candidate look better than the evidence supports.

Evidence is written per *feature*, not per candidate (review finding R-22):
every feature has at least one row, negative evidence is a row ("0 conflicts
over 12 metrics"), additive features carry their addends with a contribution
marker so their sum can be checked, evidence is never truncated, and the run
maximum used as a normaliser is named. Definition coverage credits a glossary
term by its lifecycle status (R-34), source health carries the catalog's own
trust signals - certification and quality score - as evidence (R-35), and the
usage weight is the cadence-aware, tool-parity weight of ``dpre.score.demand``
(R-49) with the applied half-life on each row.
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import Counter
from typing import TYPE_CHECKING

from ..config import (
    DISPOSITION_WEIGHT, ER_PROBABLE_THRESHOLD, GATE_GRAIN_AMBIGUITY_MAX, GATE_LINEAGE_FLOOR,
    GATE_MIN_USERS_PER_BU, SENSITIVITY_RANK, EngineConfig,
)
from ..glossary.terms import term_link_for
from ..models import (
    Candidate, CandidateScore, EvidenceRow, GateResult, KnowledgeGraph, ScoreFeature,
)
from ..usage import scheduled_share
from .demand import ReportDemand, report_demand
from .evidence import assert_feature_evidence, contributes, negative

if TYPE_CHECKING:                      # annotation only: the canonicalizer imports demand
    from ..canonicalize.grouping import CanonicalizationResult

# Catalog certification statuses and what they say about a source (R-35).
CERTIFICATION_TRUST = {"certified": 1.0, "endorsed": 1.0, "approved": 1.0, "promoted": 0.8,
                       "candidate": 0.5, "proposed": 0.5, "draft": 0.3, "deprecated": 0.0,
                       "": 0.4}


def score_candidates(candidates: list[Candidate], result: CanonicalizationResult,
                     graph: KnowledgeGraph, config: EngineConfig | None = None,
                     as_of: _dt.date | None = None) -> None:
    config = config or EngineConfig()
    as_of = as_of or graph.as_of_date or _dt.date.today()
    demand = report_demand(list(graph.reports.values()), as_of, config.recency_half_life_months)
    weights = {rid: d.weight for rid, d in demand.items()}

    context = _RunContext(candidates, result, graph, weights, config)
    for candidate in candidates:
        _score_one(candidate, result, graph, weights, demand, config, context)
    # The per-feature evidence contract is an invariant of this module, not a
    # hope: a feature without a row cannot leave the scorer.
    assert_feature_evidence(candidates)


class _RunContext:
    """Run-level maxima, so a feature is normalized against this estate, not a guess.

    Each maximum remembers the candidate that set it, so the normaliser on a
    card is a named reference and the number is reproducible (R-22).
    """

    def __init__(self, candidates, result, graph, weights, config):
        self.max_usage, self.max_usage_ref = _argmax(
            candidates, lambda c: sum(weights.get(r.report_id, 0.0) for r in c.reports))
        self.max_units, self.max_units_ref = _argmax(
            candidates, lambda c: float(len([x for x in c.consumers if x.business_unit])))
        self.max_retirable, self.max_retirable_ref = _argmax(
            candidates, lambda c: _weighted_retirable(c, config))
        self.max_variants, self.max_variants_ref = _argmax(
            candidates, lambda c: float(_variants_collapsed(c, result)))
        self.max_conflicts, self.max_conflicts_ref = _argmax(
            candidates, lambda c: float(len(c.conflicts)))
        self.metrics_by_report = {}
        for metric in result.metrics.values():
            for report_id in metric.report_ids:
                self.metrics_by_report.setdefault(report_id, set()).add(metric.metric_id)


def _argmax(candidates, key) -> tuple[float, str]:
    best, ref = 0.0, ""
    for candidate in candidates:
        value = key(candidate)
        if value > best:
            best, ref = value, candidate.candidate_id
    return (best or 1.0), ref


def _weighted_retirable(candidate: Candidate, config: EngineConfig) -> float:
    return sum(_retirable_weight(r, config) for r in candidate.reports if r.coverage >= 1.0)


def _retirable_weight(report, config: EngineConfig) -> float:
    weight = DISPOSITION_WEIGHT.get((report.disposition or "keep").lower(), 0.5)
    if not config.keep_counts_toward_consolidation and (report.disposition or "").lower() == "keep":
        weight = 0.0
    return weight


def _variants_collapsed(candidate: Candidate, result: CanonicalizationResult) -> int:
    total = 0
    for metric_id in candidate.metric_ids:
        metric = result.metrics.get(metric_id)
        if metric:
            total += max(0, len(metric.kpi_ids) - 1)
    return total


# --------------------------------------------------------------------------

def _score_one(candidate: Candidate, result: CanonicalizationResult, graph: KnowledgeGraph,
               weights: dict[str, float], demand: dict[str, ReportDemand],
               config: EngineConfig, context: _RunContext) -> None:
    metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
    features: list[ScoreFeature] = []
    evidence: list[EvidenceRow] = []
    feature_weights = config.weights.features
    cid = candidate.candidate_id

    def add(feature: ScoreFeature) -> None:
        features.append(feature)

    def row(feature: str, kind: str, evidence_id: str, detail: str) -> None:
        evidence.append(EvidenceRow(cid, feature, kind, evidence_id, detail))

    # ---- Demand -------------------------------------------------------
    usage = round(sum(weights.get(r.report_id, 0.0) for r in candidate.reports), 4)
    add(_feature("demand", "usage_weight", usage, _log_ratio(usage, context.max_usage),
                 feature_weights["demand"]["usage_weight"],
                 "sum over reports of ln(1 + parity-adjusted runs) x users x recency decay, "
                 "half-life max(config, 2 x cadence)"
                 + _reference(context.max_usage, context.max_usage_ref)))
    for report in sorted(candidate.reports, key=lambda r: (-weights.get(r.report_id, 0.0),
                                                          r.report_id)):
        d = demand.get(report.report_id)
        if d is None:
            continue
        row("usage_weight", "report", report.report_id, contributes(
            f"{report.report_name}: runs {d.raw_runs} (tool percentile {d.percentile:.2f}, "
            f"parity-adjusted {d.adjusted_runs:.0f}) x {d.users} users x decay {d.decay:.3f} "
            f"at half-life {d.half_life_months:.0f} months"
            + (f", cadence {d.cadence}" if d.cadence else "")
            + (", decision-critical floor applied" if d.floored else ""), d.weight))
    if not candidate.reports:
        evidence.append(negative(cid, "usage_weight", "no report reads this candidate's metrics"))

    units = [c for c in candidate.consumers if c.business_unit]
    breadth = len(units)
    add(_feature("demand", "consumer_breadth", breadth, _log_ratio(breadth, context.max_units),
                 feature_weights["demand"]["consumer_breadth"],
                 "distinct business units using the candidate's metrics, log-scaled"
                 + _reference(context.max_units, context.max_units_ref)))
    for consumer in units:
        row("consumer_breadth", "business_unit", consumer.business_unit, contributes(
            f"{consumer.users} users across {consumer.report_count} reports", 1.0))
    if not units:
        evidence.append(negative(cid, "consumer_breadth",
                                 "no report carries a business unit; breadth scored at 0"))

    reports = [graph.reports[r.report_id] for r in candidate.reports if r.report_id in graph.reports]
    cadence = scheduled_share(reports)
    scheduled = [r for r in reports if r.schedule_flag]
    add(_feature("demand", "cadence", cadence, cadence, feature_weights["demand"]["cadence"],
                 f"{len(scheduled)} of {len(reports)} reports are scheduled; a schedule "
                 "implies a recurring decision"))
    for report in sorted(scheduled, key=lambda r: r.report_id):
        row("cadence", "report", report.report_id,
            f"scheduled {report.schedule_frequency or 'recurring'}")
    if not scheduled:
        evidence.append(negative(cid, "cadence",
                                 f"0 of {len(reports)} reports are scheduled; cadence scored at 0"))

    # ---- Consolidation ------------------------------------------------
    retirable = round(_weighted_retirable(candidate, config), 4)
    add(_feature("consolidation", "reports_retirable", retirable,
                 _log_ratio(retirable, context.max_retirable),
                 feature_weights["consolidation"]["reports_retirable"],
                 "reports whose every metric this candidate covers, weighted by disposition "
                 "(Retire 1.0, Merge 0.8, Keep 0.5, Migrate 0.3)"
                 + _reference(context.max_retirable, context.max_retirable_ref)))
    covered = [r for r in candidate.reports if r.coverage >= 1.0]
    for report in sorted(covered, key=lambda r: r.report_id):
        row("reports_retirable", "report", report.report_id, contributes(
            f"coverage 1.0, disposition {report.disposition}", _retirable_weight(report, config)))
    if not covered:
        evidence.append(negative(cid, "reports_retirable",
                                 f"none of {len(candidate.reports)} reports is fully covered"))

    variants = _variants_collapsed(candidate, result)
    add(_feature("consolidation", "variants_collapsed", variants,
                 _log_ratio(variants, context.max_variants),
                 feature_weights["consolidation"]["variants_collapsed"],
                 "KPI rows merged into the candidate's canonical metrics"
                 + _reference(context.max_variants, context.max_variants_ref)))
    merged = [m for m in metrics if len(m.kpi_ids) > 1]
    for metric in sorted(merged, key=lambda m: (-len(m.kpi_ids), m.metric_id)):
        row("variants_collapsed", "metric", metric.metric_id, contributes(
            f"{metric.canonical_name}: {len(metric.kpi_ids)} KPI rows collapsed",
            len(metric.kpi_ids) - 1))
    if not merged:
        evidence.append(negative(cid, "variants_collapsed",
                                 f"each of {len(metrics)} metrics comes from a single KPI row"))

    conflicts = len(candidate.conflicts)
    add(_feature("consolidation", "conflicts_surfaced", conflicts,
                 _log_ratio(conflicts, context.max_conflicts),
                 feature_weights["consolidation"]["conflicts_surfaced"],
                 "nominal conflicts this candidate's metric definitions resolve"
                 + _reference(context.max_conflicts, context.max_conflicts_ref)))
    conflict_rows = {c.conflict_id: c for c in result.conflicts}
    for conflict_id in sorted(candidate.conflicts):
        conflict = conflict_rows.get(conflict_id)
        text = (f"{conflict.pattern}: {conflict.difference_summary}" if conflict
                else "competing definition to adjudicate")
        row("conflicts_surfaced", "conflict", conflict_id, contributes(text, 1.0))
    if not candidate.conflicts:
        evidence.append(negative(cid, "conflicts_surfaced",
                                 f"0 conflicts over {len(metrics)} metrics"))

    # ---- Feasibility --------------------------------------------------
    lineage, lineage_detail = _lineage_completeness(candidate, metrics, graph)
    add(_feature("feasibility", "lineage_completeness", lineage, lineage,
                 feature_weights["feasibility"]["lineage_completeness"], lineage_detail))
    operands = [a for a in candidate.attributes if a.role == "operand"]
    for attribute in operands:
        row("lineage_completeness", "column", attribute.column_fqn,
            f"resolved at confidence {attribute.confidence:.2f}"
            + ("" if attribute.confidence >= ER_PROBABLE_THRESHOLD else " (probable only)"))
    kpi_ids = {k for m in metrics for k in m.kpi_ids}
    quarantined = Counter(q.reason_code for q in graph.quarantine
                          if q.kpi_id in kpi_ids and q.role == "operand")
    for code, count in sorted(quarantined.items()):
        row("lineage_completeness", "quarantine", code,
            f"{count} operand reference(s) quarantined: {code}")
    if not operands and not quarantined:
        evidence.append(negative(cid, "lineage_completeness",
                                 "no operand column reference resolved or quarantined"))

    definitions, definition_detail = _definition_coverage(candidate, graph)
    add(_feature("feasibility", "definition_coverage", definitions, definitions,
                 feature_weights["feasibility"]["definition_coverage"], definition_detail))
    for attribute in operands:
        link = term_link_for(graph, attribute.column_fqn)
        if attribute.business_term and attribute.definition:
            status = link.status_class if link else "column-only"
            credit = link.credit if link else 1.0
            row("definition_coverage", "term", attribute.business_term, contributes(
                f"{attribute.column_fqn}: term status {status}"
                + (f" ({link.status})" if link and link.status else "")
                + f", definition from {link.definition_source if link else 'column extract'}",
                credit))
        else:
            row("definition_coverage", "column", attribute.column_fqn, contributes(
                "no business term" if not attribute.business_term else "term without a definition",
                0.0))
    if not operands:
        evidence.append(negative(cid, "definition_coverage",
                                 "no operand columns resolved; coverage scored at 0"))

    health, health_detail = _source_health(candidate, graph)
    add(_feature("feasibility", "source_health", health, health,
                 feature_weights["feasibility"]["source_health"], health_detail))
    for source in candidate.sources:
        row("source_health", "table", source.table_fqn,
            f"{'SoR' if source.sor_flag else 'not SoR'}, {source.lifecycle_status}"
            + (f", successor {source.successor_system}" if source.successor_system else ""))
    trust = source_trust(candidate, graph)
    for item in trust["tables"]:
        row("source_health", "trust", item["table_fqn"],
            f"catalog certification {item['certification'] or 'none'} on "
            f"{item['certified_columns']} of {item['columns']} columns; mean quality score "
            f"{item['quality_score']:.2f}; trust {item['trust']:.2f}")
    if not candidate.sources:
        evidence.append(negative(cid, "source_health",
                                 "no resolved source tables; health scored at 0"))
    setattr(candidate, "_source_trust", trust)
    trust_weight = feature_weights["feasibility"].get("source_trust")
    if trust_weight is not None:
        # Only when the council has given it a weight (D-04): until then trust
        # is evidence on source_health, not a scored feature.
        add(_feature("feasibility", "source_trust", trust["score"], trust["score"],
                     trust_weight, trust["detail"]))
        for item in trust["tables"]:
            row("source_trust", "table", item["table_fqn"],
                f"certification {item['certification'] or 'none'}, quality {item['quality_score']:.2f}")
        if not trust["tables"]:
            evidence.append(negative(cid, "source_trust", "no resolved source tables"))

    determinism, determinism_detail = _determinism(metrics, graph)
    add(_feature("feasibility", "calculation_determinism", determinism, determinism,
                 feature_weights["feasibility"]["calculation_determinism"], determinism_detail))
    opaque = [m for m in metrics if m.opaque]
    for metric in opaque:
        row("calculation_determinism", "metric", metric.metric_id,
            f"{metric.canonical_name}: opaque calculation ({len(metric.kpi_ids)} KPI row(s))")
    if not opaque:
        evidence.append(negative(cid, "calculation_determinism",
                                 f"all {len(metrics)} metrics parsed; no prompt or macro embedded"))

    # ---- Risk ---------------------------------------------------------
    sensitivity, sensitivity_detail, worst_column = _sensitivity(candidate)
    add(_feature("risk", "sensitivity", sensitivity, sensitivity,
                 feature_weights["risk"]["sensitivity"], sensitivity_detail))
    pii = [a for a in candidate.attributes if a.pii_flag]
    for attribute in pii:
        row("sensitivity", "column", attribute.column_fqn, f"{attribute.sensitivity}, PII")
    if worst_column is not None and not worst_column.pii_flag:
        row("sensitivity", "column", worst_column.column_fqn,
            f"highest sensitivity class on the candidate: {worst_column.sensitivity}")
    if not pii and worst_column is None:
        evidence.append(negative(cid, "sensitivity", sensitivity_detail))
    elif not pii:
        evidence.append(negative(cid, "sensitivity",
                                 f"no PII columns; highest class {worst_column.sensitivity}"))

    ambiguity = float(getattr(candidate, "_grain_ambiguity", 0.0))
    off_grain = [m for m in metrics if m.grain != candidate.grain]
    add(_feature("risk", "grain_ambiguity", ambiguity, ambiguity,
                 feature_weights["risk"]["grain_ambiguity"],
                 f"{len(off_grain)} of {len(metrics)} metrics evaluate away from the "
                 f"candidate grain {candidate.grain}"))
    for metric in off_grain:
        row("grain_ambiguity", "metric", metric.metric_id,
            f"{metric.canonical_name} evaluates at {metric.grain}")
    if not off_grain:
        evidence.append(negative(cid, "grain_ambiguity",
                                 f"all {len(metrics)} metrics evaluate at {candidate.grain}"))

    load = min(1.0, conflicts / float(max(1, len(metrics))))
    add(_feature("risk", "conflict_load", load, load, feature_weights["risk"]["conflict_load"],
                 f"{conflicts} unresolved conflicts over {len(metrics)} metrics; more "
                 "conflicts means longer steward adjudication"))
    for conflict_id in sorted(candidate.conflicts):
        conflict = conflict_rows.get(conflict_id)
        row("conflict_load", "conflict", conflict_id,
            (f"{conflict.pattern}, steward {conflict.steward_id or 'unassigned'}, "
             f"{conflict.resolution_status}") if conflict else "open conflict")
    if not candidate.conflicts:
        evidence.append(negative(cid, "conflict_load", f"0 conflicts over {len(metrics)} metrics"))

    dimensions = _dimension_scores(features)
    composite = (
        config.weights.dimensions["demand"] * dimensions["demand"]
        + config.weights.dimensions["consolidation"] * dimensions["consolidation"]
        + config.weights.dimensions["feasibility"] * dimensions["feasibility"]
        + config.weights.dimensions["risk"] * dimensions["risk"]
    )

    gates = _apply_gates(candidate, dimensions, lineage, ambiguity, graph)
    candidate.score = CandidateScore(
        candidate_id=cid,
        weight_version=config.weights.weight_version,
        demand=round(dimensions["demand"], 1),
        consolidation=round(dimensions["consolidation"], 1),
        feasibility=round(dimensions["feasibility"], 1),
        risk=round(dimensions["risk"], 1),
        composite=round(composite, 1),
        features=features,
        gates=gates,
    )
    candidate.evidence = evidence
    candidate.status = _status_from_gates(gates)


# --------------------------------------------------------------------------

def _reference(maximum: float, reference_id: str) -> str:
    if not reference_id:
        return "; normalised against a run maximum of 1 (no candidate scored above 0)"
    return f"; normalised against the run maximum {maximum:.4g} set by {reference_id}"


def _feature(dimension: str, name: str, value: float, normalized: float, weight: float,
             detail: str) -> ScoreFeature:
    normalized = max(0.0, min(1.0, float(normalized)))
    return ScoreFeature(
        dimension=dimension, feature=name, value=round(float(value), 4),
        normalized=round(normalized, 4), weight=weight,
        contribution=round(100.0 * normalized * weight, 2), detail=detail,
    )


def _log_ratio(value: float, maximum: float) -> float:
    if value <= 0 or maximum <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(maximum))


def _dimension_scores(features: list[ScoreFeature]) -> dict[str, float]:
    out: dict[str, float] = {}
    for dimension in ("demand", "consolidation", "feasibility", "risk"):
        members = [f for f in features if f.dimension == dimension]
        total_weight = sum(f.weight for f in members) or 1.0
        out[dimension] = round(100.0 * sum(f.normalized * f.weight for f in members) / total_weight, 4)
    return out


def _lineage_completeness(candidate: Candidate, metrics, graph: KnowledgeGraph) -> tuple[float, str]:
    """Share of this candidate's operand column references resolved confidently.

    Resolved edges and quarantined rows are counted in the same unit - one row of
    the lineage extract - so the number is comparable with the run-level
    resolution rate.
    """
    kpi_ids = {kpi_id for m in metrics for kpi_id in m.kpi_ids}
    if not kpi_ids:
        return 0.0, "no KPI rows behind the candidate"
    resolved = 0
    probable = 0
    for edge in graph.edges_kpi_column:
        if edge.role != "operand" or edge.kpi_id not in kpi_ids:
            continue
        if edge.confidence >= ER_PROBABLE_THRESHOLD:
            resolved += 1
        else:
            probable += 1
    quarantined = sum(1 for q in graph.quarantine
                      if q.kpi_id in kpi_ids and q.role == "operand")
    total = resolved + probable + quarantined
    if total == 0:
        return 0.0, "no operand references at all; scored at 0"   # never raises the score
    return round(resolved / total, 4), (
        f"{resolved} of {total} operand references resolved at 0.80 confidence or better "
        f"({probable} probable, {quarantined} quarantined)")


def _definition_coverage(candidate: Candidate, graph: KnowledgeGraph) -> tuple[float, str]:
    """Share of operand columns with a term and definition, credited by term status.

    An Approved glossary term counts fully, a Draft term half, a Deprecated or
    Retired term not at all (R-34): the number a steward can sign is the one the
    glossary has signed.
    """
    operands = [a for a in candidate.attributes if a.role == "operand"]
    if not operands:
        return 0.0, "no operand columns resolved; coverage scored at 0"
    credit = 0.0
    statuses: Counter = Counter()
    for attribute in operands:
        if not (attribute.business_term and attribute.definition):
            statuses["undefined"] += 1
            continue
        link = term_link_for(graph, attribute.column_fqn)
        weight = link.credit if link else 1.0
        statuses[link.status_class if link else "column-only"] += 1
        credit += weight
    detail = (f"{len(operands) - statuses['undefined']} of {len(operands)} operand columns "
              "carry a term and definition; term status "
              + ", ".join(f"{k} x{v}" for k, v in sorted(statuses.items()))
              + " (Approved 1.0, Draft 0.5, Retired 0)")
    return round(credit / len(operands), 4), detail


def _source_health(candidate: Candidate, graph: KnowledgeGraph) -> tuple[float, str]:
    if not candidate.sources:
        return 0.0, "no resolved source tables"
    sunset = [s for s in candidate.sources
              if s.lifecycle_status == "sunset" and not s.successor_system]
    if sunset:
        return 0.0, ("sunset source with no successor: "
                     + ", ".join(sorted({s.system for s in sunset})))
    non_sor = [s for s in candidate.sources if not s.sor_flag]
    if not non_sor:
        return 1.0, "all sources are system-of-record and active"
    share = len(non_sor) / len(candidate.sources)
    return round(max(0.5, 1.0 - 0.5 * share), 4), (
        f"{len(non_sor)} of {len(candidate.sources)} sources are not the system of record")


def source_trust(candidate: Candidate, graph: KnowledgeGraph) -> dict:
    """The catalog's own trust signals on the candidate's sources (R-35, section 3.3).

    Certification status and quality score are carried per source table: the
    share of its used columns the catalog certified, and their mean quality
    score. Neither raises a score unless the council weights it; both are shown.
    """
    used = {a.column_fqn for a in candidate.attributes}
    tables = []
    for source in candidate.sources:
        columns = [c for c in graph.columns.values()
                   if c.table_fqn == source.table_fqn and c.column_fqn in used]
        if not columns:
            columns = [c for c in graph.columns.values() if c.table_fqn == source.table_fqn]
        if not columns:
            continue
        certified = [c for c in columns
                     if CERTIFICATION_TRUST.get((c.certification_status or "").lower(), 0.4) >= 0.8]
        statuses = Counter((c.certification_status or "").lower() for c in columns)
        dominant = statuses.most_common(1)[0][0]
        scores = [_quality_unit(c.quality_score) for c in columns if c.quality_score]
        quality = sum(scores) / len(scores) if scores else 0.0
        cert_trust = CERTIFICATION_TRUST.get(dominant, 0.4)
        trust = round(0.6 * cert_trust + 0.4 * quality, 4)
        tables.append({
            "table_fqn": source.table_fqn, "certification": dominant,
            "certified_columns": len(certified), "columns": len(columns),
            "quality_score": round(quality, 4), "trust": trust,
        })
    if not tables:
        return {"score": 0.0, "tables": [], "detail": "no catalog trust signals on the sources"}
    score = round(sum(t["trust"] for t in tables) / len(tables), 4)
    certified_tables = sum(1 for t in tables if t["certification"] in ("certified", "endorsed",
                                                                        "approved"))
    return {"score": score, "tables": tables,
            "detail": (f"{certified_tables} of {len(tables)} source tables certified in the "
                       f"catalog; mean quality score "
                       f"{sum(t['quality_score'] for t in tables) / len(tables):.2f}")}


def _quality_unit(value: float) -> float:
    """Catalog quality scores arrive as 0-1 or 0-100; read both as a unit share."""
    value = float(value or 0.0)
    return max(0.0, min(1.0, value / 100.0 if value > 1.0 else value))


def _determinism(metrics, graph: KnowledgeGraph) -> tuple[float, str]:
    if not metrics:
        return 0.0, "no metrics; scored at 0"
    parsed = 0
    total = 0
    for metric in metrics:
        for kpi_id in metric.kpi_ids:
            kpi = graph.kpis.get(kpi_id)
            if kpi is None:
                continue
            total += 1
            if kpi.parse_status == "PARSED":
                parsed += 1
    if total == 0:
        return 0.0, "no KPI rows behind the metrics; scored at 0"
    return round(parsed / total, 4), (f"{parsed} of {total} KPI rows parsed, with no "
                                      "report-level prompt embedded")


def _sensitivity(candidate: Candidate):
    operands = candidate.attributes
    if not operands:
        return 1.0, "no attributes resolved, so sensitivity is scored at maximum risk", None
    ranked = sorted(operands, key=lambda a: (-SENSITIVITY_RANK.get((a.sensitivity or "internal").lower(), 0.35),
                                            a.column_fqn))
    worst_column = ranked[0]
    worst = SENSITIVITY_RANK.get((worst_column.sensitivity or "internal").lower(), 0.35)
    pii = [a for a in operands if a.pii_flag]
    if pii:
        worst = max(worst, 0.7)
    detail = (f"highest sensitivity class {worst_column.sensitivity or 'Internal'}"
              f"; {len(pii)} PII columns raise Stage 9 effort")
    return round(worst, 4), detail, worst_column


def _apply_gates(candidate: Candidate, dimensions: dict[str, float], lineage: float,
                 ambiguity: float, graph: KnowledgeGraph) -> list[GateResult]:
    gates: list[GateResult] = []

    qualifying = [c for c in candidate.consumers if c.users >= GATE_MIN_USERS_PER_BU]
    g1 = bool(qualifying) and bool(getattr(candidate, "_consumer_confirmed", False) or qualifying)
    gates.append(GateResult(
        "G1", "Named consumer", g1,
        (f"{len(qualifying)} business units with at least {GATE_MIN_USERS_PER_BU} users"
         if qualifying else "no business unit has two or more users"),
        "" if g1 else "status capped at Exploratory until a reviewer confirms a consumer"))

    g2 = lineage >= GATE_LINEAGE_FLOOR
    gates.append(GateResult(
        "G2", "Lineage floor", g2,
        f"lineage completeness {lineage:.2f} against a floor of {GATE_LINEAGE_FLOOR:.2f}",
        "" if g2 else "status capped at Exploratory; listed as a catalog gap"))

    g3 = ambiguity <= GATE_GRAIN_AMBIGUITY_MAX
    gates.append(GateResult(
        "G3", "Single grain", g3,
        f"grain ambiguity {ambiguity:.2f} against a ceiling of {GATE_GRAIN_AMBIGUITY_MAX:.2f}",
        "" if g3 else "must be split by grain before it can be Proposed"))

    sunset = [s for s in candidate.sources
              if s.lifecycle_status == "sunset" and not s.successor_system]
    g4 = not sunset
    detail = "no sunset source without a successor"
    if sunset:
        dates = sorted({graph.tables[s.table_fqn].sunset_date for s in sunset
                        if s.table_fqn in graph.tables and graph.tables[s.table_fqn].sunset_date})
        detail = (f"sunset source {', '.join(sorted({s.system for s in sunset}))}"
                  + (f", sunset date {dates[0]}" if dates else "")
                  + ", no successor mapped in the catalog")
    gates.append(GateResult("G4", "Sunset source", g4, detail,
                            "" if g4 else "status Blocked"))
    return gates


def _status_from_gates(gates: list[GateResult]) -> str:
    failed = {g.gate for g in gates if not g.passed}
    if "G4" in failed:
        return "Blocked"
    if failed:
        return "Exploratory"
    return "Proposed"
