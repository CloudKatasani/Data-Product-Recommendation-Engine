"""Candidate generation (specification section 6).

A candidate is a set of canonical metrics that share one evaluation grain, draw
on an overlapping set of source tables, and serve an identifiable group of
consumers. Generation is graph clustering under a grain constraint, not a rules
list per domain.
"""
from __future__ import annotations

import datetime as _dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..canonicalize.grouping import CanonicalizationResult
from ..config import ER_PROBABLE_THRESHOLD, ClusterConfig, EngineConfig
from ..graph.grain import grain_ambiguity
from ..models import (
    Candidate, CandidateAttribute, CandidateConsumer, CandidateReport, CandidateSource,
    KnowledgeGraph,
)
from ..usage import dominant_cadence, report_weights, scheduled_share
from ..util.ids import stable_id
from ..util.text import title_case
from .bipartite import Bipartite, build_bipartite, conformed_tables, hub_tables, project
from .louvain import Graph, louvain


@dataclass
class ClusterResult:
    candidates: list[Candidate] = field(default_factory=list)
    communities: dict[str, list[str]] = field(default_factory=dict)
    affinity: list[tuple[str, str, float]] = field(default_factory=list)
    resolution_sweep: list[dict] = field(default_factory=list)
    entity_master_tables: list[str] = field(default_factory=list)
    data_gap_metrics: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def generate_candidates(result: CanonicalizationResult, graph: KnowledgeGraph,
                        config: EngineConfig | None = None, run_id: str = "",
                        as_of: _dt.date | None = None) -> ClusterResult:
    config = config or EngineConfig()
    cluster_config = config.cluster
    as_of = as_of or graph.as_of_date or _dt.date.today()
    weights = report_weights(list(graph.reports.values()), as_of, config.recency_half_life_months)

    bipartite = build_bipartite(result, graph)
    out = ClusterResult(data_gap_metrics=list(bipartite.unresolved_metrics))

    # Pass 1 forms communities with the conformed dimensions held out, so a shared
    # dimension cannot glue the estate into one cluster and can be seen for what
    # it is: a hub reaching several communities.
    conformed = conformed_tables(bipartite, graph)
    first_pass = _detect(bipartite, result, cluster_config, cluster_config.resolution, conformed)
    masters = hub_tables(bipartite, first_pass, graph, cluster_config.entity_master_min_communities)
    out.entity_master_tables = masters

    edges = project(bipartite, result, cluster_config, set(masters))
    communities = _detect(bipartite, result, cluster_config, cluster_config.resolution,
                          set(masters), edges)
    out.affinity = edges

    for resolution in cluster_config.resolution_sweep:
        swept = _detect(bipartite, result, cluster_config, resolution, set(masters), edges)
        sizes = Counter(swept.values())
        out.resolution_sweep.append({
            "resolution": resolution,
            "communities": len(sizes),
            "largest": max(sizes.values()) if sizes else 0,
            "singletons": sum(1 for v in sizes.values() if v == 1),
            "selected": abs(resolution - cluster_config.resolution) < 1e-9,
        })

    grouped: dict[str, list[str]] = defaultdict(list)
    for metric_id, community in communities.items():
        grouped[community].append(metric_id)
    out.communities = {k: sorted(v) for k, v in grouped.items()}

    candidates: list[Candidate] = []
    for community, metric_ids in sorted(out.communities.items()):
        candidates.extend(_community_candidates(
            community, metric_ids, result, graph, bipartite, config, run_id, weights, as_of))

    candidates.extend(_entity_master_candidates(
        masters, bipartite, result, graph, config, run_id, weights, as_of, communities))
    candidates.extend(_composite_candidates(
        candidates, result, graph, config, run_id, weights, as_of))

    out.candidates = candidates
    out.stats = {
        "metrics_clustered": len(communities),
        "communities": len(out.communities),
        "candidates": len(candidates),
        "entity_masters": len(masters),
        "composites": sum(1 for c in candidates if c.origin == "composite"),
        "grain_children": sum(1 for c in candidates if c.origin == "grain_child"),
        "data_gap_metrics": len(out.data_gap_metrics),
        "affinity_edges": len(edges),
        "resolution_sweep": out.resolution_sweep,
    }
    return out


