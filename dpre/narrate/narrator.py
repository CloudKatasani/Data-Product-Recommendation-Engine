"""The Narrator: purpose, decision-register drafts and the candidate story.

Everything written here is marked AI_DRAFT. A reviewer must confirm the blocked
decision, the latency tolerance and the consequence of not deciding; the engine
only drafts them from usage patterns (specification sections 9.1 and 9.2).

Review finding R-58 asked that the drafts stop reading as machine output. They
now do four things differently: report titles are de-duplicated before they
become questions; KPI labels are phrased as the question a consumer would ask
("How many active cards did we have last month?"), from the aggregation and the
cadence; the persona is left blank, with a confidence, when no role keyword
matched the business unit; and the inferred decision varies by cadence and
metric type, or says "unclear" when the titles do not support one.

Review finding R-42: every prompt goes through ``ai.build_prompt`` (field
allow-list, PII column redaction) and every call, template or model, lands on
the AI ledger with its provenance, which is kept per drafted field in
``candidate.narrative["provenance"]``.
"""
from __future__ import annotations

import re
from collections import Counter

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, DecisionDraft, KnowledgeGraph
from . import ai

# Role by keyword in the business unit name. Order matters: the first hit wins,
# so the more specific keyword sits above the generic one it contains.
ROLE_BY_KEYWORD = (
    ("collection", "Collections manager"),
    ("credit", "Credit manager"),
    ("revenue cycle", "Revenue cycle director"),
    ("revenue", "Revenue assurance analyst"),
    ("finance", "Finance business partner"),
    ("risk", "Risk manager"),
    ("compliance", "Compliance officer"),
    ("regulat", "Regulatory reporting lead"),
    ("audit", "Audit lead"),
    ("assurance", "Assurance lead"),
    ("payments", "Payments operations lead"),
    ("retail banking", "Retail banking product manager"),
    ("patient access", "Patient access manager"),
    ("clinical", "Clinical operations lead"),
    ("customer care", "Care operations lead"),
    ("customer", "Customer operations lead"),
    ("contact centre", "Contact centre manager"),
    ("contact center", "Contact centre manager"),
    ("care", "Care operations lead"),
    ("network", "Network operations manager"),
    ("meter", "Metering services lead"),
    ("quality", "Quality lead"),
    ("merchand", "Merchandising planner"),
    ("ecommerce", "Ecommerce trading manager"),
    ("e-commerce", "Ecommerce trading manager"),
    ("supply", "Supply chain planner"),
    ("store", "Store operations manager"),
    ("marketing", "Marketing analyst"),
    ("distribution", "Distribution manager"),
    ("sales", "Sales operations manager"),
    ("plant", "Plant manager"),
    ("maintenance", "Maintenance planner"),
    ("underwrit", "Underwriting manager"),
    ("claims", "Claims operations lead"),
    ("actuar", "Actuary"),
    ("casework", "Casework team leader"),
    ("case", "Casework team leader"),
    ("benefit", "Benefits delivery lead"),
    ("programme", "Programme manager"),
    ("program", "Programme manager"),
    ("executive", "Executive sponsor"),
    ("operations", "Operations manager"),
)

# What one period of a cadence is called from the consumer's side of the desk.
PERIOD_BY_CADENCE = {
    "hourly": "in the last hour",
    "daily": "yesterday",
    "weekly": "last week",
    "monthly": "last month",
    "quarterly": "last quarter",
    "annual": "last year",
    "annually": "last year",
    "yearly": "last year",
}
DECISION_RHYTHM = {
    "hourly": "through the day",
    "daily": "each morning",
    "weekly": "at the weekly review",
    "monthly": "at month end",
    "quarterly": "each quarter",
    "annual": "at the annual review",
    "annually": "at the annual review",
    "yearly": "at the annual review",
}
_TITLE_NOISE = re.compile(
    r"\b(report|dashboard|summary|detail|executive view|view|by region|by business unit|"
    r"v\d+|copy|final|new|old)\b", re.IGNORECASE)
_PARENTHETICAL = re.compile(r"\([^)]*\)")


def persona_for(business_unit: str) -> tuple[str, float]:
    """Persona and a confidence in it; blank with 0.0 when nothing matched.

    A keyword hit on the unit name is worth 0.6 (the role is plausible, the
    person is not named); a bare 'Analyst (unit)' guess is no longer made,
    because a consumer who receives a guessed persona discounts the packet.
    """
    low = (business_unit or "").lower()
    if not low:
        return "", 0.0
    for keyword, role in ROLE_BY_KEYWORD:
        if keyword in low:
            confidence = 0.75 if low.strip() == keyword else 0.6
            return f"{role} ({business_unit})", confidence
    return "", 0.0


