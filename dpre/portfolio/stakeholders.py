"""Stakeholder map: who carries how much of the programme (R-45).

Rolls the people the run already names - owner and steward candidates,
conflict stewards, report owners, table owners - up by person, so a council can
see that one steward is the adjudication bottleneck and which report owners
will receive retirement notices. A business-unit view answers the same
question from the consumer side. Report-owner strings are carried as they
appear in the extract; where they do not match a catalog identity they are
flagged rather than guessed (specification section 15.1).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..models import Candidate
from .views import attribute_reports, is_hold, is_retirable

ROLE_OWNER = "owner candidate"
ROLE_STEWARD = "steward candidate"
ROLE_CONFLICT = "conflict steward"
ROLE_REPORT_OWNER = "report owner"
ROLE_TABLE_OWNER = "table owner"
ROLE_TABLE_STEWARD = "table steward"


def stakeholder_map(result: Any, store: Any = None, run_id: str | None = None) -> dict:
    """People and business units with their load, in a stable order."""
    candidates: list[Candidate] = result.candidates
    graph = result.graph
    attribution = attribute_reports(candidates)
    open_conflicts = {c.conflict_id: c for c in result.canonical.conflicts
                      if c.resolution_status == "OPEN"}
    catalog_identities = {t.owner_id for t in graph.tables.values() if t.owner_id} | \
                         {t.steward_id for t in graph.tables.values() if t.steward_id} | \
                         {c.steward_id for c in graph.columns.values() if c.steward_id}

    people: dict[str, dict] = {}

    def person(name: str) -> dict:
        return people.setdefault(name, {
            "person": name, "roles": set(), "candidates": set(), "conflict_ids": set(),
            "report_ids": set(), "retirable_report_ids": set(), "hold_report_ids": set(),
            "tables": set(), "decisions_taken": 0,
            "catalog_identity": name in catalog_identities,
        })

    for candidate in candidates:
        cid = candidate.candidate_id
        if candidate.owner_candidate:
            entry = person(candidate.owner_candidate)
            entry["roles"].add(ROLE_OWNER)
            entry["candidates"].add(cid)
        if candidate.steward_candidate:
            entry = person(candidate.steward_candidate)
            entry["roles"].add(ROLE_STEWARD)
            entry["candidates"].add(cid)
        for conflict_id in candidate.conflicts:
            conflict = open_conflicts.get(conflict_id)
            if conflict is None:
                continue
            entry = person(conflict.steward_id or "UNASSIGNED")
            entry["roles"].add(ROLE_CONFLICT)
            entry["conflict_ids"].add(conflict_id)
            entry["candidates"].add(cid)
        for report in candidate.reports:
            if report.coverage < 1.0 or attribution.get(report.report_id) != cid:
                continue
            entry = person(report.owner or "UNKNOWN")
            entry["roles"].add(ROLE_REPORT_OWNER)
            entry["report_ids"].add(report.report_id)
            entry["candidates"].add(cid)
            hold = is_hold(graph.reports.get(report.report_id), report.report_name)
            if hold:
                entry["hold_report_ids"].add(report.report_id)
            elif is_retirable(report, hold):
                entry["retirable_report_ids"].add(report.report_id)
        for source in candidate.sources:
            table = graph.tables.get(source.table_fqn)
            if table is None:
                continue
            if table.owner_id:
                entry = person(table.owner_id)
                entry["roles"].add(ROLE_TABLE_OWNER)
                entry["tables"].add(source.table_fqn)
            if table.steward_id:
                entry = person(table.steward_id)
                entry["roles"].add(ROLE_TABLE_STEWARD)
                entry["tables"].add(source.table_fqn)

    if store is not None:
        for row in _decisions(store, run_id or result.manifest.run_id):
            if row["reviewer"] in people:
                people[row["reviewer"]]["decisions_taken"] += 1
                people[row["reviewer"]]["roles"].add("reviewer")

    rows = []
    for name, entry in people.items():
        rows.append({
            "person": name,
            "roles": sorted(entry["roles"]),
            "candidates": sorted(entry["candidates"]),
            "candidate_count": len(entry["candidates"]),
            "conflicts_to_adjudicate": len(entry["conflict_ids"]),
            "conflict_ids": sorted(entry["conflict_ids"]),
            "reports_to_be_notified": len(entry["report_ids"]),
            "reports_retirable": len(entry["retirable_report_ids"]),
            "reports_on_hold": len(entry["hold_report_ids"]),
            "report_ids": sorted(entry["report_ids"]),
            "tables": sorted(entry["tables"]),
            "decisions_taken": entry["decisions_taken"],
            "catalog_identity": entry["catalog_identity"],
            "load": (len(entry["conflict_ids"]) + len(entry["report_ids"])
                     + len(entry["candidates"])),
        })
    rows.sort(key=lambda r: (-r["load"], r["person"]))
    for rank, row in enumerate(rows, start=1):
        row["load_rank"] = rank

    unmapped = sorted(r["person"] for r in rows
                      if ROLE_REPORT_OWNER in r["roles"] and not r["catalog_identity"])
    return {
        "people": rows,
        "business_units": business_unit_view(result, attribution),
        "bottlenecks": [r for r in rows if r["conflicts_to_adjudicate"] > 0][:5],
        "report_owners_unmapped_to_catalog": unmapped,
        "report_owners_total": sum(1 for r in rows if ROLE_REPORT_OWNER in r["roles"]),
    }


def business_unit_view(result: Any, attribution: dict[str, str] | None = None) -> list[dict]:
    candidates: list[Candidate] = result.candidates
    graph = result.graph
    attribution = attribution if attribution is not None else attribute_reports(candidates)
    units: dict[str, dict] = defaultdict(lambda: {
        "users": 0, "candidates": set(), "composites": set(), "reports": set(),
        "reports_attributable": set(), "reports_retirable": set()})
    for candidate in candidates:
        for consumer in candidate.consumers:
            unit = units[consumer.business_unit or "Unassigned"]
            unit["candidates"].add(candidate.candidate_id)
            if candidate.origin == "composite":
                unit["composites"].add(candidate.candidate_id)
        for report in candidate.reports:
            if report.coverage <= 0:
                continue
            unit = units[report.business_unit or "Unassigned"]
            unit["reports"].add(report.report_id)
            if report.coverage >= 1.0 and attribution.get(report.report_id) == candidate.candidate_id:
                unit["reports_attributable"].add(report.report_id)
                hold = is_hold(graph.reports.get(report.report_id), report.report_name)
                if is_retirable(report, hold):
                    unit["reports_retirable"].add(report.report_id)
    for report in graph.reports.values():
        units[report.business_unit or "Unassigned"]["users"] += report.distinct_users_12m
    out = []
    for name, unit in units.items():
        out.append({
            "business_unit": name,
            "users": unit["users"],
            "candidates": sorted(unit["candidates"]),
            "candidate_count": len(unit["candidates"]),
            "composites": sorted(unit["composites"]),
            "reports_covered": len(unit["reports"]),
            "reports_attributable": len(unit["reports_attributable"]),
            "reports_retirable": len(unit["reports_retirable"]),
        })
    out.sort(key=lambda r: (-r["reports_retirable"], -r["users"], r["business_unit"]))
    return out


def _decisions(store: Any, run_id: str) -> list[dict]:
    try:
        return store.query("SELECT reviewer FROM REVIEW_DECISION WHERE run_id = ?", (run_id,))
    except Exception:            # a store without the table is not an error here
        return []
