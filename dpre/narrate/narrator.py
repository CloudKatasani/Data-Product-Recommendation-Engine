"""The Narrator: purpose, decision-register drafts and the candidate story.

Everything written here is marked AI_DRAFT. A reviewer must confirm the blocked
decision, the latency tolerance and the consequence of not deciding; the engine
only drafts them from usage patterns (specification sections 9.1 and 9.2).
"""
from __future__ import annotations

from collections import Counter

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, DecisionDraft, KnowledgeGraph
from ..util.jsonio import dumps
from . import ai

ROLE_BY_KEYWORD = (
    ("collection", "Collections manager"),
    ("credit", "Credit manager"),
    ("finance", "Finance business partner"),
    ("revenue", "Revenue assurance analyst"),
    ("risk", "Risk manager"),
    ("compliance", "Compliance officer"),
    ("regulat", "Regulatory reporting lead"),
    ("audit", "Audit lead"),
    ("operations", "Operations manager"),
    ("customer", "Customer operations lead"),
    ("care", "Care operations lead"),
    ("network", "Network operations manager"),
    ("meter", "Metering services lead"),
    ("clinical", "Clinical operations lead"),
    ("quality", "Quality lead"),
    ("merchand", "Merchandising planner"),
    ("supply", "Supply chain planner"),
    ("store", "Store operations manager"),
    ("marketing", "Marketing analyst"),
    ("sales", "Sales operations manager"),
    ("plant", "Plant manager"),
    ("maintenance", "Maintenance planner"),
    ("underwrit", "Underwriting manager"),
    ("claims", "Claims operations lead"),
    ("actuarial", "Actuary"),
    ("case", "Casework team leader"),
    ("benefit", "Benefits delivery lead"),
    ("programme", "Programme manager"),
    ("executive", "Executive sponsor"),
)


def persona_for(business_unit: str) -> str:
    low = (business_unit or "").lower()
    for keyword, role in ROLE_BY_KEYWORD:
        if keyword in low:
            return f"{role} ({business_unit})"
    return f"Analyst ({business_unit or 'Unassigned'})"


def narrate(candidates: list[Candidate], result: CanonicalizationResult,
            graph: KnowledgeGraph) -> None:
    for candidate in candidates:
        metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
        candidate.purpose = _purpose(candidate, metrics)
        candidate.decisions_drafted = _decision_drafts(candidate, metrics, graph)
        candidate.narrative = {
            "purpose_status": "AI_DRAFT",
            "name_status": candidate.name_status,
            "value_hypothesis": _value_hypothesis(candidate, metrics),
            "scope_in": [m.canonical_name for m in metrics],
            "scope_out": _scope_out(candidate, result),
            "headline": _headline(candidate, metrics),
            "source": "template" if not ai.available() else "model",
        }


def _purpose(candidate: Candidate, metrics) -> str:
    units = [c.business_unit for c in candidate.consumers[:3] if c.business_unit]
    if len(units) > 1:
        audience = ", ".join(units[:-1]) + " and " + units[-1]
    else:
        audience = units[0] if units else "the owning business unit"
    subject = candidate.proposed_name.split(" by ")[0].lower()
    cadence = Counter(c.cadence for c in candidate.consumers if c.cadence)
    refresh = (cadence.most_common(1)[0][0].lower() if cadence else "on a cadence to be confirmed")
    conflicts = len(candidate.conflicts)
    tail = (f" It resolves {conflicts} competing definition(s) of the same numbers."
            if conflicts else "")
    fallback = (f"Give {audience} one agreed {subject} at {candidate.grain.lower()} grain, "
                f"refreshed {refresh}.{tail}")
    prompt = (
        "Write one sentence naming the decision this candidate serves.\n\n"
        + dumps({
            "proposed_name": candidate.proposed_name,
            "grain": candidate.grain,
            "domain": candidate.domain,
            "consumers": [{"business_unit": c.business_unit, "users": c.users,
                           "cadence": c.cadence} for c in candidate.consumers[:4]],
            "metrics": [m.canonical_name for m in metrics[:12]],
            "conflicts": conflicts,
        })
    )
    return ai.complete(ai.NARRATOR_SYSTEM, prompt, fallback).text


