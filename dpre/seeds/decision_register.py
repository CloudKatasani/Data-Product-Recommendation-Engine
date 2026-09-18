"""DPF Stage 1 seed: ``decision-register.yaml`` (specification section 9.2)."""
from __future__ import annotations

from pathlib import Path

from ..models import Candidate
from ..util.yamlio import write_yaml

HEADER = (
    "Data Product Factory - Stage 1 Consumption Discovery\n"
    "Seeded by the Data Product Recommendation Engine.\n"
    "Every field marked AI_DRAFT is an inference from usage, not a fact. A human must\n"
    "confirm the blocked decision, the latency tolerance and the consequence of not\n"
    "deciding with the named consumer before this stage can exit."
)


def decision_register(candidate: Candidate) -> dict:
    return {
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
                    "persona": draft.persona,
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
            "reviewer_must": [
                "name the decision that is blocked today",
                "state how stale the answer may be",
                "state what happens if the decision is not made",
            ],
        },
    }


def write_decision_register(candidate: Candidate, path: str | Path) -> Path:
    return write_yaml(path, decision_register(candidate), header=HEADER)
