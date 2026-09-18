"""Canonicalization: collapse KPI rows into canonical metrics (section 5).

Deterministic matching runs first. Language models only name and describe;
they never decide equivalence, and everything they write stays AI_DRAFT until a
steward accepts it.
"""
from __future__ import annotations

import datetime as _dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..config import ER_PROBABLE_THRESHOLD, NOMINAL_CONFLICT_LABEL_SIMILARITY, EngineConfig
from ..models import CanonicalMetric, KnowledgeGraph, KpiNode, MetricConflict, MetricVariant
from ..usage import report_weights
from ..util.ids import stable_id
from ..util.text import embedding_similarity, snake_case, title_case
from .conflicts import build_conflict
from .fingerprint import apply_fingerprints

MATCH_TIERS = ("IDENTICAL", "VARIANT", "COUSIN", "OPAQUE")


@dataclass
class CanonicalizationResult:
    metrics: dict[str, CanonicalMetric] = field(default_factory=dict)
    variants: list[MetricVariant] = field(default_factory=list)
    conflicts: list[MetricConflict] = field(default_factory=list)
    metric_by_kpi: dict[str, str] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    def metrics_for_report(self, report_id: str) -> list[str]:
        return [m.metric_id for m in self.metrics.values() if report_id in m.report_ids]


def canonicalize(graph: KnowledgeGraph, config: EngineConfig | None = None,
                 as_of: _dt.date | None = None) -> CanonicalizationResult:
    config = config or EngineConfig()
    as_of = as_of or graph.as_of_date or _dt.date.today()
    apply_fingerprints(graph)
    weights = report_weights(list(graph.reports.values()), as_of,
                             config.recency_half_life_months)

    groups: dict[str, list[KpiNode]] = defaultdict(list)
    for kpi in graph.kpis.values():
        groups[kpi.fingerprint].append(kpi)

    result = CanonicalizationResult()
    used_names: set[str] = set()

    for fingerprint, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        metric = _build_metric(fingerprint, members, graph, weights, used_names, config)
        result.metrics[metric.metric_id] = metric
        used_names.add(metric.canonical_name)
        filter_groups = Counter(k.filter_fp for k in members)
        dominant_filter = filter_groups.most_common(1)[0][0]
        for kpi in members:
            tier = "OPAQUE" if kpi.parse_status != "PARSED" else (
                "IDENTICAL" if kpi.filter_fp == dominant_filter else "VARIANT")
            result.variants.append(MetricVariant(
                kpi_id=kpi.kpi_id, metric_id=metric.metric_id, tier=tier,
                filter_fp=kpi.filter_fp, filter_expression=kpi.filter_expression,
                variant_label=_variant_label(kpi),
            ))
            result.metric_by_kpi[kpi.kpi_id] = metric.metric_id
        metric.variant_count = len(filter_groups) - 1

    _link_cousins(result)
    result.conflicts = _find_conflicts(result, graph)
    result.stats = {
        "kpi_nodes": len(graph.kpis),
        "canonical_metrics": len(result.metrics),
        "metrics_with_variants": sum(1 for m in result.metrics.values() if m.variant_count),
        "opaque_metrics": sum(1 for m in result.metrics.values() if m.opaque),
        "conflicts": len(result.conflicts),
        "conflict_patterns": dict(Counter(c.pattern for c in result.conflicts)),
        "rows_collapsed": len(graph.kpis) - len(result.metrics),
        "unassigned_stewards": sum(1 for m in result.metrics.values() if not m.steward_id),
    }
    return result


# --------------------------------------------------------------------------

