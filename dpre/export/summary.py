"""The run-level executive summary a partner tables at a steering committee (R-12).

The review found that the engine's outputs stopped at the browser application,
stdout, a JSON dump and per-candidate seeds: a partner presenting the run had
to screenshot the UI or re-type from JSON. This module builds the one artifact
that answers, in the client's own language, what the estate looks like, whether
the run can be trusted, what the first wave is worth, what it retires, what is
in the way and who must decide what.

It is a pure function of the objects the pipeline already produces, plus an
optional Store (for reviewer decisions and the since-last-run delta) and an
optional programme enrichment (value, effort, waves, RAID, status). Nothing is
computed from the wall clock: every date comes from the run manifest, so
re-cutting a pack for an old run reproduces it exactly.

Specification references: 12.2 (card content), 13.2 (run quality gates),
14.2 (success measures), 15.1 (risks), 15.2 (open decisions D-01 to D-08).
"""
from __future__ import annotations

from typing import Any

from .. import labels
from ..portfolio.views import estate_retirement
from .blocks import (Document, Section, bullets, callout, cell_text, definitions, kpis,
                     money, paragraph, percent, table)
from .engagement import Engagement
from .enrichment import EnrichmentView

TOP_N = 10
SYNTHETIC_BANNER = ("Demonstration run on synthetic data - not client data; "
                    "no figure in this pack describes the client's estate")

# Specification section 15.2. The engine cannot decide these; it can say what
# this run assumed, so the council decides against a stated position rather
# than in the abstract.
OPEN_DECISIONS = (
    ("D-01", "Usage window: 12 or 24 months, and how seasonal reports are treated",
     "Changes demand scores for annual and rate-case reports", "Data product council"),
    ("D-02", "Minimum community size before a candidate is Proposed rather than Exploratory",
     "Controls backlog length and reviewer load", "Data product council"),
    ("D-03", "Whether 'Keep' reports count toward consolidation at all",
     "A Keep report replaced by a product is a win; one left untouched is not",
     "Rationalization lead"),
    ("D-04", "Initial score weights, and who approves a change to them",
     "Determines the first ranking; must be visible and contestable", "Data product council"),
    ("D-05", "Whether Power BI lineage is a Phase 3 adapter or a separate programme",
     "The estate is mixed; one graph needs both", "Programme sponsor"),
    ("D-06", "Sensitivity threshold that forces privacy review before Proposed",
     "Aligns with DPF Stage 9 veto", "Privacy officer"),
    ("D-07", "Catalog asset type and attributes for a Proposed data product",
     "The registration payload cannot be built without it", "Catalog admin"),
    ("D-08", "Which reviewer role may Accept, per domain",
     "Propose-only means nothing if acceptance is undefined", "Programme sponsor"),
)


def executive_summary(result: Any, store: Any = None, engagement: Any = None,
                      enrichment: Any = None) -> Document:
    """Build the executive summary document for one run."""
    engagement = Engagement.coerce(engagement)
    view = EnrichmentView(enrichment)
    manifest = result.manifest
    synthetic = bool(getattr(manifest, "synthetic", False))
    document = Document(
        title=f"{engagement.title}: data product backlog",
        subtitle=(f"Run {manifest.run_id} - {manifest.mode} mode"
                  f"{', ' + manifest.industry if manifest.industry else ''} - "
                  f"estate as of {manifest.as_of_date}"),
        banner=SYNTHETIC_BANNER if synthetic else "",
        meta={"run_id": manifest.run_id, "as_of": manifest.as_of_date,
              "synthetic": synthetic, "engagement": engagement.to_dict(),
              "footer": (f"{engagement.confidentiality}. Produced by the Data Product "
                         f"Recommendation Engine {getattr(result.config, 'engine_version', '')} "
                         f"from run {manifest.run_id}; the engine proposes and only a named "
                         "reviewer decides.")},
    )
    document.add(_cover(result, engagement, synthetic))
    document.add(_headline(result, view))
    document.add(_estate(result))
    document.add(_gates(result))
    document.add(_top_candidates(result, view))
    document.add(_coverage(result))
    document.add(_value(result, view))
    document.add(_waves(view))
    document.add(_retirement(result))
    document.add(_risks(result, view))
    document.add(_status(view))
    document.add(_since_last_run(result, store))
    document.add(_open_decisions(result, store))
    document.add(_asks(result, view))
    document.add(_method(result, view))
    return document


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def _cover(result: Any, engagement: Engagement, synthetic: bool) -> Section:
    manifest = result.manifest
    section = Section("cover", "Engagement")
    rows = list(engagement.cover_rows())
    rows += [("Run", manifest.run_id), ("Estate as of", manifest.as_of_date)]
    if engagement.as_of_override and engagement.as_of_override != manifest.as_of_date:
        rows.append(("Reporting date on the cover", engagement.as_of_override))
    rows += [
        ("Ingestion mode", f"{manifest.mode}"
                           f"{' (' + manifest.industry + ' pack)' if manifest.industry else ''}"),
        ("Catalog of record", manifest.catalog),
        ("Run published", "yes - every quality gate passed" if manifest.published
                          else "no - see the quality-gate section"),
        ("Confidentiality", engagement.confidentiality),
    ]
    section.add(definitions(rows))
    if synthetic:
        section.add(callout(
            "This run was produced from a generated demonstration pack, not from the client's "
            f"Cognos, Power BI or catalog extracts (generation id {manifest.generation_id or 'n/a'}). "
            "Every name, owner and figure is fabricated. Nothing in this pack may be imported "
            "into a catalog, sent to a report owner or quoted as a finding.",
            tone="danger", title="Synthetic demonstration data"))
    section.add(callout(
        "The engine proposes; it never decides. Every candidate in this pack is at most "
        "'Proposed', every drafted name and decision is marked AI_DRAFT, and only a named "
        "reviewer's decision moves anything forward.", tone="note", title="How to read this pack"))
    return section


