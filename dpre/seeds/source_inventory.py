"""DPF Stage 3 seed: the source inventory and gap log."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, KnowledgeGraph
from ..util.yamlio import write_yaml

HEADER = (
    "Data Product Factory - Stage 3 Source Discovery (seed)\n"
    "Tables, systems and system-of-record designation come from the catalog. The gap\n"
    "log is what the engine could not resolve. Profiling statistics are for the team\n"
    "to add."
)


def source_inventory(candidate: Candidate, result: CanonicalizationResult,
                     graph: KnowledgeGraph) -> dict:
    metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
    kpi_ids = {k for m in metrics for k in m.kpi_ids}
    quarantined = [q for q in graph.quarantine if q.kpi_id in kpi_ids]
    reasons = Counter(q.reason_code for q in quarantined)
    return {
        "candidate_id": candidate.candidate_id,
        "proposed_name": candidate.proposed_name,
        "sources": [
            {
                "table": s.table_fqn,
                "system": s.system,
                "system_of_record": s.sor_flag,
                "lifecycle_status": s.lifecycle_status,
                "successor_system": s.successor_system or None,
                "share_of_metrics": s.share_of_metrics,
                "domain": s.domain,
                "row_count": graph.tables[s.table_fqn].row_count if s.table_fqn in graph.tables else 0,
                "inferred_grain": (graph.tables[s.table_fqn].inferred_grain
                                   if s.table_fqn in graph.tables else "unknown"),
                "profiling": "TO BE COMPLETED",
            }
            for s in candidate.sources
        ],
        "gap_log": {
            "unresolved_lineage_rows": len(quarantined),
            "by_reason": dict(reasons),
            "examples": [
                {"kpi_id": q.kpi_id, "reference": q.raw_reference,
                 "reason_code": q.reason_code, "detail": q.detail}
                for q in quarantined[:20]
            ],
            "attributes_without_definition": [
                a.column_fqn for a in candidate.attributes if not a.definition
            ][:40],
            "attributes_on_probable_lineage": [
                {"column": a.column_fqn, "confidence": a.confidence}
                for a in candidate.attributes if 0 < a.confidence < 0.80
            ][:20],
        },
    }


def write_source_inventory(candidate: Candidate, result: CanonicalizationResult,
                           graph: KnowledgeGraph, path: str | Path) -> Path:
    return write_yaml(path, source_inventory(candidate, result, graph), header=HEADER)
