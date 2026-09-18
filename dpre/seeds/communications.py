"""Communications the engine's three counterparties can act on (review finding R-37).

Every ingredient is already on the candidate record: the retirement list knows
the owner, users, coverage and action; the conflict register knows the steward,
both expressions and the difference; the Stage 1 draft knows the persona and
the inferred decision. This module turns them into one Markdown note per
recipient, so the 14.2 targets (>= 500 reports on a retirement list with a
named owner, >= 30 conflicts adjudicated) rest on outreach the run produces
rather than outreach the engagement team improvises.

Three notes:

* ``owner-notification-<owner>.md``: the owner's covered reports, coverage, the
  candidate replacing each, users affected, the disposition-aware action, how
  to contest, and the decision-critical option (specification section 15.1).
* ``steward-adjudication-<steward>.md``: the steward's open conflicts ranked by
  usage at stake, both expressions, the difference, the Stage 6 decision the
  pattern implies, and the AI-drafted names awaiting acceptance.
* ``consumer-confirmation-<business_unit>.md``: the persona, the inferred
  decision and the three questions Stage 1 reserves for the consumer.

Nothing here decides anything: every note says who must act and marks every
inference AI_DRAFT. A synthetic run puts the demonstration banner at the top
of every note (review finding R-36).
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..labels import label
from ..models import Candidate
from ..portfolio.views import attribute_reports, is_hold, is_retirable, retirement_action
from .provenance import SYNTHETIC_BANNER

UNASSIGNED = "UNASSIGNED"
MAX_CONFLICTS_PER_STEWARD = 40


def build_communications(result: Any) -> dict[str, dict[str, str]]:
    """``{"owners": {name: md}, "stewards": {name: md}, "consumers": {unit: md}}``."""
    context = _Context(result)
    return {
        "owners": {owner: owner_notification(owner, rows, context)
                   for owner, rows in sorted(_reports_by_owner(context).items())},
        "stewards": {steward: steward_adjudication(steward, rows, context)
                     for steward, rows in sorted(_conflicts_by_steward(context).items())},
        "consumers": {unit: consumer_confirmation(unit, drafts, context)
                      for unit, drafts in sorted(_drafts_by_unit(context).items())},
    }


def write_communications(result: Any, out_dir: str | Path) -> list[Path]:
    """Write every note under ``out_dir`` and return the paths, in a stable order."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    notes = build_communications(result)
    prefixes = {"owners": "owner-notification", "stewards": "steward-adjudication",
                "consumers": "consumer-confirmation"}
    index_lines = ["# Communications for run " + result.run_id, ""]
    if _synthetic(result):
        index_lines.insert(1, f"> {SYNTHETIC_BANNER}")
        index_lines.insert(2, "")
    for group, prefix in prefixes.items():
        index_lines.append(f"## {group.title()}")
        index_lines.append("")
        for recipient, markdown in notes[group].items():
            path = out / f"{prefix}-{slug(recipient)}.md"
            path.write_text(markdown, encoding="utf-8")
            written.append(path)
            index_lines.append(f"- [{recipient}]({path.name})")
        index_lines.append("")
    index = out / "README.md"
    index.write_text("\n".join(index_lines), encoding="utf-8")
    written.append(index)
    return written


def slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", (value or "unassigned")).strip("-").lower()
    return text[:60] or "unassigned"


# --------------------------------------------------------------------------
# shared context
# --------------------------------------------------------------------------