def _headline(result: Any, view: EnrichmentView) -> Section:
    section = Section("headline", "The run in five numbers")
    stats = result.manifest.stats or {}
    canonical = (stats.get("canonicalization") or {})
    retirement = _retirement_figures(result)
    items = [
        {"label": "Candidates proposed", "value": len(result.candidates),
         "note": _status_note(result)},
        {"label": "Reports it would clear",
         "value": retirement.get("reports_retirable", 0),
         "note": f"of {retirement.get('reports_in_estate', 0)} in the estate, counted once each"},
        {"label": "Usage covered by the top 20",
         "value": percent(stats.get("usage_coverage_top_n", 0.0), 1),
         "note": "usage-weighted KPI consumption"},
        {"label": "Competing definitions found", "value": canonical.get("conflicts", 0),
         "note": "each one is a steward decision, not an engine decision"},
    ]
    portfolio = view.value_portfolio
    if portfolio:
        items.append({
            "label": "Attributed annual benefit",
            "value": money(portfolio.get("attributed_annual_benefit"), view.currency),
            "note": f"illustrative, assumptions {view.assumption_version or 'unversioned'}"})
    section.add(kpis(items))
    section.add(paragraph(_headline_sentence(result, view, retirement)))
    if view.missing():
        section.add(paragraph(
            "Not computed for this pack: " + "; ".join(view.missing()) +
            ". Those sections are omitted rather than estimated."))
    return section


def _headline_sentence(result: Any, view: EnrichmentView, retirement: dict) -> str:
    ranked = result.ranked()
    proposed = [c for c in ranked if c.status == "Proposed"]
    lead = ranked[0] if ranked else None
    parts = [
        f"The engine grouped this estate into {len(ranked)} data product candidates, of which "
        f"{len(proposed)} clear every hard gate and are Proposed."
    ]
    if lead is not None:
        band = labels.composite_band(lead.score.composite if lead.score else 0.0)
        parts.append(
            f"The strongest is {lead.proposed_name} ({lead.candidate_id}), scoring "
            f"{(lead.score.composite if lead.score else 0.0):.1f} out of 100 - {band['band']}: "
            f"{band['meaning']}")
    parts.append(
        f"Together the candidates cover {retirement.get('reports_fully_covered_distinct', 0)} "
        f"reports completely, of which {retirement.get('reports_retirable', 0)} can be retired "
        f"or merged once their product publishes and {retirement.get('reports_on_hold', 0)} are "
        "held because they are decision-critical or regulatory.")
    return " ".join(parts)


def _status_note(result: Any) -> str:
    mix: dict[str, int] = {}
    for candidate in result.candidates:
        mix[candidate.status] = mix.get(candidate.status, 0) + 1
    return ", ".join(f"{count} {labels.label('status', status)}"
                     for status, count in sorted(mix.items()))


