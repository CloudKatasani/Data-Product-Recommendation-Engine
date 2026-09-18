"""Metric-table bipartite graph and its metric-metric projection (section 6.1)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..canonicalize.grouping import CanonicalizationResult
from ..config import ER_PROBABLE_THRESHOLD, ClusterConfig
from ..models import KnowledgeGraph


@dataclass
class Bipartite:
    tables_by_metric: dict[str, set[str]] = field(default_factory=dict)
    metrics_by_table: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    weight_by_metric: dict[str, float] = field(default_factory=dict)
    unresolved_metrics: list[str] = field(default_factory=list)


def build_bipartite(result: CanonicalizationResult, graph: KnowledgeGraph) -> Bipartite:
    """Edge metric to table for every operand column resolved at >= 0.80 confidence.

    Edge weight is the usage-weighted report count of the metric.
    """
    confident: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges_kpi_column:
        if edge.role == "operand" and edge.confidence >= ER_PROBABLE_THRESHOLD:
            confident[edge.kpi_id].add(edge.column_fqn)

    bipartite = Bipartite()
    for metric in result.metrics.values():
        columns: set[str] = set()
        for kpi_id in metric.kpi_ids:
            columns |= confident.get(kpi_id, set())
        tables = {c.rsplit(".", 1)[0] for c in columns}
        if not tables:
            # A metric with no resolved lineage is a data gap, not a candidate.
            bipartite.unresolved_metrics.append(metric.metric_id)
            continue
        bipartite.tables_by_metric[metric.metric_id] = tables
        bipartite.weight_by_metric[metric.metric_id] = max(metric.usage_weight, 0.0)
        for table in tables:
            bipartite.metrics_by_table[table].add(metric.metric_id)
    return bipartite


def project(bipartite: Bipartite, result: CanonicalizationResult, config: ClusterConfig,
            excluded_tables: set[str] | None = None) -> list[tuple[str, str, float]]:
    """Metric-metric similarity: Jaccard of source table sets plus a domain bonus."""
    excluded = excluded_tables or set()
    tables_by_metric = {
        metric_id: (tables - excluded)
        for metric_id, tables in bipartite.tables_by_metric.items()
    }
    candidates: dict[tuple[str, str], None] = {}
    for table, metric_ids in bipartite.metrics_by_table.items():
        if table in excluded:
            continue
        ordered = sorted(metric_ids)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                candidates[(a, b)] = None

    edges: list[tuple[str, str, float]] = []
    for a, b in candidates:
        tables_a, tables_b = tables_by_metric.get(a, set()), tables_by_metric.get(b, set())
        if not tables_a or not tables_b:
            continue
        union = tables_a | tables_b
        jaccard = len(tables_a & tables_b) / float(len(union)) if union else 0.0
        metric_a, metric_b = result.metrics[a], result.metrics[b]
        if metric_a.domain and metric_a.domain == metric_b.domain:
            jaccard += config.same_domain_bonus
        if jaccard >= config.min_similarity:
            edges.append((a, b, round(min(jaccard, 1.2), 4)))
    edges.sort(key=lambda e: (-e[2], e[0], e[1]))
    return edges


def conformed_tables(bipartite: Bipartite, graph: KnowledgeGraph) -> set[str]:
    """Tables that behave like a conformed dimension: a primary key, no measures."""
    out = set()
    pk_tables = {c.table_fqn for c in graph.columns.values() if c.pk_flag}
    for table in bipartite.metrics_by_table:
        node = graph.tables.get(table)
        if node is None:
            continue
        if table in pk_tables and node.measure_count < 2:
            out.add(table)
    return out


def hub_tables(bipartite: Bipartite, communities: dict[str, str], graph: KnowledgeGraph,
               min_communities: int = 3) -> list[str]:
    """Tables referenced by metrics in several communities, with a backbone key.

    Reach is measured against communities formed *without* the conformed
    dimensions, otherwise a hub glues its own metrics together and then looks
    like it reaches only one community. Hubs become Entity Master candidates and
    leave the projection, so one shared dimension cannot merge the whole estate
    into a single cluster (section 15.1).
    """
    out = []
    for table, metric_ids in bipartite.metrics_by_table.items():
        node = graph.tables.get(table)
        if node is None or table not in conformed_tables(bipartite, graph):
            continue
        reached = {communities.get(m) for m in metric_ids if communities.get(m)}
        if len(reached) >= min_communities:
            out.append(table)
    return sorted(out)
