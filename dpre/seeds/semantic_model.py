"""DPF Stage 6 seed: the semantic-model skeleton.

Conflict patterns become explicit modelling decisions here: a threshold becomes a
parameter, an exclusion becomes a filter dimension, a time basis becomes a
declared time grain (specification sections 5.4 and 12.3).
"""
from __future__ import annotations

from pathlib import Path

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, KnowledgeGraph
from ..util.text import snake_case
from ..util.yamlio import write_yaml

HEADER = (
    "Data Product Factory - Stage 6 Semantic Model (skeleton)\n"
    "Entities, dimensions and metrics are seeded from the knowledge graph. Joins are\n"
    "not validated and metrics are not certified: both are for the modelling team.\n"
    "Every name marked AI_DRAFT must be accepted by a steward first."
)


def semantic_model(candidate: Candidate, result: CanonicalizationResult,
                   graph: KnowledgeGraph) -> dict:
    metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
    conflicts = [c for c in result.conflicts if c.conflict_id in candidate.conflicts]
    questions = {
        draft.business_unit: draft.questions for draft in candidate.decisions_drafted
    }

    dimensions = _dimensions(candidate, metrics, graph)
    return {
        "semantic_model": {
            "name": snake_case(candidate.proposed_name),
            "name_status": candidate.name_status,
            "description": candidate.purpose,
            "grain": candidate.grain,
            "domain": candidate.domain,
            "candidate_id": candidate.candidate_id,
            "certified": False,
        },
        "entities": [
            {
                "name": snake_case(graph.tables[s.table_fqn].inferred_grain)
                if s.table_fqn in graph.tables else snake_case(s.table_fqn.split(".")[-1]),
                "table": s.table_fqn,
                "system": s.system,
                "system_of_record": s.sor_flag,
                "primary_key": _primary_key(s.table_fqn, graph),
                "join_validated": False,
            }
            for s in candidate.sources
        ],
        "dimensions": dimensions,
        "metrics": [
            {
                "name": m.canonical_name,
                "name_status": m.name_status,
                "description": m.definition,
                "aggregation": m.aggregation,
                "expression": getattr(m, "_sample_expression", ""),
                "source_columns": m.operand_columns,
                "grain": m.grain,
                "steward": m.steward_id or "UNASSIGNED",
                "certified": False,
                "answers_questions": _questions_for(m, questions),
                "variants": m.variant_count,
                "time_modifiers": m.time_modifiers,
                "tools_seen_in": m.tools,
            }
            for m in sorted(metrics, key=lambda m: -m.usage_weight)
        ],
        "parameters": [
            {
                "name": f"{snake_case(c.label)}_threshold",
                "from_conflict": c.conflict_id,
                "reason": c.difference_summary,
                "decision": c.semantic_model_decision,
                "default": "TO BE SET BY STEWARD",
            }
            for c in conflicts if c.pattern == "THRESHOLD"
        ],
        "open_decisions": [
            {
                "conflict_id": c.conflict_id,
                "label": c.label,
                "pattern": c.pattern,
                "difference": c.difference_summary,
                "semantic_model_decision": c.semantic_model_decision,
                "usage_at_stake": round(c.usage_weight_a + c.usage_weight_b, 2),
                "steward": c.steward_id or "UNASSIGNED",
            }
            for c in conflicts
        ],
    }


def _primary_key(table_fqn: str, graph: KnowledgeGraph) -> list[str]:
    return sorted(c.column_name for c in graph.columns.values()
                  if c.table_fqn == table_fqn and c.pk_flag)


def _dimensions(candidate: Candidate, metrics, graph: KnowledgeGraph) -> list[dict]:
    """Filter variants become named dimensions rather than hidden report filters."""
    out: dict[str, dict] = {}
    for attribute in candidate.attributes:
        if attribute.role != "filter":
            continue
        out[attribute.column_fqn] = {
            "name": snake_case(attribute.name),
            "column": attribute.column_fqn,
            "business_term": attribute.business_term or "MISSING - catalog gap",
            "from": "filter variant observed in the reports",
            "sensitivity": attribute.sensitivity,
        }
    for metric in metrics:
        for column in metric.filter_columns:
            if column in out:
                continue
            node = graph.columns.get(column)
            if node is None:
                continue
            out[column] = {
                "name": snake_case(node.column_name),
                "column": column,
                "business_term": node.business_term or "MISSING - catalog gap",
                "from": f"filter on metric {metric.canonical_name}",
                "sensitivity": node.sensitivity,
            }
    return sorted(out.values(), key=lambda d: d["name"])


def _questions_for(metric, questions: dict[str, list[str]]) -> list[str]:
    label = (metric.labels[0] if metric.labels else metric.canonical_name).lower()
    hits = []
    for unit, unit_questions in questions.items():
        for question in unit_questions:
            if label.split()[0] in question.lower():
                hits.append(f"{unit}: {question}")
    return hits[:4]


def write_semantic_model(candidate: Candidate, result: CanonicalizationResult,
                         graph: KnowledgeGraph, path: str | Path) -> Path:
    return write_yaml(path, semantic_model(candidate, result, graph), header=HEADER)
