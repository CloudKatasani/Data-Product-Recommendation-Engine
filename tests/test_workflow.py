"""Review workflow, feedback loop, seeds, portfolio views and the CLI."""
from __future__ import annotations

import datetime as _dt
import json
import random

import pytest

from dpre.config import EngineConfig
from dpre.governance import ReasonCodeError, TransitionError, lineage_id
from dpre.ingest import ingest_automated
from dpre.models import ReviewDecision
from dpre.pipeline import run_pipeline
from dpre.review import (
    accept_with_exception, feedback_report, mark_decision_critical, reverse_decision, review,
)
from dpre.review.feedback import (
    FEATURES, MIN_DECISIONS, approve_weights, archetype_override_rates, reestimate_weights,
    resolution_advice,
)
from dpre.seeds import SEED_INDEX, build_seeds, write_seeds
from dpre.store import GateError, ProposeOnlyError, Store


@pytest.fixture(scope="module")
def workflow_run(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("wf") / "engine.db")
    result = run_pipeline(ingest_automated("utility", as_of=_dt.date(2026, 9, 17)), store=store)
    yield result, store
    store.close()


def _proposed(store: Store, run_id: str) -> list[dict]:
    """Candidates a reviewer can still decide on, in a stable order."""
    return store.candidates(run_id, "Proposed")


def test_accept_reject_defer_are_recorded_with_their_reason(workflow_run):
    result, store = workflow_run
    candidates = _proposed(store, result.run_id)
    for candidate, decision, reason, status in (
        (candidates[0], "Accept", "value_clear", "Accepted"),
        (candidates[1], "Reject", "too_small", "Rejected"),
        (candidates[2], "Defer", "capacity", "Deferred"),
    ):
        outcome = review(store, result.run_id, candidate["candidate_id"], decision,
                         "priya.silva", reason_code=reason, note="tested")
        assert outcome.status == status
        assert outcome.previous_status == "Proposed" and outcome.decision_id
        assert store.status_history(result.run_id, candidate["candidate_id"])[-1]["to_status"] == status
    decisions = store.decisions(result.run_id)
    assert len(decisions) >= 3
    assert all(row["reviewer"] == "priya.silva" for row in decisions)
    assert all(row["decided_at"].endswith("+00:00") for row in decisions), "UTC timestamps"
    assert all(row["row_hash"] and row["reason_code"] for row in decisions)
    assert store.verify_audit_chain(result.run_id)["ok"]


def test_a_reason_code_outside_the_taxonomy_is_refused(workflow_run):
    result, store = workflow_run
    candidate = _proposed(store, result.run_id)[0]
    with pytest.raises(ReasonCodeError):
        review(store, result.run_id, candidate["candidate_id"], "Reject", "priya.silva",
               reason_code="not_a_real_code")
    with pytest.raises(ReasonCodeError):
        review(store, result.run_id, candidate["candidate_id"], "Defer", "priya.silva")
    assert store.candidate(result.run_id, candidate["candidate_id"])["status"] == "Proposed"


def test_accepting_a_blocked_candidate_raises(workflow_run):
    result, store = workflow_run
    blocked = store.candidates(result.run_id, "Blocked")
    assert blocked, "the utility pack plants a sunset source with no successor (G4)"
    with pytest.raises(GateError, match="Blocked"):
        review(store, result.run_id, blocked[0]["candidate_id"], "Accept", "priya.silva",
               reason_code="value_clear")
    rejected = review(store, result.run_id, blocked[0]["candidate_id"], "Reject", "priya.silva",
                      reason_code="source_not_viable")
    assert rejected.status == "Rejected" and rejected.previous_status == "Blocked"


def test_an_exploratory_candidate_needs_an_exception_with_a_second_approver(workflow_run):
    result, store = workflow_run
    exploratory = store.candidates(result.run_id, "Exploratory")
    assert exploratory
    candidate = exploratory[0]
    with pytest.raises(GateError):
        review(store, result.run_id, candidate["candidate_id"], "Accept", "priya.silva")
    failed = list(store._failed_gates(candidate))
    with pytest.raises(GateError):
        accept_with_exception(store, result.run_id, candidate["candidate_id"], "priya.silva",
                              ",".join(failed), "pilot exception", "priya.silva")
    outcome = accept_with_exception(store, result.run_id, candidate["candidate_id"], "priya.silva",
                                    ",".join(failed), "pilot exception", "cdo.office")
    assert outcome.status == "Accepted"
    waivers = store.open_waivers(result.run_id)
    assert [w["candidate_id"] for w in waivers] == [candidate["candidate_id"]]
    assert waivers[0]["second_approver"] == "cdo.office"