def _estate(result: Any) -> Section:
    stats = result.manifest.stats or {}
    graph_stats = stats.get("graph") or {}
    canonical = stats.get("canonicalization") or {}
    clustering = stats.get("clustering") or {}
    section = Section("estate", "The estate we measured")
    section.add(paragraph(
        "These are counts of what the extracts contained, before any judgement about value. "
        "They are the baseline the programme will be measured against."))
    section.add(table(
        ["What", "Count", "What it means"],
        [
            ["Reports", stats.get("reports", 0),
             "Cognos reports and Power BI reports in the rationalization and lineage extracts"],
            ["Semantic containers", graph_stats.get("packages", graph_stats.get("tables", 0)),
             "Cognos FM packages and Power BI semantic models behind those reports"],
            ["KPI definitions", stats.get("kpi_rows", 0),
             "One row per calculation as it is defined in a report or a model"],
            ["Canonical metrics", canonical.get("canonical_metrics", 0),
             "Distinct calculations after identical definitions were merged by fingerprint"],
            ["Competing definitions", canonical.get("conflicts", 0),
             "Same label, different calculation: a steward must choose"],
            ["Catalog columns", stats.get("catalog_columns", 0),
             "Columns in the Collibra or Alation extract"],
            ["Unresolved lineage rows", graph_stats.get("quarantined", 0),
             "Lineage the catalog could not confirm; listed in the gap register"],
            ["Candidates", clustering.get("candidates", len(result.candidates)),
             "Proposed products; the backlog in this pack"],
        ]))
    duplication = canonical.get("duplication_ratio")
    if duplication is not None:
        section.add(paragraph(
            f"{percent(duplication, 0)} of the KPI definitions in this estate collapse into a "
            "smaller set of canonical metrics - that is the duplication the programme exists "
            "to remove."))
    return section


def _gates(result: Any) -> Section:
    manifest = result.manifest
    section = Section("gates", "Can this run be trusted?")
    gates = manifest.quality_gates or []
    failed = [g for g in gates if not g.get("passed")]
    if manifest.published and not failed:
        section.add(callout(
            "Every run quality gate passed, so the full backlog in this pack is publishable "
            "and can be taken to the council as it stands.", tone="ok", title="Verdict"))
    elif failed:
        names = ", ".join(labels.label("quality_gate", g.get("gate", "")) for g in failed)
        section.add(callout(
            f"{len(failed)} of {len(gates)} quality gates failed ({names}). The run is "
            "published only as a gap list: the ranking in this pack is indicative and must not "
            "be used to commit build capacity until the underlying metadata is corrected.",
            tone="danger", title="Verdict"))
    else:
        section.add(callout(
            "The run was not published. Treat every figure in this pack as provisional.",
            tone="warning", title="Verdict"))
    rows = []
    for gate in gates:
        key = gate.get("gate", "")
        rows.append([
            labels.label("quality_gate", key),
            "Passed" if gate.get("passed") else "Failed",
            _gate_value(gate),
            f"threshold {cell_text(gate.get('threshold'))}",
            labels.explanation("quality_gate", key) or gate.get("detail", ""),
        ])
    section.add(table(["Gate", "Verdict", "Measured", "Required", "What it checks"], rows))
    if manifest.warnings:
        section.add(bullets(list(manifest.warnings)[:10], lead="Warnings raised by this run:"))
    return section


def _gate_value(gate: dict) -> str:
    value = gate.get("value")
    if isinstance(value, float) and 0.0 <= value <= 1.0:
        return percent(value, 2)
    return cell_text(value)


