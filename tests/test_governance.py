"""Governance core: audit integrity, gates at acceptance, weight governance,
candidate identity across runs, conflict and name ledgers, reason codes and
the feedback statistics (review findings R-02, R-03, R-04, R-07, R-09, R-14,
R-15, R-17, R-43, R-57)."""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import random
import sqlite3
import threading

import pytest

from dpre.config import EngineConfig, ScoreWeights
from dpre.governance import (
    REASON_CODES, ReasonCodeError, TransitionError, audit_report, benefits_summary,
    carry_forward, lineage_id, next_status, record_benefit_event, seed_from_ledgers,
    transitions_table, validate_reason,
)
from dpre.governance.reasons import OverrideValueError, validate_override
from dpre.ingest import ingest_automated
from dpre.models import ReviewDecision
from dpre.pipeline import run_pipeline
from dpre.review import (
    FeedbackGateError, accept_with_exception, approve_weights, confirm_consumer,
    mark_decision_critical, reestimate_weights, reverse_decision, review,
)
from dpre.review.feedback import (
    FEATURES, MIN_DECISIONS, MIN_PER_CLASS, STATUS_CONTRADICTION, STATUS_PROPOSED,
    WeightProposal, collect_training_rows,
)
from dpre.store import (
    GateError, ProposeOnlyError, PublishError, RunExistsError, Store, WeightVersionError,
    conflict_key,
)

AS_OF = _dt.date(2026, 9, 17)


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def governed(tmp_path_factory):
    """One utility run in a fresh store; tests below add decisions to it."""
    store = Store(tmp_path_factory.mktemp("gov") / "engine.db")
    result = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store)
    yield result, store
    store.close()


def _by_status(store: Store, run_id: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in store.candidates(run_id):
        out.setdefault(row["status"], []).append(row)
    return out


def _standalone(store: Store, run_id: str, status: str = "Proposed") -> dict:
    """A candidate of this status that no other candidate's delivery depends on.

    Accepting a composite over a part the board has Rejected or Deferred is
    refused on purpose (R-11), so a test about something else picks a candidate
    where that control is not in play.
    """
    blocked = {row["candidate_id"] for row in store.query(
        "SELECT d.candidate_id FROM DP_CANDIDATE_DEPENDENCY d "
        "JOIN DP_CANDIDATE c ON c.run_id = d.run_id AND c.candidate_id = d.target "
        "WHERE d.run_id = ? AND d.type IN ('candidate', 'entity_master') "
        "  AND c.status IN ('Exploratory', 'Blocked', 'Deferred', 'Rejected')", (run_id,))}
    for row in reversed(_by_status(store, run_id)[status]):
        if row["candidate_id"] not in blocked:
            return row
    raise AssertionError(f"no {status} candidate without open dependencies")


def _synthetic_candidates(store: Store, run_id: str, count: int, seed: int = 7,
                          status: str = "Proposed", failed_gates: tuple[str, ...] = ()) -> list[dict]:
    """Candidate and score rows with known dimension scores, written directly.

    Used where a real run is too small (the feedback floor is 50 candidates)
    or where a specific gate outcome is needed.
    """
    rng = random.Random(seed)
    gates = [{"gate": g, "name": g, "passed": g not in failed_gates,
              "detail": f"{g} synthetic", "effect": ""} for g in ("G1", "G2", "G3", "G4")]
    rows = []
    with store.transaction() as cur:
        for index in range(count):
            candidate_id = f"CAND-SYN{seed:02d}{index:04d}"
            scores = {f: round(rng.uniform(0, 100), 2) for f in FEATURES}
            payload = {"metric_ids": [f"MET-SYN{index}"], "reports": [], "consumers": [],
                       "conflicts": [], "usage_weight": 0.0}
            cur.execute(
                "INSERT INTO DP_CANDIDATE (run_id, candidate_id, proposed_name, purpose, archetype, "
                "tier, grain, domain, status, name_status, payload, lineage_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, candidate_id, f"synthetic {index}", "", "Metric / KPI", "Aggregate",
                 "Account", "Synthetic", status, "AI_DRAFT", json.dumps(payload),
                 lineage_id([f"fp{index}"], "Account")))
            cur.execute(
                "INSERT INTO DP_CANDIDATE_SCORE (run_id, candidate_id, weight_version, demand, "
                "consolidation, feasibility, risk, composite, gate_results, features) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, candidate_id, "v1.0-initial", scores["demand"], scores["consolidation"],
                 scores["feasibility"], scores["risk"], 0.0, json.dumps(gates), "[]"))
            rows.append({"candidate_id": candidate_id, **scores})
    return rows


def _decide(store: Store, run_id: str, candidate_id: str, decision: str, reviewer: str,
            reason: str = "", **extras) -> dict:
    return store.record_decision(ReviewDecision(
        candidate_id=candidate_id, decision=decision, reason_code=reason, reviewer=reviewer,
        decided_at="", run_id=run_id, note=extras.pop("note", "")), **extras)


# --------------------------------------------------------------------------
# 1. Audit integrity (R-15, R-04)
# --------------------------------------------------------------------------

