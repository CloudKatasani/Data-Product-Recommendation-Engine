"""Archetype and tier classification (specification section 7).

Each candidate receives one archetype and one tier from graph features alone, so
the DPF Stage 2 charter opens pre-filled and the reviewer only confirms or
overrides. Classification confidence is the margin between the winning rule and
the runner-up; below 0.6 the archetype is shown as a choice between the top two.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import Candidate, KnowledgeGraph

if TYPE_CHECKING:                      # annotation only: the canonicalizer imports demand
    from ..canonicalize.grouping import CanonicalizationResult

ARCHETYPES = (
    "Entity Master", "Reference Data", "Event Stream", "Metric / KPI",
    "Feature Store", "Insight / Recommendation",
)
TIERS = ("Source-aligned", "Aggregate", "Consumer-aligned")

FEATURE_TERM_HINTS = ("feature", "model", "score", "propensity", "prediction", "risk score")
PREDICTIVE_REPORT_HINTS = ("model", "propensity", "predict", "score", "forecast", "churn")
RANK_FUNCTIONS = ("rank(", "rank_", "row_number", "topn", "percentile", "ntile", "dense_rank")
REFERENCE_ROW_CEILING = 10_000

# A rule matches when its score clears this bar; the first match in the order of
# ARCHETYPES wins, as the specification's rule table requires.
MATCH_THRESHOLDS = {
    "Entity Master": 0.55,
    "Reference Data": 0.60,
    "Event Stream": 0.60,
    "Metric / KPI": 0.55,
    "Feature Store": 0.45,
    "Insight / Recommendation": 0.45,
}


@dataclass
class Classification:
    archetype: str
    archetype_confidence: float
    runner_up: str
    tier: str
    tier_confidence: float
    rationale: dict[str, float]


def classify(candidate: Candidate, result: CanonicalizationResult, graph: KnowledgeGraph,
             reuse_communities: int = 0) -> Classification:
    metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
    scores = {
        "Entity Master": _entity_master_score(candidate, metrics, graph, reuse_communities),
        "Reference Data": _reference_data_score(candidate, metrics, graph),
        "Event Stream": _event_stream_score(candidate, metrics, graph),
        "Metric / KPI": _metric_score(candidate, metrics, graph),
        "Feature Store": _feature_store_score(candidate, metrics, graph),
        "Insight / Recommendation": _insight_score(candidate, metrics, graph),
    }
    # Rules are evaluated in the documented order and the first match wins; the
    # ranking by score only decides the runner-up and therefore the confidence.
    winner = ""
    for archetype in ARCHETYPES:
        if scores[archetype] >= MATCH_THRESHOLDS[archetype]:
            winner = archetype
            break
    if not winner:
        winner = max(scores, key=lambda a: (scores[a], -ARCHETYPES.index(a)))
    winning_score = scores[winner]
    others = sorted(((a, v) for a, v in scores.items() if a != winner), key=lambda kv: -kv[1])
    runner_up, runner_score = others[0]
    confidence = round(min(1.0, 0.5 * max(0.0, winning_score)
                           + 0.5 * max(0.0, winning_score - runner_score)), 3)

    tier, tier_confidence = _tier(candidate, metrics, graph)
    return Classification(
        archetype=winner, archetype_confidence=confidence, runner_up=runner_up,
        tier=tier, tier_confidence=tier_confidence,
        rationale={k: round(v, 3) for k, v in scores.items()},
    )


# --------------------------------------------------------------------------

def _aggregation_mix(metrics) -> dict[str, float]:
    if not metrics:
        return {}
    counts: dict[str, int] = {}
    for metric in metrics:
        counts[metric.aggregation or "NONE"] = counts.get(metric.aggregation or "NONE", 0) + 1
    return {k: v / len(metrics) for k, v in counts.items()}


def _entity_master_score(candidate: Candidate, metrics, graph: KnowledgeGraph,
                         reuse_communities: int) -> float:
    if candidate.origin == "entity_master":
        return 0.95
    mix = _aggregation_mix(metrics)
    distinct_share = mix.get("COUNT DISTINCT", 0.0)
    hub_tables = [s for s in candidate.sources
                  if (graph.tables.get(s.table_fqn) or None)
                  and graph.tables[s.table_fqn].measure_count < 2]
    has_backbone_pk = any(
        c.pk_flag for s in hub_tables for c in graph.columns.values()
        if c.table_fqn == s.table_fqn)
    score = 0.0
    if has_backbone_pk:
        score += 0.35
    score += 0.45 * distinct_share
    if reuse_communities >= 3:
        score += 0.25
    return min(score, 0.99)


def _reference_data_score(candidate: Candidate, metrics, graph: KnowledgeGraph) -> float:
    if not candidate.sources:
        return 0.0
    tables = [graph.tables.get(s.table_fqn) for s in candidate.sources]
    tables = [t for t in tables if t]
    if not tables:
        return 0.0
    small = [t for t in tables if 0 < t.row_count <= REFERENCE_ROW_CEILING]
    no_measures = [t for t in tables if t.measure_count == 0]
    operand_attributes = [a for a in candidate.attributes if a.role == "operand"]
    filter_only = 1.0 if not operand_attributes else 0.0
    score = 0.0
    score += 0.4 * (len(small) / len(tables))
    score += 0.3 * (len(no_measures) / len(tables))
    score += 0.3 * filter_only
    return min(score, 0.99)


def _event_stream_score(candidate: Candidate, metrics, graph: KnowledgeGraph) -> float:
    if candidate.grain not in ("Event", "Transaction"):
        grain_signal = 0.0
    else:
        grain_signal = 1.0
    timestamp_columns = [
        a for a in candidate.attributes
        if "timestamp" in (a.data_type or "").lower() or a.name.endswith("_ts")
    ]
    mix = _aggregation_mix(metrics)
    counts_and_durations = mix.get("COUNT", 0.0) + mix.get("AVG", 0.0)
    score = 0.55 * grain_signal
    score += 0.2 * (1.0 if timestamp_columns else 0.0)
    score += 0.25 * counts_and_durations
    return min(score, 0.99)


def _metric_score(candidate: Candidate, metrics, graph: KnowledgeGraph) -> float:
    """The default for aggregation-heavy communities over one or two fact tables."""
    if len(metrics) < 3:
        base = 0.25
    else:
        base = 0.6
    fact_tables = [s for s in candidate.sources
                   if graph.tables.get(s.table_fqn) and graph.tables[s.table_fqn].measure_count >= 2]
    if 1 <= len(fact_tables) <= 2:
        base += 0.2
    elif fact_tables:
        base += 0.1
    mix = _aggregation_mix(metrics)
    aggregation_share = sum(v for k, v in mix.items() if k in ("SUM", "AVG", "MAX", "MIN", "RATIO"))
    base += 0.15 * aggregation_share
    if candidate.grain in ("unknown",):
        base -= 0.15
    return max(0.0, min(base, 0.95))


def _feature_store_score(candidate: Candidate, metrics, graph: KnowledgeGraph) -> float:
    term_hits = sum(
        1 for a in candidate.attributes
        if any(hint in (a.business_term or "").lower() for hint in FEATURE_TERM_HINTS)
        or any(hint in (a.definition or "").lower() for hint in FEATURE_TERM_HINTS)
    )
    report_hits = sum(
        1 for r in candidate.reports
        if any(hint in (r.report_name or "").lower() for hint in PREDICTIVE_REPORT_HINTS)
    )
    score = 0.0
    if term_hits:
        score += min(0.5, 0.12 * term_hits)
    if report_hits:
        score += min(0.45, 0.1 * report_hits)
    return min(score, 0.95)


def _insight_score(candidate: Candidate, metrics, graph: KnowledgeGraph) -> float:
    case_heavy = 0
    rank_heavy = 0
    for metric in metrics:
        shape = getattr(metric, "_shape", "") or ""
        expression = (getattr(metric, "_sample_expression", "") or "").lower()
        if "case(" in shape:
            case_heavy += 1
        if any(fn in expression for fn in RANK_FUNCTIONS):
            rank_heavy += 1
    if not metrics:
        return 0.0
    score = 0.55 * (case_heavy / len(metrics)) + 0.45 * (rank_heavy / len(metrics))
    return min(score, 0.95)


def _tier(candidate: Candidate, metrics, graph: KnowledgeGraph) -> tuple[str, float]:
    if candidate.origin == "composite" or candidate.tier == "Consumer-aligned":
        return "Consumer-aligned", 0.9
    systems = {s.system for s in candidate.sources if s.system}
    single_unit = (len(candidate.consumers) == 1)
    if single_unit and len(candidate.depends_on) >= 2:
        return "Consumer-aligned", 0.75
    if len(systems) <= 1:
        margin = 0.9 if len(systems) == 1 else 0.5
        return "Source-aligned", margin
    if candidate.parent_candidate_id:
        return "Aggregate", 0.8
    return "Aggregate", round(min(0.95, 0.55 + 0.1 * len(systems)), 2)


def apply_classification(candidates: list[Candidate], result: CanonicalizationResult,
                         graph: KnowledgeGraph) -> None:
    for candidate in candidates:
        classification = classify(candidate, result, graph,
                                  reuse_communities=getattr(candidate, "_reuse_communities", 0))
        if candidate.origin == "entity_master":
            candidate.archetype = "Entity Master"
            candidate.archetype_confidence = max(candidate.archetype_confidence, 0.9)
        else:
            candidate.archetype = classification.archetype
            candidate.archetype_confidence = classification.archetype_confidence
        candidate.archetype_runner_up = (
            classification.runner_up if classification.archetype_confidence < 0.6 else "")
        candidate.tier = classification.tier
        candidate.tier_confidence = classification.tier_confidence
        setattr(candidate, "_classification_rationale", classification.rationale)