def _top_candidates(result: Any, view: EnrichmentView) -> Section:
    from ..portfolio.views import attribute_reports
    section = Section("top", f"The top {TOP_N} candidates")
    ranked = result.ranked()[:TOP_N]
    attribution = attribute_reports(result.candidates)
    section.add(paragraph(
        "Ranked by composite score (0-100), which combines demand, consolidation and "
        "feasibility and subtracts risk under weight version "
        f"{result.manifest.weight_version}. The score orders the backlog; it does not "
        "authorise anything."))
    columns = ["#", "Candidate", "Status", "Score", "Band", "Retirable reports", "Users",
               "Conflicts to settle"]
    with_value = bool(view.value)
    with_wave = bool(view.waves)
    if with_value:
        columns.append("Annual benefit")
    if with_wave:
        columns.append("Wave")
    rows = []
    for index, candidate in enumerate(ranked, start=1):
        composite = candidate.score.composite if candidate.score else 0.0
        figures = _candidate_reports(candidate, result, attribution)
        row = [
            index,
            f"{candidate.proposed_name}"
            f"{' (AI_DRAFT name)' if candidate.name_status == 'AI_DRAFT' else ''} "
            f"[{candidate.candidate_id}]",
            labels.label("status", candidate.status),
            f"{composite:.1f}",
            labels.composite_band(composite)["band"],
            figures["retirable"],
            figures["users"],
            len(candidate.conflicts),
        ]
        if with_value:
            row.append(money(view.value_for(candidate.candidate_id)
                             .get("attributed_annual_benefit"), view.currency))
        if with_wave:
            wave = view.wave_of(candidate.candidate_id)
            row.append(f"Wave {wave}" if wave else "not scheduled")
        rows.append(row)
    section.add(table(columns, rows, empty="This run produced no candidates."))
    section.add(paragraph(
        "'Retirable reports' counts only reports this candidate covers completely and whose "
        "disposition allows retirement, each attributed to one candidate so the same report is "
        "never counted twice across the backlog. 'Users' is the distinct users behind them."))
    return section


def _candidate_reports(candidate: Any, result: Any,
                      attribution: dict[str, str]) -> dict[str, int]:
    """Retirable reports and users for one candidate, attributed and disposition-aware.

    ``attribution`` is computed once per section rather than cached on the run,
    because the RunResult is shared and a pack must not mutate it.
    """
    from ..portfolio.views import is_hold, is_retirable
    retirable = 0
    users = 0
    for report in candidate.reports:
        if attribution.get(report.report_id) != candidate.candidate_id:
            continue
        hold = is_hold(result.graph.reports.get(report.report_id), report.report_name)
        if is_retirable(report, hold):
            retirable += 1
            users += report.users
    return {"retirable": retirable, "users": users}


def _coverage(result: Any) -> Section:
    section = Section("coverage", "How much of the estate the backlog covers")
    curve = (result.portfolio or {}).get("coverage_curve") or []
    if not curve:
        return section
    marks = [1, 5, 10, 20]
    rows = []
    for mark in marks:
        point = next((p for p in curve if p["n"] == mark), None)
        if point is None:
            continue
        rows.append([f"Top {mark}", percent(point["cumulative_coverage"], 1),
                     point["metrics_covered"], point["candidate"]])
    last = curve[-1]
    rows.append([f"All {last['n']}", percent(last["cumulative_coverage"], 1),
                 last["metrics_covered"], "whole backlog"])
    section.add(table(
        ["Candidates", "Usage-weighted KPI consumption covered", "Canonical metrics covered",
         "Last candidate added"], rows,
        caption="Coverage is cumulative: how much of what the estate actually consumes would "
                "be served by building the first N candidates."))
    top20 = next((p["cumulative_coverage"] for p in curve if p["n"] == 20), None)
    if top20 is not None:
        section.add(paragraph(
            f"Building the top 20 candidates would serve {percent(top20, 1)} of the estate's "
            "usage-weighted KPI consumption. This is a coverage figure, not a benefit figure: "
            "it says how much of what people read today would come from a certified product."))
    return section


def _value(result: Any, view: EnrichmentView) -> Section | None:
    portfolio = view.value_portfolio
    if not portfolio:
        return None
    section = Section("value", "What the backlog is worth")
    basis = view.value_basis.strip().rstrip(".")
    section.add(callout(
        f"Every figure below is illustrative and carries assumption version "
        f"{view.assumption_version or 'unversioned'}"
        + (f" ({basis})" if basis else "")
        + ". The client owns these assumptions: contest the rate card, not the arithmetic.",
        tone="warning", title="Assumptions, not promises"))
    section.add(kpis([
        {"label": "Attributed annual benefit",
         "value": money(portfolio.get("attributed_annual_benefit"), view.currency),
         "note": "double counting removed across candidates"},
        {"label": "Double counting removed",
         "value": money(portfolio.get("double_count_removed"), view.currency),
         "note": f"from a standalone sum of "
                 f"{money(portfolio.get('standalone_sum_annual_benefit'), view.currency)}"},
        {"label": "Indicative build cost",
         "value": money(portfolio.get("build_cost"), view.currency),
         "note": "from the effort model, at the engagement rate card"},
        {"label": "Three-year NPV", "value": money(portfolio.get("npv_3y"), view.currency),
         "note": "benefit less build cost, discounted"},
    ]))
    by_component = portfolio.get("by_component") or {}
    if by_component:
        section.add(table(
            ["Benefit component", "Annual", "What it assumes"],
            [[_component_label(name), money(amount, view.currency), _component_basis(name)]
             for name, amount in by_component.items()],
            caption="Components are attributed once: a report retired by two candidates is "
                    "counted for one of them only."))
    section.add(paragraph(
        f"Counted once across the portfolio: {cell_text(portfolio.get('reports_counted_once', 0))} "
        f"reports and {cell_text(portfolio.get('conflicts_counted_once', 0))} conflicts. "
        "Benefit from candidates that are not yet Proposed is excluded from the headline."))
    return section


