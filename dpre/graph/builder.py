"""Builds the canonical knowledge graph from a validated extract bundle.

The graph is the only thing the recommender reads; nothing downstream touches
the raw extracts (specification section 4).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..canonicalize.expr import parse_expression
from ..config import ER_PROBABLE_THRESHOLD
from ..models import (
    ColumnNode, EdgeKpiColumn, ExtractBundle, KnowledgeGraph, KpiNode, QuarantineRow, TableNode,
)
from ..util.text import normalize_identifier, token_key
from .grain import (
    backbone_for, finest_grain, infer_backbone, infer_table_grain, is_measure_like,
)
from .resolver import ResolverIndex, build_index, extend_upstream, resolve_reference

_BRACKET_REF = re.compile(r"\[([^\[\]]+)\]")


@dataclass
class GraphStats:
    kpi_rows: int = 0
    resolved_rows: int = 0
    confident_rows: int = 0
    quarantined_rows: int = 0
    parsed_kpis: int = 0
    opaque_kpis: int = 0

    @property
    def resolution_rate(self) -> float:
        return self.confident_rows / self.kpi_rows if self.kpi_rows else 0.0

    @property
    def parse_rate(self) -> float:
        total = self.parsed_kpis + self.opaque_kpis
        return self.parsed_kpis / total if total else 0.0


def build_graph(bundle: ExtractBundle, backbone: list[str] | None = None) -> KnowledgeGraph:
    graph = KnowledgeGraph(as_of_date=bundle.as_of_date)
    declared = bool(backbone)
    backbone = backbone_for(backbone)

    for report in bundle.reports:
        graph.reports[report.report_id] = report
    for term in bundle.glossary:
        graph.glossary[token_key(term.term)] = term

    backbone = _build_physical_nodes(bundle, graph, backbone, declared=declared)
    index = build_index(bundle.columns, bundle.lineage)
    stats = _build_kpi_nodes(bundle, graph, index, backbone)

    graph.stats = {
        "kpi_rows": stats.kpi_rows,
        "resolved_rows": stats.resolved_rows,
        "confident_rows": stats.confident_rows,
        "quarantined_rows": stats.quarantined_rows,
        "resolution_rate": round(stats.resolution_rate, 4),
        "parsed_kpis": stats.parsed_kpis,
        "opaque_kpis": stats.opaque_kpis,
        "parse_rate": round(stats.parse_rate, 4),
        "reports": len(graph.reports),
        "kpis": len(graph.kpis),
        "columns": len(graph.columns),
        "tables": len(graph.tables),
        "edges": len(graph.edges_kpi_column),
        "backbone": backbone,
    }
    return graph


# --------------------------------------------------------------------------

def _build_physical_nodes(bundle: ExtractBundle, graph: KnowledgeGraph, backbone: list[str],
                          declared: bool = False) -> list[str]:
    by_table: dict[str, list[ColumnNode]] = {}
    table_meta: dict[str, dict] = {}
    for record in bundle.columns:
        node = ColumnNode(
            column_fqn=record.column_fqn, table_fqn=record.table_fqn, system=record.system,
            column_name=record.column, data_type=record.data_type, nullable=record.nullable,
            pk_flag=record.primary_key,
            fk_ref=record.foreign_key, sensitivity=record.classification or "Internal",
            pii_flag=record.pii_flag, business_term=record.business_term,
            definition=record.definition, steward_id=record.data_steward,
            owner_id=record.data_owner, domain=record.data_domain, sub_domain=record.sub_domain,
            certification_status=record.certification_status, quality_score=record.quality_score,
        )
        graph.columns[node.column_fqn] = node
        by_table.setdefault(node.table_fqn, []).append(node)
        meta = table_meta.setdefault(record.table_fqn, {
            "system": record.system, "table": record.table, "sor": record.system_of_record,
            "lifecycle": record.lifecycle_status, "sunset_date": record.sunset_date,
            "successor": record.successor_system, "domain": record.data_domain,
            "sub_domain": record.sub_domain, "owner": record.data_owner,
            "steward": record.data_steward, "row_count": record.row_count,
        })
        meta["sor"] = meta["sor"] or record.system_of_record
        if record.lifecycle_status == "sunset":
            meta["lifecycle"] = "sunset"
            meta["sunset_date"] = meta["sunset_date"] or record.sunset_date
            meta["successor"] = meta["successor"] or record.successor_system
        graph.systems.setdefault(record.system, {
            "system": record.system, "lifecycle": record.lifecycle_status,
            "sor": record.system_of_record,
        })
        if record.lifecycle_status == "sunset":
            graph.systems[record.system]["lifecycle"] = "sunset"
            graph.systems[record.system]["successor"] = record.successor_system

    for table_fqn, meta in table_meta.items():
        columns = by_table.get(table_fqn, [])
        node = TableNode(
            table_fqn=table_fqn, system=meta["system"], table_name=meta["table"],
            sor_flag=meta["sor"], lifecycle_status=meta["lifecycle"],
            sunset_date=meta["sunset_date"], successor_system=meta["successor"],
            domain=meta["domain"], sub_domain=meta["sub_domain"], owner_id=meta["owner"],
            steward_id=meta["steward"], row_count=meta["row_count"],
            column_count=len(columns),
            measure_count=sum(1 for c in columns if is_measure_like(c)),
        )
        graph.tables[table_fqn] = node

    # The conformed backbone comes from the catalog unless one was declared.
    if not declared:
        backbone = infer_backbone(list(graph.tables.values()), by_table, backbone)
    for table_fqn, node in graph.tables.items():
        node.inferred_grain, node.grain_source = infer_table_grain(
            node, by_table.get(table_fqn, []), backbone)
    return backbone


def _build_kpi_nodes(bundle: ExtractBundle, graph: KnowledgeGraph, index: ResolverIndex,
                     backbone: list[str]) -> GraphStats:
    stats = GraphStats()
    grouped: dict[str, list] = {}
    for kpi in bundle.kpis:
        grouped.setdefault(kpi.kpi_id, []).append(kpi)

    for kpi_id, rows in grouped.items():
        head = rows[0]
        report_ids: list[str] = []
        for row in rows:
            if row.report_id and row.report_id not in report_ids:
                report_ids.append(row.report_id)
        graph.kpi_reports[kpi_id] = report_ids

        parsed = parse_expression(head.calculation_expression, head.expression_language,
                                  aggregation_hint=head.aggregation_type)
        operand_columns: list[str] = []
        filter_columns: list[str] = []
        seen_edges: set[tuple[str, str]] = set()

        for row in rows:
            stats.kpi_rows += 1
            role = _role_for(row, parsed)
            outcome = resolve_reference(row, row.column, index, row.query_item)
            if isinstance(outcome, tuple):
                _, reason, detail = outcome
                stats.quarantined_rows += 1
                graph.quarantine.append(QuarantineRow(
                    kpi_id=kpi_id, raw_reference=row.raw_reference,
                    reason_code=reason, detail=detail, role=role))
                continue
            resolution = extend_upstream(outcome, index)
            stats.resolved_rows += 1
            if resolution.confidence >= ER_PROBABLE_THRESHOLD:
                stats.confident_rows += 1
            key = (resolution.column_fqn, role)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            graph.edges_kpi_column.append(EdgeKpiColumn(
                kpi_id=kpi_id, column_fqn=resolution.column_fqn, role=role,
                er_rule=resolution.er_rule, confidence=resolution.confidence,
                raw_reference=row.raw_reference,
            ))
            (operand_columns if role == "operand" else filter_columns).append(resolution.column_fqn)

        # A KPI inherits the grain of its fact table. Dimension columns that appear
        # as operands (a time basis, an entity key) do not set the grain unless the
        # KPI touches no fact table at all.
        operand_tables = [graph.tables[c.rsplit(".", 1)[0]] for c in operand_columns
                          if c.rsplit(".", 1)[0] in graph.tables]
        fact_like = [t for t in operand_tables if t.measure_count >= 2]
        grains = [t.inferred_grain for t in (fact_like or operand_tables)]
        node = KpiNode(
            kpi_id=kpi_id, report_id=head.report_id, label=head.kpi_label, tool=head.tool,
            aggregation=(parsed.aggregation or head.aggregation_type or "").upper(),
            expression=head.calculation_expression, expression_ast=parsed.ast,
            fingerprint="", filter_fp="", parse_status=parsed.status,
            filter_expression=head.filter_expression,
            semantic_container=head.semantic_container, measure_scope=head.measure_scope,
            grain=finest_grain(grains, backbone), operand_columns=sorted(set(operand_columns)),
            filter_columns=sorted(set(filter_columns)), time_modifier=parsed.time_modifier,
            parse_note=parsed.note,
        )
        if parsed.status == "PARSED":
            stats.parsed_kpis += 1
        else:
            stats.opaque_kpis += 1
        graph.kpis[kpi_id] = node
    return stats


def _role_for(row, parsed) -> str:
    """Operand or filter. An explicit role wins; otherwise infer from the filter text."""
    explicit = getattr(row, "_column_role", "")
    if explicit in ("operand", "filter"):
        return explicit
    column = normalize_identifier(row.column)
    filter_text = normalize_identifier(row.filter_expression or "")
    if column and filter_text and column in filter_text:
        expression = normalize_identifier(row.calculation_expression or "")
        if column not in expression:
            return "filter"
    if parsed and column and column in {normalize_identifier(c) for c in parsed.filter_columns}:
        return "filter"
    return "operand"
