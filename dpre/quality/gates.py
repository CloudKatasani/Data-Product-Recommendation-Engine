"""Run quality gates a partner can trust (review finding R-06, section 13.2).

The stability gate compares this run's candidates with "the previous run". The
shipped engine took the previous run to be whatever ran last in the database,
so a utility demo in the morning held the client's banking extracts unpublished
in the afternoon with a red chip and a wrong explanation, and a first run
reported "[pass] stability 1.0" for a comparison that never happened.

Two rules fix that:

* The previous run is a *comparable* one: same mode, and for an automated run
  the same industry and generation lineage, for a manual run the same extract
  lineage. Published runs are preferred, because a run held back by a gate is
  not a baseline anyone signed off.
* A gate has three states. ``pass`` and ``fail`` mean what they say;
  ``not_assessed`` is neither, does not block publication and is rendered as
  such rather than as a pass.

``run_publishable`` is the rule the pipeline should apply instead of
``all(g["passed"])``: a failed gate blocks, an unassessed one does not.
"""
from __future__ import annotations

import json
from typing import Any

from ..config import QUALITY_GATES
from ..models import Candidate

NOT_ASSESSED = "not_assessed"


def gate_state(gate: dict) -> str:
    """``pass`` | ``fail`` | ``not_assessed`` | ``passed after retune``."""
    if gate.get("assessed") is False:
        return NOT_ASSESSED
    state = gate.get("state")
    if state:
        return state
    return "pass" if gate.get("passed") else "fail"


def run_publishable(gates: list[dict]) -> bool:
    """A run publishes unless a gate that was actually assessed failed."""
    return all(gate_state(g) != "fail" for g in gates)


def comparable_previous_run(store: Any, mode: str, industry: str = "",
                            generation_id: str = "", extract_ids: list[str] | None = None,
                            label: str = "", published_only: bool = True,
                            before: str = "") -> str | None:
    """The latest comparable run in the store, or ``None`` when there is none.

    Comparable means: same mode; for an automated run the same industry and
    the same generation id (a different seed is a different estate); for a
    manual run at least one shared extract id, or the same label when no
    extract ids were recorded. ``before`` excludes runs started at or after an
    ISO timestamp, so a run can find its own predecessor after the fact.
    """
    if store is None:
        return None
    sql = ("SELECT run_id, mode, industry, generation_id, extract_ids, label, published, "
           "started_at FROM RUN WHERE mode = ?")
    params: list = [mode]
    if published_only:
        sql += " AND published = 1"
    if before:
        sql += " AND started_at < ?"
        params.append(before)
    sql += " ORDER BY started_at DESC, run_id DESC"
    rows = store.query(sql, tuple(params))
    wanted_ids = set(extract_ids or [])
    for row in rows:
        if mode == "automated":
            if (row.get("industry") or "") != (industry or ""):
                continue
            if generation_id and (row.get("generation_id") or "") != generation_id:
                continue
            return row["run_id"]
        recorded = set(_loads(row.get("extract_ids")))
        if wanted_ids and recorded:
            if wanted_ids & recorded:
                return row["run_id"]
            continue
        if label and (row.get("label") or "") == label:
            return row["run_id"]
        if not wanted_ids and not label and (row.get("industry") or "") == (industry or ""):
            return row["run_id"]
    return None


def _loads(value: Any) -> list:
    if isinstance(value, list):
        return value
    try:
        loaded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []


def stability_between(current: list[set[str]], previous: list[set[str]],
                      jaccard_floor: float | None = None) -> tuple[float, int, int]:
    """Share of previous candidates that map to a current one at Jaccard >= floor."""
    floor = QUALITY_GATES["stability_jaccard"] if jaccard_floor is None else jaccard_floor
    if not previous:
        return 1.0, 0, 0
    matched = 0
    for previous_set in previous:
        best = 0.0
        for current_set in current:
            union = previous_set | current_set
            if union:
                best = max(best, len(previous_set & current_set) / len(union))
        if best >= floor:
            matched += 1
    return round(matched / len(previous), 4), matched, len(previous)


def stability_gate(candidates: list[Candidate], store: Any,
                   previous_run_id: str | None) -> dict:
    """The stability gate in three states; never a silent pass."""
    threshold = QUALITY_GATES["stability_floor"]
    base = {"gate": "stability", "threshold": threshold,
            "previous_run_id": previous_run_id or ""}
    if store is None or not previous_run_id:
        return {**base, "passed": True, "assessed": False, "state": NOT_ASSESSED, "value": None,
                "detail": "no comparable previous run; stability not assessed"}
    previous_rows = store.candidates(previous_run_id)
    if not previous_rows:
        return {**base, "passed": True, "assessed": False, "state": NOT_ASSESSED, "value": None,
                "detail": f"previous run {previous_run_id} holds no candidates; stability not "
                          "assessed"}
    previous = [set(_payload(row).get("metric_ids", [])) for row in previous_rows]
    current = [set(c.metric_ids) for c in candidates]
    rate, matched, total = stability_between(current, previous)
    passed = rate >= threshold
    return {**base, "passed": passed, "assessed": True, "state": "pass" if passed else "fail",
            "value": rate,
            "detail": (f"{matched} of {total} candidates from run {previous_run_id} map to a "
                       f"candidate in this run at Jaccard >= {QUALITY_GATES['stability_jaccard']}")}


def _payload(row: dict) -> dict:
    payload = row.get("payload")
    if isinstance(payload, dict):
        return payload
    try:
        return json.loads(payload or "{}")
    except (TypeError, ValueError):
        return {}