def narrate(candidates: list[Candidate], result: CanonicalizationResult,
            graph: KnowledgeGraph) -> None:
    for candidate in candidates:
        metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
        pii = _pii_names(candidate)
        purpose = _purpose(candidate, metrics, pii)
        candidate.purpose = purpose.text
        drafts, draft_provenance, persona_confidence = _decision_drafts(
            candidate, metrics, graph, pii)
        candidate.decisions_drafted = drafts
        candidate.narrative = {
            "purpose_status": "AI_DRAFT",
            "name_status": candidate.name_status,
            "value_hypothesis": _value_hypothesis(candidate, metrics),
            "scope_in": [m.canonical_name for m in metrics],
            "scope_out": _scope_out(candidate, result),
            "headline": _headline(candidate, metrics),
            "source": purpose.source,
            "persona_confidence": persona_confidence,
            "provenance": {"purpose": purpose.provenance, "decisions": draft_provenance},
        }


def _pii_names(candidate: Candidate) -> set[str]:
    return {a.column_fqn for a in candidate.attributes if a.pii_flag} | \
           {a.name for a in candidate.attributes if a.pii_flag}


def _purpose(candidate: Candidate, metrics, pii: set[str]) -> ai.AiResult:
    units = [c.business_unit for c in candidate.consumers[:3] if c.business_unit]
    if len(units) > 1:
        audience = ", ".join(units[:-1]) + " and " + units[-1]
    else:
        audience = units[0] if units else "the owning business unit"
    subject = candidate.proposed_name.split(" by ")[0].lower()
    cadence = Counter(c.cadence for c in candidate.consumers if c.cadence)
    # "consumed ... today" states what the reports do now; the refresh commitment
    # is a Stage 1 latency decision for the consumer, not a promise by the engine.
    consumed = (f"consumed {cadence.most_common(1)[0][0].lower()} today" if cadence
                else "on a cadence the consumer must confirm")
    conflicts = len(candidate.conflicts)
    tail = (f" It resolves {conflicts} competing definition(s) of the same numbers."
            if conflicts else "")
    fallback = (f"Give {audience} one agreed {subject} at {candidate.grain.lower()} grain, "
                f"{consumed}.{tail}")
    prompt, redactions = ai.build_prompt(
        "Write one sentence naming the decision this candidate serves.",
        {
            "proposed_name": candidate.proposed_name,
            "grain": candidate.grain,
            "domain": candidate.domain,
            "consumers": [{"business_unit": c.business_unit, "users": c.users,
                           "cadence": c.cadence} for c in candidate.consumers[:4]],
            "metrics": [m.canonical_name for m in metrics[:12]],
            "conflicts": conflicts,
        }, pii)
    return ai.complete(ai.NARRATOR_SYSTEM, prompt, fallback, purpose="purpose",
                       candidate_id=candidate.candidate_id, redactions=redactions)


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


# --------------------------------------------------------------------------
# Stage 1 drafts
# --------------------------------------------------------------------------

def _decision_drafts(candidate: Candidate, metrics, graph: KnowledgeGraph,
                     pii: set[str]) -> tuple[list[DecisionDraft], list[dict], dict[str, float]]:
    """One decision-register entry per business unit, drafted from usage.

    The engine writes what the unit runs and how often, and infers a decision
    from the report titles and the metric types. A human must confirm the
    blocked decision, the latency tolerance and the consequence of not deciding.
    """
    drafts: list[DecisionDraft] = []
    provenance: list[dict] = []
    persona_confidence: dict[str, float] = {}
    for consumer in candidate.consumers[:6]:
        reports = [r for r in candidate.reports if r.business_unit == consumer.business_unit]
        titles = unique_titles(r.report_name for r in sorted(reports, key=lambda r: -r.users))
        questions = draft_questions(titles, metrics, consumer.cadence)
        persona, confidence = persona_for(consumer.business_unit)
        persona_confidence[consumer.business_unit] = confidence
        inferred = _infer_decision(consumer.business_unit, titles, metrics, candidate,
                                   consumer.cadence, pii)
        draft = DecisionDraft(
            business_unit=consumer.business_unit,
            persona=persona,
            cadence=consumer.cadence or "Ad hoc",
            questions=questions[:6],
            inferred_decision=inferred.text,
            latency_tolerance="TO BE CONFIRMED by the named consumer",
            consequence="TO BE CONFIRMED by the named consumer",
            status="AI_DRAFT",
        )
        # Kept beside the dataclass so seeds can show it without a model change.
        setattr(draft, "persona_confidence", confidence)
        drafts.append(draft)
        provenance.append({"business_unit": consumer.business_unit, **inferred.provenance})
    return drafts, provenance, persona_confidence


