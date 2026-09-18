"""Re-apply steward decisions to a new run (R-14, R-57, R-07).

A run's ``KPI_CONFLICT.resolution_status`` and ``KPI_CANONICAL.name_status``
start from the engine's defaults. The ledgers in this package remember what a
steward decided; ``seed_from_ledgers`` writes those decisions back into the
new run's view where the fingerprints are unchanged, and records "definition
changed" where the same extract-level KPIs now fingerprint differently, so a
steward re-adjudicates only what actually moved.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .ledger import ensure_schema, normalize_timestamp

if TYPE_CHECKING:  # pragma: no cover
    from ..store import Store


def seed_from_ledgers(store: "Store", run_id: str, seeded_at: str | None = None) -> dict:
    """Seed conflicts, metric names and report overrides for ``run_id``. Idempotent."""
    from ..store import conflict_key  # local import: store imports this package

    ensure_schema(store.connection)
    already = store.query("SELECT COUNT(*) AS n FROM GOVERNANCE_SEED WHERE run_id = ?",
                          (run_id,))[0]["n"]
    if already:
        return _summary(store, run_id, already=True)
    stamp = normalize_timestamp(seeded_at)
    fingerprints = {row["metric_id"]: row["fingerprint"]
                    for row in store.query("SELECT metric_id, fingerprint FROM KPI_CANONICAL "
                                           "WHERE run_id = ?", (run_id,))}
    seeds: list[tuple] = []

    with store.transaction() as cur:
        # -- conflicts: latest decision per conflict key. A prior decision whose
        #    KPI ids still sit on both sides but whose fingerprint pair differs
        #    means one calculation changed since the steward adjudicated.
        kpis = _kpi_ids(store, run_id)
        latest_by_key: dict[str, dict] = {}
        for row in store.conflict_decisions():
            latest_by_key[row["conflict_key"]] = row
        for conflict in store.query("SELECT * FROM KPI_CONFLICT WHERE run_id = ?", (run_id,)):
            key = conflict_key(fingerprints.get(conflict["metric_id_a"], conflict["metric_id_a"]),
                               fingerprints.get(conflict["metric_id_b"], conflict["metric_id_b"]))
            decided = latest_by_key.get(key)
            if decided:
                cur.execute("UPDATE KPI_CONFLICT SET resolution_status = ?, steward_id = ? "
                            "WHERE run_id = ? AND conflict_id = ?",
                            (decided["status"], decided["steward"], run_id,
                             conflict["conflict_id"]))
                seeds.append((run_id, "conflict", conflict["conflict_id"], key, decided["status"],
                              "applied", f"decision #{decided['conflict_decision_id']} by "
                                         f"{decided['steward']} on {decided['decided_at']}", stamp))
                continue
            moved = _same_kpis_other_fingerprints(
                latest_by_key.values(), kpis.get(conflict["metric_id_a"], []),
                kpis.get(conflict["metric_id_b"], []))
            if moved and moved["status"] != "OPEN":
                seeds.append((run_id, "conflict", conflict["conflict_id"], key, "OPEN",
                              "definition changed",
                              f"the same KPIs were {moved['status']} by {moved['steward']} "
                              f"(key {moved['conflict_key']}) but a calculation has changed; "
                              "re-opened for adjudication", stamp))

        # -- metric names: latest decision per fingerprint; a decided name whose
        #    KPIs now fingerprint differently is flagged, not silently re-drafted.
        latest_by_fp: dict[str, dict] = {}
        for row in store.metric_name_decisions():
            latest_by_fp[row["fingerprint"]] = row
        for metric in store.query("SELECT * FROM KPI_CANONICAL WHERE run_id = ?", (run_id,)):
            decided = latest_by_fp.get(metric["fingerprint"])
            if decided:
                cur.execute(
                    "INSERT INTO KPI_CANONICAL_HISTORY (run_id, metric_id, fingerprint, "
                    "canonical_name, name_status, definition_status, change, changed_at, "
                    "changed_by, decision_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, metric["metric_id"], metric["fingerprint"], metric["canonical_name"],
                     metric["name_status"], metric.get("definition_status") or "Draft",
                     "before seed from ledger", stamp, decided["steward"], decided["decision_id"]))
                cur.execute(
                    "UPDATE KPI_CANONICAL SET canonical_name = ?, name_status = 'ACCEPTED', "
                    "definition_status = ? WHERE run_id = ? AND metric_id = ?",
                    (decided["new_name"], decided["definition_status"], run_id,
                     metric["metric_id"]))
                seeds.append((run_id, "metric", metric["metric_id"], metric["fingerprint"],
                              "ACCEPTED", "applied",
                              f"name '{decided['new_name']}' ({decided['definition_status']}) "
                              f"accepted by {decided['steward']} on {decided['decided_at']}", stamp))
                continue
            current_kpis = set(kpis.get(metric["metric_id"], []))
            moved = next((d for d in latest_by_fp.values()
                          if current_kpis & set(json.loads(d.get("kpi_ids") or "[]"))), None)
            if moved:
                seeds.append((run_id, "metric", metric["metric_id"], metric["fingerprint"],
                              "AI_DRAFT", "definition changed",
                              f"name '{moved['new_name']}' was accepted by {moved['steward']} for "
                              f"fingerprint {moved['fingerprint'][:12]} on the same KPIs; this "
                              "run's calculation differs, so the name stays AI_DRAFT", stamp))

        # -- report overrides: the stored graph view of this run reflects the
        #    override; the pipeline applies the same rows at graph build (R-07).
        for override in store.report_overrides():
            if override["field"] != "decision_critical":
                continue
            updated = cur.execute(
                "UPDATE GRAPH_NODE_REPORT SET decision_critical = ? WHERE run_id = ? AND report_id = ?",
                (int(override["value"] in ("1", "true", "True")), run_id, override["report_id"]))
            if updated.rowcount:
                seeds.append((run_id, "report", override["report_id"], "decision_critical",
                              override["value"], "applied",
                              f"override #{override['override_id']} by {override['actor']} on "
                              f"{override['at']}", stamp))

        if not seeds:
            seeds.append((run_id, "run", run_id, "", "", "nothing to seed",
                          "no prior steward decision applies to this run", stamp))
        cur.executemany(
            "INSERT INTO GOVERNANCE_SEED (run_id, subject_type, subject_id, ledger_key, "
            "applied_status, outcome, detail, seeded_at) VALUES (?,?,?,?,?,?,?,?)", seeds)
    return _summary(store, run_id)


def _kpi_ids(store: "Store", run_id: str) -> dict[str, list[str]]:
    return {row["metric_id"]: json.loads(row["kpi_ids"] or "[]") for row in store.query(
        "SELECT metric_id, kpi_ids FROM KPI_CANONICAL WHERE run_id = ?", (run_id,))}


def _same_kpis_other_fingerprints(decisions, kpis_a: list[str], kpis_b: list[str]) -> dict | None:
    """A prior decision on the same KPI pair (either orientation), if any."""
    a, b = set(kpis_a), set(kpis_b)
    if not a or not b:
        return None
    for decided in decisions:
        da = set(json.loads(decided.get("kpi_ids_a") or "[]"))
        db = set(json.loads(decided.get("kpi_ids_b") or "[]"))
        if (a & da and b & db) or (a & db and b & da):
            return decided
    return None


def seed_log(store: "Store", run_id: str) -> list[dict]:
    ensure_schema(store.connection)
    return store.query("SELECT * FROM GOVERNANCE_SEED WHERE run_id = ? ORDER BY rowid", (run_id,))


def _summary(store: "Store", run_id: str, already: bool = False) -> dict:
    rows = seed_log(store, run_id)
    counts: dict[str, int] = {}
    for row in rows:
        key = f"{row['subject_type']}:{row['outcome']}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "run_id": run_id,
        "applied": sum(1 for r in rows if r["outcome"] == "applied"),
        "definition_changed": sum(1 for r in rows if r["outcome"] == "definition changed"),
        "by_outcome": counts,
        "rows": rows,
        "note": "already seeded; returning the stored log" if already else "",
    }