def test_decisions_are_hash_chained_and_verify(governed):
    result, store = governed
    proposed = _by_status(store, result.run_id)["Proposed"]
    first = review(store, result.run_id, proposed[0]["candidate_id"], "Defer", "priya.silva",
                   reason_code="capacity")
    second = review(store, result.run_id, proposed[1]["candidate_id"], "Reject", "priya.silva",
                    reason_code="too_small")
    assert first.row["row_hash"] and second.row["prev_hash"] == first.row["row_hash"]
    assert first.row["previous_status"] == "Proposed" and first.row["new_status"] == "Deferred"
    assert first.row["decided_at"].endswith("+00:00"), "timestamps are UTC ISO"
    verdict = store.verify_audit_chain(result.run_id)
    assert verdict["ok"] and verdict["rows"] >= 2 and verdict["first_break"] is None


def test_ledgers_refuse_update_and_delete(governed):
    result, store = governed
    decision_id = store.decisions(result.run_id)[0]["decision_id"]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("UPDATE REVIEW_DECISION SET reviewer = 'x' WHERE decision_id = ?",
                                 (decision_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("DELETE FROM REVIEW_DECISION WHERE decision_id = ?", (decision_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("DELETE FROM SCORE_WEIGHT")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.connection.execute("UPDATE SCORE_WEIGHT SET weight = 0.9")


def test_tampering_around_the_triggers_is_detected(tmp_path):
    """Tamper-evident means detection, not merely that the application would not."""
    store = Store(tmp_path / "tamper.db")
    result = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store)
    proposed = _by_status(store, result.run_id)["Proposed"]
    review(store, result.run_id, proposed[0]["candidate_id"], "Accept", "priya.silva",
           reason_code="value_clear")
    review(store, result.run_id, proposed[1]["candidate_id"], "Reject", "priya.silva",
           reason_code="too_small")
    assert store.verify_audit_chain()["ok"]
    # A privileged actor drops the trigger and edits a row in place.
    store.connection.execute("DROP TRIGGER TRG_REVIEW_DECISION_NO_UPDATE")
    store.connection.execute("UPDATE REVIEW_DECISION SET decision = 'Reject', new_status = 'Rejected' "
                             "WHERE decision_id = 1")
    verdict = store.verify_audit_chain()
    assert not verdict["ok"]
    assert verdict["first_break"]["decision_id"] == 1
    assert "content altered" in verdict["first_break"]["reason"]
    # A status set by direct SQL with no decision behind it is an orphan.
    store.connection.execute("UPDATE DP_CANDIDATE SET status = 'Accepted' WHERE run_id = ? "
                             "AND candidate_id = ?", (result.run_id, proposed[2]["candidate_id"]))
    orphans = {o["candidate_id"] for o in store.orphan_statuses(result.run_id)}
    # Both the directly-set status and the one whose decision row was altered.
    assert orphans == {proposed[0]["candidate_id"], proposed[2]["candidate_id"]}
    # A row inserted around the store is unhashed and breaks the chain.
    store.connection.execute(
        "INSERT INTO REVIEW_DECISION (run_id, candidate_id, decision, reviewer, decided_at) "
        "VALUES (?,?,?,?,?)", (result.run_id, "CAND-X", "Accept", "nobody", "2026-01-01"))
    breaks = store.verify_audit_chain()
    assert not breaks["ok"]
    report = audit_report(store, result.run_id)
    assert not report["ok"] and any("audit chain broken" in f for f in report["findings"])
    store.close()


def test_status_history_records_every_change(governed):
    result, store = governed
    candidate = _by_status(store, result.run_id)["Proposed"][2]
    review(store, result.run_id, candidate["candidate_id"], "Accept", "priya.silva",
           reason_code="retires_reports")
    reverse_decision(store, result.run_id, candidate["candidate_id"], "council.chair",
                     "new_evidence", note="consumer withdrew")
    history = store.status_history(result.run_id, candidate["candidate_id"])
    assert [(h["from_status"], h["to_status"]) for h in history] == [
        ("Proposed", "Accepted"), ("Accepted", "Rejected")]
    assert [h["actor"] for h in history] == ["priya.silva", "council.chair"]
    assert all(h["decision_id"] for h in history)


def test_transitions_are_enforced(governed):
    result, store = governed
    candidate = _by_status(store, result.run_id)["Proposed"][3]
    review(store, result.run_id, candidate["candidate_id"], "Accept", "priya.silva",
           reason_code="value_clear")
    with pytest.raises(TransitionError):
        review(store, result.run_id, candidate["candidate_id"], "Accept", "priya.silva",
               reason_code="value_clear")
    with pytest.raises(TransitionError):
        review(store, result.run_id, candidate["candidate_id"], "Reject", "priya.silva",
               reason_code="too_small")
    with pytest.raises(TransitionError, match="different actor"):
        reverse_decision(store, result.run_id, candidate["candidate_id"], "priya.silva",
                         "decided_in_error")
    with pytest.raises(ReasonCodeError):
        reverse_decision(store, result.run_id, candidate["candidate_id"], "council.chair", "")
    assert store.candidate(result.run_id, candidate["candidate_id"])["status"] == "Accepted"
    assert next_status("Accepted", "Reverse") == "Rejected"
    assert next_status("Blocked", "CarryForward", "Rejected") == "Rejected"
    with pytest.raises(TransitionError):
        next_status("Blocked", "CarryForward", "Accepted")
    assert {(r["from_status"], r["decision"]) for r in transitions_table()} >= {
        ("Proposed", "Accept"), ("Blocked", "Reject"), ("Accepted", "Reverse")}


def test_a_run_is_persisted_in_one_transaction_and_published_last(tmp_path):
    store = Store(tmp_path / "persist.db")
    result = run_pipeline(ingest_automated("utility", as_of=AS_OF))
    # save_run alone leaves the run unpublished, whatever the manifest says.
    assert result.manifest.published is True
    store.save_run(result.manifest)
    assert store.run(result.run_id)["published"] == 0
    with pytest.raises(PublishError, match="no candidates"):
        store.publish(result.run_id)
    with pytest.raises(RunExistsError):
        store.save_run(result.manifest)
    # A failing persist leaves nothing behind.
    broken = dataclasses.replace(result.manifest, run_id="RUN-BROKEN")
    bad = dataclasses.replace(result.candidates[0], evidence=[])
    with pytest.raises(Exception):
        store.persist_run(broken, result.graph, result.canonical, [bad])
    assert store.run("RUN-BROKEN") is None
    # A whole run persists and publishes in one go, with the weight hash stamped.
    whole = dataclasses.replace(result.manifest, run_id="RUN-WHOLE")
    outcome = store.persist_run(whole, result.graph, result.canonical, result.candidates,
                                weights=result.config.weights)
    assert outcome["published"] and outcome["candidates"] == len(result.candidates)
    row = store.run("RUN-WHOLE")
    assert row["published"] == 1 and row["weight_hash"]
    store.close()


def test_concurrent_persists_leave_every_run_consistent(tmp_path):
    """R-04: several threads persist runs on the shared connection at once."""
    store = Store(tmp_path / "concurrent.db")
    result = run_pipeline(ingest_automated("utility", as_of=AS_OF))
    errors: list[BaseException] = []

    def persist(index: int) -> None:
        try:
            manifest = dataclasses.replace(result.manifest, run_id=f"RUN-T{index:02d}")
            store.persist_run(manifest, result.graph, result.canonical, result.candidates)
            for candidate in list(store.candidates(f"RUN-T{index:02d}", "Proposed"))[:2]:
                review(store, f"RUN-T{index:02d}", candidate["candidate_id"], "Defer",
                       f"reviewer.{index}", reason_code="capacity")
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=persist, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    runs = store.runs(limit=100)
    assert len(runs) == 6
    for run in runs:
        candidates = store.query("SELECT COUNT(*) AS n FROM DP_CANDIDATE WHERE run_id = ?",
                                 (run["run_id"],))[0]["n"]
        evidence = store.query("SELECT COUNT(*) AS n FROM DP_CANDIDATE_EVIDENCE WHERE run_id = ?",
                               (run["run_id"],))[0]["n"]
        assert candidates == len(result.candidates) and evidence > 0
        assert run["published"] == 1
    assert store.verify_audit_chain()["ok"]
    assert len(store.decisions()) == 12
    store.close()


def test_a_v1_database_opens_with_the_v2_schema(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript("""
        CREATE TABLE RUN (run_id TEXT PRIMARY KEY, mode TEXT, industry TEXT, catalog TEXT,
            as_of_date TEXT, started_at TEXT, finished_at TEXT, weight_version TEXT,
            parser_version TEXT, generation_id TEXT, synthetic INTEGER, published INTEGER,
            stats TEXT, quality_gates TEXT, warnings TEXT, extract_ids TEXT, agent_log TEXT,
            label TEXT);
        CREATE TABLE DP_CANDIDATE (run_id TEXT, candidate_id TEXT, proposed_name TEXT,
            purpose TEXT, archetype TEXT, tier TEXT, grain TEXT, domain TEXT, sub_domain TEXT,
            owner_candidate TEXT, steward_candidate TEXT, status TEXT, archetype_confidence REAL,
            archetype_runner_up TEXT, tier_confidence REAL, name_status TEXT,
            parent_candidate_id TEXT, origin TEXT, as_of_date TEXT, payload TEXT,
            PRIMARY KEY (run_id, candidate_id));
        CREATE TABLE REVIEW_DECISION (decision_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT,
            candidate_id TEXT, decision TEXT, reason_code TEXT, reviewer TEXT, decided_at TEXT,
            field_overridden TEXT, new_value TEXT, target_candidate_id TEXT, note TEXT);
        CREATE TABLE SCORE_WEIGHT (weight_version TEXT, dimension TEXT, feature TEXT,
            weight REAL, effective_from TEXT, note TEXT, approved_by TEXT);
        INSERT INTO RUN (run_id, mode, industry, published) VALUES ('RUN-OLD', 'automated', 'utility', 1);
        INSERT INTO DP_CANDIDATE (run_id, candidate_id, status, payload)
            VALUES ('RUN-OLD', 'CAND-OLD', 'Accepted', '{}');
        INSERT INTO REVIEW_DECISION (run_id, candidate_id, decision, reason_code, reviewer, decided_at)
            VALUES ('RUN-OLD', 'CAND-OLD', 'Accept', 'value_clear', 'priya.silva', '2026-01-05T10:00:00');
    """)
    legacy.commit()
    legacy.close()

    store = Store(path)
    assert store.schema_version() == 2
    assert {"row_hash", "prev_hash", "previous_status", "new_status"} <= store._columns("REVIEW_DECISION")
    assert "lineage_id" in store._columns("DP_CANDIDATE")
    sealed = store.decisions("RUN-OLD")[0]
    assert sealed["row_hash"] and sealed["actor_role"] == "legacy" and sealed["new_status"] == "Accepted"
    assert store.verify_audit_chain() == {"ok": True, "rows": 1, "first_break": None, "orphans": []}
    assert store.current_weights().weight_version == "v1.0-initial"
    note = store.query("SELECT note FROM SCHEMA_VERSION WHERE version = 2")[0]["note"]
    assert "1 legacy decisions sealed" in note
    store.close()


# --------------------------------------------------------------------------
# 2. Gates at Accept (R-02, R-07)
# --------------------------------------------------------------------------

def test_accept_on_blocked_raises_with_the_gate_detail(governed):
    result, store = governed
    blocked = _by_status(store, result.run_id)["Blocked"][0]
    with pytest.raises(GateError, match="G4 sunset source"):
        review(store, result.run_id, blocked["candidate_id"], "Accept", "priya.silva",
               reason_code="value_clear")
    with pytest.raises(GateError):
        accept_with_exception(store, result.run_id, blocked["candidate_id"], "priya.silva",
                              "G4", "we will migrate", "cdo.office")
    assert store.candidate(result.run_id, blocked["candidate_id"])["status"] == "Blocked"
    assert not [d for d in store.decisions(result.run_id)
                if d["candidate_id"] == blocked["candidate_id"]]


def test_exploratory_needs_a_consumer_confirmation_or_a_second_approver(tmp_path):
    store = Store(tmp_path / "gates.db")
    rows = _synthetic_candidates(store, "RUN-G1", 2, seed=3, status="Exploratory",
                                 failed_gates=("G1",))
    first, second = rows[0]["candidate_id"], rows[1]["candidate_id"]
    with pytest.raises(GateError, match="consumer confirmation"):
        review(store, "RUN-G1", first, "Accept", "priya.silva", reason_code="value_clear")
    # Confirmation needs the three human fields from spec 9.2.
    with pytest.raises(ValueError, match="blocked_decision"):
        confirm_consumer(store, "RUN-G1", first, "Finance", "", "daily", "fine", "maria.chen")
    outcome = confirm_consumer(store, "RUN-G1", first, "Finance", "approve provisioning",
                               "next business day", "regulatory breach", "maria.chen")
    assert outcome.decision.reason_code == "consumer_confirmed" and outcome.decision_id
    confirmations = store.consumer_confirmations()
    assert confirmations[-1]["lineage_id"] == lineage_id(["fp0"], "Account")
    assert confirmations[-1]["blocked_decision"] == "approve provisioning"
    accepted = review(store, "RUN-G1", first, "Accept", "priya.silva", reason_code="value_clear")
    assert accepted.status == "Accepted" and "G1 satisfied by consumer confirmation" in accepted.note
    # The second candidate has no confirmation: an exception needs a second, different approver.
    with pytest.raises(GateError, match="second approver"):
        accept_with_exception(store, "RUN-G1", second, "priya.silva", "G1", "pilot", "")
    with pytest.raises(GateError, match="different person"):
        accept_with_exception(store, "RUN-G1", second, "priya.silva", "G1", "pilot", "priya.silva")
    with pytest.raises(GateError, match="did not fail"):
        accept_with_exception(store, "RUN-G1", second, "priya.silva", "G2", "pilot", "cdo.office")
    waived = accept_with_exception(store, "RUN-G1", second, "priya.silva", "G1",
                                   "pilot domain: consumer named verbally", "cdo.office")
    assert waived.status == "Accepted"
    assert waived.row["gate_waived"] == "G1" and waived.row["second_approver"] == "cdo.office"
    waivers = store.open_waivers("RUN-G1")
    assert len(waivers) == 1 and waivers[0]["candidate_id"] == second
    # Reversing the acceptance closes the waiver.
    reverse_decision(store, "RUN-G1", second, "council.chair", "decided_in_error")
    assert store.open_waivers("RUN-G1") == []
    assert store.verify_audit_chain()["ok"]
    store.close()


def test_report_overrides_are_run_independent(governed):
    result, store = governed
    report_id = store.query("SELECT report_id FROM GRAPH_NODE_REPORT WHERE run_id = ? "
                            "ORDER BY report_id LIMIT 1", (result.run_id,))[0]["report_id"]
    with pytest.raises(KeyError):
        mark_decision_critical(store, result.run_id, "NOPE", "priya.silva")
    outcome = mark_decision_critical(store, result.run_id, report_id, "priya.silva")
    assert outcome["decision_critical"] and outcome["override_id"]
    overrides = store.report_overrides("decision_critical")
    assert [(o["report_id"], o["value"], o["actor"]) for o in overrides] == [
        (report_id, "1", "priya.silva")]
    decision = next(d for d in store.decisions(result.run_id) if d["subject_type"] == "report")
    assert decision["previous_value"] == "0" and decision["new_value"] == "1"


# --------------------------------------------------------------------------
# 3. Weight governance (R-03)
# --------------------------------------------------------------------------

def test_initial_weights_ship_unapproved_and_versions_are_immutable(tmp_path):
    store = Store(tmp_path / "weights.db")
    current = store.current_weights()
    assert current.weight_version == "v1.0-initial" and current.approved_by == ""
    assert current.note == "pending council approval (D-04)"
    assert current.dimensions == EngineConfig().weights.dimensions
    # The same numbers again are a no-op; different numbers under the same version are refused.
    assert store.save_weights(EngineConfig().weights)["created"] is False
    tilted = EngineConfig().weights
    tilted.dimensions = {"demand": 0.9, "consolidation": 0.0, "feasibility": 0.0, "risk": -0.1}
    with pytest.raises(WeightVersionError):
        store.save_weights(tilted)
    assert store.current_weights().dimensions == current.dimensions
    # Approval is a ledger row, and it changes what is in force.
    new = tilted.with_version("v2.0-council")
    store.save_weights(new)
    with pytest.raises(PermissionError):
        store.approve_weight_version("v2.0-council", "")
    store.approve_weight_version("v2.0-council", "data product council", note="minuted 2026-09-17")
    assert store.current_weights().weight_version == "v2.0-council"
    assert store.current_weights().approved_by == "data product council"
    versions = {v["weight_version"]: v for v in store.weight_versions()}
    assert versions["v1.0-initial"]["approved"] is False and versions["v2.0-council"]["approved"]
    changes = store.config_changes()
    assert [c["field"] for c in changes] == ["weights.weight_version", "weights.dimensions"]
    assert changes[0]["before"] == "v1.0-initial" and changes[0]["after"] == "v2.0-council"
    with pytest.raises(PermissionError):
        store.record_config_change("", "cluster.resolution", 1.0, 1.3)
    store.record_config_change("ops.lead", "cluster.resolution", 1.0, 1.3, note="D-03 review")
    assert store.config_changes("cluster.resolution")[0]["after"] == "1.3"
    store.close()


# --------------------------------------------------------------------------
# 4. Candidate identity across runs (R-09)
# --------------------------------------------------------------------------

def test_lineage_ids_are_stable_and_decisions_carry_forward(tmp_path):
    store = Store(tmp_path / "carry.db")
    first = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store)
    statuses = _by_status(store, first.run_id)
    accepted, rejected, deferred = (statuses["Proposed"][0], statuses["Proposed"][1],
                                    statuses["Proposed"][2])
    review(store, first.run_id, accepted["candidate_id"], "Accept", "priya.silva",
           reason_code="value_clear", value={"reports_retired": 12})
    review(store, first.run_id, rejected["candidate_id"], "Reject", "priya.silva",
           reason_code="too_small")
    review(store, first.run_id, deferred["candidate_id"], "Defer", "priya.silva",
           reason_code="capacity")

    second = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store,
                          previous_run_id=first.run_id)
    assert second.run_id != first.run_id
    assert (sorted(c["lineage_id"] for c in store.candidates(first.run_id))
            == sorted(c["lineage_id"] for c in store.candidates(second.run_id)))
    assert all(c["candidate_id"] != accepted["candidate_id"]
               for c in store.candidates(second.run_id)), "candidate ids change every run"

    delta = carry_forward(store, second.run_id, first.run_id)
    assert delta["kept"] == len(first.candidates) and delta["added"] == delta["removed"] == 0
    assert delta["carried"] == 3
    carried = {r["lineage_id"]: r["status_carried"] for r in delta["rows"] if r["status_carried"]}
    assert carried[accepted["lineage_id"]] == "Accepted"
    assert carried[rejected["lineage_id"]] == "Rejected"
    assert carried[deferred["lineage_id"]] == "Deferred"
    now_accepted = next(c for c in store.candidates(second.run_id)
                        if c["lineage_id"] == accepted["lineage_id"])
    assert now_accepted["status"] == "Accepted"
    carry_row = next(d for d in store.decisions(second.run_id)
                     if d["candidate_id"] == now_accepted["candidate_id"])
    assert carry_row["decision"] == "CarryForward" and carry_row["reviewer"] == "priya.silva"
    assert carry_row["actor_role"] == "carry_forward" and first.run_id in carry_row["note"]
    assert store.status_history(second.run_id, now_accepted["candidate_id"])
    assert store.verify_audit_chain()["ok"]
    # Idempotent, and persisted for the council packet.
    again = carry_forward(store, second.run_id, first.run_id)
    assert again["note"] and len(store.run_delta(second.run_id)) == len(delta["rows"])
    store.close()


def test_benefits_are_planned_at_accept_and_realised_by_events(governed):
    result, store = governed
    candidate = _by_status(store, result.run_id)["Proposed"][4]
    review(store, result.run_id, candidate["candidate_id"], "Accept", "priya.silva",
           reason_code="retires_reports", value={"reports_retired": 3, "users_served": 40})
    lineage = candidate["lineage_id"]
    with pytest.raises(ValueError):
        record_benefit_event(store, lineage, "bogus", "COG-1", "2026-10-01", "ops.lead")
    with pytest.raises(PermissionError):
        record_benefit_event(store, lineage, "report_retired", "COG-1", "2026-10-01", "")
    record_benefit_event(store, lineage, "report_retired", "COG-1", "2026-10-01", "ops.lead")
    record_benefit_event(store, lineage, "report_retired", "COG-1", "2026-10-02", "ops.lead",
                         note="duplicate notice counts once")
    record_benefit_event(store, lineage, "charter_approved", "DP-7", "2026-10-15T09:00:00",
                         "dpf.gate")
    summary = benefits_summary(store, lineage)
    assert summary["planned"]["reports_retired"] == 3 and summary["planned"]["users_served"] == 40
    assert summary["realised"]["reports_retired"] == 1
    assert summary["realised"]["charter_approved"] and summary["hours_accept_to_charter"] > 0
    assert summary["variance"]["reports_retired"] == -2


# --------------------------------------------------------------------------
# 5. Conflict and name ledgers (R-14, R-57)
# --------------------------------------------------------------------------

def test_conflict_adjudication_is_validated_and_ledgered(governed):
    result, store = governed
    conflict = store.conflicts(result.run_id)[0]
    with pytest.raises(ProposeOnlyError):
        store.resolve_conflict(result.run_id, conflict["conflict_id"], "RESOLVED_A", "")
    with pytest.raises(ValueError, match="not a conflict status"):
        store.resolve_conflict(result.run_id, conflict["conflict_id"], "WHATEVER", "steward.a")
    with pytest.raises(ValueError, match="rationale"):
        store.resolve_conflict(result.run_id, conflict["conflict_id"], "RESOLVED_A", "steward.a")
    outcome = store.resolve_conflict(result.run_id, conflict["conflict_id"], "RESOLVED_A",
                                     "steward.a", note="A is the ledger basis used by Finance")
    assert outcome["authoritative_metric_id"] == conflict["metric_id_a"]
    ledger = store.conflict_decisions(outcome["conflict_key"])
    assert len(ledger) == 1 and ledger[0]["rationale"] and ledger[0]["steward"] == "steward.a"
    decision = next(d for d in store.decisions(result.run_id) if d["subject_type"] == "conflict")
    assert decision["previous_value"] == "OPEN" and decision["new_value"] == "RESOLVED_A"
    refreshed = next(c for c in store.conflicts(result.run_id)
                     if c["conflict_id"] == conflict["conflict_id"])
    assert refreshed["resolution_status"] == "RESOLVED_A" and refreshed["steward_id"] == "steward.a"


def test_metric_names_keep_their_history(governed):
    result, store = governed
    metric = store.metrics(result.run_id)[1]
    with pytest.raises(ValueError, match="definition status"):
        store.accept_metric_name(result.run_id, metric["metric_id"], "steward.a",
                                 definition_status="Final")
    outcome = store.accept_metric_name(result.run_id, metric["metric_id"], "steward.a",
                                       new_name="net_receivable_days", definition_status="Certified")
    assert outcome["previous_name"] == metric["canonical_name"]
    history = store.metric_history(result.run_id, metric["metric_id"])
    assert history[0]["canonical_name"] == metric["canonical_name"]
    assert history[0]["name_status"] == "AI_DRAFT" and history[0]["change"] == "before renamed"
    refreshed = next(m for m in store.metrics(result.run_id) if m["metric_id"] == metric["metric_id"])
    assert refreshed["canonical_name"] == "net_receivable_days"
    assert refreshed["name_status"] == "ACCEPTED" and refreshed["definition_status"] == "Certified"
    ledger = store.metric_name_decisions(metric["fingerprint"])
    assert ledger[-1]["new_name"] == "net_receivable_days" and ledger[-1]["decision_id"]


def test_ledgers_seed_the_next_run_and_flag_changed_definitions(tmp_path):
    store = Store(tmp_path / "seed.db")
    first = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store)
    conflict = store.conflicts(first.run_id)[0]
    metric = store.metrics(first.run_id)[0]
    store.resolve_conflict(first.run_id, conflict["conflict_id"], "RESOLVED_B", "steward.a",
                           note="B matches the regulatory definition")
    store.accept_metric_name(first.run_id, metric["metric_id"], "steward.a", new_name="agreed_name")
    report_id = store.query("SELECT report_id FROM GRAPH_NODE_REPORT WHERE run_id = ? "
                            "ORDER BY report_id LIMIT 1", (first.run_id,))[0]["report_id"]
    mark_decision_critical(store, first.run_id, report_id, "priya.silva")

    # The pipeline seeds the ledgers itself: a steward's verdict survives the next
    # run without anyone remembering to run a command.
    second = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store)
    assert second.manifest.stats["ledger_seeding"]["applied"] == 3
    assert second.manifest.stats["ledger_seeding"]["definition_changed"] == 0
    assert next(c for c in store.conflicts(second.run_id)
                if c["conflict_id"] == conflict["conflict_id"])["resolution_status"] == "RESOLVED_B"
    renamed = next(m for m in store.metrics(second.run_id) if m["metric_id"] == metric["metric_id"])
    assert renamed["canonical_name"] == "agreed_name" and renamed["name_status"] == "ACCEPTED"
    assert store.metric_history(second.run_id, metric["metric_id"])[0]["change"] == "before seed from ledger"
    assert store.query("SELECT decision_critical FROM GRAPH_NODE_REPORT WHERE run_id = ? AND "
                       "report_id = ?", (second.run_id, report_id))[0]["decision_critical"] == 1
    assert seed_from_ledgers(store, second.run_id)["note"], "seeding twice is a no-op"

    # A third run where the adjudicated pair now fingerprints differently: the
    # ledger recognises the same KPIs and re-opens with 'definition changed'.
    # The pipeline seeds on its way through, so to stand in for a run whose
    # calculation genuinely changed we re-point the fingerprint and let the
    # ledger see this run for the first time.
    third = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store)
    kpis_a = store.query("SELECT kpi_ids FROM KPI_CANONICAL WHERE run_id = ? AND metric_id = ?",
                         (third.run_id, conflict["metric_id_a"]))[0]["kpi_ids"]
    store.connection.execute("UPDATE KPI_CANONICAL SET fingerprint = 'CHANGED' WHERE run_id = ? "
                             "AND metric_id = ?", (third.run_id, conflict["metric_id_a"]))
    store.connection.execute("DELETE FROM GOVERNANCE_SEED WHERE run_id = ?", (third.run_id,))
    store.connection.execute("UPDATE KPI_CONFLICT SET resolution_status = 'OPEN' "
                             "WHERE run_id = ?", (third.run_id,))
    seeded = seed_from_ledgers(store, third.run_id)
    flagged = [r for r in seeded["rows"] if r["outcome"] == "definition changed"]
    assert any(r["subject_id"] == conflict["conflict_id"] for r in flagged)
    assert json.loads(kpis_a), "the flag is keyed on the KPI ids behind the metric"
    assert next(c for c in store.conflicts(third.run_id)
                if c["conflict_id"] == conflict["conflict_id"])["resolution_status"] == "OPEN"
    store.close()


