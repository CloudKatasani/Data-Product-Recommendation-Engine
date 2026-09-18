"""Fingerprinting (specification section 5.1, steps 2 and 3).

    fingerprint = SHA-256(aggregation + sorted operand columns + arithmetic shape)

Filters are fingerprinted separately as ``filter_fp``. Query items are resolved
to catalog columns first, so two reports that spell the same column differently
still fingerprint alike.

Two rules here answer review finding R-20, because they decide whether a steward
signs off the Phase 1 sample or rejects the method (section 14 falsifier):

* A reference in the expression is mapped to its resolved catalog column by
  ``(table token, column token)``, not by the column leaf alone. Otherwise
  ``[bill].[amount] - [pay].[amount]`` collapses to one column subtracted from
  itself and a net position fingerprints the same as its negative.
* An opaque calculation is keyed on its own normalised expression text plus its
  operand columns, never on its label. Two byte-identical prompts are the same
  calculation; two different prompts under one label are not, and section 5.2
  forbids merging on a label.
"""
from __future__ import annotations

import re

from ..canonicalize.expr import ParsedExpression, parse_expression, shape_of
from ..models import KnowledgeGraph, KpiNode
from ..util.text import normalize_identifier, sha256_hex

OPAQUE_PREFIX = "opaque:"
_WHITESPACE = re.compile(r"\s+")


def _split_ref(raw: str) -> tuple[str, str]:
    """``(table token, column token)`` of a raw expression reference, normalised."""
    parts = [p for p in raw.split(".") if p]
    column = normalize_identifier(parts[-1]) if parts else ""
    table = normalize_identifier(parts[-2]) if len(parts) > 1 else ""
    return table, column


def _ref_name_map(kpi: KpiNode, parsed: ParsedExpression,
                  graph: KnowledgeGraph | None = None) -> dict[str, str]:
    """Map each raw expression reference to the catalog column it resolved to.

    Resolution order: exact ``(table, column)`` against the resolved columns; the
    lineage row that resolved the same raw reference (its table is the physical
    one even when the expression names a query subject); then the leaf alone,
    but only when exactly one resolved column carries that leaf. An ambiguous
    leaf keeps its own table token so two columns can never collide (R-20c).
    """
    resolved = list(kpi.operand_columns) + list(kpi.filter_columns)
    by_pair: dict[tuple[str, str], str] = {}
    by_leaf: dict[str, list[str]] = {}
    for fqn in resolved:
        table, column = _split_ref(fqn)
        by_pair.setdefault((table, column), fqn)
        leaves = by_leaf.setdefault(column, [])
        if fqn not in leaves:
            leaves.append(fqn)
    if graph is not None:
        # The lineage row's physical table stands in for a query-subject alias.
        for edge in graph.edges_kpi_column:
            if edge.kpi_id == kpi.kpi_id and edge.raw_reference:
                by_pair.setdefault(_split_ref(edge.raw_reference), edge.column_fqn)

    mapping: dict[str, str] = {}
    for raw in parsed.operand_refs + parsed.filter_refs:
        table, column = _split_ref(raw)
        hit = by_pair.get((table, column))
        if hit is None:
            leaves = by_leaf.get(column, [])
            if len(leaves) == 1:
                # Exactly one resolved column carries this leaf, so the table
                # token was an alias (a query subject, a model table) and the
                # reference is unambiguous.
                hit = leaves[0]
        if hit is None:
            # Unresolved or ambiguous: keep the raw table.column so two different
            # raw references never map onto one name.
            hit = f"{table}.{column}" if table else column
        mapping[raw] = hit
    return mapping


def normalized_expression_text(expression: str) -> str:
    """Whitespace-collapsed, case-folded expression text.

    Used to key opaque calculations and to recognise a conflict whose two sides
    are the same text resolved differently (a lineage artefact, R-18).
    """
    return _WHITESPACE.sub(" ", (expression or "").strip()).lower()


