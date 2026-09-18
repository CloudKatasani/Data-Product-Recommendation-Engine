"""One printable page per candidate, for a business owner (review finding R-12).

The card in the browser application spreads a candidate over eleven tabs and
the drawer is a fixed overlay, so printing it yields one viewport. A council
member needs a single-column page they can read on paper, forward to a report
owner and annotate: what this product is, who asked for it, why it ranks where
it does, what it retires, what it costs, what a reviewer will object to, and
what happens next.

Everything on the page is already on the candidate record. The dossier adds no
judgement of its own - it translates codes into words through
``dpre.labels`` and writes the score as a sentence instead of a number
(review finding R-41).
"""
from __future__ import annotations

from typing import Any

from .. import labels
from ..portfolio.views import attribute_reports, is_hold, is_retirable, retirement_action
from .blocks import (Document, Section, bullets, callout, cell_text, definitions, kpis,
                     money, paragraph, percent, table)
from .engagement import Engagement
from .enrichment import EnrichmentView
from .summary import SYNTHETIC_BANNER

MAX_ROWS = 40


def candidate_dossier(candidate: Any, result: Any, engagement: Any = None,
                      enrichment: Any = None, attribution: dict[str, str] | None = None
                      ) -> Document:
    """The dossier document for one candidate."""
    engagement = Engagement.coerce(engagement)
    view = EnrichmentView(enrichment)
    attribution = attribution if attribution is not None else attribute_reports(result.candidates)
    manifest = result.manifest
    synthetic = bool(getattr(manifest, "synthetic", False))
    name = candidate.proposed_name
    document = Document(
        title=f"{name}",
        subtitle=(f"{engagement.title} - candidate {candidate.candidate_id} - "
                  f"run {manifest.run_id}, estate as of {manifest.as_of_date}"),
        banner=SYNTHETIC_BANNER if synthetic else "",
        meta={"candidate_id": candidate.candidate_id, "run_id": manifest.run_id,
              "synthetic": synthetic,
              "footer": (f"{engagement.confidentiality}. Candidate {candidate.candidate_id} is "
                         f"{candidate.status}; the engine proposes and only a named reviewer "
                         "decides.")},
    )
    document.add(_what_it_is(candidate, result, view))
    document.add(_who_uses_it(candidate))
    document.add(_why_this_score(candidate))
    document.add(_gates(candidate))
    document.add(_reports(candidate, result, attribution))
    document.add(_metrics(candidate, result))
    document.add(_conflicts(candidate, result))
    document.add(_data(candidate))
    document.add(_delivery(candidate, view))
    document.add(_objections(candidate))
    document.add(_decisions_drafted(candidate))
    document.add(_next_steps(candidate))
    document.add(_provenance(candidate, result))
    return document


# --------------------------------------------------------------------------

def _what_it_is(candidate: Any, result: Any, view: EnrichmentView) -> Section:
    section = Section("what", "What this product is")
    if candidate.name_status == "AI_DRAFT":
        section.add(callout(
            "The name and purpose on this page were drafted by the engine and are marked "
            "AI_DRAFT. They are a starting point for the owner to correct, not a decision.",
            tone="warning", title="Drafted, not agreed"))
    section.add(paragraph(candidate.purpose))
    composite = candidate.score.composite if candidate.score else 0.0
    band = labels.composite_band(composite)
    section.add(kpis([
        {"label": "Composite score", "value": f"{composite:.1f} / 100",
         "note": f"{band['band']} - {band['meaning']}"},
        {"label": "Status", "value": labels.label("status", candidate.status),
         "note": labels.explanation("status", candidate.status)},
        {"label": "Kind of product", "value": labels.label("archetype", candidate.archetype),
         "note": labels.explanation("archetype", candidate.archetype)},
        {"label": "Where it sits", "value": labels.label("tier", candidate.tier),
         "note": labels.explanation("tier", candidate.tier)},
    ]))
    rows = [
        ("Grain (one row is)", candidate.grain),
        ("Domain", candidate.domain + (f" / {candidate.sub_domain}" if candidate.sub_domain else "")),
        ("Proposed owner", candidate.owner_candidate or "not identified - a reviewer must name one"),
        ("Proposed steward", candidate.steward_candidate or "not identified in the catalog"),
        ("How it was found", labels.label("origin", candidate.origin)),
        ("Reporting tools in scope", ", ".join(candidate.tools) or "n/a"),
    ]
    if candidate.archetype_runner_up:
        rows.append(("Alternative classification",
                     f"{labels.label('archetype', candidate.archetype_runner_up)} - "
                     + labels.classification_margin_note(candidate.archetype_confidence)))
    if candidate.depends_on:
        rows.append(("Depends on", ", ".join(candidate.depends_on)))
    section.add(definitions(rows))
    headline = (candidate.narrative or {}).get("headline")
    if headline:
        section.add(paragraph(headline))
    quantified = (candidate.narrative or {}).get("value_hypothesis_quantified")
    if quantified:
        section.add(callout(quantified, tone="note", title="Value hypothesis (illustrative)"))
    return section


