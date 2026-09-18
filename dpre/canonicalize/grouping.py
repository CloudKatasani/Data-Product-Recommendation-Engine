"""Canonicalization: collapse KPI rows into canonical metrics (section 5).

Deterministic matching runs first. Language models only name and describe;
they never decide equivalence, and everything they write stays AI_DRAFT until a
steward accepts it.

The conflict register is the first artefact a domain steward sees (section
5.3), so its precision and recall are governed here rather than left to a
label prefix (review finding R-18):

* Precision. A pair whose two sides are the same expression text resolved to
  different catalog columns, or whose operand difference is exactly the set of
  references quarantined on one side, is a *lineage-induced apparent conflict*.
  It is a gap for the catalog admin, not a decision for the steward, and it is
  kept out of the register, the consolidation and risk features and the heat
  map. Every conflict that remains has expression text that differs.
* Recall. Candidates for comparison are blocked three ways - equal label key
  (abbreviations expanded, plurals folded, order ignored), a shared operand
  column, and a shared glossary term - instead of the first twelve characters
  of the label, so "Net Revenue" meets "Revenue, Net" and "Arrears 60+ Days"
  meets "60+ Day Arrears". The similarity method and parser version are
  recorded on each conflict.

Stewardship is resolved in a declared order with a confidence (R-44, R-34):
glossary term steward, column-extract term steward, fact-table steward by
majority, domain steward, and only then the most frequent report owner - which
is recorded as a suggestion, never as a confirmed steward - with the domain
owner named as the escalation point when nothing resolves.
"""
from __future__ import annotations

import datetime as _dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..config import (
    ER_PROBABLE_THRESHOLD, NOMINAL_CONFLICT_LABEL_SIMILARITY, PARSER_VERSION, EngineConfig,
)
from ..graph.domains import domain_nodes
from ..models import CanonicalMetric, KnowledgeGraph, KpiNode, MetricConflict, MetricVariant
from ..score.demand import report_weights
from ..util.ids import stable_id
from ..util.text import embedding_similarity, snake_case, title_case, tokenize
from .conflicts import build_conflict
from .fingerprint import apply_fingerprints, normalized_expression_text

MATCH_TIERS = ("IDENTICAL", "VARIANT", "COUSIN", "OPAQUE")
SIMILARITY_METHOD = "offline-token"      # cortex-embed when an embedder is registered

# Steward resolution order (R-44): rule name, confidence, and the vocabulary the
# card and the 14.2 measures already understand.
STEWARD_RULES = (
    ("glossary term steward", 0.95, "business term steward"),
    ("column term steward", 0.85, "business term steward"),
    ("fact table steward", 0.75, "column steward"),
    ("table steward", 0.70, "column steward"),
    ("domain steward", 0.60, "column steward"),
    ("report owner suggestion (not a steward)", 0.30, "most frequent report owner"),
    ("unassigned; escalate to domain owner", 0.0, "unassigned"),
)


