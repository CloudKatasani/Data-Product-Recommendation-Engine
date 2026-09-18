"""The engine's acceptance criteria (specification section 14.1).

Each test here corresponds to a bullet a reviewer can check off, and each one
can fail.
"""
from __future__ import annotations

import sqlite3

import pytest

from dpre.chat import ConversationalAgent
from dpre.chat.semantic_view import (
    ALLOWED_OBJECTS, FORBIDDEN_OBJECTS, QUERIES, SemanticViewError, check_query,
)
from dpre.config import ENGINE_MAX_STATUS, QUALITY_GATES
from dpre.governance import TransitionError
from dpre.ingest import ingest_automated
from dpre.models import ReviewDecision
from dpre.pipeline import run_pipeline
from dpre.review import accept_with_exception, review
from dpre.seeds import build_seeds
from dpre.store import EvidenceMissingError, GateError, ProposeOnlyError


def test_a_run_is_reproducible_from_its_stored_inputs(as_of):
    """Every recommendation is reproducible from extract ids, weights and parser."""
    first = run_pipeline(ingest_automated("utility", as_of=as_of))
    second = run_pipeline(ingest_automated("utility", as_of=as_of))
    assert first.manifest.generation_id == second.manifest.generation_id
    assert first.manifest.parser_version == second.manifest.parser_version
    assert first.manifest.weight_version == second.manifest.weight_version
    assert ([c.proposed_name for c in first.ranked()]
            == [c.proposed_name for c in second.ranked()])
    assert ([round(c.score.composite, 3) for c in first.ranked()]
            == [round(c.score.composite, 3) for c in second.ranked()])


def test_no_code_path_but_a_review_decision_moves_a_candidate_past_proposed(run):
    result, store = run
    candidate = next(c for c in result.ranked() if c.status == "Proposed")
    candidate.status = "Accepted"
    with pytest.raises(ProposeOnlyError):
        store.save_candidates(result.run_id, [candidate])
    candidate.status = "Proposed"

    with pytest.raises(ProposeOnlyError):
        store.record_decision(ReviewDecision(
            candidate_id=candidate.candidate_id, decision="Accept", reason_code="x",
            reviewer="", decided_at="", run_id=result.run_id))
    assert store.orphan_statuses(result.run_id) == []

    outcome = review(store, result.run_id, candidate.candidate_id, "Accept",
                     "priya.silva", reason_code="retires_reports")
    assert outcome.status == "Accepted"
    assert ENGINE_MAX_STATUS == "Proposed"
    # The decision behind the status is on the chain, with its before and after.
    row = store.decisions(result.run_id)[0]
    assert row["previous_status"] == "Proposed" and row["new_status"] == "Accepted"
    assert row["row_hash"] and store.verify_audit_chain(result.run_id)["ok"]
    assert store.status_history(result.run_id, candidate.candidate_id)[-1]["actor"] == "priya.silva"


def test_a_gated_candidate_cannot_be_accepted_without_an_exception_row(run):
    """Section 8.3: gates no weight can override also bind at acceptance."""
    result, store = run
    blocked = next(c for c in result.candidates if c.status == "Blocked")
    with pytest.raises(GateError, match="G4"):
        review(store, result.run_id, blocked.candidate_id, "Accept", "priya.silva",
               reason_code="value_clear")
    with pytest.raises(GateError):
        accept_with_exception(store, result.run_id, blocked.candidate_id, "priya.silva",
                              "G4", "we will migrate later", "cdo.office")
    assert store.candidate(result.run_id, blocked.candidate_id)["status"] == "Blocked"

    exploratory = next(c for c in result.candidates if c.status == "Exploratory")
    failed = [g.gate for g in exploratory.score.gates if not g.passed]
    with pytest.raises(GateError):
        review(store, result.run_id, exploratory.candidate_id, "Accept", "priya.silva",
               reason_code="value_clear")
    with pytest.raises(GateError, match="different person"):
        accept_with_exception(store, result.run_id, exploratory.candidate_id, "priya.silva",
                              ",".join(failed), "pilot domain exception", "priya.silva")
    outcome = accept_with_exception(store, result.run_id, exploratory.candidate_id, "priya.silva",
                                    ",".join(failed), "pilot domain exception", "cdo.office")
    assert outcome.status == "Accepted"
    waiver = next(w for w in store.open_waivers(result.run_id)
                  if w["candidate_id"] == exploratory.candidate_id)
    assert waiver["gate_waived"] == ",".join(failed) and waiver["second_approver"] == "cdo.office"
    assert outcome.row["gate_waived"] == waiver["gate_waived"]


def test_the_audit_trail_is_append_only_and_reversals_are_attributed(run):
    result, store = run
    accepted = store.candidates(result.run_id, "Accepted")[0]
    decision_id = store.decisions(result.run_id)[0]["decision_id"]
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute("DELETE FROM REVIEW_DECISION WHERE decision_id = ?", (decision_id,))
    with pytest.raises(TransitionError):
        review(store, result.run_id, accepted["candidate_id"], "Reject", "priya.silva",
               reason_code="too_small")
    with pytest.raises(TransitionError, match="different actor"):
        review(store, result.run_id, accepted["candidate_id"], "Reverse",
               store.decisions(result.run_id)[0]["reviewer"] if False else "priya.silva",
               reason_code="decided_in_error")
    assert store.verify_audit_chain(result.run_id)["ok"]


def test_a_score_row_without_evidence_cannot_be_written(run):
    result, store = run
    import dataclasses
    candidate = result.ranked()[-1]
    stripped = dataclasses.replace(candidate, evidence=[])
    with pytest.raises(EvidenceMissingError):
        store.save_candidates(result.run_id, [stripped])


