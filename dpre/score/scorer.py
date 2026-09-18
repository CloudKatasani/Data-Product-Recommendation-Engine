"""Scoring (specification section 8).

Four dimensions scored 0-100, combined into a composite, constrained by hard
gates that no weight can override. Every score exposes its features and the
graph rows behind them; a score row without evidence is invalid and is not
published.

Null rule: every feature resolves to a number, never NULL. Missing inputs
coalesce to the value that lowers the score, so a metadata gap can never make a
candidate look better than the evidence supports.
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import Counter

from ..canonicalize.grouping import CanonicalizationResult
from ..config import (
    DISPOSITION_WEIGHT, ER_PROBABLE_THRESHOLD, GATE_GRAIN_AMBIGUITY_MAX, GATE_LINEAGE_FLOOR,
    GATE_MIN_USERS_PER_BU, SENSITIVITY_RANK, EngineConfig,
)
from ..models import (
    Candidate, CandidateScore, EvidenceRow, GateResult, KnowledgeGraph, ScoreFeature,
)
from ..usage import report_weights, scheduled_share


def score_candidates(candidates: list[Candidate], result: CanonicalizationResult,
                     graph: KnowledgeGraph, config: EngineConfig | None = None,
                     as_of: _dt.date | None = None) -> None:
    config = config or EngineConfig()
    as_of = as_of or graph.as_of_date or _dt.date.today()
    weights = report_weights(list(graph.reports.values()), as_of, config.recency_half_life_months)

    context = _RunContext(candidates, result, graph, weights, config)
    for candidate in candidates:
        _score_one(candidate, result, graph, weights, config, context)


class _RunContext:
    """Run-level maxima, so a feature is normalized against this estate, not a guess."""

    def __init__(self, candidates, result, graph, weights, config):
        self.max_usage = max(
            (sum(weights.get(r.report_id, 0.0) for r in c.reports) for c in candidates),
            default=0.0) or 1.0
        self.max_units = max((len(c.consumers) for c in candidates), default=0) or 1
        self.max_retirable = max(
            (_weighted_retirable(c, config) for c in candidates), default=0.0) or 1.0
        self.max_variants = max(
            (_variants_collapsed(c, result) for c in candidates), default=0) or 1
        self.max_conflicts = max((len(c.conflicts) for c in candidates), default=0) or 1
        self.metrics_by_report = {}
        for metric in result.metrics.values():
            for report_id in metric.report_ids:
                self.metrics_by_report.setdefault(report_id, set()).add(metric.metric_id)


def _weighted_retirable(candidate: Candidate, config: EngineConfig) -> float:
    total = 0.0
    for report in candidate.reports:
        if report.coverage < 1.0:
            continue
        weight = DISPOSITION_WEIGHT.get((report.disposition or "keep").lower(), 0.5)
        if not config.keep_counts_toward_consolidation and report.disposition.lower() == "keep":
            weight = 0.0
        total += weight
    return total


def _variants_collapsed(candidate: Candidate, result: CanonicalizationResult) -> int:
    total = 0
    for metric_id in candidate.metric_ids:
        metric = result.metrics.get(metric_id)
        if metric:
            total += max(0, len(metric.kpi_ids) - 1)
    return total


# --------------------------------------------------------------------------

def _score_one(candidate: Candidate, result: CanonicalizationResult, graph: KnowledgeGraph,
               weights: dict[str, float], config: EngineConfig, context: _RunContext) -> None:
    metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
    features: list[ScoreFeature] = []
    evidence: list[EvidenceRow] = []
    feature_weights = config.weights.features

    # ---- Demand -------------------------------------------------------
    usage = sum(weights.get(r.report_id, 0.0) for r in candidate.reports)
    usage_norm = _log_ratio(usage, context.max_usage)
    features.append(_feature("demand", "usage_weight", usage, usage_norm,
                             feature_weights["demand"]["usage_weight"],
                             "sum of ln(1 + runs) x users x recency decay over the "
                             "candidate's reports"))
    for report in sorted(candidate.reports, key=lambda r: -weights.get(r.report_id, 0.0))[:8]:
        evidence.append(EvidenceRow(candidate.candidate_id, "usage_weight", "report",
                                    report.report_id,
                                    f"{report.report_name}: weight {weights.get(report.report_id, 0.0):.1f}"))

    breadth = len([c for c in candidate.consumers if c.business_unit])
    breadth_norm = _log_ratio(breadth, context.max_units)
    features.append(_feature("demand", "consumer_breadth", breadth, breadth_norm,
                             feature_weights["demand"]["consumer_breadth"],
                             "distinct business units using the candidate's metrics, log-scaled"))
    for consumer in candidate.consumers[:8]:
        evidence.append(EvidenceRow(candidate.candidate_id, "consumer_breadth", "business_unit",
                                    consumer.business_unit,
                                    f"{consumer.users} users across {consumer.report_count} reports"))

    reports = [graph.reports[r.report_id] for r in candidate.reports if r.report_id in graph.reports]
    cadence = scheduled_share(reports)
    features.append(_feature("demand", "cadence", cadence, cadence,
                             feature_weights["demand"]["cadence"],
                             "share of scheduled reports; a schedule implies a recurring decision"))
    for report in [r for r in reports if r.schedule_flag][:5]:
        evidence.append(EvidenceRow(candidate.candidate_id, "cadence", "report", report.report_id,
                                    f"scheduled {report.schedule_frequency or 'recurring'}"))

    # ---- Consolidation ------------------------------------------------
    retirable = _weighted_retirable(candidate, config)
    retirable_norm = _log_ratio(retirable, context.max_retirable)
    features.append(_feature("consolidation", "reports_retirable", retirable, retirable_norm,
                             feature_weights["consolidation"]["reports_retirable"],
                             "reports whose every metric this candidate covers, weighted by "
                             "disposition"))
    for report in [r for r in candidate.reports if r.coverage >= 1.0][:8]:
        evidence.append(EvidenceRow(candidate.candidate_id, "reports_retirable", "report",
                                    report.report_id,
                                    f"coverage 1.0, disposition {report.disposition}"))

    variants = _variants_collapsed(candidate, result)
    variants_norm = _log_ratio(variants, context.max_variants)
    features.append(_feature("consolidation", "variants_collapsed", variants, variants_norm,
                             feature_weights["consolidation"]["variants_collapsed"],
                             "KPI rows merged into the candidate's canonical metrics"))
    for metric in sorted(metrics, key=lambda m: -len(m.kpi_ids))[:6]:
        if len(metric.kpi_ids) > 1:
            evidence.append(EvidenceRow(candidate.candidate_id, "variants_collapsed", "metric",
                                        metric.metric_id,
                                        f"{metric.canonical_name}: {len(metric.kpi_ids)} KPI rows"))

    conflicts = len(candidate.conflicts)
    conflicts_norm = _log_ratio(conflicts, context.max_conflicts)
    features.append(_feature("consolidation", "conflicts_surfaced", conflicts, conflicts_norm,
                             feature_weights["consolidation"]["conflicts_surfaced"],
                             "nominal conflicts this candidate's metric definitions resolve"))
    for conflict_id in candidate.conflicts[:6]:
        evidence.append(EvidenceRow(candidate.candidate_id, "conflicts_surfaced", "conflict",
                                    conflict_id, "competing definition to adjudicate"))

    # ---- Feasibility --------------------------------------------------
    lineage = _lineage_completeness(candidate, metrics, graph)
    features.append(_feature("feasibility", "lineage_completeness", lineage, lineage,
                             feature_weights["feasibility"]["lineage_completeness"],
                             "share of operand columns resolved at 0.80 confidence or better"))
    for attribute in [a for a in candidate.attributes if a.role == "operand"][:8]:
        evidence.append(EvidenceRow(candidate.candidate_id, "lineage_completeness", "column",
                                    attribute.column_fqn,
                                    f"resolution confidence {attribute.confidence}"))

    definitions = _definition_coverage(candidate)
    features.append(_feature("feasibility", "definition_coverage", definitions, definitions,
                             feature_weights["feasibility"]["definition_coverage"],
                             "share of operand columns with a catalog business term and definition"))
    for attribute in [a for a in candidate.attributes if a.role == "operand" and a.business_term][:6]:
        evidence.append(EvidenceRow(candidate.candidate_id, "definition_coverage", "term",
                                    attribute.business_term, attribute.column_fqn))

    health, health_detail = _source_health(candidate, graph)
    features.append(_feature("feasibility", "source_health", health, health,
                             feature_weights["feasibility"]["source_health"], health_detail))
    for source in candidate.sources[:8]:
        evidence.append(EvidenceRow(candidate.candidate_id, "source_health", "table",
                                    source.table_fqn,
                                    f"{'SoR' if source.sor_flag else 'not SoR'}, "
                                    f"{source.lifecycle_status}"))

    determinism = _determinism(metrics, graph)
    features.append(_feature("feasibility", "calculation_determinism", determinism, determinism,
                             feature_weights["feasibility"]["calculation_determinism"],
                             "share of metrics parsed, with no report-level prompt embedded"))
    for metric in [m for m in metrics if m.opaque][:5]:
        evidence.append(EvidenceRow(candidate.candidate_id, "calculation_determinism", "metric",
                                    metric.metric_id, f"{metric.canonical_name}: opaque calculation"))

    # ---- Risk ---------------------------------------------------------
    sensitivity, sensitivity_detail = _sensitivity(candidate)
    features.append(_feature("risk", "sensitivity", sensitivity, sensitivity,
                             feature_weights["risk"]["sensitivity"], sensitivity_detail))
    for attribute in [a for a in candidate.attributes if a.pii_flag][:8]:
        evidence.append(EvidenceRow(candidate.candidate_id, "sensitivity", "column",
                                    attribute.column_fqn,
                                    f"{attribute.sensitivity}, PII"))

    ambiguity = float(getattr(candidate, "_grain_ambiguity", 0.0))
    features.append(_feature("risk", "grain_ambiguity", ambiguity, ambiguity,
                             feature_weights["risk"]["grain_ambiguity"],
                             "share of metrics whose grain differs from the candidate grain"))
    for metric in [m for m in metrics if m.grain != candidate.grain][:6]:
        evidence.append(EvidenceRow(candidate.candidate_id, "grain_ambiguity", "metric",
                                    metric.metric_id,
                                    f"{metric.canonical_name} evaluates at {metric.grain}"))

    load = min(1.0, conflicts / float(max(1, len(metrics))))
    features.append(_feature("risk", "conflict_load", load, load,
                             feature_weights["risk"]["conflict_load"],
                             "unresolved conflicts per metric; more conflicts means longer "
                             "steward adjudication"))

    dimensions = _dimension_scores(features)
    composite = (
        config.weights.dimensions["demand"] * dimensions["demand"]
        + config.weights.dimensions["consolidation"] * dimensions["consolidation"]
        + config.weights.dimensions["feasibility"] * dimensions["feasibility"]
        + config.weights.dimensions["risk"] * dimensions["risk"]
    )

    gates = _apply_gates(candidate, dimensions, lineage, ambiguity, graph)
    candidate.score = CandidateScore(
        candidate_id=candidate.candidate_id,
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


def _lineage_completeness(candidate: Candidate, metrics, graph: KnowledgeGraph) -> float:
    """Share of this candidate's operand column references resolved confidently.

    Resolved edges and quarantined rows are counted in the same unit - one row of
    the lineage extract - so the number is comparable with the run-level
    resolution rate.
    """
    kpi_ids = {kpi_id for m in metrics for kpi_id in m.kpi_ids}
    if not kpi_ids:
        return 0.0
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
        return 0.0                     # no evidence lowers the score, never raises it
    return round(resolved / total, 4)


def _definition_coverage(candidate: Candidate) -> float:
    operands = [a for a in candidate.attributes if a.role == "operand"]
    if not operands:
        return 0.0
    defined = sum(1 for a in operands if a.business_term and a.definition)
    return round(defined / len(operands), 4)


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


def _determinism(metrics, graph: KnowledgeGraph) -> float:
    if not metrics:
        return 0.0
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
        return 0.0
    return round(parsed / total, 4)


def _sensitivity(candidate: Candidate) -> tuple[float, str]:
    operands = candidate.attributes
    if not operands:
        return 1.0, "no attributes resolved, so sensitivity is scored at maximum risk"
    ranks = [SENSITIVITY_RANK.get((a.sensitivity or "internal").lower(), 0.35) for a in operands]
    worst = max(ranks) if ranks else 0.35
    pii = [a for a in operands if a.pii_flag]
    if pii:
        worst = max(worst, 0.7)
    classes = Counter((a.sensitivity or "Internal") for a in operands)
    detail = (f"highest sensitivity class {max(classes, key=lambda c: SENSITIVITY_RANK.get(c.lower(), 0))}"
              f"; {len(pii)} PII columns raise Stage 9 effort")
    return round(worst, 4), detail


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