def _who_uses_it(candidate: Any) -> Section:
    section = Section("consumers", "Who reads these numbers today")
    section.add(table(
        ["Business unit", "Users", "Reports", "How often", "On a schedule"],
        [[c.business_unit or "Unassigned", c.users, c.report_count, c.cadence or "ad hoc",
          percent(c.scheduled_share, 0)] for c in candidate.consumers[:MAX_ROWS]],
        empty="No business unit could be attributed to the reports behind this candidate, "
              "which is why gate G1 is not satisfied."))
    return section


def _why_this_score(candidate: Any) -> Section | None:
    score = candidate.score
    if score is None:
        return None
    section = Section("score", "Why it scores what it does")
    section.add(paragraph(_score_sentence(candidate)))
    section.add(table(
        ["Dimension", "Score", "What it measures"],
        [[labels.label("dimension", d), f"{getattr(score, d):.1f}",
          labels.explanation("dimension", d)]
         for d in ("demand", "consolidation", "feasibility", "risk")]))
    rows = []
    for feature in sorted(score.features, key=lambda f: -abs(f.contribution))[:12]:
        rows.append([
            labels.label("feature", feature.feature),
            labels.label("dimension", feature.dimension),
            cell_text(feature.value),
            percent(feature.normalized, 0),
            f"{feature.contribution:+.2f}",
            feature.detail or labels.explanation("feature", feature.feature),
        ])
    section.add(table(
        ["Feature", "Dimension", "Measured", "Normalised", "Contribution", "What it means"],
        rows, caption="Contribution is this feature's share of the composite under weight "
                      "version " + score.weight_version + ". Every value has evidence rows "
                      "behind it in the workbook."))
    evidence_count = len(candidate.evidence)
    section.add(paragraph(
        f"{evidence_count} evidence row(s) support these figures. The engine cannot write a "
        "score without them: a score without evidence fails the run."))
    return section


def _score_sentence(candidate: Any) -> str:
    """The 'why this score' sentence the review asked for (R-41)."""
    score = candidate.score
    composite = score.composite
    band = labels.composite_band(composite)
    # Risk features carry a positive contribution that is subtracted from the
    # composite, so they belong on the "held back by" side of the sentence, not
    # the "lifted by" side - a reader told that data sensitivity lifted a score
    # would draw exactly the wrong conclusion.
    positives = sorted((f for f in score.features
                        if f.dimension != "risk" and f.contribution > 0),
                       key=lambda f: -f.contribution)[:3]
    drags = sorted((f for f in score.features
                    if f.dimension == "risk" or f.contribution < 0),
                   key=lambda f: -abs(f.contribution))[:2]
    lifted = ", ".join(labels.label("feature", f.feature).lower() for f in positives)
    held = ", ".join(labels.label("feature", f.feature).lower() for f in drags)
    sentence = (f"{composite:.1f} out of 100 - {band['band']}: {band['meaning']} "
                f"It is lifted mainly by {lifted or 'no single feature'}")
    if held:
        sentence += f", and held back by {held}"
    return sentence + "."


def _gates(candidate: Any) -> Section | None:
    score = candidate.score
    if score is None or not score.gates:
        return None
    section = Section("gates", "The four hard gates")
    section.add(paragraph(
        "A candidate that fails a hard gate cannot be Proposed, whatever it scores. The gates "
        "are not opinions about value; they are conditions a build would fail on."))
    section.add(table(
        ["Gate", "What it requires", "Verdict", "Evidence", "If it fails"],
        [[f"{gate.gate} {labels.label('gate', gate.gate)}",
          labels.explanation("gate", gate.gate),
          "Passed" if gate.passed else "FAILED", gate.detail, gate.effect or ""]
         for gate in score.gates]))
    return section


