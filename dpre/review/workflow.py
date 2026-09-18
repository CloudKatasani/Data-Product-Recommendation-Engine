"""The human review gate (specification section 10.2).

Candidates arrive Proposed, or Exploratory if a gate capped them, or Blocked
if a source is sunset with no successor. The reviewer can Accept, Reject,
Merge into another candidate, Split by grain or consumer, Defer with a reason,
Override a field, or Reverse a prior decision. Every decision is one
hash-chained ``REVIEW_DECISION`` row with the reviewer, a UTC timestamp, a
validated reason code, the status before and after and, for overrides, the
field changed and its prior value. The store enforces the gates (R-02) and the
state machine (R-15); this module applies the consequences (merge, split,
confirmations, overrides) around that single write path.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..governance.reasons import DECISIONS, REASON_CODES
from ..models import ReviewDecision
from ..store import ProposeOnlyError, Store
from ..util.ids import stable_id

__all__ = ["DECISIONS", "REASON_CODES", "ReviewOutcome", "review", "confirm_consumer",
           "accept_with_exception", "reverse_decision", "mark_decision_critical"]


@dataclass
class ReviewOutcome:
    decision: ReviewDecision
    status: str
    created_candidates: list[str]
    note: str = ""
    decision_id: int = 0
    previous_status: str = ""
    row: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "candidate_id": self.decision.candidate_id,
            "decision": self.decision.decision,
            "reason_code": self.decision.reason_code,
            "reviewer": self.decision.reviewer,
            "decided_at": self.decision.decided_at,
            "previous_status": self.previous_status,
            "status": self.status,
            "created_candidates": self.created_candidates,
            "note": self.note,
            "row_hash": self.row.get("row_hash", ""),
        }


def review(store: Store, run_id: str, candidate_id: str, decision: str, reviewer: str,
           reason_code: str = "", note: str = "", target_candidate_id: str = "",
           field_overridden: str = "", new_value: str = "", split_by: str = "grain", *,
           decided_at: str | None = None, actor_role: str = "reviewer", engagement_id: str = "",
           gate_waived: str = "", waiver_reason: str = "", second_approver: str = "",
           usable_without_rework: bool | None = None, rework_needed: list[str] | None = None,
           value: dict | None = None) -> ReviewOutcome:
    """Record one reviewer action and apply its consequences.

    The decision row is written first, so a refused gate or transition leaves
    nothing behind; Merge and Split then edit payloads with a snapshot of the
    prior state and the decision id that caused the change.
    """
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision '{decision}'; expected one of {', '.join(DECISIONS)}")
    if not reviewer:
        raise ProposeOnlyError("a review decision must name an authenticated reviewer")
    candidate = store.candidate(run_id, candidate_id)
    if candidate is None:
        raise KeyError(f"candidate {candidate_id} not found in run {run_id}")
    if decision == "Merge" and not target_candidate_id:
        raise ValueError("a merge decision must name the candidate to merge into")
    if decision == "Merge" and target_candidate_id == candidate_id:
        raise ValueError("a candidate cannot be merged into itself")

    record = ReviewDecision(
        candidate_id=candidate_id, decision=decision, reason_code=reason_code,
        reviewer=reviewer, decided_at=decided_at or "", field_overridden=field_overridden,
        new_value=new_value, target_candidate_id=target_candidate_id, note=note, run_id=run_id,
    )
    with store.transaction():
        row = store.record_decision(
            record, actor_role=actor_role, engagement_id=engagement_id, gate_waived=gate_waived,
            waiver_reason=waiver_reason, second_approver=second_approver,
            usable_without_rework=usable_without_rework, rework_needed=rework_needed,
            value=value)
        created: list[str] = []
        outcome_note = note
        if decision == "Merge":
            outcome_note = _merge(store, run_id, candidate, target_candidate_id,
                                  row["decision_id"], reviewer, row["decided_at"]) or note
        elif decision == "Split":
            created = _split(store, run_id, candidate, split_by, row["decision_id"],
                             row["decided_at"])
            outcome_note = (f"split by {split_by} into {len(created) + 1} candidates"
                            if created else "nothing to split: one group only")
        elif row.get("note") and row["note"] != note:
            outcome_note = row["note"]
    refreshed = store.candidate(run_id, candidate_id)
    return ReviewOutcome(record, refreshed["status"] if refreshed else "", created, outcome_note,
                         decision_id=row["decision_id"], previous_status=row["previous_status"] or "",
                         row=row)


def accept_with_exception(store: Store, run_id: str, candidate_id: str, reviewer: str,
                          gate_waived: str, waiver_reason: str, second_approver: str,
                          note: str = "", decided_at: str | None = None,
                          usable_without_rework: bool | None = None,
                          rework_needed: list[str] | None = None) -> ReviewOutcome:
    """Accept an Exploratory candidate by waiving a named gate with a second approver."""
    return review(store, run_id, candidate_id, "AcceptWithException", reviewer,
                  reason_code="gate_waived", note=note, decided_at=decided_at,
                  gate_waived=gate_waived, waiver_reason=waiver_reason,
                  second_approver=second_approver, usable_without_rework=usable_without_rework,
                  rework_needed=rework_needed)


def reverse_decision(store: Store, run_id: str, candidate_id: str, reviewer: str,
                     reason_code: str, note: str = "", decided_at: str | None = None) -> ReviewOutcome:
    """Withdraw a reviewed status; needs a reason and a different actor."""
    return review(store, run_id, candidate_id, "Reverse", reviewer, reason_code=reason_code,
                  note=note, decided_at=decided_at)


def confirm_consumer(store: Store, run_id: str, candidate_id: str, business_unit: str,
                     blocked_decision: str, latency_tolerance: str, consequence: str,
                     reviewer: str, note: str = "", decided_at: str | None = None) -> ReviewOutcome:
    """Gate G1 needs a human to name the consumer and the decision it blocks.

    Writes the run-independent confirmation (keyed by lineage id so it applies
    to the same candidate in later runs, R-07) and the REVIEW_DECISION row that
    makes it attributable; the Stage 1 draft for that unit becomes CONFIRMED.
    """
    candidate = store.candidate(run_id, candidate_id)
    if candidate is None:
        raise KeyError(f"candidate {candidate_id} not found in run {run_id}")
    record = ReviewDecision(
        candidate_id=candidate_id, decision="Override", reason_code="consumer_confirmed",
        reviewer=reviewer, decided_at=decided_at or "", field_overridden="consumer",
        new_value=business_unit, run_id=run_id,
        note=note or f"consumer confirmed: {business_unit}; blocked decision: {blocked_decision}")
    with store.transaction():
        row = store.record_decision(record, subject_type="confirmation", actor_role="consumer",
                                    previous_value="")
        confirmation = store.record_consumer_confirmation(
            candidate.get("lineage_id") or "", run_id, candidate_id, business_unit,
            blocked_decision, latency_tolerance, consequence, reviewer,
            decision_id=row["decision_id"], note=note, at=row["decided_at"])
    return ReviewOutcome(record, candidate["status"], [],
                         f"confirmation #{confirmation['confirmation_id']} recorded for "
                         f"{business_unit}", decision_id=row["decision_id"],
                         previous_status=candidate["status"], row=row)


def mark_decision_critical(store: Store, run_id: str, report_id: str, reviewer: str,
                           note: str = "", decided_at: str | None = None) -> dict:
    """A low-run, high-consequence report can have its usage weight floored.

    This is the mitigation for the deliberate limitation in section 15.1: usage
    is a demand proxy, and a regulatory filing that runs four times a year is
    not worth less than a dashboard that runs daily. The override is stored
    run-independently (REPORT_OVERRIDE) so every later run applies it.
    """
    if not reviewer:
        raise ProposeOnlyError("marking a report decision-critical requires a reviewer")
    report = store.query("SELECT report_id, decision_critical FROM GRAPH_NODE_REPORT "
                         "WHERE run_id = ? AND report_id = ?", (run_id, report_id))
    if not report:
        raise KeyError(f"report {report_id} not found in run {run_id}")
    record = ReviewDecision(
        candidate_id=report_id, decision="Override", reason_code="decision_critical",
        reviewer=reviewer, decided_at=decided_at or "", field_overridden="decision_critical",
        new_value="1", run_id=run_id,
        note=note or "report marked decision-critical; its usage weight is floored on every run")
    with store.transaction() as cur:
        row = store.record_decision(record, subject_type="report", actor_role="reviewer",
                                    previous_value=str(report[0]["decision_critical"]))
        override = store.record_report_override(
            report_id, "decision_critical", "1", reviewer, note=record.note, run_id=run_id,
            decision_id=row["decision_id"], at=row["decided_at"])
        cur.execute("UPDATE GRAPH_NODE_REPORT SET decision_critical = 1 "
                    "WHERE run_id = ? AND report_id = ?", (run_id, report_id))
    return {"run_id": run_id, "report_id": report_id, "decision_critical": True,
            "decision_id": row["decision_id"], "override_id": override["override_id"],
            "at": row["decided_at"]}


# --------------------------------------------------------------------------

def _merge(store: Store, run_id: str, candidate: dict, target_id: str, decision_id: int,
           reviewer: str, at: str) -> str:
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
    store.update_candidate_payload(run_id, target_id, payload,
                                   reason=f"merge of {candidate['candidate_id']}",
                                   decision_id=decision_id, actor=reviewer, at=at)
    store.add_candidate_metrics(run_id, target_id,
                                [m for m in source_metrics if m not in target_metrics])
    return (f"{len(source_metrics)} metrics merged into {target_id}; it now carries "
            f"{len(merged)} metrics")


def _split(store: Store, run_id: str, candidate: dict, split_by: str, decision_id: int,
           at: str) -> list[str]:
    """Split a candidate by grain or by consumer; the parent link is kept."""
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
            store.update_candidate_payload(
                run_id, candidate["candidate_id"], parent_payload,
                grain=key if split_by == "grain" else None,
                reason=f"split by {split_by}", decision_id=decision_id, at=at)
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
        store.create_candidate_row(run_id, {
            "candidate_id": child_id, "proposed_name": child_payload["proposed_name"],
            "purpose": candidate["purpose"], "archetype": candidate["archetype"],
            "tier": candidate["tier"],
            "grain": key if split_by == "grain" else candidate["grain"],
            "domain": candidate["domain"], "sub_domain": candidate["sub_domain"],
            "owner_candidate": candidate["owner_candidate"],
            "steward_candidate": candidate["steward_candidate"], "status": "Proposed",
            "archetype_confidence": candidate["archetype_confidence"],
            "archetype_runner_up": candidate["archetype_runner_up"],
            "tier_confidence": candidate["tier_confidence"],
            "name_status": candidate["name_status"],
            "parent_candidate_id": candidate["candidate_id"], "origin": "reviewer_split",
            "as_of_date": candidate["as_of_date"], "payload": child_payload,
        })
        created.append(child_id)
    return created
