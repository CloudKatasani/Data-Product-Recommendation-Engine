"""Builds the canonical knowledge graph from a validated extract bundle.

The graph is the only thing the recommender reads; nothing downstream touches
the raw extracts (specification section 4).

Three decisions here answer review findings a governance office would raise on
the first run:

* A table's owner and steward are the majority over its columns, not whichever
  column happened to be first in the extract, and a split vote is flagged
  (R-44). Ownership and stewardship stay separate roles; a Data Domain node
  carries the domain owner as the escalation point.
* The business glossary is the term of record (R-34): a column's business term
  is resolved to a glossary term at build time, and the glossary's definition,
  steward and status are what downstream reads.
* The backbone is recorded as declared or inferred (R-47), non-business load
  steps never lend their grain to a KPI, and a reviewer-confirmed mapping is
  honoured before ER-1 (R-46).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..canonicalize.expr import parse_expression
from ..config import ER_PROBABLE_THRESHOLD
from ..models import (
    ColumnNode, EdgeKpiColumn, ExtractBundle, KnowledgeGraph, KpiNode, QuarantineRow, TableNode,
)
from ..glossary.terms import glossary_summary, link_terms
from ..util.text import normalize_identifier, token_key
from .domains import build_domain_nodes
from .grain import (
    backbone_for, backbone_source, finest_grain, infer_backbone, infer_table_grain,
    is_measure_like, is_non_business_grain,
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


def build_graph(bundle: ExtractBundle, backbone: list[str] | None = None,
                manual_mappings: dict[str, str] | None = None) -> KnowledgeGraph:
    graph = KnowledgeGraph(as_of_date=bundle.as_of_date)
    declared = bool(backbone)
    backbone = backbone_for(backbone)

    duplicate_reports = 0
    for report in bundle.reports:
        # A duplicated report row is a data-quality fact the DQ scorecard reports;
        # here the first row stands and the count is kept so it is never silent.
        if report.report_id in graph.reports:
            duplicate_reports += 1
            continue
        graph.reports[report.report_id] = report
    for term in bundle.glossary:
        graph.glossary[token_key(term.term)] = term

    backbone, source = _build_physical_nodes(bundle, graph, backbone, declared=declared)
    link_terms(graph)
    setattr(graph, "_domains", build_domain_nodes(graph))
    index = build_index(bundle.columns, bundle.lineage, manual_mappings)
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
        "backbone_source": source,
        "grain_sources": _count(t.grain_source for t in graph.tables.values()),
        "non_business_tables": sum(1 for t in graph.tables.values()
                                   if is_non_business_grain(t.inferred_grain)),
        "tables_with_steward_disagreement": sum(
            1 for t in graph.tables.values() if getattr(t, "_steward_disagreement", False)),
        "duplicate_report_rows": duplicate_reports,
        "manual_mappings_applied": sum(1 for e in graph.edges_kpi_column if e.er_rule == "ER-0"),
        "domains": len(getattr(graph, "_domains", {})),
        "glossary": glossary_summary(graph),
    }
    return graph


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------

def _build_physical_nodes(bundle: ExtractBundle, graph: KnowledgeGraph, backbone: list[str],
                          declared: bool = False) -> tuple[list[str], str]:
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
            "sub_domain": record.sub_domain, "row_count": record.row_count,
            "owners": {}, "stewards": {},
        })
        # Owner and steward are decided by majority over the table's columns, so
        # the answer does not depend on extract order (R-44).
        if record.data_owner:
            meta["owners"][record.data_owner] = meta["owners"].get(record.data_owner, 0) + 1
        if record.data_steward:
            meta["stewards"][record.data_steward] = meta["stewards"].get(record.data_steward, 0) + 1
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
            domain=meta["domain"], sub_domain=meta["sub_domain"],
            owner_id=_majority(meta["owners"]), steward_id=_majority(meta["stewards"]),
            row_count=meta["row_count"],
            column_count=len(columns),
            measure_count=sum(1 for c in columns if is_measure_like(c)),
        )
        setattr(node, "_owner_disagreement", len(meta["owners"]) > 1)
        setattr(node, "_steward_disagreement", len(meta["stewards"]) > 1)
        setattr(node, "_steward_votes", dict(meta["stewards"]))
        graph.tables[table_fqn] = node

    # The conformed backbone comes from the catalog unless one was declared, and
    # the manifest says which it was (R-47).
    source = backbone_source(list(graph.tables.values()), by_table, declared)
    if not declared:
        backbone = infer_backbone(list(graph.tables.values()), by_table, backbone)
    for table_fqn, node in graph.tables.items():
        node.inferred_grain, node.grain_source = infer_table_grain(
            node, by_table.get(table_fqn, []), backbone)
    return backbone, source


def _majority(votes: dict[str, int]) -> str:
    if not votes:
        return ""
    return sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


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
                _, reason, detail, suggestion = outcome
                stats.quarantined_rows += 1
                quarantined = QuarantineRow(
                    kpi_id=kpi_id, raw_reference=row.raw_reference,
                    reason_code=reason, detail=detail, role=role)
                # The near-miss travels as data so the remediation plan can offer
                # "confirm this mapping" rather than a sentence (R-46).
                setattr(quarantined, "_suggestion", suggestion or {})
                setattr(quarantined, "_report_id", row.report_id)
                graph.quarantine.append(quarantined)
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
                          if c.rsplit(".", 1)[0] in graph.tables
                          and not is_non_business_grain(
                              graph.tables[c.rsplit(".", 1)[0]].inferred_grain)]
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