# --------------------------------------------------------------------------

def _detect(bipartite: Bipartite, result: CanonicalizationResult, config: ClusterConfig,
            resolution: float, excluded: set[str],
            edges: list[tuple[str, str, float]] | None = None) -> dict[str, str]:
    if edges is None:
        edges = project(bipartite, result, config, excluded)
    graph = Graph()
    for metric_id in bipartite.tables_by_metric:
        if bipartite.tables_by_metric[metric_id] - excluded:
            graph.add_node(metric_id)
    for a, b, weight in edges:
        graph.add_edge(a, b, weight)
    communities = louvain(graph, resolution=resolution, seed=config.random_seed)
    for metric_id in graph.nodes:
        communities.setdefault(metric_id, f"S-{metric_id}")
    return communities


def _community_candidates(community: str, metric_ids: list[str], result: CanonicalizationResult,
                          graph: KnowledgeGraph, bipartite: Bipartite, config: EngineConfig,
                          run_id: str, weights: dict[str, float],
                          as_of: _dt.date) -> list[Candidate]:
    """One community becomes one candidate, split first if it mixes grains."""
    by_grain: dict[str, list[str]] = defaultdict(list)
    for metric_id in metric_ids:
        by_grain[result.metrics[metric_id].grain or "unknown"].append(metric_id)

    ordered = sorted(by_grain.items(),
                     key=lambda kv: (-sum(result.metrics[m].usage_weight for m in kv[1]), kv[0]))
    candidates: list[Candidate] = []
    parent: Candidate | None = None
    for index, (grain, members) in enumerate(ordered):
        origin = "community" if index == 0 else "grain_child"
        candidate = _build_candidate(members, result, graph, bipartite, config, run_id,
                                     weights, as_of, origin=origin, community=community,
                                     grain=grain)
        if index == 0:
            parent = candidate
        elif parent is not None:
            candidate.parent_candidate_id = parent.candidate_id
            parent.child_candidate_ids.append(candidate.candidate_id)
            candidate.depends_on.append(parent.candidate_id)
        candidates.append(candidate)
    return candidates


def _build_candidate(metric_ids: list[str], result: CanonicalizationResult, graph: KnowledgeGraph,
                     bipartite: Bipartite, config: EngineConfig, run_id: str,
                     weights: dict[str, float], as_of: _dt.date, origin: str,
                     community: str = "", grain: str = "",
                     name_hint: str = "") -> Candidate:
    metrics = [result.metrics[m] for m in metric_ids]
    report_ids: list[str] = []
    for metric in metrics:
        for report_id in metric.report_ids:
            if report_id not in report_ids:
                report_ids.append(report_id)
    reports = [graph.reports[r] for r in report_ids if r in graph.reports]

    grains = [m.grain for m in metrics]
    grain = grain or (Counter(grains).most_common(1)[0][0] if grains else "unknown")
    domains = Counter(m.domain for m in metrics if m.domain)
    sub_domains = Counter(m.sub_domain for m in metrics if m.sub_domain)
    domain = domains.most_common(1)[0][0] if domains else ""
    sub_domain = sub_domains.most_common(1)[0][0] if sub_domains else ""

    candidate_id = stable_id("CAND", run_id, community or origin, grain,
                             ";".join(sorted(metric_ids)))
    candidate = Candidate(
        candidate_id=candidate_id,
        run_id=run_id,
        proposed_name=name_hint or _propose_name(metrics, domain, grain),
        purpose="",
        archetype="",
        tier="",
        grain=grain,
        domain=domain,
        sub_domain=sub_domain,
        metric_ids=sorted(metric_ids),
        origin=origin,
        resolution=config.cluster.resolution,
        as_of_date=as_of.isoformat(),
        tools=sorted({tool for m in metrics for tool in m.tools}),
    )
    candidate.sources = _sources(metrics, graph)
    candidate.consumers = _consumers(reports, weights)
    candidate.reports = _retirable_reports(candidate, metrics, result, graph)
    candidate.attributes = _attributes(metrics, graph)
    candidate.conflicts = sorted({
        conflict.conflict_id for conflict in result.conflicts
        if conflict.metric_id_a in metric_ids or conflict.metric_id_b in metric_ids
    })
    candidate.owner_candidate, candidate.steward_candidate = _owner_and_steward(
        metrics, candidate.sources, graph)
    candidate.gaps = _gaps(candidate, metrics, graph)
    setattr(candidate, "_grain_ambiguity", grain_ambiguity(grains))
    setattr(candidate, "_usage_weight", sum(weights.get(r, 0.0) for r in report_ids))
    return candidate