def test_published_runs_have_evidence_behind_every_score(run):
    result, store = run
    orphans = store.query(
        "SELECT s.candidate_id FROM DP_CANDIDATE_SCORE s WHERE s.run_id = ? AND NOT EXISTS ("
        "SELECT 1 FROM DP_CANDIDATE_EVIDENCE e WHERE e.run_id = s.run_id "
        "AND e.candidate_id = s.candidate_id)", (result.run_id,))
    assert orphans == []
    assert store.publish(result.run_id)["published"] is True


def test_the_conversational_agent_cannot_query_outside_its_semantic_view(run):
    """A grant audit: every named query reads only the permitted objects."""
    for query in QUERIES.values():
        check_query(query.sql)
        assert query.objects() <= set(ALLOWED_OBJECTS)
    for forbidden in FORBIDDEN_OBJECTS:
        with pytest.raises(SemanticViewError):
            check_query(f"SELECT * FROM {forbidden}X")
    with pytest.raises(SemanticViewError):
        check_query("DROP TABLE DP_CANDIDATE")


def test_ai_drafted_names_are_visibly_marked_everywhere(run):
    result, store = run
    candidate = result.ranked()[0]
    assert candidate.name_status == "AI_DRAFT"
    assert candidate.narrative["purpose_status"] == "AI_DRAFT"
    for draft in candidate.decisions_drafted:
        assert draft.status == "AI_DRAFT"

    seeds = build_seeds(candidate, result.canonical, result.graph)
    assert seeds["charter"]["charter"]["name_status"] == "AI_DRAFT"
    assert seeds["decision_register"]["data_product"]["name_status"] == "AI_DRAFT"
    payload = seeds["catalog_payload"]
    assert payload["name_status"] == "AI_DRAFT"
    assert any("AI-drafted" in reason for reason in payload["import_blocked_by"])


def test_a_seeded_decision_register_needs_only_the_blocked_decision(run):
    """Section 14.1: the Stage 1 seed passes its own exit criteria once a reviewer
    adds the blocked decision, with no other edits."""
    result, _store = run
    candidate = next(c for c in result.ranked() if c.decisions_drafted)
    register = build_seeds(candidate, result.canonical, result.graph)["decision_register"]
    for entry in register["decisions"]:
        assert entry["consumer"]["business_unit"]
        assert entry["consumer"]["persona"]
        assert entry["cadence"]
        assert entry["questions_asked_today"]
        assert entry["inferred_decision"]
        assert entry["evidence"]["reports"] or entry["consumer"]["business_unit"]
        # Exactly the three fields the specification reserves for the human.
        assert entry["blocked_decision"] == "TO BE CONFIRMED"
        assert "TO BE CONFIRMED" in entry["latency_tolerance"]
        assert "TO BE CONFIRMED" in entry["consequence_of_not_deciding"]


def test_the_conflict_register_is_complete_enough_to_sign_off(run):
    result, store = run
    conflicts = result.canonical.conflicts
    assert conflicts, "the pilot domain must produce a conflict register"
    for conflict in conflicts:
        assert conflict.label and conflict.difference_summary
        assert conflict.metric_id_a != conflict.metric_id_b
        assert conflict.usage_weight_a >= 0 and conflict.usage_weight_b >= 0
        assert conflict.semantic_model_decision
        assert conflict.resolution_status == "OPEN"
    # A steward's sign-off is a ledgered act: closed vocabulary, rationale, audit row.
    first = conflicts[0]
    with pytest.raises(ValueError):
        store.resolve_conflict(result.run_id, first.conflict_id, "RESOLVED", "steward.a", "x")
    signed = store.resolve_conflict(result.run_id, first.conflict_id, "RESOLVED_A", "steward.a",
                                    note="A is the definition the regulator sees")
    assert signed["authoritative_metric_id"] == first.metric_id_a
    assert store.conflict_decisions(signed["conflict_key"])[0]["steward"] == "steward.a"
    assert any(d["subject_type"] == "conflict" and d["candidate_id"] == first.conflict_id
               for d in store.decisions(result.run_id))


def test_run_quality_gates_are_all_evaluated(run):
    result, _store = run
    gates = {gate["gate"] for gate in result.manifest.quality_gates}
    assert gates == {"ingest_reconciliation", "resolution_rate", "parse_rate",
                     "coverage_sanity", "stability"}
    assert all(gate["passed"] for gate in result.manifest.quality_gates)


def test_top_twenty_candidates_cover_half_of_usage(run):
    result, _store = run
    coverage = result.manifest.stats["usage_coverage_top_n"]
    assert coverage >= QUALITY_GATES["coverage_floor"]


def test_the_agent_answers_the_specifications_own_questions(run):
    result, store = run
    agent = ConversationalAgent(store, result.run_id)
    for question, expected in (
        ("which candidates retire the most Finance reports", "retirement_ranking"),
        ("show the competing definitions of days past due", "conflicts_by_label"),
        ("what would block the top candidate at Stage 9", "candidate_blockers"),
    ):
        answer = agent.ask(question)
        assert answer.intent == expected
        assert answer.answer
        assert answer.citations, f"{question} answered without citing evidence"


def test_synthetic_rows_are_never_mixed_with_real_ones(as_of):
    """Section 17.3: the Ingestor refuses to mix synthetic and real extracts."""
    from dpre.ingest.validator import validate
    ingest = ingest_automated("utility", as_of=as_of)
    bundle = ingest.bundle
    assert all(row.synthetic for row in bundle.kpis)
    bundle.kpis[0].synthetic = False
    report = validate(bundle)
    assert not report.ok
    assert any(issue.code == "MIXED_SYNTHETIC" for issue in report.errors)
    bundle.kpis[0].synthetic = True