def test_reversing_an_acceptance_needs_a_reason_and_another_actor(workflow_run):
    result, store = workflow_run
    accepted = store.candidates(result.run_id, "Accepted")[0]
    with pytest.raises(TransitionError):
        reverse_decision(store, result.run_id, accepted["candidate_id"], "priya.silva",
                         "decided_in_error")
    outcome = reverse_decision(store, result.run_id, accepted["candidate_id"], "council.chair",
                               "decided_in_error", note="accepted before the steward review")
    assert outcome.status == "Rejected" and outcome.previous_status == "Accepted"
    history = store.status_history(result.run_id, accepted["candidate_id"])
    assert history[-1]["from_status"] == "Accepted" and history[-1]["actor"] == "council.chair"


def test_merge_moves_the_metrics_and_marks_the_source(workflow_run):
    result, store = workflow_run
    candidates = _proposed(store, result.run_id)
    source, target = candidates[0], candidates[1]
    before = len(target["payload"]["metric_ids"])
    outcome = review(store, result.run_id, source["candidate_id"], "Merge", "priya.silva",
                     reason_code="duplicate_candidate",
                     target_candidate_id=target["candidate_id"])
    assert outcome.status == "Merged"
    after = store.candidate(result.run_id, target["candidate_id"])
    assert len(after["payload"]["metric_ids"]) >= before
    assert source["candidate_id"] in after["payload"]["merged_from"]
    snapshots = store.payload_history(result.run_id, target["candidate_id"])
    assert snapshots and snapshots[0]["payload"]["metric_ids"] == target["payload"]["metric_ids"]
    assert snapshots[0]["decision_id"] == outcome.decision_id


def test_split_by_consumer_creates_linked_children(workflow_run):
    result, store = workflow_run
    biggest = max(_proposed(store, result.run_id),
                  key=lambda row: len(row["payload"]["metric_ids"]))
    outcome = review(store, result.run_id, biggest["candidate_id"], "Split", "priya.silva",
                     reason_code="mixed_consumers", split_by="consumer")
    assert outcome.status == "Proposed"
    if outcome.created_candidates:
        for child_id in outcome.created_candidates:
            child = store.candidate(result.run_id, child_id)
            assert child["parent_candidate_id"] == biggest["candidate_id"]
            assert child["origin"] == "reviewer_split"
            assert child["status"] == "Proposed"
            assert child["lineage_id"]
        assert store.payload_history(result.run_id, biggest["candidate_id"])


def test_an_override_is_logged_with_the_field_and_prior_value(workflow_run):
    result, store = workflow_run
    candidate = _proposed(store, result.run_id)[0]
    outcome = review(store, result.run_id, candidate["candidate_id"], "Override", "priya.silva",
                     reason_code="wrong_archetype", field_overridden="archetype",
                     new_value="Event Stream")
    refreshed = store.candidate(result.run_id, candidate["candidate_id"])
    assert refreshed["archetype"] == "Event Stream"
    assert refreshed["status"] == candidate["status"], "an override does not move the status"
    logged = [d for d in store.decisions(result.run_id) if d["decision_id"] == outcome.decision_id]
    assert logged[0]["field_overridden"] == "archetype"
    assert logged[0]["previous_value"] == candidate["archetype"]
    assert logged[0]["new_value"] == "Event Stream"


def test_marking_a_report_decision_critical_needs_a_reviewer(workflow_run):
    result, store = workflow_run
    report_id = store.query("SELECT report_id FROM GRAPH_NODE_REPORT WHERE run_id = ? LIMIT 1",
                            (result.run_id,))[0]["report_id"]
    with pytest.raises(ProposeOnlyError):
        mark_decision_critical(store, result.run_id, report_id, "")
    assert mark_decision_critical(store, result.run_id, report_id, "priya.silva")["decision_critical"]
    assert [o["report_id"] for o in store.report_overrides("decision_critical")] == [report_id]


