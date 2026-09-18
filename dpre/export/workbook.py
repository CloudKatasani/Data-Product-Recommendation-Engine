"""The backlog workbook: twelve tabs an engagement team actually works in (R-12).

``dpre.util.xlsx.write_workbook`` existed and was used only for the synthetic
data pack. This module feeds it the run: the ranked backlog, every score
feature with the evidence rows behind it, the canonical metrics, the conflicts,
the de-duplicated retirement map, the gap register, the reviewer decisions, the
wave plan, the RAID log, the stakeholder map, progress against the 14.2
measures, and a method appendix that prints ``EngineConfig.to_dict`` so a
reviewer can contest a weight rather than guess at it.

Tabs whose input is absent (no enrichment, no store) are still written, with
one row saying what would fill them, because an empty tab that explains itself
is better than a missing tab an engagement manager has to ask about.

Every cell is produced by a pure function of the run, so the workbook is
reproducible.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import labels
from ..portfolio.views import attribute_reports, is_hold, is_retirable, retirement_action
from ..util.xlsx import write_workbook
from .blocks import cell_text
from .enrichment import EnrichmentView

TAB_NAMES = ("Candidates", "Scores & Evidence", "Metrics", "Conflicts", "Retirement map",
             "Gap register", "Decisions", "Waves", "RAID", "Stakeholders", "Status",
             "Method appendix")


def build_workbook(result: Any, store: Any = None, enrichment: Any = None
                   ) -> dict[str, list[list[Any]]]:
    """``{tab name: rows}``, row 1 of each being the header."""
    view = EnrichmentView(enrichment)
    attribution = attribute_reports(result.candidates)
    return {
        "Candidates": _candidates(result, view, attribution),
        "Scores & Evidence": _scores(result),
        "Metrics": _metrics(result),
        "Conflicts": _conflicts(result),
        "Retirement map": _retirement(result, attribution),
        "Gap register": _gaps(result),
        "Decisions": _decisions(result, store),
        "Waves": _waves(view),
        "RAID": _raid(view),
        "Stakeholders": _stakeholders(view),
        "Status": _status(view),
        "Method appendix": _method(result, view),
    }


def write_backlog_workbook(result: Any, path: str | Path, store: Any = None,
                           enrichment: Any = None) -> Path:
    """Write the workbook and return its path."""
    return write_workbook(path, build_workbook(result, store, enrichment))


def _note(header: list[str], message: str) -> list[list[Any]]:
    """A tab with nothing to show still says why, on one row under the header."""
    return [header, [message] + [""] * (len(header) - 1)]


# --------------------------------------------------------------------------
# tabs
# --------------------------------------------------------------------------

def _candidates(result: Any, view: EnrichmentView, attribution: dict[str, str]
                ) -> list[list[Any]]:
    header = ["Rank", "Candidate id", "Name", "Name status", "Status", "Composite", "Band",
              "Demand", "Consolidation", "Feasibility", "Risk", "Archetype", "Tier", "Grain",
              "Domain", "Proposed owner", "Proposed steward", "Origin", "Metrics",
              "Business units", "Users", "Reports covered", "Reports retirable",
              "Conflicts open", "PII attributes", "Gates failed", "Annual benefit",
              "Build cost", "Payback months", "Effort size", "Effort weeks", "Wave", "Purpose"]
    rows = [header]
    for index, candidate in enumerate(result.ranked(), start=1):
        score = candidate.score
        composite = score.composite if score else 0.0
        value = view.value_for(candidate.candidate_id)
        effort = view.effort_for(candidate.candidate_id)
        retirable = sum(
            1 for r in candidate.reports
            if attribution.get(r.report_id) == candidate.candidate_id
            and is_retirable(r, is_hold(result.graph.reports.get(r.report_id), r.report_name)))
        failed = [g.gate for g in (score.gates if score else []) if not g.passed]
        rows.append([
            index, candidate.candidate_id, candidate.proposed_name,
            labels.label("name_status", candidate.name_status),
            candidate.status, round(composite, 1), labels.composite_band(composite)["band"],
            round(score.demand, 1) if score else 0.0,
            round(score.consolidation, 1) if score else 0.0,
            round(score.feasibility, 1) if score else 0.0,
            round(score.risk, 1) if score else 0.0,
            candidate.archetype, candidate.tier, candidate.grain, candidate.domain,
            candidate.owner_candidate, candidate.steward_candidate,
            labels.label("origin", candidate.origin),
            len(candidate.metric_ids), len(candidate.consumers),
            sum(c.users for c in candidate.consumers),
            sum(1 for r in candidate.reports if r.coverage >= 1.0), retirable,
            len(candidate.conflicts),
            sum(1 for a in candidate.attributes if a.pii_flag),
            ", ".join(failed), value.get("attributed_annual_benefit", ""),
            value.get("build_cost", ""), value.get("payback_months", ""),
            effort.get("size", ""), effort.get("total_weeks", ""),
            view.wave_of(candidate.candidate_id) or "", candidate.purpose,
        ])
    return rows


def _scores(result: Any) -> list[list[Any]]:
    header = ["Candidate id", "Candidate", "Dimension", "Feature", "Feature label", "Value",
              "Normalized", "Weight", "Contribution", "Detail", "Evidence type", "Evidence id",
              "Evidence detail"]
    rows = [header]
    for candidate in result.ranked():
        if candidate.score is None:
            continue
        by_feature: dict[str, list] = {}
        for evidence in candidate.evidence:
            by_feature.setdefault(evidence.feature, []).append(evidence)
        for feature in candidate.score.features:
            evidence_rows = by_feature.get(feature.feature) or [None]
            for evidence in evidence_rows:
                rows.append([
                    candidate.candidate_id, candidate.proposed_name,
                    labels.label("dimension", feature.dimension), feature.feature,
                    labels.label("feature", feature.feature),
                    round(feature.value, 4), round(feature.normalized, 4),
                    round(feature.weight, 4), round(feature.contribution, 4), feature.detail,
                    evidence.evidence_type if evidence else "",
                    evidence.evidence_id if evidence else "",
                    evidence.detail if evidence else "no evidence row for this feature",
                ])
    return rows


def _metrics(result: Any) -> list[list[Any]]:
    header = ["Metric id", "Canonical name", "Name status", "Labels seen in reports",
              "Aggregation", "Grain", "Domain", "Steward", "Steward source", "Definition",
              "Reports", "Usage weight", "Business units", "Variants collapsed", "Opaque",
              "Tools", "Source tables", "Fingerprint"]
    rows = [header]
    for metric in sorted(result.canonical.metrics.values(), key=lambda m: -m.usage_weight):
        rows.append([
            metric.metric_id, metric.canonical_name,
            labels.label("name_status", metric.name_status), ", ".join(metric.labels[:6]),
            metric.aggregation, metric.grain, metric.domain, metric.steward_id,
            labels.label("steward_source", metric.steward_source),
            metric.definition or "MISSING", metric.report_count, round(metric.usage_weight, 2),
            metric.consumer_breadth, metric.variant_count, "yes" if metric.opaque else "no",
            ", ".join(metric.tools), ", ".join(metric.source_tables[:6]), metric.fingerprint,
        ])
    return rows


def _conflicts(result: Any) -> list[list[Any]]:
    header = ["Conflict id", "Label", "Pattern", "Pattern in plain English", "What differs",
              "Usage at stake", "Metric A", "Usage A", "Expression A", "Reports A", "Metric B",
              "Usage B", "Expression B", "Reports B", "Steward", "Stage 6 decision",
              "Status", "Candidates affected"]
    rows = [header]
    candidate_of: dict[str, list[str]] = {}
    for candidate in result.candidates:
        for conflict_id in candidate.conflicts:
            candidate_of.setdefault(conflict_id, []).append(candidate.candidate_id)
    conflicts = sorted(result.canonical.conflicts,
                       key=lambda c: -(c.usage_weight_a + c.usage_weight_b))
    for conflict in conflicts:
        rows.append([
            conflict.conflict_id, conflict.label, conflict.pattern,
            labels.label("conflict_pattern", conflict.pattern), conflict.difference_summary,
            round(conflict.usage_weight_a + conflict.usage_weight_b, 2),
            conflict.metric_id_a, round(conflict.usage_weight_a, 2), conflict.expression_a,
            ", ".join(conflict.reports_a[:8]),
            conflict.metric_id_b, round(conflict.usage_weight_b, 2), conflict.expression_b,
            ", ".join(conflict.reports_b[:8]),
            conflict.steward_id or "unassigned", conflict.semantic_model_decision,
            conflict.resolution_status, ", ".join(sorted(candidate_of.get(conflict.conflict_id, []))),
        ])
    if len(rows) == 1:
        return _note(header, "No competing definitions were found in this estate.")
    return rows


def _retirement(result: Any, attribution: dict[str, str]) -> list[list[Any]]:
    header = ["Report id", "Report name", "Business unit", "Owner", "Users", "Last run",
              "Disposition", "Coverage", "Action", "Retirable", "Held", "Counted against",
              "Candidate name", "Also covered by", "Semantic container", "Tool"]
    rows = [header]
    seen: dict[str, list[str]] = {}
    for candidate in result.candidates:
        for report in candidate.reports:
            if report.coverage >= 1.0:
                seen.setdefault(report.report_id, []).append(candidate.candidate_id)
    by_id = {c.candidate_id: c for c in result.candidates}
    for candidate in result.ranked():
        for report in sorted(candidate.reports, key=lambda r: (-r.coverage, -r.users)):
            if report.coverage <= 0:
                continue
            record = result.graph.reports.get(report.report_id)
            hold = is_hold(record, report.report_name)
            primary = attribution.get(report.report_id)
            others = [c for c in seen.get(report.report_id, []) if c != primary]
            rows.append([
                report.report_id, report.report_name, report.business_unit,
                report.owner or "unassigned", report.users, report.last_run,
                report.disposition or "Keep", round(report.coverage, 4),
                retirement_action(report, hold),
                "yes" if is_retirable(report, hold) else "no", "yes" if hold else "no",
                primary or "", (by_id[primary].proposed_name if primary in by_id else ""),
                ", ".join(sorted(others)),
                getattr(record, "semantic_container", "") if record else "",
                getattr(record, "tool", "") if record else "",
            ])
    if len(rows) == 1:
        return _note(header, "No report is covered by any candidate in this run.")
    return rows


def _gaps(result: Any) -> list[list[Any]]:
    header = ["Gap type", "Reason code", "Reason in plain English", "Subject", "Detail",
              "What it blocks", "Who resolves it"]
    rows = [header]
    for row in result.graph.quarantine:
        rows.append([
            "Unresolved lineage", row.reason_code,
            labels.label("quarantine_reason", row.reason_code), row.raw_reference,
            f"{row.detail} (KPI {row.kpi_id}, role {row.role})",
            "lineage completeness, gate G2", "Catalog admin",
        ])
    for column_fqn, column in sorted(result.graph.columns.items()):
        if column.business_term and column.definition:
            continue
        missing = "no business term" if not column.business_term else "no definition"
        rows.append([
            "Catalog gap", "NO_DEFINITION", f"Column has {missing} in the catalog",
            column_fqn, f"steward {column.steward_id or 'unassigned'}",
            "definition coverage, Stage 6 certification", "Domain steward",
        ])
    for metric in result.canonical.metrics.values():
        if metric.steward_id:
            continue
        rows.append([
            "Stewardship gap", "NO_STEWARD", "No steward could be found or inferred",
            metric.canonical_name, f"{metric.report_count} report(s) use this metric",
            "conflict adjudication, name acceptance", "Data product council",
        ])
    for metric in result.canonical.metrics.values():
        if not metric.opaque:
            continue
        rows.append([
            "Parse gap", "OPAQUE", labels.explanation("match_tier", "OPAQUE"),
            metric.canonical_name, ", ".join(metric.labels[:3]),
            "feasibility, fingerprint matching", "Engine team",
        ])
    if len(rows) == 1:
        return _note(header, "No gaps: every lineage row resolved and every column carries a "
                             "term and a definition.")
    return rows


def _decisions(result: Any, store: Any) -> list[list[Any]]:
    header = ["Decided at", "Candidate id", "Decision", "Decision in plain English",
              "Reason code", "Reason in plain English", "Reviewer", "Role", "Previous status",
              "New status", "Gate waived", "Second approver", "Note"]
    if store is None:
        return _note(header, "No database was supplied with this pack, so recorded reviewer "
                             "decisions could not be listed.")
    try:
        decisions = store.decisions(result.manifest.run_id)
    except Exception as error:                      # a pack must not fail on a store problem
        return _note(header, f"Reviewer decisions could not be read: {error}")
    if not decisions:
        return _note(header, "No reviewer decision has been recorded on this run yet. Every "
                             "candidate is therefore at most Proposed.")
    rows = [header]
    for decision in decisions:
        verb = str(decision.get("decision", ""))
        reason = str(decision.get("reason_code", ""))
        rows.append([
            decision.get("decided_at", ""), decision.get("candidate_id", ""), verb,
            labels.label("decision", verb), reason, labels.label("reason_code", reason),
            decision.get("reviewer", ""), decision.get("actor_role", ""),
            decision.get("previous_status", ""), decision.get("new_status", ""),
            decision.get("gate_waived", ""), decision.get("second_approver", ""),
            decision.get("note", ""),
        ])
    return rows


def _waves(view: EnrichmentView) -> list[list[Any]]:
    header = ["Wave", "Starts week", "Ends week", "Candidate id", "Candidate", "Status",
              "Size", "Effort points", "Annual benefit", "Value per point", "DPF readiness",
              "Depends on", "Rationale"]
    if not view.waves:
        return _note(header, "No wave plan was supplied with this pack. Run the programme "
                             "enrichment to schedule the backlog under a capacity assumption.")
    rows = [header]
    for wave in view.waves:
        for candidate in wave.get("candidates", []):
            rows.append([
                wave.get("wave"), wave.get("starts_week"), wave.get("ends_week"),
                candidate.get("candidate_id", ""), candidate.get("name", ""),
                candidate.get("status", ""), candidate.get("size", ""),
                candidate.get("points", ""), candidate.get("annual_benefit", ""),
                candidate.get("value_per_point", ""), candidate.get("readiness", ""),
                ", ".join(candidate.get("depends_on") or []), candidate.get("rationale", ""),
            ])
    for unscheduled in view.unscheduled:
        rows.append([
            "not scheduled", "", "", unscheduled.get("candidate_id", ""),
            unscheduled.get("name", ""), unscheduled.get("status", ""), "", "", "", "", "",
            "", f"{unscheduled.get('reason', '')} | release: "
                f"{unscheduled.get('release_hint', '')}",
        ])
    return rows


def _raid(view: EnrichmentView) -> list[list[Any]]:
    header = ["Id", "Type", "Severity", "Title", "Detail", "Owner role", "Due hint", "Status",
              "Candidates affected", "Source"]
    if not view.raid:
        return _note(header, "No RAID log was supplied with this pack. The executive summary "
                             "lists the risks read directly off the run instead.")
    rows = [header]
    for entry in view.raid:
        rows.append([
            entry.get("id", ""), entry.get("type", ""), entry.get("severity", ""),
            entry.get("title", ""), entry.get("detail", ""), entry.get("owner_role", ""),
            entry.get("due_hint", ""), entry.get("status", ""),
            ", ".join(entry.get("candidate_ids") or []), entry.get("source", ""),
        ])
    return rows


def _stakeholders(view: EnrichmentView) -> list[list[Any]]:
    header = ["Person", "Roles", "Candidates", "Candidate count", "Conflicts to adjudicate",
              "Reports owned", "Users behind them", "What is asked of them"]
    if not view.stakeholders:
        return _note(header, "No stakeholder map was supplied with this pack.")
    rows = [header]
    for person in view.stakeholders:
        rows.append([
            person.get("person", ""), ", ".join(person.get("roles") or []),
            ", ".join((person.get("candidates") or [])[:20]),
            person.get("candidate_count", ""), person.get("conflicts_to_adjudicate", ""),
            person.get("reports_owned", person.get("report_count", "")),
            person.get("users", person.get("users_affected", "")),
            person.get("ask", _stakeholder_ask(person)),
        ])
    return rows


def _stakeholder_ask(person: dict) -> str:
    asks = []
    if person.get("conflicts_to_adjudicate"):
        asks.append(f"adjudicate {person['conflicts_to_adjudicate']} conflict(s)")
    if person.get("reports_owned") or person.get("report_count"):
        asks.append("confirm the retirement list for the reports they own")
    if person.get("candidate_count"):
        asks.append(f"review {person['candidate_count']} candidate(s)")
    return "; ".join(asks) or "no action requested by this run"


def _status(view: EnrichmentView) -> list[list[Any]]:
    header = ["Measure", "Key", "Now", "Target", "Unit", "Progress", "Status", "Note"]
    if not view.measures:
        return _note(header, "No status report was supplied with this pack. The 14.2 success "
                             "measures move only once reviewers start deciding.")
    rows = [header]
    for measure in view.measures:
        rows.append([
            measure.get("measure", ""), measure.get("key", ""), measure.get("actual", ""),
            measure.get("target", ""), measure.get("unit", ""), measure.get("progress", ""),
            str(measure.get("rag", "")).upper(), measure.get("note", ""),
        ])
    return rows


def _method(result: Any, view: EnrichmentView) -> list[list[Any]]:
    """Every knob that produced this ranking, flattened from ``EngineConfig.to_dict``."""
    header = ["Setting", "Value", "What it controls"]
    rows = [header]
    manifest = result.manifest
    rows += [
        ["Run id", manifest.run_id, "This run"],
        ["Mode", manifest.mode, "manual extracts or a generated pack"],
        ["Industry pack", manifest.industry or "n/a", "which synthetic estate, if any"],
        ["Catalog of record", manifest.catalog, "where the payload would be imported"],
        ["Estate as of", manifest.as_of_date, "the date every usage window is measured from"],
        ["Run started", manifest.started_at, "wall clock, recorded but never scored on"],
        ["Synthetic", "yes" if manifest.synthetic else "no", "demonstration data or client data"],
        ["Generation id", manifest.generation_id or "n/a", "identifies the generated pack"],
        ["Published", "yes" if manifest.published else "no", "did every quality gate pass"],
        ["Extract ids", ", ".join(manifest.extract_ids), "what was ingested"],
    ]
    rows.append(["", "", ""])
    for key, value in _flatten(result.config.to_dict()).items():
        rows.append([key, cell_text(value), _knob_note(key)])
    rows.append(["", "", ""])
    for gate in manifest.quality_gates or []:
        rows.append([f"quality_gate.{gate.get('gate', '')}",
                     f"{cell_text(gate.get('value'))} (threshold {cell_text(gate.get('threshold'))})",
                     gate.get("detail", "")])
    if view.assumption_version:
        rows.append(["", "", ""])
        rows.append(["value.assumption_version", view.assumption_version,
                     "the rate card behind every benefit figure; the client owns it"])
        rows.append(["value.basis", view.value_basis, "how to read the benefit figures"])
    rows.append(["", "", ""])
    for category, entries in labels.LABELS.items():
        rows.append([f"glossary.{category}", f"{len(entries)} term(s)",
                     "plain-English label and explanation for every code in this category"])
    return rows


def _flatten(payload: Any, prefix: str = "") -> dict[str, Any]:
    """Nested config to dotted keys, so the appendix is one flat, sortable table."""
    out: dict[str, Any] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    else:
        out[prefix] = payload
    return out


def _knob_note(key: str) -> str:
    if key.startswith("weights.dimensions"):
        return "share of the composite this dimension carries"
    if key.startswith("weights.features"):
        return "share of its dimension this feature carries"
    if key.startswith("weights."):
        return "the weight set, versioned and contestable (open decision D-04)"
    if key.startswith("cluster."):
        return "how metrics are grouped into candidates"
    if key == "usage_window_months":
        return "open decision D-01"
    if key == "keep_counts_toward_consolidation":
        return "open decision D-03"
    if key == "sensitivity_review_threshold":
        return "open decision D-06"
    if key.startswith("ai_"):
        return "language-model seam; drafts are marked AI_DRAFT either way"
    return "engine setting recorded for reproducibility"
