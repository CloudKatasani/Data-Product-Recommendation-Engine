"""Status report: where the programme stands against the measures it signed up to.

Computes the specification's 14.2 success measures with actual, target and RAG,
decisions since the previous run, the Blocked and Deferred queues with reasons,
open conflicts by steward, acceptance rate by domain and reviewer, and time to
first decision (review finding R-13). Reads only through ``Store.query`` so
the report can be reproduced from the governed tables alone. No wall clock:
"now" is ``as_of`` or, failing that, the latest timestamp the store holds.
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any

# Section 14.2 targets. The retirement-list target is an estate number for a
# large client; a synthetic run is far below it, and the RAG says so honestly.
TARGETS = {
    "usage_coverage_accepted": 0.40,
    "reports_on_retirement_list_with_owner": 500,
    "metrics_with_confirmed_steward_share": 0.80,
    "hours_accept_to_charter_ratio": 0.50,
    "conflicts_adjudicated": 30,
}
CONFIRMED_STEWARD_SOURCES = ("business term steward", "column steward")


def status_report(store: Any, run_id: str, previous_run_id: str | None = None,
                  as_of: str | None = None) -> dict[str, Any]:
    run = _run(store, run_id)
    if run is None:
        raise KeyError(f"run {run_id} not found")
    previous = _run(store, previous_run_id) if previous_run_id else None
    since = previous["finished_at"] if previous else ""
    decisions = store.query(
        "SELECT * FROM REVIEW_DECISION WHERE run_id = ? ORDER BY decided_at, decision_id",
        (run_id,))
    now = as_of or _latest_timestamp(run, decisions)
    candidates = store.query("SELECT candidate_id, status, domain, proposed_name, payload "
                             "FROM DP_CANDIDATE WHERE run_id = ?", (run_id,))
    measures = _measures(store, run_id, candidates)
    return {
        "run_id": run_id,
        "previous_run_id": previous_run_id,
        "as_of": now,
        "run": {"industry": run["industry"], "mode": run["mode"], "started_at": run["started_at"],
                "finished_at": run["finished_at"], "published": bool(run["published"]),
                "label": run.get("label", "")},
        "measures": measures,
        "rag_summary": dict(_count(m["rag"] for m in measures)),
        "decisions": _decisions_since(decisions, since),
        "queues": _queues(candidates, decisions),
        "open_conflicts_by_steward": _conflicts_by_steward(store, run_id),
        "acceptance": _acceptance(candidates, decisions),
        "time_to_first_decision": _time_to_first_decision(run, candidates, decisions),
        "backlog_ageing": _backlog_ageing(run, candidates, decisions, now),
        "delta": _delta(store, run, previous),
        "phase_exit": _phase_exit(run, measures),
    }


# --------------------------------------------------------------------------
# 14.2 measures
# --------------------------------------------------------------------------

def _measures(store: Any, run_id: str, candidates: list[dict]) -> list[dict]:
    accepted = sorted(c["candidate_id"] for c in candidates if c["status"] == "Accepted")
    proposed = sorted(c["candidate_id"] for c in candidates
                      if c["status"] in ("Proposed", "Accepted"))
    metrics = store.query("SELECT metric_id, usage_weight, steward_id, steward_source "
                          "FROM KPI_CANONICAL WHERE run_id = ?", (run_id,))
    total_usage = sum(float(m["usage_weight"] or 0.0) for m in metrics) or 1.0
    metric_rows = store.query("SELECT candidate_id, metric_id FROM DP_CANDIDATE_METRIC "
                              "WHERE run_id = ?", (run_id,))

    def usage_of(ids: list[str]) -> float:
        wanted = set(ids)
        covered = {r["metric_id"] for r in metric_rows if r["candidate_id"] in wanted}
        return sum(float(m["usage_weight"] or 0.0) for m in metrics
                   if m["metric_id"] in covered) / total_usage

    out: list[dict] = []
    coverage_accepted = round(usage_of(accepted), 4)
    out.append(_measure(
        "usage_coverage_accepted",
        "Usage-weighted KPI consumption covered by Accepted candidates",
        coverage_accepted, TARGETS["usage_coverage_accepted"], "share",
        {"accepted_candidates": len(accepted), "proposed_or_accepted_coverage":
         round(usage_of(proposed), 4)}))

    reports = store.query(
        "SELECT DISTINCT cr.report_id, cr.disposition, r.owner, r.name, r.decision_critical "
        "FROM DP_CANDIDATE_REPORT cr JOIN GRAPH_NODE_REPORT r "
        "ON r.run_id = cr.run_id AND r.report_id = cr.report_id "
        "WHERE cr.run_id = ? AND cr.coverage >= 1.0 AND cr.candidate_id IN (%s)"
        % ",".join("?" * len(accepted)) if accepted else
        "SELECT report_id, disposition, owner, name, decision_critical FROM GRAPH_NODE_REPORT "
        "WHERE 1 = 0",
        tuple([run_id] + accepted) if accepted else ())
    from ..portfolio.views import is_hold
    listed = [r for r in reports if (r["disposition"] or "Keep").lower() != "keep"
              and not r["decision_critical"] and not is_hold(None, r["name"])]
    with_owner = [r for r in listed if r["owner"]]
    out.append(_measure(
        "reports_on_retirement_list_with_owner",
        "Reports on a retirement list with a named owner (Accepted candidates, "
        "disposition-aware, holds excluded)",
        len(with_owner), TARGETS["reports_on_retirement_list_with_owner"], "count",
        {"listed_without_owner": len(listed) - len(with_owner),
         "keep_or_hold_excluded": len(reports) - len(listed)}))

    accepted_metric_ids = {r["metric_id"] for r in metric_rows if r["candidate_id"] in set(accepted)}
    in_accepted = [m for m in metrics if m["metric_id"] in accepted_metric_ids]
    confirmed = [m for m in in_accepted if m["steward_id"]
                 and (m["steward_source"] or "") in CONFIRMED_STEWARD_SOURCES]
    share = round(len(confirmed) / len(in_accepted), 4) if in_accepted else 0.0
    out.append(_measure(
        "metrics_with_confirmed_steward_share",
        "Canonical metrics with a confirmed steward, among Accepted candidates",
        share, TARGETS["metrics_with_confirmed_steward_share"], "share",
        {"metrics_in_accepted": len(in_accepted), "confirmed": len(confirmed),
         "inferred_from_report_owner": sum(1 for m in in_accepted if m["steward_id"]
                                           and m["steward_source"] not in
                                           CONFIRMED_STEWARD_SOURCES)},
        not_measurable=None if in_accepted else "no Accepted candidates yet"))

    out.append(_measure(
        "hours_accept_to_charter_ratio",
        "Hours from Accept to approved DPF charter, as a share of the pre-engine baseline",
        None, TARGETS["hours_accept_to_charter_ratio"], "ratio", {},
        not_measurable="charter approval is recorded in the DPF, not the engine; supply "
                       "the baseline and the charter timestamps to compute"))

    conflicts = store.query("SELECT resolution_status FROM KPI_CONFLICT WHERE run_id = ?",
                            (run_id,))
    adjudicated = sum(1 for c in conflicts if (c["resolution_status"] or "OPEN") != "OPEN")
    out.append(_measure(
        "conflicts_adjudicated", "Conflicts adjudicated by stewards", adjudicated,
        TARGETS["conflicts_adjudicated"], "count",
        {"open": len(conflicts) - adjudicated, "total": len(conflicts)}))
    return out


def _measure(key: str, label: str, actual: Any, target: Any, unit: str, context: dict,
             not_measurable: str | None = None) -> dict:
    if not_measurable or actual is None:
        rag = "grey"
        progress = None
    else:
        progress = round(actual / target, 4) if target else None
        rag = "green" if actual >= target else "amber" if actual >= 0.5 * target else "red"
    return {"key": key, "measure": label, "actual": actual, "target": target, "unit": unit,
            "progress": progress, "rag": rag, "context": context,
            "note": not_measurable or ""}


# --------------------------------------------------------------------------
# adoption
# --------------------------------------------------------------------------

def _decisions_since(decisions: list[dict], since: str) -> dict:
    """Decisions humans took in this period.

    A status carried forward from an earlier run writes a decision row so the
    audit chain has something behind the status, but nobody decided anything
    this period: counting those as throughput would flatter the report. They are
    reported separately.
    """
    human = [d for d in decisions if (d.get("actor_role") or "") != "carry_forward"]
    carried = [d for d in decisions if (d.get("actor_role") or "") == "carry_forward"]
    recent = [d for d in human if not since or (d["decided_at"] or "") > since]
    return {
        "since": since or "start of run",
        "total": len(recent),
        "by_type": dict(_count(d["decision"] for d in recent)),
        "by_reason": dict(_count(f"{d['decision']}:{d['reason_code']}" for d in recent
                                 if d["reason_code"])),
        "by_reviewer": dict(_count(d["reviewer"] for d in recent)),
        "all_time_by_type": dict(_count(d["decision"] for d in human)),
        "carried_forward": len(carried),
        "carried_forward_by_status": dict(_count(d.get("new_status") or d["decision"]
                                                 for d in carried)),
    }


def _queues(candidates: list[dict], decisions: list[dict]) -> dict:
    last_decision: dict[str, dict] = {}
    for decision in decisions:
        last_decision[decision["candidate_id"]] = decision
    blocked, deferred, exploratory = [], [], []
    for row in candidates:
        payload = json.loads(row["payload"] or "{}") if isinstance(row["payload"], str) \
            else (row["payload"] or {})
        gates = ((payload.get("score") or {}).get("gates") or [])
        failed = [g for g in gates if not g.get("passed")]
        if row["status"] == "Blocked":
            g4 = next((g for g in failed if g.get("gate") == "G4"), None)
            blocked.append({"candidate_id": row["candidate_id"], "name": row["proposed_name"],
                            "reason": g4["detail"] if g4 else "gate G4",
                            "owner_role": "Catalog admin"})
        elif row["status"] == "Deferred":
            decision = last_decision.get(row["candidate_id"], {})
            deferred.append({"candidate_id": row["candidate_id"], "name": row["proposed_name"],
                             "reason": decision.get("reason_code", ""),
                             "note": decision.get("note", ""),
                             "reviewer": decision.get("reviewer", ""),
                             "decided_at": decision.get("decided_at", ""),
                             "revisit_hint": _revisit_hint(decision.get("reason_code", ""))})
        elif row["status"] == "Exploratory":
            exploratory.append({"candidate_id": row["candidate_id"],
                                "name": row["proposed_name"],
                                "reason": "; ".join(f"{g['gate']}: {g['detail']}" for g in failed)
                                or "capped by a gate"})
    return {"blocked": blocked, "deferred": deferred, "exploratory": exploratory,
            "by_status": dict(_count(c["status"] for c in candidates))}


def _revisit_hint(reason_code: str) -> str:
    return {
        "awaiting_source_migration": "re-flag when the sunset source maps a successor",
        "awaiting_steward": "re-flag when a steward is assigned to the metrics",
        "awaiting_privacy": "re-flag when the privacy review closes",
        "capacity": "re-flag at the next wave-planning session",
    }.get(reason_code, "re-flag on the next run")


def _conflicts_by_steward(store: Any, run_id: str) -> list[dict]:
    rows = store.query(
        "SELECT steward_id, resolution_status, usage_weight_a + usage_weight_b AS at_stake "
        "FROM KPI_CONFLICT WHERE run_id = ?", (run_id,))
    by_steward: dict[str, dict] = {}
    for row in rows:
        entry = by_steward.setdefault(row["steward_id"] or "UNASSIGNED",
                                      {"steward": row["steward_id"] or "UNASSIGNED",
                                       "open": 0, "adjudicated": 0, "usage_at_stake_open": 0.0})
        if (row["resolution_status"] or "OPEN") == "OPEN":
            entry["open"] += 1
            entry["usage_at_stake_open"] += float(row["at_stake"] or 0.0)
        else:
            entry["adjudicated"] += 1
    out = list(by_steward.values())
    for entry in out:
        entry["usage_at_stake_open"] = round(entry["usage_at_stake_open"], 2)
    out.sort(key=lambda e: (-e["open"], -e["usage_at_stake_open"], e["steward"]))
    return out


def _acceptance(candidates: list[dict], decisions: list[dict]) -> dict:
    domain_of = {c["candidate_id"]: c["domain"] or "Unassigned" for c in candidates}
    terminal = [d for d in decisions if d["decision"] in ("Accept", "Reject", "Defer", "Merge")]
    by_domain: dict[str, dict] = {}
    by_reviewer: dict[str, dict] = {}
    for decision in terminal:
        for bucket, key in ((by_domain, domain_of.get(decision["candidate_id"], "Unassigned")),
                            (by_reviewer, decision["reviewer"])):
            entry = bucket.setdefault(key, {"decisions": 0, "accepted": 0})
            entry["decisions"] += 1
            entry["accepted"] += 1 if decision["decision"] == "Accept" else 0

    def rate(bucket: dict) -> list[dict]:
        out = [{"key": k, **v, "acceptance_rate": round(v["accepted"] / v["decisions"], 4)}
               for k, v in bucket.items()]
        return sorted(out, key=lambda r: (-r["decisions"], r["key"]))
    reviewed = {d["candidate_id"] for d in terminal}
    return {
        "by_domain": rate(by_domain),
        "by_reviewer": rate(by_reviewer),
        "overall": (round(sum(1 for d in terminal if d["decision"] == "Accept") / len(terminal), 4)
                    if terminal else None),
        "candidates_reviewed": len(reviewed),
        "candidates_unreviewed": len([c for c in candidates
                                      if c["candidate_id"] not in reviewed
                                      and c["status"] in ("Proposed", "Exploratory")]),
    }


def _time_to_first_decision(run: dict, candidates: list[dict], decisions: list[dict]) -> dict:
    started = _parse(run["started_at"])
    first: dict[str, _dt.datetime] = {}
    for decision in decisions:
        stamp = _parse(decision["decided_at"])
        if stamp is None or decision["decision"] == "Override":
            continue
        cid = decision["candidate_id"]
        if cid not in first or stamp < first[cid]:
            first[cid] = stamp
    hours = sorted(max(0.0, (stamp - started).total_seconds() / 3600.0)
                   for stamp in first.values()) if started else []
    return {
        "candidates_with_a_decision": len(hours),
        "median_hours": _percentile(hours, 0.5),
        "p90_hours": _percentile(hours, 0.9),
        "max_hours": hours[-1] if hours else None,
        "basis": "REVIEW_DECISION.decided_at minus RUN.started_at, first non-override decision",
    }


def _backlog_ageing(run: dict, candidates: list[dict], decisions: list[dict], now: str) -> dict:
    started = _parse(run["started_at"])
    at = _parse(now)
    reviewed = {d["candidate_id"] for d in decisions if d["decision"] != "Override"}
    waiting = [c for c in candidates if c["candidate_id"] not in reviewed
               and c["status"] in ("Proposed", "Exploratory")]
    age_days = (round((at - started).total_seconds() / 86400.0, 2)
                if started and at else None)
    return {"awaiting_first_decision": len(waiting), "age_days": age_days,
            "oldest_candidates": [c["candidate_id"] for c in waiting[:10]]}


def _delta(store: Any, run: dict, previous: dict | None) -> dict:
    stats = json.loads(run["stats"] or "{}") if isinstance(run["stats"], str) else run["stats"]
    if previous is None:
        return {"previous_run_id": None, "note": "no previous run"}
    prev_stats = (json.loads(previous["stats"] or "{}") if isinstance(previous["stats"], str)
                  else previous["stats"]) or {}

    def get(s: dict, *keys: str) -> Any:
        node: Any = s or {}
        for key in keys:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        return node if not isinstance(node, dict) else None
    fields = {
        "candidates_by_status": (stats.get("candidates_by_status"),
                                 prev_stats.get("candidates_by_status")),
        "conflicts": (get(stats, "canonicalization", "conflicts"),
                      get(prev_stats, "canonicalization", "conflicts")),
        "canonical_metrics": (get(stats, "canonicalization", "canonical_metrics"),
                              get(prev_stats, "canonicalization", "canonical_metrics")),
        "resolution_rate": (get(stats, "graph", "resolution_rate"),
                            get(prev_stats, "graph", "resolution_rate")),
        "usage_coverage_top_n": (stats.get("usage_coverage_top_n"),
                                 prev_stats.get("usage_coverage_top_n")),
    }
    return {"previous_run_id": previous["run_id"],
            "changes": {k: {"now": v[0], "previous": v[1]} for k, v in fields.items()}}


def _phase_exit(run: dict, measures: list[dict]) -> list[dict]:
    """Section 14 phase exit criteria the store can check (R-41 in spirit)."""
    gates = json.loads(run["quality_gates"] or "[]") if isinstance(run["quality_gates"], str) \
        else (run["quality_gates"] or [])
    by_gate = {g["gate"]: g for g in gates}
    by_key = {m["key"]: m for m in measures}
    return [
        {"phase": 1, "criterion": ">= 80% lineage resolution",
         "met": bool(by_gate.get("resolution_rate", {}).get("passed")),
         "value": by_gate.get("resolution_rate", {}).get("value")},
        {"phase": 1, "criterion": ">= 70% parse rate",
         "met": bool(by_gate.get("parse_rate", {}).get("passed")),
         "value": by_gate.get("parse_rate", {}).get("value")},
        {"phase": 2, "criterion": "top 20 candidates cover >= 50% of usage-weighted consumption",
         "met": bool(by_gate.get("coverage_sanity", {}).get("passed")),
         "value": by_gate.get("coverage_sanity", {}).get("value")},
        {"phase": 2, "criterion": "2 candidates Accepted and opened in the DPF",
         "met": by_key["usage_coverage_accepted"]["context"]["accepted_candidates"] >= 2,
         "value": by_key["usage_coverage_accepted"]["context"]["accepted_candidates"]},
    ]


# --------------------------------------------------------------------------

def _run(store: Any, run_id: str | None) -> dict | None:
    if not run_id:
        return None
    rows = store.query("SELECT * FROM RUN WHERE run_id = ?", (run_id,))
    return rows[0] if rows else None


def _latest_timestamp(run: dict, decisions: list[dict]) -> str:
    stamps = [run.get("finished_at") or run.get("started_at") or ""]
    stamps += [d["decided_at"] for d in decisions if d["decided_at"]]
    return max(stamps)


def _parse(value: str | None) -> _dt.datetime | None:
    """ISO timestamp to a naive datetime; an aware stamp is read in UTC first.

    RUN.started_at is written naive and REVIEW_DECISION.decided_at may carry an
    offset, so both are brought to one basis before any subtraction.
    """
    if not value:
        return None
    try:
        stamp = _dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return stamp


def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, int(round(share * (len(values) - 1))))
    return round(values[index], 3)


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return dict(sorted(out.items()))