def test_accepting_an_ai_drafted_name_needs_a_steward(workflow_run):
    result, store = workflow_run
    metric_id = store.metrics(result.run_id)[0]["metric_id"]
    with pytest.raises(ProposeOnlyError):
        store.accept_metric_name(result.run_id, metric_id, "")
    store.accept_metric_name(result.run_id, metric_id, "priya.silva")
    refreshed = next(m for m in store.metrics(result.run_id) if m["metric_id"] == metric_id)
    assert refreshed["name_status"] == "ACCEPTED"
    assert store.metric_history(result.run_id, metric_id)[0]["name_status"] == "AI_DRAFT"
    assert any(d["subject_type"] == "metric" for d in store.decisions(result.run_id))


def test_weights_are_not_re_estimated_from_too_few_decisions(workflow_run):
    result, store = workflow_run
    proposal = reestimate_weights(store, EngineConfig().weights)
    assert proposal.sample_size < MIN_DECISIONS
    assert str(MIN_DECISIONS) in proposal.note
    assert proposal.proposed.dimensions == EngineConfig().weights.dimensions


def _synthetic_estate(store: Store, run_id: str, count: int, seed: int) -> list[dict]:
    """Candidates with known dimension scores, enough to clear the 50-candidate floor."""
    rng = random.Random(seed)
    gates = [{"gate": g, "name": g, "passed": True, "detail": "", "effect": ""}
             for g in ("G1", "G2", "G3", "G4")]
    rows = []
    with store.transaction() as cur:
        for index in range(count):
            candidate_id = f"CAND-SYN{index:04d}"
            scores = {f: round(rng.uniform(0, 100), 2) for f in FEATURES}
            cur.execute(
                "INSERT INTO DP_CANDIDATE (run_id, candidate_id, proposed_name, archetype, tier, "
                "grain, domain, status, name_status, payload, lineage_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, candidate_id, f"synthetic {index}", "Metric / KPI", "Aggregate", "Account",
                 "Synthetic", "Proposed", "AI_DRAFT",
                 json.dumps({"metric_ids": [f"MET-{index}"], "reports": [], "consumers": [],
                             "conflicts": []}), lineage_id([f"fp{index}"], "Account")))
            cur.execute(
                "INSERT INTO DP_CANDIDATE_SCORE (run_id, candidate_id, weight_version, demand, "
                "consolidation, feasibility, risk, composite, gate_results, features) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, candidate_id, "v1.0-initial", scores["demand"], scores["consolidation"],
                 scores["feasibility"], scores["risk"], 0.0, json.dumps(gates), "[]"))
            rows.append({"candidate_id": candidate_id, **scores})
    return rows


def test_weights_are_re_estimated_once_there_are_enough_decisions(tmp_path):
    """Regularised logistic regression of Accepted against Rejected on the four
    standardised dimensions, from a reviewer who accepts on consolidation and
    rejects on risk."""
    store = Store(tmp_path / "feedback.db")
    rows = _synthetic_estate(store, "RUN-FB", MIN_DECISIONS + 20, seed=3)
    for row in rows:
        accept = row["consolidation"] - 0.5 * row["risk"] >= 25
        store.record_decision(ReviewDecision(
            candidate_id=row["candidate_id"], decision="Accept" if accept else "Reject",
            reason_code="value_clear" if accept else "too_small", reviewer="council",
            decided_at="2026-09-17T00:00:00", run_id="RUN-FB"))

    proposal = reestimate_weights(store, EngineConfig().weights, as_of=_dt.date(2026, 9, 17))
    assert proposal.status == "proposed"
    assert proposal.sample_size >= MIN_DECISIONS
    assert proposal.coefficients["consolidation"] > 0 and proposal.coefficients["risk"] < 0
    assert 0.0 <= proposal.holdout_accuracy <= 1.0 and proposal.holdout_accuracy > proposal.baseline_accuracy
    assert set(proposal.intervals) == set(FEATURES)
    assert abs(sum(v for k, v in proposal.proposed.dimensions.items() if k != "risk") - 0.9) < 0.01
    assert proposal.proposed.dimensions["risk"] < 0
    # The rule ignored demand and feasibility: reported as unsupported, floored, never dropped.
    assert {"demand", "feasibility"} <= set(proposal.unsupported)
    assert all(proposal.proposed.dimensions[k] >= 0.05 for k in ("demand", "consolidation", "feasibility"))
    assert proposal.proposed.dimensions["consolidation"] > proposal.proposed.dimensions["demand"]
    assert not proposal.approved
    report = proposal.to_dict()
    assert {"baseline_accuracy", "holdout_accuracy", "intervals_95", "coefficients", "status"} <= set(report)

    with pytest.raises(PermissionError):
        approve_weights(store, proposal, "")
    approved = approve_weights(store, proposal, "data product council")
    assert approved.approved_by == "data product council"
    assert store.weights(approved.weight_version)
    assert store.current_weights().weight_version == approved.weight_version
    store.close()


