"""The glossary delta: what this run proposes back to the term of record (R-34).

A governance office expects the engine's main output - a hundred-odd canonical
metric definitions - to arrive as a glossary change request, not only as a data
product asset payload. Each run therefore emits one delta with:

* proposed terms: one per canonical metric that matches no glossary term, with
  the drafted definition, grain, aggregation, synonyms (the report labels) and
  the proposed steward - all AI_DRAFT until a steward accepts;
* definition updates: a canonical metric whose label matches an existing term
  but whose observed calculation reads differently from the term's definition;
* draft and retired terms in use by resolved columns;
* steward disagreements between the column extract and the glossary;
* terms without a steward that a candidate depends on.

Nothing here writes to the catalog: the delta is a proposal file a steward
imports (specification section 13.1, "Collibra remains the record").
"""
from __future__ import annotations

from pathlib import Path

from ..models import Candidate, KnowledgeGraph
from ..util.text import token_key
from ..util.yamlio import write_yaml
from .terms import term_links


def glossary_delta(graph: KnowledgeGraph, canonical, candidates: list[Candidate] | None = None,
                   run_id: str = "") -> dict:
    links = term_links(graph)
    by_key = {key: term for key, term in graph.glossary.items()}
    candidates = candidates or []
    metric_to_candidates: dict[str, list[str]] = {}
    for candidate in candidates:
        for metric_id in candidate.metric_ids:
            metric_to_candidates.setdefault(metric_id, []).append(candidate.candidate_id)

    proposed: list[dict] = []
    updates: list[dict] = []
    for metric_id in sorted(canonical.metrics):
        metric = canonical.metrics[metric_id]
        label = metric.labels[0] if metric.labels else metric.canonical_name
        keys = {token_key(label), token_key(metric.canonical_name)}
        existing = next((by_key[k] for k in keys if k in by_key), None)
        entry = {
            "metric_id": metric_id,
            "term": label,
            "canonical_name": metric.canonical_name,
            "definition": metric.definition,
            "definition_status": "AI_DRAFT",
            "grain": metric.grain,
            "aggregation": metric.aggregation,
            "synonyms": [l for l in metric.labels[1:6]],
            "proposed_steward": metric.steward_id,
            "steward_source": metric.steward_source,
            "domain": metric.domain,
            "source_columns": list(metric.operand_columns),
            "in_candidates": sorted(metric_to_candidates.get(metric_id, [])),
        }
        if existing is None:
            proposed.append(entry)
            continue
        # The term exists: propose an update only where the observed calculation
        # contradicts what the glossary says. Aggregation words are the cheapest
        # honest test: a term defined as an average that reports compute as a sum.
        contradiction = _contradiction(existing.definition, metric)
        if contradiction:
            updates.append({**entry, "term_id": existing.term_id,
                            "current_definition": existing.definition,
                            "current_status": existing.status,
                            "reason": contradiction})

    draft_in_use = sorted({
        (link.term_id, link.business_term, link.status)
        for link in links.values() if link.status_class == "draft"})
    retired_in_use = sorted({
        (link.term_id, link.business_term, link.status)
        for link in links.values() if link.status_class == "retired"})
    disagreements = sorted(
        {"column_fqn": link.column_fqn, "term": link.business_term,
         "glossary_steward": link.glossary_steward, "column_steward": link.column_steward}
        for link in links.values() if link.steward_disagreement)
    unassigned = sorted({
        (link.term_id, link.business_term) for link in links.values()
        if link.term_id and not link.glossary_steward})

    return {
        "run_id": run_id,
        "as_of": graph.as_of_date.isoformat(),
        "status": "PROPOSAL",
        "note": "Drafted by the engine; a steward imports what they accept. Nothing here "
                "has been written to the catalog.",
        "proposed_terms": proposed,
        "definition_updates": updates,
        "draft_terms_in_use": [{"term_id": t, "term": n, "status": s} for t, n, s in draft_in_use],
        "retired_terms_in_use": [{"term_id": t, "term": n, "status": s}
                                 for t, n, s in retired_in_use],
        "steward_disagreements": disagreements,
        "terms_without_steward": [{"term_id": t, "term": n} for t, n in unassigned],
        "summary": {
            "proposed_terms": len(proposed),
            "definition_updates": len(updates),
            "draft_terms_in_use": len(draft_in_use),
            "retired_terms_in_use": len(retired_in_use),
            "steward_disagreements": len(disagreements),
            "terms_without_steward": len(unassigned),
        },
    }


AGGREGATION_WORDS = {
    "SUM": ("total", "sum"), "AVG": ("average", "mean"), "COUNT": ("count", "number of"),
    "COUNT DISTINCT": ("distinct", "unique"), "MAX": ("maximum", "highest"),
    "MIN": ("minimum", "lowest"), "RATIO": ("ratio", "rate", "per", "share"),
}


def _contradiction(definition: str, metric) -> str:
    text = (definition or "").lower()
    if not text or not metric.aggregation:
        return ""
    stated = {agg for agg, words in AGGREGATION_WORDS.items() if any(w in text for w in words)}
    if not stated or metric.aggregation in stated:
        return ""
    if metric.aggregation == "RATIO" and stated & {"SUM", "COUNT"}:
        return ""          # a ratio of totals is still described in terms of totals
    return (f"the glossary describes the term as {', '.join(sorted(stated))}; the reports "
            f"compute it as {metric.aggregation}")


def write_glossary_delta(delta: dict, path: str | Path) -> Path:
    return write_yaml(path, delta, header="Glossary change proposal drafted by the engine; "
                                         "AI_DRAFT until a steward accepts each item.")