class _Context:
    """What every note reads: the candidates, the graph, attribution and the run."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.candidates: list[Candidate] = list(result.candidates)
        self.by_id = {c.candidate_id: c for c in self.candidates}
        self.graph = result.graph
        self.canonical = result.canonical
        self.attribution = attribute_reports(self.candidates)
        self.run_id = result.run_id
        self.as_of = getattr(result.manifest, "as_of_date", "")
        self.synthetic = _synthetic(result)
        self.metric_names = {m.metric_id: m.canonical_name for m in self.canonical.metrics.values()}

    def banner(self) -> list[str]:
        if not self.synthetic:
            return []
        return [f"> **{SYNTHETIC_BANNER}**", ""]

    def footer(self) -> list[str]:
        return ["", "---", f"Run `{self.run_id}`, data as of {self.as_of}. Every name and "
                "inference marked AI_DRAFT is a draft until a named person accepts it; the "
                "engine proposes and only a reviewer decides."]


def _synthetic(result: Any) -> bool:
    manifest = getattr(result, "manifest", None)
    if manifest is not None and getattr(manifest, "synthetic", None) is not None:
        return bool(manifest.synthetic)
    return any(r.synthetic for r in result.graph.reports.values())


def _name(candidate: Candidate) -> str:
    mark = " (AI_DRAFT)" if candidate.name_status == "AI_DRAFT" else ""
    return f"{candidate.proposed_name}{mark}"


# --------------------------------------------------------------------------
# owner notifications
# --------------------------------------------------------------------------

def _reports_by_owner(context: _Context) -> dict[str, list[dict]]:
    """Each fully covered report once, for its primary candidate, by owner."""
    rows: dict[str, list[dict]] = defaultdict(list)
    for report_id, candidate_id in sorted(context.attribution.items()):
        candidate = context.by_id[candidate_id]
        report = next(r for r in candidate.reports if r.report_id == report_id)
        record = context.graph.reports.get(report_id)
        hold = is_hold(record, report.report_name)
        rows[report.owner or UNASSIGNED].append({
            "report": report, "candidate": candidate, "hold": hold,
            "action": retirement_action(report, hold),
            "retirable": is_retirable(report, hold),
            "also_covered": [c.candidate_id for c in context.candidates
                             if c.candidate_id != candidate_id
                             and any(r.report_id == report_id and r.coverage >= 1.0
                                     for r in c.reports)],
        })
    for owner_rows in rows.values():
        owner_rows.sort(key=lambda r: (-r["report"].users, r["report"].report_id))
    return rows


def owner_notification(owner: str, rows: list[dict], context: _Context) -> str:
    reports = [r["report"] for r in rows]
    users = sum(r.users for r in reports)
    retirable = [r for r in rows if r["retirable"]]
    holds = [r for r in rows if r["hold"]]
    candidates = sorted({r["candidate"].candidate_id for r in rows})
    lines = [f"# Retirement notice for {owner}", ""]
    lines += context.banner()
    lines += [
        f"**To:** {owner} (report owner)  ",
        f"**From:** Data product programme, run `{context.run_id}` as of {context.as_of}  ",
        f"**Why you are receiving this:** {len(reports)} report(s) you own are fully covered by "
        f"{len(candidates)} proposed data product candidate(s). Nothing is retired by this "
        "note; it asks you to check the list and reply.",
        "",
        "## Summary",
        "",
        f"- Reports fully covered: **{len(reports)}**, used by **{users}** distinct users in "
        "the last 12 months",
        f"- Would be retired or rebuilt once the product publishes: **{len(retirable)}**",
        f"- Held whatever the disposition (decision-critical or regulatory): **{len(holds)}**",
        f"- Kept and re-pointed at the product: **{len(rows) - len(retirable) - len(holds)}**",
        "",
        "## Your reports",
        "",
        "| Report | Users | Disposition | Coverage | Replacing candidate | Action | Also covered by |",
        "| --- | ---: | --- | ---: | --- | --- | --- |",
    ]
    for row in rows:
        report, candidate = row["report"], row["candidate"]
        lines.append(
            f"| {report.report_name} (`{report.report_id}`) | {report.users} | "
            f"{report.disposition or 'Keep'} | {report.coverage:.0%} | {_name(candidate)} "
            f"(`{candidate.candidate_id}`, {label('status', candidate.status)}) | "
            f"{row['action']} | {', '.join(row['also_covered']) or '-'} |")
    lines += [
        "",
        "## What the action means",
        "",
        "- *retire on publication*: the report's disposition is Retire and every number on it "
        "is certified by the product; it is switched off after the cut-over date you agree.",
        "- *merge and retire*: disposition Merge; its numbers join the product and the report "
        "goes.",
        "- *rebuild on the product*: disposition Migrate; the report is rebuilt on top of the "
        "product, not switched off.",
        "- *re-point source, report retained*: disposition Keep; the report stays and reads "
        "from the product instead of its current source.",
        "- *hold*: decision-critical or a regulatory filing; nothing changes without a "
        "separate decision.",
        "",
        "## How to respond",
        "",
        "1. **Agree**: reply with a notification date and a cut-over date per report, or one "
        "for all; they go on the Stage 12 retirement list, which today says `TO BE SET`.",
        "2. **Contest**: name the report and the number it needs that the candidate does not "
        "certify; the reviewer records a Defer or Split decision with a reason code, and the "
        "report stays on your side until the gap is closed.",
        "3. **Mark decision-critical**: if a report matters more than its run count says "
        "(a filing, a board pack), ask the reviewer to mark it decision-critical; its usage "
        "weight is floored and it is held from retirement (specification section 15.1).",
        "",
        "Reply to the programme reviewer for your domain; decisions are recorded against the "
        "candidate id above with your name and a reason code.",
    ]
    lines += context.footer()
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# steward adjudications
# --------------------------------------------------------------------------

def _conflicts_by_steward(context: _Context) -> dict[str, list[dict]]:
    """Open conflicts per steward, each with the candidates it sits in."""
    candidate_of: dict[str, list[str]] = defaultdict(list)
    for candidate in context.candidates:
        for conflict_id in candidate.conflicts:
            candidate_of[conflict_id].append(candidate.candidate_id)
    rows: dict[str, list[dict]] = defaultdict(list)
    for conflict in context.canonical.conflicts:
        if conflict.resolution_status != "OPEN":
            continue
        rows[conflict.steward_id or UNASSIGNED].append({
            "conflict": conflict,
            "at_stake": round(conflict.usage_weight_a + conflict.usage_weight_b, 2),
            "candidates": sorted(candidate_of.get(conflict.conflict_id, [])),
        })
    for steward_rows in rows.values():
        steward_rows.sort(key=lambda r: (-r["at_stake"], r["conflict"].conflict_id))
    return rows


def _names_awaiting(steward: str, context: _Context) -> list:
    return sorted((m for m in context.canonical.metrics.values()
                   if m.name_status == "AI_DRAFT"
                   and (m.steward_id or UNASSIGNED) == steward),
                  key=lambda m: (-m.usage_weight, m.metric_id))


def steward_adjudication(steward: str, rows: list[dict], context: _Context) -> str:
    awaiting = _names_awaiting(steward, context)
    at_stake = round(sum(r["at_stake"] for r in rows), 2)
    lines = [f"# Adjudication request for {steward}", ""]
    lines += context.banner()
    lines += [
        f"**To:** {steward} (domain steward)  ",
        f"**From:** Data product programme, run `{context.run_id}` as of {context.as_of}  ",
        f"**Why you are receiving this:** {len(rows)} competing definition(s) of numbers in your "
        f"domain are open, with {at_stake:,.0f} units of usage weight at stake, and "
        f"{len(awaiting)} AI-drafted metric name(s) await your acceptance. Nothing enters the "
        "semantic model or the catalog until you decide.",
        "",
    ]
    if steward == UNASSIGNED:
        lines += ["> No steward is assigned to these conflicts in the catalog. The data "
                  "product council must name one before adjudication can start.", ""]
    lines += ["## Conflicts, ranked by usage at stake", ""]
    for index, row in enumerate(rows[:MAX_CONFLICTS_PER_STEWARD], start=1):
        conflict = row["conflict"]
        name_a = context.metric_names.get(conflict.metric_id_a, conflict.metric_id_a)
        name_b = context.metric_names.get(conflict.metric_id_b, conflict.metric_id_b)
        lines += [
            f"### {index}. {conflict.label} - {label('conflict_pattern', conflict.pattern)} "
            f"(`{conflict.conflict_id}`)",
            "",
            f"- Usage at stake: **{row['at_stake']:,.0f}** "
            f"({conflict.usage_weight_a:,.0f} behind A, {conflict.usage_weight_b:,.0f} behind B)",
            f"- Difference: {conflict.difference_summary}",
            f"- Stage 6 decision the pattern implies: **{conflict.semantic_model_decision}**",
            f"- Candidates affected: {', '.join(row['candidates']) or 'none yet'}",
            "",
            f"**A. `{name_a}`** used by {len(conflict.reports_a)} report(s) "
            f"({', '.join(conflict.reports_a[:5]) or 'none listed'})",
            "",
            "```",
            conflict.expression_a or "(expression not captured)",
            "```",
            "",
            f"**B. `{name_b}`** used by {len(conflict.reports_b)} report(s) "
            f"({', '.join(conflict.reports_b[:5]) or 'none listed'})",
            "",
            "```",
            conflict.expression_b or "(expression not captured)",
            "```",
            "",
            "Decide one of: **A is authoritative**, **B is authoritative**, **both readings "
            "stand** (the Stage 6 decision above keeps both under one metric), or "
            "**rename one** so they no longer claim the same label.",
            "",
        ]
    if len(rows) > MAX_CONFLICTS_PER_STEWARD:
        lines += [f"... and {len(rows) - MAX_CONFLICTS_PER_STEWARD} more, in the Conflicts tab "
                  "of the backlog workbook.", ""]
    lines += ["## Metric names awaiting your acceptance", ""]
    if awaiting:
        lines += ["| Canonical name (AI_DRAFT) | Labels seen in reports | Reports | Definition |",
                  "| --- | --- | ---: | --- |"]
        for metric in awaiting[:60]:
            lines.append(f"| `{metric.canonical_name}` | {', '.join(metric.labels[:3])} | "
                         f"{len(metric.report_ids)} | {(metric.definition or 'MISSING')[:120]} |")
    else:
        lines.append("None.")
    lines += [
        "",
        "## How to respond",
        "",
        "Record each decision through the reviewer (`resolve conflict` with your name and the "
        "reading you chose; `accept name` for each metric name). A conflict you resolve here "
        "is carried into every later run by its fingerprint, so you decide once.",
    ]
    lines += context.footer()
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# consumer confirmations
# --------------------------------------------------------------------------

def _drafts_by_unit(context: _Context) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = defaultdict(list)
    for candidate in sorted(context.candidates,
                            key=lambda c: -(c.score.composite if c.score else 0.0)):
        for draft in candidate.decisions_drafted:
            consumer = next((c for c in candidate.consumers
                             if c.business_unit == draft.business_unit), None)
            rows[draft.business_unit or "Unassigned"].append({
                "candidate": candidate, "draft": draft,
                "users": consumer.users if consumer else 0,
                "reports": consumer.report_count if consumer else 0,
                "confidence": getattr(draft, "persona_confidence", None),
            })
    return rows


def consumer_confirmation(unit: str, rows: list[dict], context: _Context) -> str:
    persona = next((r["draft"].persona for r in rows if r["draft"].persona), "")
    confidence = next((r["confidence"] for r in rows if r["confidence"] is not None), None)
    lines = [f"# Consumption confirmation for {unit}", ""]
    lines += context.banner()
    lines += [
        f"**To:** {persona or 'the person in ' + unit + ' who owns the decisions below'}  ",
        f"**From:** Data product programme, run `{context.run_id}` as of {context.as_of}  ",
        f"**Why you are receiving this:** {unit} runs the reports behind {len(rows)} proposed "
        "data product candidate(s). The engine has drafted what it thinks you decide with "
        "those numbers; only you can confirm it.",
        "",
        "## Who we think you are",
        "",
    ]
    if persona:
        lines.append(f"- Persona (AI_DRAFT): **{persona}**, inferred from the business unit "
                     f"name with confidence {confidence if confidence is not None else 0.0:.2f}. "
                     "Correct it if wrong.")
    else:
        lines.append("- Persona: **not inferred** - no role keyword matched your business "
                     "unit's name. Please tell us who owns these decisions.")
    lines += ["", "## Candidates that would serve you", ""]
    for row in rows:
        candidate, draft = row["candidate"], row["draft"]
        lines += [
            f"### {_name(candidate)} (`{candidate.candidate_id}`)",
            "",
            f"- What it is: {candidate.purpose}",
            f"- Your usage: {row['users']} users across {row['reports']} report(s), "
            f"cadence {draft.cadence}",
            "- Questions those reports answer today (AI_DRAFT):",
        ]
        lines += [f"    - {q}" for q in draft.questions]
        lines += [
            f"- Inferred decision (AI_DRAFT): {draft.inferred_decision}",
            "",
        ]
    lines += [
        "## The three questions only you can answer",
        "",
        "Stage 1 of the Data Product Factory cannot exit until the named consumer answers "
        "these; the engine has deliberately left them blank.",
        "",
        "1. **Which decision is blocked or slowed today** because these numbers are not "
        "agreed, not timely or not trusted? (`blocked_decision`)",
        "2. **How stale may the answer be** before it is useless to you: an hour, a day, a "
        "week? (`latency_tolerance`)",
        "3. **What happens if the decision is not made**, or is made on the wrong number: "
        "cost, risk, regulatory exposure? (`consequence_of_not_deciding`)",
        "",
        "Reply to the programme reviewer for your domain, or annotate the Stage 1 "
        "decision-register seed for each candidate above. Your answers are recorded with "
        "your name as a consumer confirmation and clear gate G1 for the candidate.",
    ]
    lines += context.footer()
    return "\n".join(lines) + "\n"