_COMPONENT_LABELS = {
    "report_retirement": ("Report maintenance avoided",
                          "maintenance hours per retired report at the loaded rate"),
    "licence": ("Licence and refresh cost avoided", "per-report tool licence and refresh cost"),
    "infrastructure": ("Infrastructure avoided", "storage and compute behind retired extracts"),
    "conflict_reconciliation": ("Reconciliation effort avoided",
                                "analyst hours spent reconciling competing definitions"),
    "mis_decision_avoidance": ("Mis-decision exposure reduced",
                               "the most contested assumption: value of decisions taken on an "
                               "agreed number"),
}


def _component_label(name: str) -> str:
    return _COMPONENT_LABELS.get(name, (name.replace("_", " ").capitalize(), ""))[0]


def _component_basis(name: str) -> str:
    return _COMPONENT_LABELS.get(name, ("", "see the method appendix"))[1]


def _waves(view: EnrichmentView) -> Section | None:
    if not view.waves:
        return None
    section = Section("waves", "The delivery plan")
    config = view.wave_config
    section.add(paragraph(
        f"{len(view.waves)} waves of at most "
        f"{cell_text(config.get('products_per_wave', ''))} products, "
        f"{cell_text(config.get('wave_length_weeks', ''))} weeks each, sequenced so that no "
        "product starts before what it depends on. This is a capacity plan, not a commitment."))
    rows = []
    for wave in view.waves:
        names = [c.get("name", c.get("candidate_id", "")) for c in wave.get("candidates", [])]
        benefit = sum(float(c.get("annual_benefit") or 0.0) for c in wave.get("candidates", []))
        points = sum(float(c.get("points") or 0.0) for c in wave.get("candidates", []))
        rows.append([
            f"Wave {wave.get('wave')}",
            f"weeks {wave.get('starts_week')}-{wave.get('ends_week')}",
            len(names),
            money(benefit, view.currency),
            f"{points:.0f}",
            "; ".join(names[:4]) + (" ..." if len(names) > 4 else ""),
        ])
    section.add(table(["Wave", "Timing", "Products", "Annual benefit", "Effort points",
                       "Products in the wave"], rows))
    if view.unscheduled:
        section.add(table(
            ["Candidate", "Why it is not scheduled", "What would release it"],
            [[f"{u.get('name', '')} [{u.get('candidate_id', '')}]", u.get("reason", ""),
              u.get("release_hint", "") or "a reviewer decision"]
             for u in view.unscheduled],
            caption="Not in any wave. Each needs a decision or a correction before it can be "
                    "planned."))
    return section


