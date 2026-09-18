"""Review workflow, feedback loop, seeds, portfolio views and the CLI."""
from __future__ import annotations

import datetime as _dt

import pytest

from dpre.config import EngineConfig
from dpre.ingest import ingest_automated
from dpre.pipeline import run_pipeline
from dpre.review import feedback_report, mark_decision_critical, review
from dpre.review.feedback import (
    MIN_DECISIONS, approve_weights, archetype_override_rates, reestimate_weights,
    resolution_advice,
)
from dpre.seeds import SEED_INDEX, build_seeds, write_seeds
from dpre.store import ProposeOnlyError, Store


@pytest.fixture(scope="module")
def workflow_run(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("wf") / "engine.db")
    result = run_pipeline(ingest_automated("utility", as_of=_dt.date(2026, 9, 17)), store=store)
    yield result, store
    store.close()


def test_accept_reject_defer_are_recorded_with_their_reason(workflow_run):
    result, store = workflow_run
    candidates = store.candidates(result.run_id)
    for candidate, decision, status in (
        (candidates[0], "Accept", "Accepted"),
        (candidates[1], "Reject", "Rejected"),
        (candidates[2], "Defer", "Deferred"),
    ):
        outcome = review(store, result.run_id, candidate["candidate_id"], decision,
                         "priya.silva", reason_code="value_clear", note="tested")
        assert outcome.status == status
    decisions = store.decisions(result.run_id)
    assert len(decisions) >= 3
    assert all(row["reviewer"] == "priya.silva" for row in decisions)
    assert all(row["decided_at"] for row in decisions)


def test_merge_moves_the_metrics_and_marks_the_source(workflow_run):
    result, store = workflow_run
    candidates = store.candidates(result.run_id)
    source, target = candidates[3], candidates[4]
    before = len(target["payload"]["metric_ids"])
    outcome = review(store, result.run_id, source["candidate_id"], "Merge", "priya.silva",
                     reason_code="duplicate_candidate",
                     target_candidate_id=target["candidate_id"])
    assert outcome.status == "Merged"
    after = store.candidate(result.run_id, target["candidate_id"])
    assert len(after["payload"]["metric_ids"]) >= before
    assert source["candidate_id"] in after["payload"]["merged_from"]


def test_split_by_consumer_creates_linked_children(workflow_run):
    result, store = workflow_run
    biggest = max(store.candidates(result.run_id),
                  key=lambda row: len(row["payload"]["metric_ids"]))
    outcome = review(store, result.run_id, biggest["candidate_id"], "Split", "priya.silva",
                     reason_code="mixed_consumers", split_by="consumer")
    if outcome.created_candidates:
        for child_id in outcome.created_candidates:
            child = store.candidate(result.run_id, child_id)
            assert child["parent_candidate_id"] == biggest["candidate_id"]
            assert child["origin"] == "reviewer_split"
            assert child["status"] == "Proposed"


def test_an_override_is_logged_with_the_field_changed(workflow_run):
    result, store = workflow_run
    candidate = store.candidates(result.run_id)[5]
    review(store, result.run_id, candidate["candidate_id"], "Override", "priya.silva",
           reason_code="wrong_archetype", field_overridden="archetype",
           new_value="Event Stream")
    refreshed = store.candidate(result.run_id, candidate["candidate_id"])
    assert refreshed["archetype"] == "Event Stream"
    logged = [d for d in store.decisions(result.run_id) if d["decision"] == "Override"]
    assert logged and logged[0]["field_overridden"] == "archetype"


def test_marking_a_report_decision_critical_needs_a_reviewer(workflow_run):
    result, store = workflow_run
    report_id = store.query("SELECT report_id FROM GRAPH_NODE_REPORT WHERE run_id = ? LIMIT 1",
                            (result.run_id,))[0]["report_id"]
    with pytest.raises(ProposeOnlyError):
        mark_decision_critical(store, result.run_id, report_id, "")
    assert mark_decision_critical(store, result.run_id, report_id, "priya.silva")["decision_critical"]


def test_accepting_an_ai_drafted_name_needs_a_steward(workflow_run):
    result, store = workflow_run
    metric_id = store.metrics(result.run_id)[0]["metric_id"]
    with pytest.raises(ProposeOnlyError):
        store.accept_metric_name(result.run_id, metric_id, "")
    store.accept_metric_name(result.run_id, metric_id, "priya.silva")
    refreshed = next(m for m in store.metrics(result.run_id) if m["metric_id"] == metric_id)
    assert refreshed["name_status"] == "ACCEPTED"


def test_weights_are_not_re_estimated_from_too_few_decisions(workflow_run):
    result, store = workflow_run
    proposal = reestimate_weights(store, EngineConfig().weights)
    assert proposal.sample_size < MIN_DECISIONS
    assert str(MIN_DECISIONS) in proposal.note
    assert proposal.proposed.dimensions == EngineConfig().weights.dimensions


def test_weights_are_re_estimated_once_there_are_enough_decisions(tmp_path):
    """Logistic regression of Accept against Reject on the four dimensions."""
    store = Store(tmp_path / "feedback.db")
    result = run_pipeline(ingest_automated("banking", as_of=_dt.date(2026, 9, 17)), store=store)
    # Synthesize a reviewer who accepts on consolidation and rejects on risk.
    rows = sorted(store.candidates(result.run_id),
                  key=lambda r: -(r["payload"]["score"] or {}).get("consolidation", 0))
    for index in range(MIN_DECISIONS + 10):
        row = rows[index % len(rows)]
        payload = row["payload"]["score"] or {}
        decision = "Accept" if payload.get("consolidation", 0) >= 70 else "Reject"
        store.connection.execute(
            "INSERT INTO REVIEW_DECISION (run_id, candidate_id, decision, reason_code, "
            "reviewer, decided_at, field_overridden, new_value, target_candidate_id, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (result.run_id, row["candidate_id"], decision, "synthetic", "council",
             "2026-09-17T00:00:00", "", "", "", ""))
    store.connection.commit()

    proposal = reestimate_weights(store, EngineConfig().weights)
    assert proposal.sample_size >= MIN_DECISIONS
    assert proposal.coefficients
    assert 0.0 <= proposal.accuracy <= 1.0
    assert abs(sum(v for k, v in proposal.proposed.dimensions.items() if k != "risk") - 0.9) < 0.01
    assert proposal.proposed.dimensions["risk"] < 0
    assert not proposal.approved

    with pytest.raises(PermissionError):
        approve_weights(store, proposal, "")
    approved = approve_weights(store, proposal, "data product council")
    assert approved.approved_by == "data product council"
    assert store.weights(approved.weight_version)
    store.close()


def test_the_feedback_report_covers_all_three_re_estimations(workflow_run):
    result, store = workflow_run
    report = feedback_report(store, EngineConfig().weights)
    assert set(report) >= {"weights", "archetype_rules", "clustering_resolution", "decisions"}
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