def fingerprint_details(kpi: KpiNode,
                        graph: KnowledgeGraph | None = None) -> tuple[str, str, str, str, str]:
    """Return ``(fingerprint, filter_fp, shape, null_rule, raw_shape)`` for one KPI node.

    ``raw_shape`` names references by their leaf as written, before catalog
    resolution; two KPIs with equal raw shapes but different fingerprints differ
    only in how their references resolved (R-18).
    """
    parsed = parse_expression(kpi.expression, "dax" if kpi.tool == "powerbi" else "cognos",
                              aggregation_hint=kpi.aggregation)
    if parsed.status != "PARSED":
        # An opaque calculation is its own metric: keyed on what was written,
        # never on its label (section 5.2), and never merged with a parsed one.
        opaque_key = (f"{OPAQUE_PREFIX}{normalized_expression_text(kpi.expression)}|"
                      f"{'|'.join(sorted(kpi.operand_columns))}")
        return sha256_hex(opaque_key), sha256_hex(_filter_key(kpi, parsed)), "", "", ""

    names = _ref_name_map(kpi, parsed, graph)
    shape = shape_of(parsed.ast, names)
    aggregation = (parsed.aggregation or kpi.aggregation or "").upper()
    operands = sorted(set(kpi.operand_columns)) or sorted(set(parsed.operand_refs))
    payload = "|".join([aggregation, ";".join(operands), shape])
    filter_fp = sha256_hex(_filter_key(kpi, parsed, names))
    raw_shape = "|".join([aggregation, parsed.shape])
    return sha256_hex(payload), filter_fp, shape, parsed.null_rule, raw_shape


def fingerprint_kpi(kpi: KpiNode, graph: KnowledgeGraph | None = None) -> tuple[str, str, str]:
    """Return ``(fingerprint, filter_fp, shape)`` for one KPI node."""
    fingerprint, filter_fp, shape, _null_rule, _raw = fingerprint_details(kpi, graph)
    return fingerprint, filter_fp, shape


def _filter_key(kpi: KpiNode, parsed: ParsedExpression, names: dict[str, str] | None = None) -> str:
    """Filters fingerprint separately: the declared filter plus any CALCULATE args.

    The null rule joins the filter key rather than the fingerprint: ``DIVIDE(a,
    b, 0)`` and ``a / b`` are one metric whose null handling differs, which is a
    documented variant (section 5.4), not a second metric.
    """
    parts: list[str] = []
    if parsed.filter_shape:
        parts.append(parsed.filter_shape)
    declared = (kpi.filter_expression or "").strip()
    if declared:
        parsed_filter = parse_expression(declared,
                                         "dax" if kpi.tool == "powerbi" else "cognos")
        if parsed_filter.status == "PARSED":
            filter_names = dict(names or {})
            for raw in parsed_filter.operand_refs + parsed_filter.filter_refs:
                column = normalize_identifier(raw.rsplit(".", 1)[-1])
                for fqn in kpi.filter_columns + kpi.operand_columns:
                    if normalize_identifier(fqn.rsplit(".", 1)[-1]) == column:
                        filter_names[raw] = fqn
                        break
            parts.append(shape_of(parsed_filter.ast, filter_names))
        else:
            parts.append(f"unparsed:{normalize_identifier(declared)}")
    if kpi.time_modifier:
        parts.append(f"time:{kpi.time_modifier}")
    if parsed.null_rule:
        parts.append(f"null:{parsed.null_rule}")
    return "&".join(sorted(p for p in parts if p)) or "none"


def apply_fingerprints(graph: KnowledgeGraph) -> KnowledgeGraph:
    """Stamp every KPI node in the graph with its fingerprints."""
    for kpi in graph.kpis.values():
        fingerprint, filter_fp, shape, null_rule, raw_shape = fingerprint_details(kpi, graph)
        kpi.fingerprint = fingerprint
        kpi.filter_fp = filter_fp
        kpi.expression_ast = {**(kpi.expression_ast or {}), "shape": shape,
                              "null_rule": null_rule, "raw_shape": raw_shape}
    return graph
