"""The conflict register (specification sections 5.3 and 5.4).

Every nominal conflict produces a row with the competing expressions, the
reports using each, their usage weight, and the difference in operand columns or
filters. It is the first artifact a domain steward sees, because unresolved
conflicts are the reason two teams will never trust the same number.
"""
from __future__ import annotations

import re

from ..models import CanonicalMetric, MetricConflict
from ..util.ids import stable_id
from ..util.text import tokenize
from .expr import NULL_FUNCTIONS

TIME_TOKENS = {"calendar", "fiscal", "period", "month", "quarter", "year", "date", "week", "day"}
DENOMINATOR_PATTERNS = ("denominator", "per", "rate", "ratio", "index")

# Section 5.4: each pattern maps to a Stage 6 semantic-model decision.
PATTERN_DECISIONS = {
    "THRESHOLD": ("Hard-coded threshold differs between reports",
                  "Parameterized threshold in the semantic model"),
    "EXCLUSION": ("One definition excludes rows the other keeps",
                  "Filter dimension so both readings exist under one metric"),
    "TIME_BASIS": ("Different time basis (calendar versus fiscal)",
                   "Explicit time grain on the metric"),
    "DENOMINATOR": ("Same label, different denominator",
                    "Explicit grain declaration and one certified denominator"),
    "NULL_HANDLING": ("Null handling differs and silently changes the result",
                      "Documented null rule on the metric"),
    "AGGREGATION": ("Same measure, different aggregation",
                    "Separate certified metrics, cross-linked"),
    "OPERANDS": ("Different source columns behind the same label",
                 "Steward picks the authoritative definition or renames one"),
}


_COL = re.compile(r"col\(([^()]+)\)")


def leaf_operands(metric: CanonicalMetric) -> set[str]:
    """Operand column leaves as the reports wrote them (R-18).

    Read from the raw arithmetic shape when the canonicalizer recorded one, so a
    reference the catalog could not resolve still counts as an operand when the
    pattern is classified; a quarantined denominator is still a denominator.
    """
    leaves: set[str] = set()
    for shape in getattr(metric, "_raw_shapes", []) or []:
        leaves.update(name.rsplit(".", 1)[-1] for name in _COL.findall(shape))
    if leaves:
        return leaves
    return {c.rsplit(".", 1)[-1].lower() for c in metric.operand_columns}


def classify_pattern(metric_a: CanonicalMetric, metric_b: CanonicalMetric,
                     shape_a: str = "", shape_b: str = "") -> str:
    operands_a, operands_b = leaf_operands(metric_a), leaf_operands(metric_b)
    filters_a, filters_b = set(metric_a.filter_columns), set(metric_b.filter_columns)

    if operands_a == operands_b and metric_a.aggregation != metric_b.aggregation:
        return "AGGREGATION"
    if operands_a == operands_b and shape_a and shape_b and shape_a != shape_b:
        if _literals(shape_a) != _literals(shape_b):
            return "THRESHOLD"
        if any(fn in (shape_a + shape_b).lower() for fn in NULL_FUNCTIONS):
            return "NULL_HANDLING"
    # A null rule that differs with everything else equal is a documented null
    # rule, not a threshold (R-20a).
    if operands_a == operands_b and shape_a == shape_b and \
            set(getattr(metric_a, "_null_rules", [])) != set(getattr(metric_b, "_null_rules", [])):
        return "NULL_HANDLING"
    difference = operands_a.symmetric_difference(operands_b)
    if difference and all(_is_time_column(c) for c in difference):
        return "TIME_BASIS"
    if metric_a.aggregation == "RATIO" or metric_b.aggregation == "RATIO":
        if len(operands_a) == len(operands_b) and len(difference) <= 2:
            return "DENOMINATOR"
    if filters_a != filters_b and operands_a == operands_b:
        return "EXCLUSION"
    if difference and _literals(shape_a) != _literals(shape_b):
        return "THRESHOLD"
    return "OPERANDS"


def _literals(shape: str) -> list[str]:
    out, i = [], 0
    while True:
        i = shape.find("lit(", i)
        if i == -1:
            return sorted(out)
        end = shape.find(")", i)
        out.append(shape[i + 4:end])
        i = end + 1


