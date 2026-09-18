"""DPF Stage 2 seed: the charter draft (specification section 9.2)."""
from __future__ import annotations

from pathlib import Path

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, KnowledgeGraph
from ..util.yamlio import write_yaml

HEADER = (
    "Data Product Factory - Stage 2 Charter (draft)\n"
    "Seeded by the Data Product Recommendation Engine. Success measures and sign-off\n"
    "are for the data product owner to add. The archetype and tier are rule output and\n"
    "may be overridden; the override is logged and tunes the rules."
)


def charter_draft(candidate: Candidate, result: CanonicalizationResult,
                  graph: KnowledgeGraph) -> dict:
    metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
    score = candidate.score
    return {
        "charter": {
            "candidate_id": candidate.candidate_id,
            "proposed_name": candidate.proposed_name,
            "name_status": candidate.name_status,
            "purpose": candidate.purpose,
            "purpose_status": "AI_DRAFT",
            "archetype": candidate.archetype,
            "archetype_confidence": candidate.archetype_confidence,
            "archetype_alternative": candidate.archetype_runner_up or None,
            "tier": candidate.tier,
            "grain": candidate.grain,
            "domain": candidate.domain,
            "sub_domain": candidate.sub_domain,
            "owner_candidate": candidate.owner_candidate or "UNASSIGNED",
            "steward_candidate": candidate.steward_candidate or "UNASSIGNED",
            "status": candidate.status,
        },
        "scope": {
            "in_scope_metrics": [
                {"name": m.canonical_name, "definition": m.definition,
                 "name_status": m.name_status, "grain": m.grain,
                 "reports_using_it": len(m.report_ids)}
                for m in sorted(metrics, key=lambda m: -m.usage_weight)
            ],
            "out_of_scope": candidate.narrative.get("scope_out", []),
            "depends_on": candidate.depends_on,
            "child_candidates": candidate.child_candidate_ids,
        },
        "value_hypothesis": candidate.narrative.get("value_hypothesis", ""),
        "consumers": [
            {"business_unit": c.business_unit, "users": c.users,
             "reports": c.report_count, "cadence": c.cadence,
             "scheduled_share": c.scheduled_share}
            for c in candidate.consumers
        ],
        "scores": {
            "weight_version": score.weight_version if score else "",
            "demand": score.demand if score else 0,
            "consolidation": score.consolidation if score else 0,
            "feasibility": score.feasibility if score else 0,
            "risk": score.risk if score else 0,
            "composite": score.composite if score else 0,
            "gates": [
                {"gate": g.gate, "name": g.name, "passed": g.passed, "detail": g.detail}
                for g in (score.gates if score else [])
            ],
        },
        "open_questions": [f.finding for f in candidate.critique if f.severity in
                           ("blocker", "major")],
        "to_be_completed_by_owner": [
            "success measures with a baseline and a target",
            "sign-off by the domain data product council",
        ],
    }


def write_charter(candidate: Candidate, result: CanonicalizationResult, graph: KnowledgeGraph,
                  path: str | Path) -> Path:
    return write_yaml(path, charter_draft(candidate, result, graph), header=HEADER)