def _build_metric(fingerprint: str, members: list[KpiNode], graph: KnowledgeGraph,
                  weights: dict[str, float], used_names: set[str],
                  config: EngineConfig) -> CanonicalMetric:
    labels = [k.label for k in members if k.label]
    label_counts = Counter(labels)
    operand_columns = sorted({c for k in members for c in k.operand_columns})
    filter_columns = sorted({c for k in members for c in k.filter_columns})
    source_tables = sorted({c.rsplit(".", 1)[0] for c in operand_columns})
    report_ids: list[str] = []
    for kpi in members:
        for report_id in graph.reports_for_kpi(kpi.kpi_id) or [kpi.report_id]:
            if report_id and report_id not in report_ids:
                report_ids.append(report_id)
    grains = Counter(k.grain for k in members if k.grain)
    aggregations = Counter(k.aggregation for k in members if k.aggregation)
    opaque = all(k.parse_status != "PARSED" for k in members)
    domain, sub_domain = _dominant_domain(operand_columns, graph)

    metric_id = stable_id("MET", fingerprint)
    display_label = label_counts.most_common(1)[0][0] if label_counts else "unnamed metric"
    name = _propose_name(display_label, grains, used_names, config)
    definition = _propose_definition(display_label, members, operand_columns, grains, graph, opaque)

    steward_id, steward_source = _assign_steward(operand_columns, report_ids, graph)
    usage_weight = sum(weights.get(r, 0.0) for r in report_ids)
    business_units = {graph.reports[r].business_unit for r in report_ids if r in graph.reports}

    metric = CanonicalMetric(
        metric_id=metric_id,
        canonical_name=name,
        definition=definition,
        fingerprint=fingerprint,
        grain=grains.most_common(1)[0][0] if grains else "unknown",
        aggregation=aggregations.most_common(1)[0][0] if aggregations else "",
        steward_id=steward_id,
        steward_source=steward_source,
        name_status="AI_DRAFT",
        domain=domain,
        sub_domain=sub_domain,
        operand_columns=operand_columns,
        filter_columns=filter_columns,
        source_tables=source_tables,
        kpi_ids=[k.kpi_id for k in members],
        report_ids=report_ids,
        tools=sorted({k.tool for k in members}),
        labels=[label for label, _ in label_counts.most_common()],
        opaque=opaque,
        time_modifiers=sorted({k.time_modifier for k in members if k.time_modifier}),
        usage_weight=round(usage_weight, 4),
        consumer_breadth=len({bu for bu in business_units if bu}),
        report_count=len(report_ids),
    )
    setattr(metric, "_sample_expression", members[0].expression)
    setattr(metric, "_shape", (members[0].expression_ast or {}).get("shape", ""))
    return metric


def _variant_label(kpi: KpiNode) -> str:
    if kpi.filter_expression:
        return f"filtered: {kpi.filter_expression[:90]}"
    if kpi.time_modifier:
        return f"time modifier: {kpi.time_modifier}"
    return "base"


def _dominant_domain(columns: list[str], graph: KnowledgeGraph) -> tuple[str, str]:
    domains = Counter()
    sub_domains = Counter()
    for fqn in columns:
        column = graph.columns.get(fqn)
        if column and column.domain:
            domains[column.domain] += 1
            if column.sub_domain:
                sub_domains[column.sub_domain] += 1
    domain = domains.most_common(1)[0][0] if domains else ""
    sub_domain = sub_domains.most_common(1)[0][0] if sub_domains else ""
    return domain, sub_domain


def _propose_name(label: str, grains: Counter, used: set[str], config: EngineConfig) -> str:
    """Draft a snake_case canonical name. Marked AI_DRAFT until a steward accepts."""
    base = snake_case(label) or "metric"
    name = base
    if name in used:
        grain = grains.most_common(1)[0][0] if grains else ""
        if grain and grain != "unknown":
            name = f"{base}_by_{snake_case(grain)}"
    suffix = 2
    while name in used:
        name = f"{base}_{suffix}"
        suffix += 1
    return name


def _propose_definition(label: str, members: list[KpiNode], operand_columns: list[str],
                        grains: Counter, graph: KnowledgeGraph, opaque: bool) -> str:
    if opaque:
        return (f"{title_case(label)} as computed by {len(members)} report calculation(s) the "
                "parser could not read; a steward must write this definition by hand.")
    grain = grains.most_common(1)[0][0] if grains else "unknown"
    terms = []
    for fqn in operand_columns[:3]:
        column = graph.columns.get(fqn)
        if column:
            terms.append(column.business_term or column.column_name)
    aggregation = (members[0].aggregation or "").lower() or "aggregate"
    phrase = {
        "sum": "the total of", "avg": "the average of", "count": "the count of",
        "count distinct": "the distinct count of", "max": "the maximum of",
        "min": "the minimum of", "ratio": "the ratio of",
    }.get(aggregation, f"the {aggregation} of")
    source = ", ".join(terms) if terms else "its source columns"
    return (f"{title_case(label)} is {phrase} {source}, evaluated at {grain} grain "
            f"across {len(members)} report calculation(s).")


