"""DPF Stage 1 seed: ``decision-register.yaml`` (specification section 9.2).

The persona is an inference from the business unit name and carries its
confidence; when nothing matched, the field says so instead of guessing an
"Analyst" (review finding R-58). The provenance block and header say whether
the run was synthetic (review finding R-36).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import Candidate, KnowledgeGraph
from ..util.yamlio import write_yaml
from .provenance import provenance_block, run_provenance, seed_header

HEADER = (
    "Data Product Factory - Stage 1 Consumption Discovery\n"
    "Seeded by the Data Product Recommendation Engine.\n"
    "Every field marked AI_DRAFT is an inference from usage, not a fact. A human must\n"
    "confirm the blocked decision, the latency tolerance and the consequence of not\n"
    "deciding with the named consumer before this stage can exit."
)
PERSONA_UNMATCHED = "TO BE NAMED by the business unit - no role could be inferred"


def decision_register(candidate: Candidate, graph: KnowledgeGraph | None = None,
                      provenance: dict[str, Any] | None = None) -> dict:
    provenance = provenance or run_provenance(candidate, graph)
    return {
        "provenance": provenance_block(provenance),
        "data_product": {
            "candidate_id": candidate.candidate_id,
            "proposed_name": candidate.proposed_name,
            "name_status": candidate.name_status,
            "domain": candidate.domain,
            "grain": candidate.grain,
            "as_of_date": candidate.as_of_date,
            "run_id": candidate.run_id,
        },
        "decisions": [
            {
                "consumer": {
                    "business_unit": draft.business_unit,
                    "persona": draft.persona or PERSONA_UNMATCHED,
                    "persona_confidence": _persona_confidence(candidate, draft),
                    "persona_basis": ("role keyword in the business unit name" if draft.persona
                                      else "no keyword matched; left for the consumer"),
                    "status": draft.status,
                },
                "cadence": draft.cadence,
                "questions_asked_today": draft.questions,
                "inferred_decision": draft.inferred_decision,
                "blocked_decision": "TO BE CONFIRMED",
                "latency_tolerance": draft.latency_tolerance,
                "consequence_of_not_deciding": draft.consequence,
                "evidence": {
                    "reports": [r.report_id for r in candidate.reports
                                if r.business_unit == draft.business_unit][:10],
                },
            }
            for draft in candidate.decisions_drafted
        ],
        "engine_notes": {
            "drafted_from": "report titles, KPI labels, run counts and schedules",
            "ai_provenance": (candidate.narrative.get("provenance", {}) or {}).get("purpose", {}),
            "reviewer_must": [
                "name the decision that is blocked today",
                "state how stale the answer may be",
                "state what happens if the decision is not made",
            ],
        },
    }


def _persona_confidence(candidate: Candidate, draft) -> float:
    explicit = getattr(draft, "persona_confidence", None)
    if explicit is not None:
        return round(float(explicit), 2)
    by_unit = candidate.narrative.get("persona_confidence", {}) or {}
    return round(float(by_unit.get(draft.business_unit, 0.0)), 2)


def write_decision_register(candidate: Candidate, path: str | Path,
                            graph: KnowledgeGraph | None = None) -> Path:
    provenance = run_provenance(candidate, graph)
    return write_yaml(path, decision_register(candidate, graph, provenance),
                      header=seed_header(HEADER, provenance))