def test_conflict_key_is_order_free():
    assert conflict_key("a", "b") == conflict_key("b", "a") != conflict_key("a", "c")


# --------------------------------------------------------------------------
# 6. Reason codes (R-43)
# --------------------------------------------------------------------------

def test_reason_codes_are_validated_server_side(governed):
    result, store = governed
    candidate = _standalone(store, result.run_id)
    for decision in ("Reject", "Defer", "Merge", "Split", "Override"):
        with pytest.raises(ReasonCodeError):
            validate_reason(decision, "")
    with pytest.raises(ReasonCodeError):
        validate_reason("Reject", "not_a_real_code")
    with pytest.raises(ReasonCodeError, match="requires a note"):
        validate_reason("Reject", "other")
    assert validate_reason("Reject", "other", "the taxonomy lacks 'superseded by policy'") == "other"
    assert validate_reason("Accept", "") == ""
    assert {"consumer_confirmed", "decision_critical"} <= set(REASON_CODES["Override"])
    assert "accept_with_open_dependencies" in REASON_CODES["Accept"]
    assert "gate_waived" in REASON_CODES["AcceptWithException"]
    with pytest.raises(ReasonCodeError):
        review(store, result.run_id, candidate["candidate_id"], "Reject", "priya.silva",
               reason_code="value_clear")
    outcome = review(store, result.run_id, candidate["candidate_id"], "Accept", "priya.silva",
                     reason_code="value_clear", usable_without_rework=False,
                     rework_needed=["rename", "drop two metrics"])
    row = store.decisions(result.run_id)[0]
    assert row["decision_id"] == outcome.decision_id
    assert row["usable_without_rework"] == 0
    assert json.loads(row["rework_needed"]) == ["rename", "drop two metrics"]