def _headline(candidate: Candidate, metrics) -> str:
    retirable = sum(1 for r in candidate.reports if r.coverage >= 1.0)
    return (f"{len(metrics)} canonical metrics, {retirable} reports fully covered, "
            f"{len(candidate.consumers)} business unit(s)")


def _value_hypothesis(candidate: Candidate, metrics) -> str:
    retirable = [r for r in candidate.reports if r.coverage >= 1.0]
    partial = [r for r in candidate.reports if 0 < r.coverage < 1.0]
    users = sum(c.users for c in candidate.consumers)
    collapsed = sum(max(0, len(m.kpi_ids) - 1) for m in metrics)
    return (
        f"Retires {len(retirable)} reports outright and consolidates {len(partial)} more, "
        f"serving {users} users across {len(candidate.consumers)} business unit(s). "
        f"Collapses {collapsed} duplicate KPI definitions into {len(metrics)} certified "
        f"metrics and puts {len(candidate.conflicts)} conflicting definition(s) in front of "
        "a steward."
    )


def _scope_out(candidate: Candidate, result: CanonicalizationResult) -> list[str]:
    out = []
    for child_id in candidate.child_candidate_ids:
        out.append(f"metrics split into child candidate {child_id} on grain")
    if candidate.parent_candidate_id:
        out.append(f"metrics held by parent candidate {candidate.parent_candidate_id}")
    for dependency in candidate.depends_on:
        if dependency != candidate.parent_candidate_id:
            out.append(f"metrics owned by candidate {dependency}")
    return out


def _decision_drafts(candidate: Candidate, metrics, graph: KnowledgeGraph) -> list[DecisionDraft]:
    """One decision-register entry per business unit, drafted from usage.

    The engine writes what the unit runs and how often, and infers a decision
    from the report titles. A human must confirm the blocked decision, the
    latency tolerance and the consequence of not deciding.
    """
    drafts: list[DecisionDraft] = []
    for consumer in candidate.consumers[:6]:
        reports = [r for r in candidate.reports if r.business_unit == consumer.business_unit]
        titles = [r.report_name for r in sorted(reports, key=lambda r: -r.users)[:5]]
        questions = [f"{title}?" for title in titles]
        questions += [f"What is our {m.labels[0] if m.labels else m.canonical_name}?"
                      for m in metrics[:3]]
        inferred = _infer_decision(consumer.business_unit, titles, candidate, consumer.cadence)
        drafts.append(DecisionDraft(
            business_unit=consumer.business_unit,
            persona=persona_for(consumer.business_unit),
            cadence=consumer.cadence or "Ad hoc",
            questions=questions[:6],
            inferred_decision=inferred,
            latency_tolerance="TO BE CONFIRMED by the named consumer",
            consequence="TO BE CONFIRMED by the named consumer",
            status="AI_DRAFT",
        ))
    return drafts


def _infer_decision(business_unit: str, titles: list[str], candidate: Candidate,
                    cadence: str) -> str:
    subject = candidate.proposed_name.split(" by ")[0].lower()
    when = (cadence or "each period").lower()
    head = titles[0].lower() if titles else subject
    fallback = (f"{business_unit} runs {head} {when}; the decision this appears to serve is "
                f"which {candidate.grain.lower()}s to act on next, based on {subject}. "
                "A reviewer must confirm or rewrite this with the named consumer.")
    prompt = (
        "From the report titles and cadence below, infer in one sentence the recurring "
        "decision this business unit is making. Say 'unclear' if the titles do not "
        "support an inference.\n\n"
        + dumps({"business_unit": business_unit, "cadence": cadence, "titles": titles,
                 "grain": candidate.grain, "candidate": candidate.proposed_name})
    )
    return ai.complete(ai.NARRATOR_SYSTEM, prompt, fallback).text
