"""Fingerprinting (specification section 5.1, steps 2 and 3).

    fingerprint = SHA-256(aggregation + sorted operand columns + arithmetic shape)

Filters are fingerprinted separately as ``filter_fp``. Query items are resolved
to catalog columns first, so two reports that spell the same column differently
still fingerprint alike.
"""
from __future__ import annotations

from ..canonicalize.expr import ParsedExpression, parse_expression, shape_of
from ..models import KnowledgeGraph, KpiNode
from ..util.text import normalize_identifier, sha256_hex

OPAQUE_PREFIX = "opaque:"


def _ref_name_map(kpi: KpiNode, parsed: ParsedExpression) -> dict[str, str]:
    """Map each raw expression reference to the catalog column it resolved to."""
    resolved = {}
    for fqn in list(kpi.operand_columns) + list(kpi.filter_columns):
        resolved[normalize_identifier(fqn.rsplit(".", 1)[-1])] = fqn
    mapping: dict[str, str] = {}
    unresolved = [r for r in parsed.operand_refs + parsed.filter_refs]
    for raw in unresolved:
        column = normalize_identifier(raw.rsplit(".", 1)[-1])
        if column in resolved:
            mapping[raw] = resolved[column]
    # Anything the expression names that did not resolve keeps its raw column
    # name, so two unresolved KPIs over the same raw reference still agree.
    for raw in unresolved:
        mapping.setdefault(raw, raw.rsplit(".", 1)[-1].lower())
    return mapping


def fingerprint_kpi(kpi: KpiNode, graph: KnowledgeGraph | None = None) -> tuple[str, str, str]:
    """Return ``(fingerprint, filter_fp, shape)`` for one KPI node."""
    parsed = parse_expression(kpi.expression, "dax" if kpi.tool == "powerbi" else "cognos",
                              aggregation_hint=kpi.aggregation)
    if parsed.status != "PARSED":
        # An opaque calculation is its own metric; it must never merge with a
        # parsed one on the strength of a label.
        opaque_key = f"{OPAQUE_PREFIX}{kpi.label.lower()}|{'|'.join(sorted(kpi.operand_columns))}"
        return sha256_hex(opaque_key), sha256_hex(_filter_key(kpi, parsed)), ""

    names = _ref_name_map(kpi, parsed)
    shape = shape_of(parsed.ast, names)
    aggregation = (parsed.aggregation or kpi.aggregation or "").upper()
    operands = sorted(set(kpi.operand_columns)) or sorted(set(parsed.operand_refs))
    payload = "|".join([aggregation, ";".join(operands), shape])
    filter_fp = sha256_hex(_filter_key(kpi, parsed, names))
    return sha256_hex(payload), filter_fp, shape


def _filter_key(kpi: KpiNode, parsed: ParsedExpression, names: dict[str, str] | None = None) -> str:
    """Filters fingerprint separately: the declared filter plus any CALCULATE args."""
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
    return "&".join(sorted(p for p in parts if p)) or "none"


def apply_fingerprints(graph: KnowledgeGraph) -> KnowledgeGraph:
    """Stamp every KPI node in the graph with its fingerprints."""
    for kpi in graph.kpis.values():
        fingerprint, filter_fp, shape = fingerprint_kpi(kpi, graph)
        kpi.fingerprint = fingerprint
        kpi.filter_fp = filter_fp
        kpi.expression_ast = {**(kpi.expression_ast or {}), "shape": shape}
    return graph