def _assign_steward(operand_columns: list[str], report_ids: list[str],
                    graph: KnowledgeGraph) -> tuple[str, str]:
    """Steward of the fact table's business term, else the most frequent report owner."""
    for fqn in operand_columns:
        column = graph.columns.get(fqn)
        if column and column.business_term and column.steward_id:
            return column.steward_id, "business term steward"
    for fqn in operand_columns:
        column = graph.columns.get(fqn)
        if column and column.steward_id:
            return column.steward_id, "column steward"
    owners = Counter(graph.reports[r].owner for r in report_ids
                     if r in graph.reports and graph.reports[r].owner)
    if owners:
        return owners.most_common(1)[0][0], "most frequent report owner"
    return "", "unassigned"


def _link_cousins(result: CanonicalizationResult) -> None:
    """Same operand columns, different aggregation: separate metrics, cross-linked."""
    by_operands: dict[tuple[str, ...], list[CanonicalMetric]] = defaultdict(list)
    for metric in result.metrics.values():
        if metric.operand_columns:
            by_operands[tuple(metric.operand_columns)].append(metric)
    for group in by_operands.values():
        if len(group) < 2:
            continue
        for metric in group:
            for other in group:
                if other.metric_id == metric.metric_id:
                    continue
                if other.aggregation != metric.aggregation:
                    if other.metric_id not in metric.cousins:
                        metric.cousins.append(other.metric_id)


def _find_conflicts(result: CanonicalizationResult, graph: KnowledgeGraph) -> list[MetricConflict]:
    """Similar label, different fingerprint: a nominal conflict for a steward.

    Metrics whose lineage resolved only on probable edges are excluded: an edge
    below 0.80 confidence is never used to claim a KPI conflict (section 4.2).
    """
    metrics = [m for m in result.metrics.values() if not m.opaque]
    confident = _confident_metric_ids(result, graph)
    conflicts: list[MetricConflict] = []
    seen: set[tuple[str, str]] = set()
    by_token: dict[str, list[CanonicalMetric]] = defaultdict(list)
    for metric in metrics:
        for label in metric.labels[:3]:
            head = (label or "").strip().lower()[:12]
            by_token[head].append(metric)

    for bucket in by_token.values():
        for i, metric_a in enumerate(bucket):
            for metric_b in bucket[i + 1:]:
                if metric_a.metric_id == metric_b.metric_id:
                    continue
                if metric_a.fingerprint == metric_b.fingerprint:
                    continue
                key = tuple(sorted((metric_a.metric_id, metric_b.metric_id)))
                if key in seen:
                    continue
                if metric_a.metric_id not in confident or metric_b.metric_id not in confident:
                    continue
                similarity = max(
                    embedding_similarity(label_a, label_b)
                    for label_a in metric_a.labels[:3] for label_b in metric_b.labels[:3]
                ) if metric_a.labels and metric_b.labels else 0.0
                if similarity < NOMINAL_CONFLICT_LABEL_SIMILARITY:
                    continue
                seen.add(key)
                conflicts.append(build_conflict(
                    metric_a, metric_b, similarity,
                    getattr(metric_a, "_shape", ""), getattr(metric_b, "_shape", "")))
    conflicts.sort(key=lambda c: -(c.usage_weight_a + c.usage_weight_b))
    return conflicts


def _confident_metric_ids(result: CanonicalizationResult, graph: KnowledgeGraph) -> set[str]:
    confidence_by_kpi: dict[str, float] = defaultdict(lambda: 1.0)
    for edge in graph.edges_kpi_column:
        if edge.role == "operand":
            confidence_by_kpi[edge.kpi_id] = min(confidence_by_kpi[edge.kpi_id], edge.confidence)
    out = set()
    for metric in result.metrics.values():
        confidences = [confidence_by_kpi[k] for k in metric.kpi_ids if k in confidence_by_kpi]
        if confidences and max(confidences) >= ER_PROBABLE_THRESHOLD:
            out.add(metric.metric_id)
    return out