def _reports(candidate: Any, result: Any, attribution: dict[str, str]) -> Section:
    section = Section("reports", "The reports it would replace")
    rows = []
    for report in sorted(candidate.reports, key=lambda r: (-r.coverage, -r.users)):
        if report.coverage <= 0:
            continue
        record = result.graph.reports.get(report.report_id)
        hold = is_hold(record, report.report_name)
        primary = attribution.get(report.report_id) == candidate.candidate_id
        rows.append([
            f"{report.report_name} [{report.report_id}]",
            report.business_unit or "",
            report.owner or "unassigned",
            report.users,
            percent(report.coverage, 0),
            report.disposition or "Keep",
            retirement_action(report, hold),
            "this candidate" if primary else "another candidate",
        ])
    section.add(table(
        ["Report", "Business unit", "Owner", "Users", "Coverage", "Disposition", "Action",
         "Counted against"], rows[:MAX_ROWS],
        empty="No report is covered by this candidate yet."))
    retirable = sum(1 for report in candidate.reports
                    if attribution.get(report.report_id) == candidate.candidate_id
                    and is_retirable(report,
                                     is_hold(result.graph.reports.get(report.report_id),
                                             report.report_name)))
    section.add(paragraph(
        f"{retirable} report(s) are counted against this candidate for retirement purposes. "
        "Reports covered by more than one candidate are counted once, against the candidate "
        "that covers them best, so the backlog's totals can be added up. 'Action' follows the "
        "report's own disposition: a Keep report is re-pointed at the product and retained, "
        "never retired."))
    return section


def _metrics(candidate: Any, result: Any) -> Section:
    section = Section("metrics", "The numbers it would certify")
    rows = []
    for metric_id in candidate.metric_ids[:MAX_ROWS]:
        metric = result.canonical.metrics.get(metric_id)
        if metric is None:
            continue
        rows.append([
            metric.canonical_name,
            labels.label("name_status", metric.name_status),
            ", ".join(metric.labels[:3]),
            metric.aggregation,
            metric.grain,
            metric.steward_id or "unassigned",
            len(metric.report_ids),
            (metric.definition or "MISSING - a steward must write one")[:160],
        ])
    section.add(table(
        ["Canonical metric", "Name status", "Seen in reports as", "Aggregation", "Grain",
         "Steward", "Reports", "Definition"], rows,
        empty="No canonical metric is attached to this candidate."))
    drafted = sum(1 for m_id in candidate.metric_ids
                  if (m := result.canonical.metrics.get(m_id)) and m.name_status == "AI_DRAFT")
    if drafted:
        section.add(paragraph(
            f"{drafted} of these names are drafted by the engine and blocked from the catalog "
            "until a steward accepts them. Accepting a name is a separate act from accepting "
            "the candidate, deliberately: it is how drift is kept out of the semantic layer."))
    return section


def _conflicts(candidate: Any, result: Any) -> Section | None:
    conflicts = [c for c in result.canonical.conflicts if c.conflict_id in set(candidate.conflicts)]
    if not conflicts:
        return None
    section = Section("conflicts", "Competing definitions a steward must settle")
    section.add(paragraph(
        "Each row is the same number calculated two ways in two places. The engine never "
        "chooses between them; it puts them in front of the named steward with the usage "
        "weight behind each reading."))
    section.add(table(
        ["Number", "What differs", "Usage behind A", "Usage behind B", "Steward",
         "What Stage 6 would do", "Status"],
        [[c.label, f"{labels.label('conflict_pattern', c.pattern)}: {c.difference_summary}",
          f"{c.usage_weight_a:,.0f}", f"{c.usage_weight_b:,.0f}",
          c.steward_id or "unassigned", c.semantic_model_decision,
          "Open" if c.resolution_status == "OPEN" else c.resolution_status]
         for c in sorted(conflicts, key=lambda c: -(c.usage_weight_a + c.usage_weight_b))
         [:MAX_ROWS]]))
    return section