def unique_titles(titles) -> list[str]:
    """Titles once each, case- and punctuation-insensitively, in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for title in titles:
        key = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(title)
    return out


def period_phrase(cadence: str) -> str:
    return PERIOD_BY_CADENCE.get((cadence or "").strip().lower(), "in the period")


def plain_label(label: str) -> str:
    """A KPI label in sentence case: 'Delinquent Accounts' -> 'delinquent accounts',
    acronyms such as 'DSO' or 'PII' kept as they are."""
    words = (label or "").replace("_", " ").split()
    return " ".join(w if (w.isupper() and len(w) > 1) else w.lower() for w in words)


def question_for_metric(metric, cadence: str) -> str:
    """Phrase a KPI label as the question a consumer asks, from its aggregation."""
    label = (metric.labels[0] if metric.labels else metric.canonical_name.replace("_", " "))
    subject = plain_label(label) or "this number"
    when = period_phrase(cadence)
    aggregation = (metric.aggregation or "").upper()
    if aggregation in ("COUNT", "COUNT DISTINCT"):
        return f"How many {subject} did we have {when}?"
    if aggregation == "SUM":
        return f"What was our total {subject} {when}?"
    if aggregation == "AVG":
        return f"What was the average {subject} {when}?"
    if aggregation == "MAX":
        return f"What was the highest {subject} {when}?"
    if aggregation == "MIN":
        return f"What was the lowest {subject} {when}?"
    if aggregation == "RATIO" or any(w in subject for w in (" rate", " ratio", " share", "%")):
        return f"What was our {subject} {when}, and is it inside tolerance?"
    return f"What was our {subject} {when}?"


def question_for_title(title: str, cadence: str) -> str:
    """A report title as the question its reader opens it to answer."""
    cleaned = _TITLE_NOISE.sub("", _PARENTHETICAL.sub("", title or "")).strip(" -:")
    cleaned = re.sub(r"\s{2,}", " ", cleaned) or (title or "").strip()
    return f"What does {cleaned} look like {period_phrase(cadence)}?"


def draft_questions(titles: list[str], metrics, cadence: str) -> list[str]:
    """Questions from KPI labels first (they are what the product certifies),
    then from distinct report titles, de-duplicated after phrasing."""
    ranked = sorted(metrics, key=lambda m: -m.usage_weight)
    questions = [question_for_metric(m, cadence) for m in ranked[:3]]
    questions += [question_for_title(t, cadence) for t in titles[:4]]
    return unique_titles(questions)


def _dominant_metric_type(metrics) -> str:
    """count | amount | rate | average | unclear, from the metrics' aggregations."""
    kinds: Counter = Counter()
    for metric in metrics:
        aggregation = (metric.aggregation or "").upper()
        if aggregation in ("COUNT", "COUNT DISTINCT"):
            kinds["count"] += 1
        elif aggregation == "SUM":
            kinds["amount"] += 1
        elif aggregation == "RATIO":
            kinds["rate"] += 1
        elif aggregation in ("AVG", "MAX", "MIN"):
            kinds["average"] += 1
    return kinds.most_common(1)[0][0] if kinds else "unclear"


def _decision_fallback(business_unit: str, titles: list[str], metrics, candidate: Candidate,
                       cadence: str) -> str:
    """A decision sentence that changes with cadence and metric type, or 'unclear'."""
    review = "A reviewer must confirm or rewrite this with the named consumer."
    if not titles and not metrics:
        return ("unclear: no report titles or metrics support an inference for "
                f"{business_unit}. {review}")
    subject = candidate.proposed_name.split(" by ")[0].lower()
    grain = (candidate.grain or "record").lower()
    rhythm = DECISION_RHYTHM.get((cadence or "").strip().lower(), "as needed")
    kind = _dominant_metric_type(metrics)
    lead = (metrics and sorted(metrics, key=lambda m: -m.usage_weight)[0]) or None
    lead_label = plain_label(lead.labels[0] if lead and lead.labels else
                             (lead.canonical_name if lead else subject))
    if kind == "count":
        decision = (f"which {grain}s to act on {rhythm}, from the count of {lead_label} "
                    f"and how it moved")
    elif kind == "amount":
        decision = (f"whether the {lead_label} total is on plan {rhythm}, and where to "
                    f"intervene by {grain}")
    elif kind == "rate":
        decision = (f"whether {lead_label} has crossed its tolerance {rhythm}, and which "
                    f"{grain}s to escalate")
    elif kind == "average":
        decision = (f"whether the typical {lead_label} is drifting {rhythm}, and which "
                    f"{grain}s sit outside the norm")
    else:
        return (f"unclear: {business_unit} runs {titles[0] if titles else subject} "
                f"{rhythm}, but the metric types do not point to one decision. {review}")
    opener = (f"{business_unit} opens {titles[0]} {rhythm}" if titles
              else f"{business_unit} reads {subject} {rhythm}")
    return f"{opener}; the decision this appears to serve is {decision}. {review}"


def _infer_decision(business_unit: str, titles: list[str], metrics, candidate: Candidate,
                    cadence: str, pii: set[str]) -> ai.AiResult:
    fallback = _decision_fallback(business_unit, titles, metrics, candidate, cadence)
    prompt, redactions = ai.build_prompt(
        "From the report titles, metrics and cadence below, infer in one sentence the "
        "recurring decision this business unit is making. Say 'unclear' if the titles do "
        "not support an inference.",
        {"business_unit": business_unit, "cadence": cadence, "titles": titles[:6],
         "metric_labels": [(m.labels[0] if m.labels else m.canonical_name) for m in metrics[:8]],
         "grain": candidate.grain, "candidate": candidate.proposed_name}, pii)
    return ai.complete(ai.NARRATOR_SYSTEM, prompt, fallback, purpose="inferred_decision",
                       candidate_id=candidate.candidate_id, redactions=redactions)
