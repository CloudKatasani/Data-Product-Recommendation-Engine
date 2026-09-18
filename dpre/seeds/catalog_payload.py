"""Catalog registration payload (specification section 9.3).

For each Accepted candidate the engine produces a JSON payload conforming to the
catalog's data product asset type. A steward loads it through the catalog's
import. The engine never writes to the catalog directly: the catalog remains the
system of record and the engine produces proposals for it.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, KnowledgeGraph
from ..util.jsonio import write_json


def catalog_payload(candidate: Candidate, result: CanonicalizationResult,
                    graph: KnowledgeGraph, catalog: str = "collibra") -> dict:
    metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
    ai_drafted = [m.canonical_name for m in metrics if m.name_status == "AI_DRAFT"]
    payload = {
        "asset_type": "Data Product",
        "catalog": catalog,
        "import_mode": "proposal",
        "written_by": "Data Product Recommendation Engine",
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "name": candidate.proposed_name,
        "name_status": candidate.name_status,
        "status": "Proposed",
        "domain": candidate.domain,
        "sub_domain": candidate.sub_domain,
        "description": candidate.purpose,
        "owner": candidate.owner_candidate or "UNASSIGNED",
        "steward": candidate.steward_candidate or "UNASSIGNED",
        "attributes": {
            "archetype": candidate.archetype,
            "tier": candidate.tier,
            "grain": candidate.grain,
            "candidate_id": candidate.candidate_id,
            "run_id": candidate.run_id,
            "as_of_date": candidate.as_of_date,
            "composite_score": candidate.score.composite if candidate.score else 0,
            "weight_version": candidate.score.weight_version if candidate.score else "",
        },
        "relations": {
            "linked_business_terms": sorted({a.business_term for a in candidate.attributes
                                             if a.business_term}),
            "linked_tables": [s.table_fqn for s in candidate.sources],
            "linked_columns": [a.column_fqn for a in candidate.attributes],
            "linked_reports": [r.report_id for r in candidate.reports if r.coverage >= 1.0],
            "depends_on_candidates": candidate.depends_on,
        },
        "metrics": [
            {"name": m.canonical_name, "definition": m.definition,
             "name_status": m.name_status, "steward": m.steward_id or "UNASSIGNED"}
            for m in metrics
        ],
        "import_blocked_by": (
            [f"{len(ai_drafted)} AI-drafted names must be accepted by a steward first"]
            if ai_drafted else []
        ),
    }
    if candidate.status != "Accepted":
        payload["import_blocked_by"] = payload["import_blocked_by"] + [
            f"candidate status is {candidate.status}; only Accepted candidates are registered"
        ]
    return payload


def write_catalog_payload(candidate: Candidate, result: CanonicalizationResult,
                          graph: KnowledgeGraph, path: str | Path,
                          catalog: str = "collibra") -> Path:
    return write_json(path, catalog_payload(candidate, result, graph, catalog))