def _data(candidate: Any) -> Section:
    section = Section("data", "Where the data comes from, and how sensitive it is")
    section.add(table(
        ["Source table", "System", "Share of metrics", "System of record", "Lifecycle",
         "Successor"],
        [[s.table_fqn, s.system, percent(s.share_of_metrics, 0),
          "yes" if s.sor_flag else "no", s.lifecycle_status, s.successor_system or ""]
         for s in candidate.sources[:MAX_ROWS]],
        empty="No source table resolved for this candidate, which is why its lineage gate fails."))
    pii = [a for a in candidate.attributes if a.pii_flag]
    restricted = [a for a in candidate.attributes
                  if a.sensitivity in ("Restricted", "Confidential")]
    if pii or restricted:
        section.add(callout(
            f"{len(pii)} attribute(s) are flagged as personal data and "
            f"{len(restricted)} are Confidential or Restricted. DPF Stage 9 privacy review "
            "applies before this product can be built, and the column names of PII attributes "
            "are withheld from any language-model prompt.",
            tone="warning", title="Sensitive data in scope"))
    missing = [a for a in candidate.attributes if not a.definition]
    section.add(paragraph(
        f"{len(candidate.attributes)} attribute(s) in scope; {len(missing)} carry no definition "
        "in the catalog and appear in the gap register. The full attribute register, with "
        "sensitivity per column, ships as a Stage 5 seed."))
    return section


def _delivery(candidate: Any, view: EnrichmentView) -> Section | None:
    value = view.value_for(candidate.candidate_id)
    effort = view.effort_for(candidate.candidate_id)
    wave = view.wave_of(candidate.candidate_id)
    if not (value or effort or wave):
        return None
    section = Section("delivery", "What it would take, and what it would return")
    items = []
    if value:
        items.append({"label": "Attributed annual benefit",
                      "value": money(value.get("attributed_annual_benefit"),
                                     value.get("currency", "")),
                      "note": f"illustrative, assumptions {value.get('assumption_version', '')}"})
        items.append({"label": "Payback", "value": _payback(value),
                      "note": f"against an indicative build cost of "
                              f"{money(value.get('build_cost'), value.get('currency', ''))}"})
    if effort:
        items.append({"label": "Size", "value": str(effort.get("size", "")),
                      "note": f"{cell_text(effort.get('points'))} effort points, about "
                              f"{cell_text(effort.get('total_weeks'))} weeks across DPF stages"})
    if wave:
        items.append({"label": "Planned wave", "value": f"Wave {wave}",
                      "note": "capacity plan, not a commitment"})
    section.add(kpis(items))
    if value and value.get("components"):
        section.add(table(
            ["Benefit component", "Annual", "Drivers", "What it assumes"],
            [[str(c.get("name", "")).replace("_", " ").capitalize(),
              money(c.get("attributed_benefit"), value.get("currency", "")),
              f"{cell_text(c.get('driver_count'))} {c.get('unit', '')}", c.get("basis", "")]
             for c in value["components"]]))
    if effort and effort.get("drivers"):
        section.add(bullets(
            [f"{d.get('detail', d.get('name', ''))} - {cell_text(d.get('points'))} points"
             for d in effort["drivers"][:8]],
            lead="What drives the effort estimate:"))
    rank = view.sensitivity_for(candidate.candidate_id)
    if rank:
        section.add(paragraph(
            f"Under a {percent(0.2, 0)} perturbation of the score weights this candidate ranks "
            f"between {cell_text(rank.get('rank_low'))} and {cell_text(rank.get('rank_high'))} "
            f"(base rank {cell_text(rank.get('rank_base'))}). "
            + ("Its place in the top of the backlog is robust to the weights."
               if rank.get("robust_top_n") else
               "Its place is sensitive to the weights, so the ranking alone should not decide it.")))
    return section


def _payback(value: dict) -> str:
    months = value.get("payback_months")
    if months in (None, "", 0):
        return "not reached"
    try:
        return f"{float(months):.0f} months"
    except (TypeError, ValueError):
        return cell_text(months)