# Measure words carry no subject, so they never name a candidate.
GENERIC_TOKENS = {
    "amount", "balance", "count", "total", "average", "rate", "ratio", "value",
    "number", "days", "minutes", "seconds", "hours", "score", "index", "share",
    "per", "of", "and", "the", "by", "in", "flag", "volume", "position", "legacy",
}


def _propose_name(metrics, domain: str, grain: str) -> str:
    """Name from the dominant domain, grain and top metrics. Marked AI_DRAFT."""
    top = sorted(metrics, key=lambda m: -m.usage_weight)[:4]
    subjects: list[str] = []
    for metric in top:
        label = metric.labels[0] if metric.labels else metric.canonical_name
        for token in label.replace("_", " ").split():
            clean = token.strip().lower()
            if clean in GENERIC_TOKENS or len(clean) < 3:
                continue
            if clean not in subjects:
                subjects.append(clean)
    head = " and ".join(subjects[:2]) if subjects else (domain.lower() or "metrics")
    noun = "position" if any(m.aggregation in ("SUM", "AVG") for m in top) else "activity"
    subject = f"{head} {noun}"
    if grain and grain != "unknown":
        return f"{title_case(subject)} by {grain.lower()}"
    return title_case(subject)


def _sources(metrics, graph: KnowledgeGraph) -> list[CandidateSource]:
    counts: Counter = Counter()
    for metric in metrics:
        for table in {c.rsplit(".", 1)[0] for c in metric.operand_columns}:
            counts[table] += 1
    total = max(1, len(metrics))
    out = []
    for table_fqn, count in counts.most_common():
        table = graph.tables.get(table_fqn)
        if table is None:
            continue
        out.append(CandidateSource(
            table_fqn=table_fqn, system=table.system,
            share_of_metrics=round(count / total, 3), sor_flag=table.sor_flag,
            lifecycle_status=table.lifecycle_status, domain=table.domain,
            successor_system=table.successor_system,
        ))
    return out


def _consumers(reports, weights: dict[str, float]) -> list[CandidateConsumer]:
    by_unit: dict[str, list] = defaultdict(list)
    for report in reports:
        by_unit[report.business_unit or "Unassigned"].append(report)
    out = []
    for unit, group in by_unit.items():
        top = sorted(group, key=lambda r: -weights.get(r.report_id, 0.0))[:5]
        out.append(CandidateConsumer(
            business_unit=unit,
            users=sum(r.distinct_users_12m for r in group),
            report_count=len(group),
            scheduled_share=round(scheduled_share(group), 3),
            top_reports=[r.report_id for r in top],
            cadence=dominant_cadence(group),
        ))
    out.sort(key=lambda c: (-c.users, c.business_unit))
    return out