def _short_names(columns: set[str], other: set[str]) -> list[str]:
    """Name a column by its leaf, qualified by its table when that is ambiguous.

    Two reports reading ``billed_revenue`` from the reporting view and from the
    fact table are genuinely different sources, and the register has to say so.
    """
    other_leaves = {c.rsplit(".", 1)[-1] for c in other}
    out = []
    for column in columns:
        parts = column.split(".")
        leaf = parts[-1]
        out.append(f"{parts[-2]}.{leaf}" if leaf in other_leaves and len(parts) > 1 else leaf)
    return sorted(out)


def _is_time_column(column_fqn: str) -> bool:
    name = column_fqn.rsplit(".", 1)[-1]
    return bool(set(tokenize(name)) & TIME_TOKENS)


def describe_difference(metric_a: CanonicalMetric, metric_b: CanonicalMetric,
                        pattern: str, shape_a: str = "", shape_b: str = "") -> str:
    operands_a, operands_b = set(metric_a.operand_columns), set(metric_b.operand_columns)
    only_a = _short_names(operands_a - operands_b, operands_b - operands_a)
    only_b = _short_names(operands_b - operands_a, operands_a - operands_b)
    # Where the catalog could not resolve a reference, describe what the report
    # wrote rather than "none": the steward adjudicates the calculation, the
    # catalog admin fixes the lineage.
    leaves_a, leaves_b = leaf_operands(metric_a), leaf_operands(metric_b)
    if leaves_a != leaves_b and (not only_a or not only_b):
        only_a = only_a or sorted(leaves_a - leaves_b)
        only_b = only_b or sorted(leaves_b - leaves_a)
    if pattern == "THRESHOLD":
        lit_a, lit_b = _literals(shape_a), _literals(shape_b)
        return (f"Same columns, different threshold: {', '.join(lit_a) or 'none'} "
                f"versus {', '.join(lit_b) or 'none'}")
    if pattern == "AGGREGATION":
        return f"{metric_a.aggregation} versus {metric_b.aggregation} over the same column"
    if pattern == "TIME_BASIS":
        return (f"Time basis differs: {', '.join(only_a) or 'none'} versus "
                f"{', '.join(only_b) or 'none'}")
    if pattern == "DENOMINATOR":
        return (f"Denominator differs: {', '.join(only_a) or 'none'} versus "
                f"{', '.join(only_b) or 'none'}")
    if pattern == "EXCLUSION":
        fa = sorted(c.rsplit(".", 1)[-1] for c in set(metric_a.filter_columns))
        fb = sorted(c.rsplit(".", 1)[-1] for c in set(metric_b.filter_columns))
        return f"Filters differ: {', '.join(fa) or 'none'} versus {', '.join(fb) or 'none'}"
    if pattern == "NULL_HANDLING":
        rules_a = ", ".join(getattr(metric_a, "_null_rules", [])) or "none"
        rules_b = ", ".join(getattr(metric_b, "_null_rules", [])) or "none"
        return f"Null handling differs: {rules_a} versus {rules_b}"
    if only_a or only_b:
        return (f"Different source columns: {', '.join(only_a) or 'none'} versus "
                f"{', '.join(only_b) or 'none'}")
    return "Different arithmetic over the same columns"


def build_conflict(metric_a: CanonicalMetric, metric_b: CanonicalMetric, similarity: float,
                   shape_a: str = "", shape_b: str = "") -> MetricConflict:
    pattern = classify_pattern(metric_a, metric_b, shape_a, shape_b)
    summary, decision = PATTERN_DECISIONS[pattern]
    label = metric_a.labels[0] if metric_a.labels else metric_a.canonical_name
    return MetricConflict(
        conflict_id=stable_id("CONF", metric_a.metric_id, metric_b.metric_id),
        label=label,
        metric_id_a=metric_a.metric_id,
        metric_id_b=metric_b.metric_id,
        usage_weight_a=round(metric_a.usage_weight, 2),
        usage_weight_b=round(metric_b.usage_weight, 2),
        difference_summary=describe_difference(metric_a, metric_b, pattern, shape_a, shape_b),
        pattern=pattern,
        reports_a=list(metric_a.report_ids[:10]),
        reports_b=list(metric_b.report_ids[:10]),
        expression_a=_sample_expression(metric_a),
        expression_b=_sample_expression(metric_b),
        steward_id=metric_a.steward_id or metric_b.steward_id,
        similarity=round(similarity, 3),
        semantic_model_decision=decision,
        resolution_status="OPEN",
    )


def _sample_expression(metric: CanonicalMetric) -> str:
    return getattr(metric, "_sample_expression", "") or ""
