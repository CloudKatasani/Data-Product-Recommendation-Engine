"""Planned versus realised benefits after acceptance (R-09, spec 14.2).

``BENEFIT_PLAN`` is written by the store at Accept from the card's value row.
``BENEFIT_ACTUAL`` is written here as events arrive from outside the engine: a
report retired, a DPF charter approved, a conflict resolved. Both are keyed by
lineage id so they survive the run that produced the candidate.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .ledger import ensure_schema, normalize_timestamp, utc_now

if TYPE_CHECKING:  # pragma: no cover
    from ..store import Store

EVENT_TYPES = ("report_retired", "charter_approved", "conflict_resolved")


def record_benefit_event(store: "Store", lineage_id: str, event_type: str, subject_id: str,
                         occurred_at: str, confirmed_by: str, note: str = "") -> dict:
    """Append one realised-benefit event; the confirmer and the date are mandatory."""
    ensure_schema(store.connection)
    if event_type not in EVENT_TYPES:
        raise ValueError(f"'{event_type}' is not a benefit event; expected one of "
                         + ", ".join(EVENT_TYPES))
    if not lineage_id:
        raise ValueError("a benefit event needs the candidate's lineage id")
    if not confirmed_by:
        raise PermissionError("a benefit event must name who confirmed it")
    if not occurred_at:
        raise ValueError("a benefit event needs the date it occurred")
    occurred = normalize_timestamp(occurred_at)
    recorded = utc_now()
    with store.transaction() as cur:
        cursor = cur.execute(
            "INSERT INTO BENEFIT_ACTUAL (lineage_id, event_type, subject_id, occurred_at, "
            "confirmed_by, note, recorded_at) VALUES (?,?,?,?,?,?,?)",
            (lineage_id, event_type, subject_id, occurred, confirmed_by, note, recorded))
    return {"event_id": cursor.lastrowid, "lineage_id": lineage_id, "event_type": event_type,
            "subject_id": subject_id, "occurred_at": occurred, "confirmed_by": confirmed_by,
            "note": note, "recorded_at": recorded}


def benefits_summary(store: "Store", lineage_id: str) -> dict:
    """Planned (latest plan) against realised (distinct subjects per event type)."""
    ensure_schema(store.connection)
    plans = store.query("SELECT * FROM BENEFIT_PLAN WHERE lineage_id = ? ORDER BY plan_id DESC",
                        (lineage_id,))
    events = store.query("SELECT * FROM BENEFIT_ACTUAL WHERE lineage_id = ? ORDER BY event_id",
                         (lineage_id,))
    plan = plans[0] if plans else None
    retired = {e["subject_id"] for e in events if e["event_type"] == "report_retired"}
    resolved = {e["subject_id"] for e in events if e["event_type"] == "conflict_resolved"}
    charters = [e for e in events if e["event_type"] == "charter_approved"]
    planned = {
        "reports_retired": plan["reports_retired"] if plan else 0,
        "conflicts_resolved": plan["conflicts_resolved"] if plan else 0,
        "users_served": plan["users_served"] if plan else 0,
        "usage_weight": plan["usage_weight"] if plan else 0.0,
    }
    realised = {
        "reports_retired": len(retired),
        "conflicts_resolved": len(resolved),
        "charter_approved": bool(charters),
        "charter_approved_at": charters[0]["occurred_at"] if charters else "",
    }
    hours_to_charter = None
    if plan and charters:
        hours_to_charter = _hours_between(plan["planned_at"], charters[0]["occurred_at"])
    return {
        "lineage_id": lineage_id,
        "accepted": plan is not None,
        "accepted_at": plan["planned_at"] if plan else "",
        "accepted_by": plan["planned_by"] if plan else "",
        "planned": planned,
        "realised": realised,
        "variance": {
            "reports_retired": realised["reports_retired"] - planned["reports_retired"],
            "conflicts_resolved": realised["conflicts_resolved"] - planned["conflicts_resolved"],
        },
        "hours_accept_to_charter": hours_to_charter,
        "events": events,
    }


def realisation_view(store: "Store") -> list[dict]:
    """One row per accepted lineage: what was promised and what has landed."""
    ensure_schema(store.connection)
    lineages = store.query("SELECT DISTINCT lineage_id FROM BENEFIT_PLAN ORDER BY lineage_id")
    return [benefits_summary(store, row["lineage_id"]) for row in lineages]


def _hours_between(start: str, end: str) -> float | None:
    import datetime as _dt
    try:
        a = _dt.datetime.fromisoformat(start)
        b = _dt.datetime.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=_dt.timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=_dt.timezone.utc)
    return round((b - a).total_seconds() / 3600.0, 2)
