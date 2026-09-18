"""The human review gate (specification section 10.2).

Candidates arrive Proposed, or Exploratory if a gate capped them. The reviewer
can Accept, Reject, Merge into another candidate, Split by grain or consumer, or
Defer with a reason. Every decision writes to the feedback table with the
reviewer, timestamp, reason code and, for overrides, the field changed.
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass

from ..models import ReviewDecision
from ..store import ProposeOnlyError, Store
from ..util.ids import stable_id

DECISIONS = ("Accept", "Reject", "Merge", "Split", "Defer", "Override")

REASON_CODES = {
    "Accept": ("value_clear", "retires_reports", "resolves_conflicts", "strategic"),
    "Reject": ("no_named_consumer", "duplicate_of_existing", "too_small", "wrong_boundary",
               "source_not_viable", "already_planned"),
    "Merge": ("same_decision", "same_grain_and_sources", "duplicate_candidate"),
    "Split": ("mixed_grain", "mixed_consumers", "scope_too_broad"),
    "Defer": ("awaiting_source_migration", "awaiting_steward", "capacity", "awaiting_privacy"),
    "Override": ("wrong_archetype", "wrong_tier", "wrong_name", "wrong_owner", "wrong_grain"),
}


@dataclass
class ReviewOutcome:
    decision: ReviewDecision
    status: str
    created_candidates: list[str]
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.decision.candidate_id,
            "decision": self.decision.decision,
            "reason_code": self.decision.reason_code,
            "reviewer": self.decision.reviewer,
            "decided_at": self.decision.decided_at,
            "status": self.status,
            "created_candidates": self.created_candidates,
            "note": self.note,
        }


def review(store: Store, run_id: str, candidate_id: str, decision: str, reviewer: str,
           reason_code: str = "", note: str = "", target_candidate_id: str = "",
           field_overridden: str = "", new_value: str = "",
           split_by: str = "grain") -> ReviewOutcome:
    """Record one reviewer action and apply its consequences."""
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision '{decision}'; expected one of {', '.join(DECISIONS)}")
    if not reviewer:
        raise ProposeOnlyError("a review decision must name an authenticated reviewer")
    candidate = store.candidate(run_id, candidate_id)
    if candidate is None:
        raise KeyError(f"candidate {candidate_id} not found in run {run_id}")

    record = ReviewDecision(
        candidate_id=candidate_id, decision=decision, reason_code=reason_code,
        reviewer=reviewer, decided_at=_dt.datetime.now().isoformat(timespec="seconds"),
        field_overridden=field_overridden, new_value=new_value,
        target_candidate_id=target_candidate_id, note=note, run_id=run_id,
    )
    created: list[str] = []
    outcome_note = note

    if decision == "Merge":
        if not target_candidate_id:
            raise ValueError("a merge decision must name the candidate to merge into")
        outcome_note = _merge(store, run_id, candidate, target_candidate_id) or note
    elif decision == "Split":
        created = _split(store, run_id, candidate, split_by, reviewer)
        outcome_note = (f"split by {split_by} into {len(created)} candidates"
                        if created else "nothing to split: one group only")

    store.record_decision(record)
    refreshed = store.candidate(run_id, candidate_id)
    return ReviewOutcome(record, refreshed["status"] if refreshed else "", created, outcome_note)


def confirm_consumer(store: Store, run_id: str, candidate_id: str, business_unit: str,
                     reviewer: str, note: str = "") -> ReviewOutcome:
    """Gate G1 needs a human to confirm a named consumer, not just a run count."""
    return review(store, run_id, candidate_id, "Override", reviewer,
                  reason_code="consumer_confirmed",
                  note=note or f"consumer confirmed: {business_unit}",
                  field_overridden="", new_value=business_unit)


def mark_decision_critical(store: Store, run_id: str, report_id: str, reviewer: str,
                           note: str = "") -> dict:
    """A low-run, high-consequence report can have its usage weight floored.

    This is the mitigation for the deliberate limitation in section 15.1: usage
    is a demand proxy, and a regulatory filing that runs four times a year is not
    worth less than a dashboard that runs daily.
    """
    if not reviewer:
        raise ProposeOnlyError("marking a report decision-critical requires a reviewer")
    store.connection.execute(
        "UPDATE GRAPH_NODE_REPORT SET decision_critical = 1 WHERE run_id = ? AND report_id = ?",
        (run_id, report_id))
    store.connection.execute(
        "INSERT INTO REVIEW_DECISION (run_id, candidate_id, decision, reason_code, reviewer, "
        "decided_at, field_overridden, new_value, target_candidate_id, note) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, report_id, "Override", "decision_critical", reviewer,
         _dt.datetime.now().isoformat(timespec="seconds"), "decision_critical", "1", "",
         note or "report marked decision-critical; its usage weight is floored on the next run"))
    store.connection.commit()
    return {"run_id": run_id, "report_id": report_id, "decision_critical": True}


# --------------------------------------------------------------------------

def _merge(store: Store, run_id: str, candidate: dict, target_id: str) -> str:
    target = store.candidate(run_id, target_id)
    if target is None:
        raise KeyError(f"merge target {target_id} not found in run {run_id}")
    source_metrics = candidate["payload"].get("metric_ids", [])
    target_metrics = target["payload"].get("metric_ids", [])
    merged = sorted(set(target_metrics) | set(source_metrics))
    payload = dict(target["payload"])
    payload["metric_ids"] = merged
    payload["merged_from"] = sorted(set(payload.get("merged_from", []))
                                    | {candidate["candidate_id"]})
    import json
    store.connection.execute(
        "UPDATE DP_CANDIDATE SET payload = ? WHERE run_id = ? AND candidate_id = ?",
        (json.dumps(payload), run_id, target_id))
    store.connection.executemany(
        "INSERT INTO DP_CANDIDATE_METRIC VALUES (?,?,?)",
        [(run_id, target_id, m) for m in source_metrics if m not in target_metrics])
    store.connection.commit()
    return (f"{len(source_metrics)} metrics merged into {target_id}; it now carries "
            f"{len(merged)} metrics")


def _split(store: Store, run_id: str, candidate: dict, split_by: str, reviewer: str) -> list[str]:
    """Split a candidate by grain or by consumer; the parent link is kept."""
    import json
    payload = candidate["payload"]
    metric_ids = payload.get("metric_ids", [])
    metrics = {row["metric_id"]: row for row in store.metrics(run_id)}
    groups: dict[str, list[str]] = defaultdict(list)

    if split_by == "consumer":
        reports_by_metric = defaultdict(set)
        rows = store.query(
            "SELECT r.report_id, r.business_unit FROM GRAPH_NODE_REPORT r WHERE r.run_id = ?",
            (run_id,))
        unit_of = {row["report_id"]: row["business_unit"] for row in rows}
        for metric_id in metric_ids:
            metric = metrics.get(metric_id)
            if not metric:
                continue
            for variant in store.query(
                    "SELECT kpi_id FROM KPI_VARIANT WHERE run_id = ? AND metric_id = ?",
                    (run_id, metric_id)):
                kpi = store.query("SELECT report_id FROM GRAPH_NODE_KPI WHERE run_id = ? "
                                  "AND kpi_id = ?", (run_id, variant["kpi_id"]))
                for row in kpi:
                    reports_by_metric[metric_id].add(unit_of.get(row["report_id"], "Unassigned"))
        for metric_id in metric_ids:
            units = sorted(reports_by_metric.get(metric_id, {"Unassigned"}))
            groups[units[0] if units else "Unassigned"].append(metric_id)
    else:
        for metric_id in metric_ids:
            metric = metrics.get(metric_id)
            groups[(metric or {}).get("grain", "unknown")].append(metric_id)

    if len(groups) < 2:
        return []

    created: list[str] = []
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for index, (key, members) in enumerate(ordered):
        if index == 0:
            parent_payload = dict(payload)
            parent_payload["metric_ids"] = sorted(members)
            store.connection.execute(
                "UPDATE DP_CANDIDATE SET payload = ?, grain = ? "
                "WHERE run_id = ? AND candidate_id = ?",
                (json.dumps(parent_payload), key if split_by == "grain" else payload.get("grain"),
                 run_id, candidate["candidate_id"]))
            continue
        child_id = stable_id("CAND", run_id, candidate["candidate_id"], key)
        child_payload = dict(payload)
        child_payload.update({
            "candidate_id": child_id,
            "metric_ids": sorted(members),
            "parent_candidate_id": candidate["candidate_id"],
            "origin": "reviewer_split",
            "proposed_name": f"{candidate['proposed_name']} - {key}",
        })
        store.connection.execute(
            "INSERT OR REPLACE INTO DP_CANDIDATE VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, child_id, child_payload["proposed_name"], candidate["purpose"],
             candidate["archetype"], candidate["tier"],
             key if split_by == "grain" else candidate["grain"], candidate["domain"],
             candidate["sub_domain"], candidate["owner_candidate"],
             candidate["steward_candidate"], "Proposed", candidate["archetype_confidence"],
             candidate["archetype_runner_up"], candidate["tier_confidence"],
             candidate["name_status"], candidate["candidate_id"], "reviewer_split",
             candidate["as_of_date"], json.dumps(child_payload)))
        store.connection.executemany("INSERT INTO DP_CANDIDATE_METRIC VALUES (?,?,?)",
                                     [(run_id, child_id, m) for m in members])
        created.append(child_id)
    store.connection.commit()
    return created