def _retirement(result: Any) -> Section:
    figures = _retirement_figures(result)
    section = Section("retirement", "What the backlog retires")
    section.add(paragraph(
        "Every report below is counted once, against the single candidate that covers it best, "
        "so these figures can be added up. A report is only retirable if its disposition allows "
        "it and it is neither decision-critical nor a regulatory filing."))
    section.add(table(
        ["Measure", "Reports", "What it means"],
        [
            ["In the estate", figures.get("reports_in_estate", 0), "every report ingested"],
            ["Covered completely", figures.get("reports_fully_covered_distinct", 0),
             "every number on the report is certified by one candidate"],
            ["Retirable or mergeable", figures.get("reports_retirable", 0),
             "switched off or folded in once its product publishes"],
            ["Kept and re-pointed", figures.get("reports_keep_repointed", 0),
             "the report stays and reads from the product instead of its current source"],
            ["Held", figures.get("reports_on_hold", 0),
             "decision-critical or regulatory: nothing changes without a separate decision"],
        ]))
    by_disposition = figures.get("retirable_by_disposition") or {}
    if by_disposition:
        section.add(bullets(
            [f"{count} report(s) whose disposition is already '{disposition}'"
             for disposition, count in by_disposition.items()],
            lead="The retirable reports, by the disposition the rationalization extract "
                 "already carries:"))
    section.add(paragraph(
        f"{cell_text(figures.get('users_on_retirable_reports', 0))} distinct users read the "
        f"retirable reports and must be notified before any cut-over. "
        f"{len(figures.get('packages_fully_retirable') or [])} semantic container(s) could be "
        f"retired whole and {len(figures.get('packages_partially_affected') or [])} would be "
        "partially affected."))
    claimed = figures.get("reports_fully_covered_claimed", 0)
    distinct = figures.get("reports_fully_covered_distinct", 0)
    if claimed > distinct:
        section.add(callout(
            f"Candidates claim {cell_text(claimed)} fully covered reports between them, but only "
            f"{cell_text(distinct)} distinct reports are involved: {cell_text(claimed - distinct)} "
            "claims are the same report covered by more than one candidate. This pack always "
            "reports the de-duplicated figure.", tone="note", title="Why two numbers differ"))
    return section


def _retirement_figures(result: Any) -> dict[str, Any]:
    figures = (result.portfolio or {}).get("estate_retirement")
    if isinstance(figures, dict) and figures:
        return figures
    return estate_retirement(result.candidates, result.graph)


def _risks(result: Any, view: EnrichmentView) -> Section:
    section = Section("risks", "What is in the way")
    risks = view.raid_of_type("Risk") + view.raid_of_type("Issue")
    if risks:
        rows = [[r.get("title", ""), labels.label("severity", r.get("severity", "")),
                 r.get("owner_role", ""),
                 len(r.get("candidate_ids") or []), r.get("detail", "")[:240]]
                for r in risks[:12]]
        section.add(table(
            ["Risk or issue", "Severity", "Owner role", "Candidates affected", "Detail"], rows,
            caption="From the RAID log; the full log is a tab in the backlog workbook."))
        return section
    section.add(paragraph(
        "No RAID log was supplied with this pack, so the risks below are read directly off "
        "the run."))
    section.add(table(["Risk", "Evidence in this run", "Owner role"], _derived_risks(result)))
    return section


def _derived_risks(result: Any) -> list[list[Any]]:
    stats = result.manifest.stats or {}
    graph_stats = stats.get("graph") or {}
    canonical = stats.get("canonicalization") or {}
    rows: list[list[Any]] = []
    blocked = [c for c in result.candidates if c.status == "Blocked"]
    if blocked:
        rows.append(["Candidates blocked by a sunset source with no successor",
                     f"{len(blocked)} candidate(s): "
                     + ", ".join(c.candidate_id for c in blocked[:5]), "Catalog admin"])
    exploratory = [c for c in result.candidates if c.status == "Exploratory"]
    if exploratory:
        rows.append(["Candidates without a confirmed consumer or sufficient lineage",
                     f"{len(exploratory)} Exploratory candidate(s) awaiting a reviewer",
                     "Data product council"])
    definition_coverage = graph_stats.get("definition_coverage")
    if definition_coverage is not None and definition_coverage < 0.6:
        rows.append(["Catalog definitions are sparse, so metrics cannot be signed off",
                     f"only {percent(definition_coverage, 0)} of columns carry a term and a "
                     "definition", "Domain steward"])
    opaque = canonical.get("opaque_metrics")
    if opaque:
        rows.append(["Calculations the parser could not read need manual definitions",
                     f"{cell_text(opaque)} opaque metric(s)", "Engine team"])
    unassigned = sum(1 for c in getattr(result.canonical, "conflicts", [])
                     if not c.steward_id and c.resolution_status == "OPEN")
    if unassigned:
        rows.append(["Open conflicts with no steward to adjudicate them",
                     f"{unassigned} conflict(s) have no steward in the catalog",
                     "Data product council"])
    ai_names = sum(1 for m in result.canonical.metrics.values() if m.name_status == "AI_DRAFT")
    if ai_names:
        rows.append(["Drafted names entering the semantic layer unread",
                     f"{ai_names} metric name(s) are AI_DRAFT and blocked from catalog import "
                     "until a steward accepts them", "Domain steward"])
    if not rows:
        rows.append(["No material risk read off this run", "every gate passed and nothing is "
                     "blocked", "Programme manager"])
    return rows