def test_override_values_are_validated(governed):
    result, store = governed
    candidate = _by_status(store, result.run_id)["Proposed"][-1]
    with pytest.raises(OverrideValueError):
        validate_override("payload", "x")
    with pytest.raises(OverrideValueError):
        validate_override("archetype", "Spreadsheet")
    with pytest.raises(OverrideValueError):
        validate_override("proposed_name", "x" * 5000)
    with pytest.raises(OverrideValueError):
        review(store, result.run_id, candidate["candidate_id"], "Override", "priya.silva",
               reason_code="wrong_tier", field_overridden="tier", new_value="Gold")
    outcome = review(store, result.run_id, candidate["candidate_id"], "Override", "priya.silva",
                     reason_code="wrong_tier", field_overridden="tier", new_value="Aggregate")
    assert outcome.row["previous_value"] == candidate["tier"]
    assert store.candidate(result.run_id, candidate["candidate_id"])["tier"] == "Aggregate"


# --------------------------------------------------------------------------
# 7. Feedback statistics (R-17)
# --------------------------------------------------------------------------

def _synthetic_reviewer(store: Store, run_id: str, rule, count: int = 80, seed: int = 11) -> None:
    rows = _synthetic_candidates(store, run_id, count, seed=seed)
    values = [rule(r) for r in rows]
    threshold = sorted(values)[len(values) // 2]
    for index, (row, value) in enumerate(zip(rows, values)):
        accept = value > threshold
        if index % 20 == 0:
            accept = not accept                       # a little reviewer noise
        _decide(store, run_id, row["candidate_id"], "Accept" if accept else "Reject",
                "council", "value_clear" if accept else "too_small")


def test_weights_recover_a_known_reviewer_rule(tmp_path):
    store = Store(tmp_path / "feedback.db")
    _synthetic_reviewer(store, "RUN-FB", lambda r: (0.4 * r["demand"] + 0.4 * r["consolidation"]
                                                    + 0.2 * r["feasibility"] - 0.3 * r["risk"]))
    # Repeated decisions on one candidate count once, and the last one wins.
    first = next(r for r in collect_training_rows(store) if r["label"] == 1.0)
    for _ in range(3):
        reverse_decision(store, "RUN-FB", first["candidate_id"], "council.chair", "new_evidence")
        reverse_decision(store, "RUN-FB", first["candidate_id"], "council", "new_evidence")
        review(store, "RUN-FB", first["candidate_id"], "Accept", "council", reason_code="value_clear")
    rows = collect_training_rows(store)
    assert len(rows) == 80 and len({r["lineage_key"] for r in rows}) == 80

    proposal = reestimate_weights(store, EngineConfig().weights, as_of=AS_OF)
    assert proposal.status == STATUS_PROPOSED
    assert proposal.sample_size == 80 and min(proposal.accepted, proposal.rejected) >= MIN_PER_CLASS
    assert proposal.coefficients["demand"] > 0 and proposal.coefficients["consolidation"] > 0
    assert proposal.coefficients["feasibility"] > 0 and proposal.coefficients["risk"] < 0
    assert proposal.intervals["demand"][0] > 0 and proposal.intervals["risk"][1] < 0
    assert proposal.baseline_accuracy >= 0.5
    assert proposal.holdout_accuracy > proposal.baseline_accuracy
    assert proposal.cv_scheme == "leave-one-out" and proposal.regularisation in (0.01, 0.1, 1.0, 10.0)
    assert proposal.proposed.weight_version == "v20260917-proposed"
    dims = proposal.proposed.dimensions
    assert abs(sum(v for k, v in dims.items() if k != "risk") - 0.9) < 0.01 and dims["risk"] < 0
    assert dims["demand"] > dims["feasibility"], "the rule weighted demand twice feasibility"
    assert reestimate_weights(store, EngineConfig().weights, as_of=AS_OF).to_dict() == proposal.to_dict()

    approved = approve_weights(store, proposal, "data product council")
    assert store.current_weights().weight_version == approved.weight_version
    assert store.current_weights().approved_by == "data product council"
    assert store.config_changes()[0]["actor"] == "data product council"
    store.close()


def test_a_sign_contradiction_is_not_proposed_and_cannot_be_approved(tmp_path):
    store = Store(tmp_path / "contradiction.db")
    _synthetic_reviewer(store, "RUN-FB", lambda r: r["risk"] - 0.2 * r["demand"], seed=5)
    proposal = reestimate_weights(store, EngineConfig().weights, as_of=AS_OF)
    assert proposal.status == STATUS_CONTRADICTION
    assert "risk" in proposal.contradictions and proposal.coefficients["risk"] > 0
    assert proposal.proposed.dimensions == EngineConfig().weights.dimensions
    with pytest.raises(FeedbackGateError):
        approve_weights(store, proposal, "data product council")
    assert store.current_weights().weight_version == "v1.0-initial"
    store.close()


def test_approval_needs_the_floor_and_a_hold_out_win(tmp_path):
    store = Store(tmp_path / "gate.db")
    _synthetic_candidates(store, "RUN-FEW", 30, seed=2)
    for index, row in enumerate(store.candidates("RUN-FEW")):
        _decide(store, "RUN-FEW", row["candidate_id"], "Accept" if index % 2 else "Reject",
                "council", "" if index % 2 else "too_small")
    proposal = reestimate_weights(store, EngineConfig().weights, as_of=AS_OF)
    assert proposal.sample_size == 30 < MIN_DECISIONS
    assert str(MIN_DECISIONS) in proposal.note and str(MIN_PER_CLASS) in proposal.note
    with pytest.raises(FeedbackGateError):
        approve_weights(store, proposal, "data product council")
    weak = WeightProposal(proposed=ScoreWeights(weight_version="v-weak"), current_version="v1.0-initial",
                          sample_size=60, accepted=30, rejected=30, baseline_accuracy=0.5,
                          holdout_accuracy=0.5, status=STATUS_PROPOSED)
    with pytest.raises(FeedbackGateError, match="does not beat"):
        approve_weights(store, weak, "data product council")
    assert store.weights("v-weak") == []
    store.close()


def test_a_composite_is_not_accepted_over_a_part_the_board_parked(governed):
    """R-11: accepting on top of a Rejected or Deferred part is a commitment
    to a delivery date nobody has agreed to, so it needs saying out loud."""
    result, store = governed
    rows = store.query(
        "SELECT DISTINCT d.candidate_id FROM DP_CANDIDATE_DEPENDENCY d "
        "JOIN DP_CANDIDATE c ON c.run_id = d.run_id AND c.candidate_id = d.target "
        "WHERE d.run_id = ? AND d.type IN ('candidate', 'entity_master') "
        "  AND c.status IN ('Exploratory', 'Blocked', 'Deferred', 'Rejected')",
        (result.run_id,))
    stacked = [row["candidate_id"] for row in rows
               if (store.candidate(result.run_id, row["candidate_id"]) or {}).get("status")
               == "Proposed"]
    assert stacked, "earlier decisions in this module should have parked a part"
    candidate_id = stacked[0]

    with pytest.raises(GateError, match="decided not to build yet"):
        review(store, result.run_id, candidate_id, "Accept", "priya.silva",
               reason_code="value_clear")
    # The refusal leaves nothing behind: the decision row rolls back with it.
    assert store.candidate(result.run_id, candidate_id)["status"] == "Proposed"
    assert not [d for d in store.decisions(result.run_id)
                if d["candidate_id"] == candidate_id and d["decision"] == "Accept"]

    outcome = review(store, result.run_id, candidate_id, "Accept", "priya.silva",
                     reason_code="accept_with_open_dependencies",
                     note="the entity master is in the next wave and the board knows")
    assert outcome.status == "Accepted"