def _retirable_reports(candidate: Candidate, metrics, result: CanonicalizationResult,
                       graph: KnowledgeGraph) -> list[CandidateReport]:
    """Coverage = share of a report's metrics this candidate would carry."""
    candidate_metrics = set(candidate.metric_ids)
    metrics_by_report: dict[str, set[str]] = defaultdict(set)
    for metric in result.metrics.values():
        for report_id in metric.report_ids:
            metrics_by_report[report_id].add(metric.metric_id)

    out = []
    for report_id in {r for m in metrics for r in m.report_ids}:
        report = graph.reports.get(report_id)
        if report is None:
            continue
        all_metrics = metrics_by_report.get(report_id, set())
        if not all_metrics:
            continue
        covered = len(all_metrics & candidate_metrics)
        out.append(CandidateReport(
            report_id=report_id, report_name=report.report_name,
            coverage=round(covered / len(all_metrics), 3), disposition=report.disposition,
            users=report.distinct_users_12m,
            last_run=report.last_run_date.isoformat() if report.last_run_date else "",
            business_unit=report.business_unit, owner=report.owner,
        ))
    out.sort(key=lambda r: (-r.coverage, -r.users))
    return out


def _attributes(metrics, graph: KnowledgeGraph) -> list[CandidateAttribute]:
    seen: dict[str, CandidateAttribute] = {}
    confidence_by_column: dict[str, float] = {}
    for edge in graph.edges_kpi_column:
        confidence_by_column[edge.column_fqn] = max(
            confidence_by_column.get(edge.column_fqn, 0.0), edge.confidence)
    for metric in metrics:
        for role, columns in (("operand", metric.operand_columns),
                              ("filter", metric.filter_columns)):
            for fqn in columns:
                if fqn in seen:
                    continue
                column = graph.columns.get(fqn)
                if column is None:
                    continue
                seen[fqn] = CandidateAttribute(
                    column_fqn=fqn, name=column.column_name, role=role,
                    data_type=column.data_type, definition=column.definition,
                    business_term=column.business_term, sensitivity=column.sensitivity,
                    pii_flag=column.pii_flag, steward_id=column.steward_id,
                    nullable=column.nullable,
                    confidence=round(confidence_by_column.get(fqn, 0.0), 2),
                )
    return sorted(seen.values(), key=lambda a: (a.role != "operand", a.name))


def _owner_and_steward(metrics, sources: list[CandidateSource],
                       graph: KnowledgeGraph) -> tuple[str, str]:
    owners: Counter = Counter()
    stewards: Counter = Counter()
    for source in sources:
        table = graph.tables.get(source.table_fqn)
        if table:
            if table.owner_id:
                owners[table.owner_id] += 1
            if table.steward_id:
                stewards[table.steward_id] += 1
    for metric in metrics:
        if metric.steward_id:
            stewards[metric.steward_id] += 1
    owner = owners.most_common(1)[0][0] if owners else ""
    steward = stewards.most_common(1)[0][0] if stewards else ""
    return owner, steward


def _gaps(candidate: Candidate, metrics, graph: KnowledgeGraph) -> list[str]:
    gaps: list[str] = []
    missing_definitions = [a.name for a in candidate.attributes
                           if a.role == "operand" and not a.definition]
    if missing_definitions:
        gaps.append(f"{len(missing_definitions)} operand columns have no catalog definition: "
                    + ", ".join(sorted(missing_definitions)[:5]))
    missing_stewards = [a.name for a in candidate.attributes if not a.steward_id]
    if missing_stewards:
        gaps.append(f"{len(missing_stewards)} attributes have no steward")
    opaque = [m.canonical_name for m in metrics if m.opaque]
    if opaque:
        gaps.append("opaque calculations needing a manual definition: " + ", ".join(opaque[:5]))
    quarantined = [q for q in graph.quarantine
                   if any(q.kpi_id in m.kpi_ids for m in metrics)]
    if quarantined:
        reasons = Counter(q.reason_code for q in quarantined)
        gaps.append("unresolved lineage rows: "
                    + ", ".join(f"{code} x{count}" for code, count in reasons.most_common()))
    probable = [a.name for a in candidate.attributes
                if 0 < a.confidence < ER_PROBABLE_THRESHOLD]
    if probable:
        gaps.append(f"{len(probable)} attributes resolved on probable lineage only "
                    "(below 0.80 confidence)")
    return gaps