def _objections(candidate: Any) -> Section | None:
    if not candidate.critique:
        return None
    section = Section("objections", "What a reviewer will object to")
    section.add(paragraph(
        "The engine reviews its own proposal against the Data Product Factory Stage 1 and "
        "Stage 2 exit criteria and lists what a reviewer is likely to reject. These are not "
        "defects in the data; they are the work still to do."))
    order = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
    findings = sorted(candidate.critique, key=lambda f: order.get(f.severity, 9))
    section.add(table(
        ["Severity", "Criterion", "Finding"],
        [[labels.label("severity", f.severity),
          labels.label("critique_criterion", f.criterion), f.finding] for f in findings]))
    return section


def _decisions_drafted(candidate: Any) -> Section | None:
    if not candidate.decisions_drafted:
        return None
    section = Section("stage1", "The decision this product would serve (drafted)")
    section.add(callout(
        "Everything in this section is AI_DRAFT. The named consumer must confirm the decision, "
        "say how stale the answer may be, and say what happens if the decision is not made. "
        "The engine deliberately leaves those three blank.", tone="warning",
        title="For the consumer to confirm"))
    for draft in candidate.decisions_drafted:
        confidence = getattr(draft, "persona_confidence", None)
        persona = draft.persona or "not inferred - no role keyword matched this business unit"
        if draft.persona and confidence is not None:
            persona += f" (confidence {confidence:.2f})"
        sub = Section(f"stage1-{draft.business_unit}", draft.business_unit or "Unassigned",
                      level=3)
        sub.add(definitions([
            ("Who we think reads it", persona),
            ("How often", draft.cadence),
            ("Inferred decision", draft.inferred_decision),
            ("Latency tolerance", draft.latency_tolerance),
            ("Consequence of not deciding", draft.consequence),
        ]))
        sub.add(bullets(draft.questions, lead="Questions these reports answer today:"))
        section.blocks.extend([{"type": "paragraph", "text": f"**{sub.title}**"}] + sub.blocks)
    return section


def _next_steps(candidate: Any) -> Section:
    section = Section("next", "What happens next")
    steps = [
        "A named reviewer records Accept, Reject, Merge, Split or Defer against "
        f"{candidate.candidate_id}, with a reason code. Until then this candidate stays "
        f"{labels.label('status', candidate.status)} - the engine cannot move it.",
        "The proposed owner confirms or corrects the name and purpose; accepting the name is "
        "a separate act and is what unblocks catalog import.",
    ]
    if candidate.conflicts:
        steps.append(f"The steward adjudicates {len(candidate.conflicts)} competing "
                     "definition(s); the adjudication request is in the communications pack.")
    if candidate.decisions_drafted:
        steps.append("The named consumer answers the three Stage 1 questions above.")
    steps.append("On acceptance, the pre-filled DPF seeds (Stage 1, 2, 3, 5, 6 and 12) open "
                 "the product in the factory; the catalog payload stays blocked until every "
                 "AI_DRAFT name is accepted.")
    section.add(bullets(steps))
    return section


def _provenance(candidate: Any, result: Any) -> Section:
    narrative = candidate.narrative or {}
    provenance = narrative.get("provenance") or {}
    purpose = provenance.get("purpose") or {}
    section = Section("provenance", "Provenance of the drafted text on this page")
    rows = [
        ("Run", result.manifest.run_id),
        ("Estate as of", result.manifest.as_of_date),
        ("Name status", labels.label("name_status", candidate.name_status)),
        ("Purpose drafted by", _source_words(purpose.get("source") or narrative.get("source"),
                                             purpose.get("model"))),
        ("Prompt version", purpose.get("prompt_version", "")),
        ("Prompt fingerprint", (purpose.get("prompt_sha256") or "")[:16]),
    ]
    # "No provider configured" is the normal offline state, not an incident; only a
    # provider that was configured and then failed is worth a line on a client page.
    fallback = purpose.get("fallback_reason", "")
    if fallback and fallback != "no provider configured":
        rows.append(("Language model unavailable, template used instead", fallback))
    rows += [
        ("Score weight version", result.manifest.weight_version),
        ("Expression parser version", result.manifest.parser_version),
    ]
    section.add(definitions(rows))
    return section


def _source_words(source: Any, model: Any) -> str:
    if source == "model" and model:
        return f"language model {model} (output marked AI_DRAFT)"
    if source == "model":
        return "a language model (output marked AI_DRAFT)"
    return "a deterministic template - no text left the estate"