@dataclass
class CanonicalizationResult:
    metrics: dict[str, CanonicalMetric] = field(default_factory=dict)
    variants: list[MetricVariant] = field(default_factory=list)
    conflicts: list[MetricConflict] = field(default_factory=list)
    metric_by_kpi: dict[str, str] = field(default_factory=dict)
    # Pairs that looked like conflicts but are lineage artefacts (R-18): a gap
    # list for the catalog admin, never a steward decision.
    lineage_induced: list[dict] = field(default_factory=list)
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

    for fingerprint, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
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
        "lineage_induced_conflicts": len(result.lineage_induced),
        "similarity_method": SIMILARITY_METHOD,
        "rows_collapsed": len(graph.kpis) - len(result.metrics),
        "unassigned_stewards": sum(1 for m in result.metrics.values() if not m.steward_id),
        "steward_rules": dict(Counter(getattr(m, "_steward_rule", "") for m in result.metrics.values())),
        "stewards_confirmed": sum(1 for m in result.metrics.values()
                                  if m.steward_source in ("business term steward", "column steward")),
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

    steward = assign_steward(operand_columns, report_ids, graph, domain)
    usage_weight = sum(weights.get(r, 0.0) for r in report_ids)
    business_units = {graph.reports[r].business_unit for r in report_ids if r in graph.reports}

    metric = CanonicalMetric(
        metric_id=metric_id,
        canonical_name=name,
        definition=definition,
        fingerprint=fingerprint,
        grain=grains.most_common(1)[0][0] if grains else "unknown",
        aggregation=aggregations.most_common(1)[0][0] if aggregations else "",
        steward_id=steward["steward_id"],
        steward_source=steward["steward_source"],
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
    setattr(metric, "_expression_texts",
            sorted({normalized_expression_text(k.expression) for k in members}))
    setattr(metric, "_raw_shapes",
            sorted({(k.expression_ast or {}).get("raw_shape", "") for k in members} - {""}))
    setattr(metric, "_null_rules",
            sorted({(k.expression_ast or {}).get("null_rule", "") for k in members} - {""}))
    setattr(metric, "_steward_rule", steward["rule"])
    setattr(metric, "_steward_confidence", steward["confidence"])
    setattr(metric, "_steward_escalation", steward["escalate_to"])
    setattr(metric, "_steward_candidates", steward["considered"])
    return metric


def _variant_label(kpi: KpiNode) -> str:
    null_rule = (kpi.expression_ast or {}).get("null_rule", "")
    if kpi.filter_expression:
        return f"filtered: {kpi.filter_expression[:90]}"
    if kpi.time_modifier:
        return f"time modifier: {kpi.time_modifier}"
    if null_rule:
        return f"null rule: {null_rule[:80]}"
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


def assign_steward(operand_columns: list[str], report_ids: list[str],
                   graph: KnowledgeGraph, domain: str = "") -> dict:
    """Resolve a steward in the declared order, with the rule and confidence (R-44).

    Fact tables are consulted before dimensions, and columns in a fixed order,
    so the answer never depends on extract order. Report owners are a suggestion
    (the card labels them as inferred); they are not stewards.
    """
    considered: list[dict] = []
    ordered = sorted(operand_columns, key=lambda fqn: (
        -(graph.tables[fqn.rsplit(".", 1)[0]].measure_count
          if fqn.rsplit(".", 1)[0] in graph.tables else 0), fqn))
    columns = [graph.columns[fqn] for fqn in ordered if fqn in graph.columns]

    def found(rule: str, steward_id: str, via: str) -> dict:
        _name, confidence, source = next(r for r in STEWARD_RULES if r[0] == rule)
        considered.append({"rule": rule, "steward_id": steward_id, "via": via})
        return {"steward_id": steward_id, "steward_source": source, "rule": rule,
                "confidence": confidence, "escalate_to": "", "considered": considered}

    for column in columns:
        link = getattr(column, "_term_link", None)
        if link and link.glossary_steward:
            return found("glossary term steward", link.glossary_steward,
                         f"{column.business_term} ({link.term_id})")
    for column in columns:
        if column.business_term and column.steward_id:
            return found("column term steward", column.steward_id, column.column_fqn)
    tables = []
    for fqn in ordered:
        table = graph.tables.get(fqn.rsplit(".", 1)[0])
        if table and table not in tables:
            tables.append(table)
    for table in tables:
        if table.measure_count >= 2 and table.steward_id:
            return found("fact table steward", table.steward_id, table.table_fqn)
    for table in tables:
        if table.steward_id:
            return found("table steward", table.steward_id, table.table_fqn)
    domain_node = domain_nodes(graph).get(domain) if domain else None
    if domain_node and domain_node.steward:
        return found("domain steward", domain_node.steward, f"domain {domain}")
    owners = Counter(graph.reports[r].owner for r in report_ids
                     if r in graph.reports and graph.reports[r].owner)
    escalate = domain_node.owner if domain_node else ""
    if owners:
        owner = sorted(owners.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        outcome = found("report owner suggestion (not a steward)", owner,
                        f"owner of {owners[owner]} report(s)")
        outcome["escalate_to"] = escalate
        return outcome
    considered.append({"rule": "unassigned; escalate to domain owner", "steward_id": "",
                       "via": f"domain owner {escalate}" if escalate else "no domain owner"})
    return {"steward_id": "", "steward_source": "unassigned",
            "rule": "unassigned; escalate to domain owner", "confidence": 0.0,
            "escalate_to": escalate, "considered": considered}


def _assign_steward(operand_columns: list[str], report_ids: list[str],
                    graph: KnowledgeGraph) -> tuple[str, str]:
    """Compatibility shim: ``(steward_id, steward_source)``."""
    outcome = assign_steward(operand_columns, report_ids, graph)
    return outcome["steward_id"], outcome["steward_source"]


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


# --------------------------------------------------------------------------
# Conflicts (section 5.2 nominal conflict; R-18)
# --------------------------------------------------------------------------

_PLURAL_KEEP = {"days", "sales", "series", "status", "basis", "analysis", "process"}


def label_key(label: str) -> str:
    """Order-free, abbreviation-expanded, plural-folded key of a label.

    ``Net Revenue`` and ``Revenue, Net`` share a key; so do ``Arrears 60+ Days``
    and ``60+ Day Arrears``. The key blocks comparison and also counts as full
    similarity, which is what a steward would say of those pairs.
    """
    tokens = []
    for token in tokenize(label):
        if token.endswith("s") and len(token) > 3 and not token.endswith("ss") \
                and token not in _PLURAL_KEEP:
            token = token[:-1]
        elif token in ("days",):
            token = "day"
        tokens.append(token)
    return " ".join(sorted(tokens))


def label_similarity(label_a: str, label_b: str) -> float:
    if label_key(label_a) and label_key(label_a) == label_key(label_b):
        return 1.0
    return embedding_similarity(label_a, label_b)


def _find_conflicts(result: CanonicalizationResult, graph: KnowledgeGraph) -> list[MetricConflict]:
    """Similar label, different fingerprint: a nominal conflict for a steward.

    Metrics whose lineage resolved only on probable edges are excluded: an edge
    below 0.80 confidence is never used to claim a KPI conflict (section 4.2).
    Pairs explained by lineage resolution are listed as lineage-induced instead.
    """
    metrics = [m for m in result.metrics.values() if not m.opaque]
    confident = _confident_metric_ids(result, graph)
    conflicts: list[MetricConflict] = []
    seen: set[tuple[str, str]] = set()

    blocks: dict[str, list[CanonicalMetric]] = defaultdict(list)
    for metric in metrics:
        for label in metric.labels[:3]:
            key = label_key(label)
            if key:
                blocks[f"label:{key}"].append(metric)
            term = graph.glossary.get(" ".join(sorted(tokenize(label))))
            if term is not None:
                blocks[f"term:{term.term_id}"].append(metric)
        for column in metric.operand_columns:
            blocks[f"column:{column}"].append(metric)

    for block_key in sorted(blocks):
        bucket = sorted(blocks[block_key], key=lambda m: m.metric_id)
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
                    label_similarity(label_a, label_b)
                    for label_a in metric_a.labels[:3] for label_b in metric_b.labels[:3]
                ) if metric_a.labels and metric_b.labels else 0.0
                if similarity < NOMINAL_CONFLICT_LABEL_SIMILARITY:
                    continue
                seen.add(key)
                induced = lineage_induced(metric_a, metric_b, graph)
                if induced:
                    result.lineage_induced.append({
                        "metric_id_a": metric_a.metric_id, "metric_id_b": metric_b.metric_id,
                        "label": metric_a.labels[0] if metric_a.labels else metric_a.canonical_name,
                        "reason": induced["reason"],
                        "unresolved_references": induced["unresolved"],
                        "quarantine_reasons": induced["reasons"],
                        "resolved_a": induced["resolved_a"],
                        "resolved_b": induced["resolved_b"],
                        "kpi_ids_a": list(metric_a.kpi_ids),
                        "kpi_ids_b": list(metric_b.kpi_ids),
                        "usage_at_stake": round(metric_a.usage_weight + metric_b.usage_weight, 2),
                        "similarity": round(similarity, 3),
                    })
                    continue
                conflict = build_conflict(
                    metric_a, metric_b, similarity,
                    getattr(metric_a, "_shape", ""), getattr(metric_b, "_shape", ""))
                unresolved = (_quarantined_operands(metric_a, graph)
                              + _quarantined_operands(metric_b, graph))
                if unresolved:
                    conflict.difference_summary += (
                        f"; {len(unresolved)} operand reference(s) unresolved on one side")
                setattr(conflict, "_similarity_method", SIMILARITY_METHOD)
                setattr(conflict, "_parser_version", PARSER_VERSION)
                setattr(conflict, "_block", block_key.split(":", 1)[0])
                conflicts.append(conflict)
    conflicts.sort(key=lambda c: (-(c.usage_weight_a + c.usage_weight_b), c.conflict_id))
    return conflicts


def lineage_induced(metric_a: CanonicalMetric, metric_b: CanonicalMetric,
                    graph: KnowledgeGraph) -> dict | None:
    """Is this apparent conflict an artefact of lineage resolution? (R-18)

    The test is the arithmetic shape with references named as the reports
    wrote them, before catalog resolution. Equal raw shapes with different
    fingerprints means the reports wrote the same calculation and only the
    resolution differs - a quarantined reference on one side, or the same
    reference resolved to two catalog columns. A threshold, denominator or time
    basis that differs shows in the raw shape, so a genuine conflict that also
    carries a lineage gap stays a conflict (with the gap noted).
    """
    shapes_a = set(getattr(metric_a, "_raw_shapes", []) or [])
    shapes_b = set(getattr(metric_b, "_raw_shapes", []) or [])
    texts_a = set(getattr(metric_a, "_expression_texts", []) or [])
    texts_b = set(getattr(metric_b, "_expression_texts", []) or [])
    if not (shapes_a & shapes_b) and not (texts_a & texts_b):
        return None
    quarantine_a = _quarantined_operands(metric_a, graph)
    quarantine_b = _quarantined_operands(metric_b, graph)
    unresolved = sorted({q.raw_reference for q in quarantine_a + quarantine_b})
    reasons = sorted({q.reason_code for q in quarantine_a + quarantine_b})
    if unresolved:
        reason = ("same calculation on both sides; the operand sets differ because "
                  f"{len(unresolved)} reference(s) quarantined on one side")
    else:
        reason = ("same calculation on both sides resolved to different catalog columns; "
                  "confirm which resolution is right")
    return {"reason": reason, "unresolved": unresolved, "reasons": reasons,
            "resolved_a": list(metric_a.operand_columns),
            "resolved_b": list(metric_b.operand_columns)}


def _quarantined_operands(metric: CanonicalMetric, graph: KnowledgeGraph) -> list:
    kpi_ids = set(metric.kpi_ids)
    return [q for q in graph.quarantine if q.kpi_id in kpi_ids and q.role == "operand"]


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
