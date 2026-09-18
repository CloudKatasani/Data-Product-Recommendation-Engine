"""Candidate identity across runs (R-09).

``candidate_id`` is a hash that includes the run id, so it changes every run.
A candidate's *lineage id* is the hash of its sorted metric fingerprints plus
its grain, which is what a reviewer means by "the same candidate": the same
calculations at the same grain. ``carry_forward`` matches a new run to the
previous one by lineage id, then by Jaccard on metric sets (the same rule the
stability gate uses, section 13.2), re-applies reviewed statuses and persists
the delta so a council can see what changed since last time.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Iterable

from ..config import QUALITY_GATES
from ..models import ReviewDecision
from .ledger import ensure_schema, normalize_timestamp
from .transitions import REVIEWED_STATUSES, TransitionError

if TYPE_CHECKING:  # pragma: no cover - typing only, no import cycle at runtime
    from ..store import Store

CARRY_JACCARD = QUALITY_GATES["stability_jaccard"]


def lineage_id(fingerprints: Iterable[str], grain: str) -> str:
    """Stable identity: sorted, de-duplicated metric fingerprints and the grain."""
    material = "|".join(sorted({fp for fp in fingerprints if fp})) + "||" + (grain or "")
    return "LIN-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12].upper()


def lineage_id_for(candidate, canonical) -> str:
    """Lineage id of an in-memory candidate, from the canonicalization result."""
    fingerprints = [canonical.metrics[m].fingerprint if m in canonical.metrics else m
                    for m in candidate.metric_ids]
    return lineage_id(fingerprints, candidate.grain)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left, right = set(a), set(b)
    union = left | right
    return round(len(left & right) / len(union), 4) if union else 0.0


def carry_forward(store: "Store", run_id: str, previous_run_id: str,
                  computed_at: str | None = None) -> dict:
    """Match ``run_id`` to ``previous_run_id``, carry statuses, persist RUN_DELTA.

    Matching is deterministic: lineage id first, then the best remaining Jaccard
    of at least 0.6, ties broken by candidate id. A reviewed status is carried
    with a ``CarryForward`` decision that names the original reviewer and
    decision, so the propose-only rule still holds: every status past Proposed
    has a reviewer's decision behind it. Running twice is a no-op.
    """
    ensure_schema(store.connection)
    existing = store.run_delta(run_id)
    if existing:
        return _summary(run_id, previous_run_id, existing, already=True)
    current = store.candidates(run_id)
    previous = store.candidates(previous_run_id)
    stamp = normalize_timestamp(computed_at)
    matches = _match(current, previous)
    rows: list[dict] = []
    matched_current = {m["candidate_id"] for m in matches}
    matched_previous = {m["previous_candidate_id"] for m in matches}

    with store.transaction() as cur:
        for match in matches:
            prev_row = next(p for p in previous if p["candidate_id"] == match["previous_candidate_id"])
            cur_row = next(c for c in current if c["candidate_id"] == match["candidate_id"])
            carried, detail = _carry_status(store, prev_row, cur_row, previous_run_id, stamp)
            rows.append({**match, "status_carried": carried, "detail": detail})
        for row in current:
            if row["candidate_id"] not in matched_current:
                rows.append({"previous_candidate_id": "", "candidate_id": row["candidate_id"],
                             "lineage_id": row.get("lineage_id") or "", "jaccard": 0.0,
                             "change": "added", "status_carried": "",
                             "detail": "no candidate in the previous run shares its metrics"})
        for row in previous:
            if row["candidate_id"] not in matched_previous:
                rows.append({"previous_candidate_id": row["candidate_id"], "candidate_id": "",
                             "lineage_id": row.get("lineage_id") or "", "jaccard": 0.0,
                             "change": "removed", "status_carried": "",
                             "detail": f"was {row['status']}; no candidate in this run matches"})
        cur.executemany(
            "INSERT INTO RUN_DELTA (run_id, previous_run_id, previous_candidate_id, candidate_id, "
            "lineage_id, jaccard, change, status_carried, detail, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(run_id, previous_run_id, r["previous_candidate_id"], r["candidate_id"],
              r["lineage_id"], r["jaccard"], r["change"], r["status_carried"], r["detail"], stamp)
             for r in rows])
    return _summary(run_id, previous_run_id, rows)


# --------------------------------------------------------------------------

def _match(current: list[dict], previous: list[dict]) -> list[dict]:
    matches: list[dict] = []
    used_current: set[str] = set()
    used_previous: set[str] = set()
    by_lineage: dict[str, dict] = {}
    for row in sorted(previous, key=lambda r: r["candidate_id"]):
        by_lineage.setdefault(row.get("lineage_id") or "", row)
    for row in sorted(current, key=lambda r: r["candidate_id"]):
        lineage = row.get("lineage_id") or ""
        prev = by_lineage.get(lineage)
        if lineage and prev and prev["candidate_id"] not in used_previous:
            score = jaccard(row["payload"].get("metric_ids", []),
                            prev["payload"].get("metric_ids", []))
            matches.append({"previous_candidate_id": prev["candidate_id"],
                            "candidate_id": row["candidate_id"], "lineage_id": lineage,
                            "jaccard": score, "change": "kept"})
            used_current.add(row["candidate_id"])
            used_previous.add(prev["candidate_id"])
    pairs = []
    for row in current:
        if row["candidate_id"] in used_current:
            continue
        for prev in previous:
            if prev["candidate_id"] in used_previous:
                continue
            score = jaccard(row["payload"].get("metric_ids", []),
                            prev["payload"].get("metric_ids", []))
            if score >= CARRY_JACCARD:
                pairs.append((-score, prev["candidate_id"], row["candidate_id"], score))
    for _neg, prev_id, cur_id, score in sorted(pairs):
        if prev_id in used_previous or cur_id in used_current:
            continue
        lineage = next(c.get("lineage_id") or "" for c in current if c["candidate_id"] == cur_id)
        matches.append({"previous_candidate_id": prev_id, "candidate_id": cur_id,
                        "lineage_id": lineage, "jaccard": score, "change": "changed"})
        used_previous.add(prev_id)
        used_current.add(cur_id)
    return matches


def _carry_status(store: "Store", prev_row: dict, cur_row: dict, previous_run_id: str,
                  stamp: str) -> tuple[str, str]:
    status = prev_row["status"]
    if status not in REVIEWED_STATUSES:
        return "", f"previous status {status} is not a reviewed status; nothing to carry"
    origin = store.query(
        "SELECT decision_id, reviewer, decision FROM REVIEW_DECISION WHERE run_id = ? AND "
        "candidate_id = ? AND new_status = ? ORDER BY decision_id DESC LIMIT 1",
        (previous_run_id, prev_row["candidate_id"], status))
    if not origin:
        return "", (f"previous status {status} has no decision row behind it; not carried "
                    "(see verify_audit_chain orphans)")
    origin = origin[0]
    try:
        store.record_decision(
            ReviewDecision(candidate_id=cur_row["candidate_id"], decision="CarryForward",
                           reason_code="carried_from_previous_run", reviewer=origin["reviewer"],
                           decided_at=stamp, run_id=cur_row["run_id"],
                           note=(f"{status} carried from {previous_run_id}/"
                                 f"{prev_row['candidate_id']} decision #{origin['decision_id']} "
                                 f"({origin['decision']} by {origin['reviewer']})")),
            actor_role="carry_forward", carried_status=status)
    except TransitionError as exc:
        return "", f"{status} not carried: {exc}"
    return status, f"{status} carried from decision #{origin['decision_id']} by {origin['reviewer']}"


def _summary(run_id: str, previous_run_id: str, rows: list[dict], already: bool = False) -> dict:
    counts = {"added": 0, "removed": 0, "kept": 0, "changed": 0}
    for row in rows:
        counts[row["change"]] = counts.get(row["change"], 0) + 1
    return {
        "run_id": run_id, "previous_run_id": previous_run_id, **counts,
        "carried": sum(1 for r in rows if r.get("status_carried")),
        "rows": rows,
        "note": "delta already computed for this run; returning the stored rows" if already else "",
    }