def _entity_master_candidates(masters: list[str], bipartite: Bipartite,
                              result: CanonicalizationResult, graph: KnowledgeGraph,
                              config: EngineConfig, run_id: str, weights: dict[str, float],
                              as_of: _dt.date, communities: dict[str, str]) -> list[Candidate]:
    """Hub tables with a backbone key become Entity Master candidates (section 6.3).

    They are ranked separately because their value is reuse, not usage.
    """
    out = []
    for table_fqn in masters:
        metric_ids = sorted(bipartite.metrics_by_table.get(table_fqn, set()))
        if not metric_ids:
            continue
        table = graph.tables.get(table_fqn)
        if table is None:
            continue
        candidate = _build_candidate(
            metric_ids, result, graph, bipartite, config, run_id, weights, as_of,
            origin="entity_master", community=f"EM-{table.table_name}",
            grain=table.inferred_grain,
            name_hint=f"{table.inferred_grain} master",
        )
        candidate.archetype = "Entity Master"
        candidate.archetype_confidence = 0.9
        reach = len({communities.get(m) for m in metric_ids if communities.get(m)})
        setattr(candidate, "_reuse_communities", reach)
        candidate.sources = [s for s in candidate.sources if s.table_fqn == table_fqn] or candidate.sources
        out.append(candidate)
    return out


def _composite_candidates(candidates: list[Candidate], result: CanonicalizationResult,
                          graph: KnowledgeGraph, config: EngineConfig, run_id: str,
                          weights: dict[str, float], as_of: _dt.date) -> list[Candidate]:
    """Second-pass seeding from the consumption side (section 6.2).

    Where one business unit runs reports across several communities, the engine
    records a composite candidate that depends on the underlying ones.
    """
    base = [c for c in candidates if c.origin in ("community", "grain_child")]
    by_unit: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in base:
        total_users = sum(c.users for c in candidate.consumers) or 1
        leaders = sorted(candidate.consumers, key=lambda c: -c.users)[:2]
        for consumer in leaders:
            # Only a business unit that genuinely leads consumption of a candidate
            # can seed a composite; otherwise every unit composites everything.
            if (consumer.users >= config.cluster.composite_min_users
                    and consumer.users / total_users >= 0.20):
                by_unit[consumer.business_unit].append(candidate)

    out = []
    for unit, members in sorted(by_unit.items()):
        unique = {c.candidate_id: c for c in members}
        if len(unique) < max(2, config.cluster.composite_min_candidates):
            continue
        ranked = sorted(unique.values(), key=lambda c: -getattr(c, "_usage_weight", 0.0))[:4]
        # A composite carries the metrics that unit actually reads most, not every
        # metric of every underlying candidate.
        pool = sorted({m for c in ranked for m in c.metric_ids},
                      key=lambda m: -result.metrics[m].usage_weight)
        metric_ids = sorted(pool[:12])
        if not metric_ids:
            continue
        candidate = _build_candidate(
            metric_ids, result, graph, None, config, run_id, weights, as_of,
            origin="composite", community=f"COMP-{unit}",
            grain=Counter(c.grain for c in ranked).most_common(1)[0][0],
            name_hint=f"{unit} view",
        )
        candidate.tier = "Consumer-aligned"
        candidate.tier_confidence = 0.9
        candidate.depends_on = [c.candidate_id for c in ranked]
        candidate.consumers = [c for c in candidate.consumers if c.business_unit == unit] \
            or candidate.consumers
        out.append(candidate)
    return out