def _status(view: EnrichmentView) -> Section | None:
    measures = view.measures
    if not measures:
        return None
    section = Section("status", "Progress against the 90-day success measures")
    section.add(paragraph(
        "The five measures the programme committed to at 90 days after Phase 2 "
        "(specification section 14.2). Grey means the measure cannot move until reviewers "
        "start deciding."))
    section.add(table(
        ["Measure", "Now", "Target", "Status", "Note"],
        [[m.get("measure", m.get("key", "")), _measure_value(m, "actual"),
          _measure_value(m, "target"), str(m.get("rag", "")).upper(),
          m.get("note", "")]
         for m in measures]))
    phase_exit = view.status.get("phase_exit") or []
    if phase_exit:
        section.add(bullets(
            [f"{cell_text(p.get('criterion', ''))}: {cell_text(p.get('verdict', p.get('met', '')))}"
             for p in phase_exit[:8]],
            lead="Phase exit criteria:"))
    return section


def _measure_value(measure: dict, key: str) -> str:
    """A 14.2 measure with its unit, so 0.4 reads as 40% and 500 reads as 500 reports."""
    value = measure.get(key)
    if value in (None, ""):
        return "not measurable yet"
    unit = str(measure.get("unit", "")).lower()
    if unit in ("share", "ratio", "percent", "%"):
        return percent(value, 1)
    if unit in ("", "count", "n"):          # a bare number needs no noun after it
        return cell_text(value)
    return f"{cell_text(value)} {unit}".strip()


def _since_last_run(result: Any, store: Any) -> Section | None:
    if store is None:
        return None
    try:
        deltas = store.run_delta(result.manifest.run_id)
    except Exception:                      # a pack must never fail on a missing table
        return None
    if not deltas:
        return None
    section = Section("delta", "Since the last run")
    counts: dict[str, int] = {}
    for row in deltas:
        change = str(row.get("change", "unknown"))
        counts[change] = counts.get(change, 0) + 1
    section.add(bullets(
        [f"{count} candidate(s) {_change_words(change)}" for change, count in sorted(counts.items())],
        lead=f"Compared with run {deltas[0].get('previous_run_id', '')}:"))
    carried = [r for r in deltas if r.get("status_carried")]
    if carried:
        section.add(table(
            ["Candidate", "Change", "Status carried forward", "Detail"],
            [[r.get("candidate_id", ""), _change_words(r.get("change", "")),
              r.get("status_carried", ""), r.get("detail", "")] for r in carried[:15]],
            caption="A reviewer decision made on a previous run is carried to the matching "
                    "candidate in this one; the engine never re-decides it."))
    return section


_CHANGE_WORDS = {
    "unchanged": "matched a candidate in the previous run unchanged",
    "new": "are new in this run",
    "dropped": "disappeared since the previous run",
    "changed": "matched a previous candidate with a changed metric set",
    "split": "were split from a previous candidate",
    "merged": "were merged from previous candidates",
}


def _change_words(change: str) -> str:
    return _CHANGE_WORDS.get(str(change).lower(), str(change))


def _open_decisions(result: Any, store: Any) -> Section:
    config = result.config
    weights = getattr(config, "weights", None)
    cluster = getattr(config, "cluster", None)
    tools = sorted({t for c in result.candidates for t in (c.tools or [])})
    assumed = {
        "D-01": f"{getattr(config, 'usage_window_months', '')} months, with a "
                f"{getattr(config, 'recency_half_life_months', '')}-month recency half-life",
        "D-02": (f"at least {getattr(cluster, 'min_metrics', '')} canonical metrics and "
                 f"{getattr(cluster, 'min_business_units', '')} business unit(s)"
                 if cluster else "engine default"),
        "D-03": ("yes - Keep reports count toward consolidation"
                 if getattr(config, "keep_counts_toward_consolidation", False)
                 else "no - Keep reports are excluded from consolidation"),
        "D-04": (f"weight version {getattr(weights, 'weight_version', '')}, approved by "
                 f"{getattr(weights, 'approved_by', '') or 'nobody yet'}" if weights else ""),
        "D-05": ("one graph: " + ", ".join(tools)) if tools else "one graph over both tools",
        "D-06": f"review above sensitivity {getattr(config, 'sensitivity_review_threshold', '')}",
        "D-07": f"payload written for {result.manifest.catalog}, import blocked while AI_DRAFT",
        "D-08": _acceptance_position(store, result.manifest.run_id),
    }
    section = Section("decisions", "Open decisions for the council")
    section.add(paragraph(
        "Eight decisions are the client's, not the engine's. The engine ran on a stated "
        "position for each; the council either confirms it or changes it, and the ranking "
        "is re-cut. Nothing here is a defect."))
    section.add(table(
        ["#", "Decision", "What this run assumed", "Why it matters", "Proposed owner"],
        [[ref, decision, assumed.get(ref, ""), why, owner]
         for ref, decision, why, owner in OPEN_DECISIONS]))
    return section