def test_the_feedback_report_covers_all_three_re_estimations(workflow_run):
    result, store = workflow_run
    report = feedback_report(store, EngineConfig().weights)
    assert set(report) >= {"weights", "weights_in_force", "archetype_rules",
                           "clustering_resolution", "usable_without_rework", "decisions"}
    assert report["weights_in_force"]["weight_version"] == "v1.0-initial"
    assert archetype_override_rates(store)
    assert resolution_advice(store)
    assert "never re-score" in report["note"]


def test_seed_artifacts_cover_the_documented_stages(workflow_run, tmp_path):
    result, store = workflow_run
    candidate = result.ranked()[0]
    files = write_seeds(candidate, result.canonical, result.graph, tmp_path)
    names = {path.name for path in files}
    assert names == {
        "stage1-decision-register.yaml", "stage2-charter.yaml", "stage3-source-inventory.yaml",
        "stage5-attribute-register.xlsx", "stage6-semantic-model.yaml",
        "stage12-retirement-list.csv", "collibra-payload.json",
    }
    assert all(path.stat().st_size > 0 for path in files)
    assert {entry["stage"] for entry in SEED_INDEX} == {1, 2, 3, 5, 6, 12}


def test_conflicts_become_explicit_semantic_model_decisions(workflow_run):
    result, _store = workflow_run
    candidate = max(result.candidates, key=lambda c: len(c.conflicts))
    model = build_seeds(candidate, result.canonical, result.graph)["semantic_model"]
    assert model["semantic_model"]["certified"] is False
    assert all(metric["certified"] is False for metric in model["metrics"])
    assert model["open_decisions"]
    thresholds = [d for d in model["open_decisions"] if d["pattern"] == "THRESHOLD"]
    if thresholds:
        assert model["parameters"], "a threshold conflict becomes a parameter, not a constant"
        assert all(p["default"] == "TO BE SET BY STEWARD" for p in model["parameters"])


def test_the_retirement_list_names_the_owners_to_notify(workflow_run):
    result, _store = workflow_run
    candidate = result.ranked()[0]
    rows = build_seeds(candidate, result.canonical, result.graph)["retirement_list"]
    assert rows
    assert all(row["notify_by"] == "TO BE SET" for row in rows)
    assert any(row["action"].startswith("retire") for row in rows)


def test_portfolio_views_are_consistent_with_the_backlog(workflow_run):
    result, _store = workflow_run
    curve = result.portfolio["coverage_curve"]
    assert len(curve) == len(result.candidates)
    assert curve == sorted(curve, key=lambda row: row["n"])
    coverages = [row["cumulative_coverage"] for row in curve]
    assert coverages == sorted(coverages), "coverage can only accumulate"
    assert 0.0 <= coverages[-1] <= 1.0
    assert len(result.portfolio["retirement_map"]) == len(result.candidates)


def test_cli_runs_the_automated_path(tmp_path, capsys):
    from dpre.cli import main
    code = main(["--db", str(tmp_path / "cli.db"), "run", "automated", "--industry", "retail",
                 "--as-of", "2026-09-17", "--workspace", str(tmp_path),
                 "--json", str(tmp_path / "out.json")])
    assert code == 0
    output = capsys.readouterr().out
    assert "Quality gates" in output and "Top candidates" in output
    assert (tmp_path / "out.json").exists()


def test_cli_lists_candidates_and_answers_questions(tmp_path, capsys):
    from dpre.cli import main
    db = str(tmp_path / "cli.db")
    main(["--db", db, "run", "automated", "--industry", "telecom", "--as-of", "2026-09-17",
          "--workspace", str(tmp_path)])
    capsys.readouterr()
    assert main(["--db", db, "candidates"]) == 0
    assert "composite" in capsys.readouterr().out
    assert main(["--db", db, "ask", "which candidates retire the most reports"]) == 0
    assert "named query" in capsys.readouterr().out