def _acceptance_position(store: Any, run_id: str) -> str:
    if store is None:
        return "no reviewer roles configured; every decision records the named reviewer"
    try:
        decisions = store.decisions(run_id)
    except Exception:
        return "no reviewer roles configured; every decision records the named reviewer"
    if not decisions:
        return "no decisions recorded yet on this run"
    roles = sorted({str(d.get("actor_role") or "reviewer") for d in decisions})
    return (f"{len(decisions)} decision(s) recorded by role(s): {', '.join(roles)}")


def _asks(result: Any, view: EnrichmentView) -> Section:
    section = Section("asks", "What we are asking for")
    ranked = result.ranked()
    proposed = [c for c in ranked if c.status == "Proposed"]
    exploratory = [c for c in ranked if c.status == "Exploratory"]
    open_conflicts = [c for c in getattr(result.canonical, "conflicts", [])
                      if c.resolution_status == "OPEN"]
    stewards = sorted({c.steward_id for c in open_conflicts if c.steward_id})
    asks = [
        f"**Review the top {min(TOP_N, len(proposed))} Proposed candidates** and record an "
        "Accept, Reject, Merge, Split or Defer against each, with a reason code and your name. "
        "Nothing moves past Proposed without it.",
        f"**Confirm a consumer for {len(exploratory)} Exploratory candidate(s)**, or accept "
        "that they stay out of the plan.",
        f"**Adjudicate {len(open_conflicts)} open conflicting definition(s)** across "
        f"{len(stewards)} named steward(s); the adjudication requests are in the "
        "communications pack, one per steward.",
        "**Accept or correct the AI_DRAFT names** on the metrics and candidates you intend to "
        "build; a drafted name is blocked from the catalog until a steward accepts it.",
        "**Confirm the retirement list with each report owner**; the owner notifications are "
        "in the communications pack and carry the contest path.",
    ]
    if view.value:
        asks.append(
            f"**Contest or confirm the value assumptions** ({view.assumption_version}); the "
            "benefit figures are only as good as the rate card behind them.")
    asks.append("**Settle the eight open decisions** in the previous section, or confirm the "
                "positions this run assumed.")
    section.add(bullets(asks))
    return section


def _method(result: Any, view: EnrichmentView) -> Section:
    manifest = result.manifest
    config = result.config
    section = Section("method", "Method and reproducibility")
    section.add(paragraph(
        "The run is reproducible: the same extracts, the same versions and the same as-of date "
        "produce the same ranking. Nothing in scoring or clustering reads the clock or a random "
        "number. The full weight set and every threshold are in the Method appendix tab of the "
        "backlog workbook."))
    section.add(definitions([
        ("Engine version", getattr(config, "engine_version", "")),
        ("Score weight version", manifest.weight_version),
        ("Expression parser version", manifest.parser_version),
        ("Usage window", f"{getattr(config, 'usage_window_months', '')} months"),
        ("Extracts", ", ".join(manifest.extract_ids[:6]) or "n/a"),
        ("Generation id", manifest.generation_id or "n/a (not a generated pack)"),
        ("Value assumptions", view.assumption_version or "not computed for this pack"),
        ("Language model", "not used - every draft came from a deterministic template"
            if not getattr(config, "ai_enabled", False)
            else f"{getattr(config, 'ai_model', '')} (drafts marked AI_DRAFT; see the AI ledger)"),
    ]))
    section.add(paragraph(
        "Score features and gates use the vocabulary in the glossary: every gate, feature, "
        "status, origin and reason code in this pack has a plain-English label and a one-line "
        "explanation, published by the engine as one dictionary."))
    return section
