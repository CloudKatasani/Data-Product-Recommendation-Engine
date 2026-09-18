"""Registers: assumptions, decisions, controls and section 14 traceability (R-25, R-27, R-40).

Every test here would fail without ``dpre/registers``: the register must
read the live constants, the decision log must be append-only, every cited
function and test must exist in the tree, and the two falsifiers the
specification left without a capture mechanism must now be computable.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from dpre.config import EngineConfig, ScoreWeights
from dpre.ingest import ingest_automated
from dpre.pipeline import run_pipeline
from dpre.registers import (
    CONTROLS, CRITERIA, DECISIONS, QUESTION_BANK, RACI, RACI_ROLES, DecisionError,
    VerdictError, assumption_register, assumptions_for_run, citation_rate, config_version,
    controls_matrix, decide, decision_log, decision_register, decisions_for_run,
    diff_registers, framework_mapping, grouping_rejection_rate, open_decisions, phase_metrics,
    raci, record_grouping_verdict, register_hash, snapshot_assumptions, snapshot_decisions,
    stage2_metric_drift, traceability_matrix, verify_controls,
)
from dpre.registers import assumptions as assumptions_module
from dpre.registers import controls as controls_module
from dpre.registers import decisions as decisions_module
from dpre.registers import traceability as traceability_module
from dpre.review import review
from dpre.store import Store
from dpre.value.assumptions import ValueAssumptions

ROOT = Path(__file__).resolve().parents[1]
REF = re.compile(r"(?P<path>dpre/[\w./-]+\.py|tests/[\w./-]+\.py)::(?P<name>\w+)")


@pytest.fixture(scope="module")
def stored(tmp_path_factory, as_of):
    store = Store(tmp_path_factory.mktemp("registers") / "engine.db")
    result = run_pipeline(ingest_automated("utility", as_of=as_of), store=store,
                          label="registers")
    yield result, store
    store.close()


def _references_resolve(text: str) -> list[str]:
    missing = []
    for match in REF.finditer(text):
        target = ROOT / match["path"]
        if not target.is_file():
            missing.append(match.group(0))
            continue
        if not controls_module._symbol_present(target.read_text(encoding="utf-8"), match["name"]):
            missing.append(match.group(0))
    return missing


# --------------------------------------------------------------------------
# Assumption register (R-25)
# --------------------------------------------------------------------------

def test_the_register_reads_the_live_constants_not_a_copy():
    rows = {r.key: r for r in assumption_register()}
    assert rows["disposition_weight.keep"].value == 0.5
    assert rows["usage_window_months"].value == 12
    assert rows["gate.G2.lineage_floor"].value == 0.60
    assert rows["archetype_threshold.Metric / KPI"].value == 0.55
    assert rows["quality_gate.coverage_floor"].value == 0.50
    # Every row says where it lives, who owns it and how it can be changed.
    for row in rows.values():
        assert row.implemented_in and row.owner and row.contestable_via and row.spec_section
    # The open decisions each point at at least one assumption.
    for ref in ("D-01", "D-02", "D-03", "D-04", "D-06"):
        assert any(r.decision_ref == ref for r in rows.values()), ref


def test_the_register_follows_the_configuration_it_is_given():
    default = {r.key: r.value for r in assumption_register()}
    tuned = EngineConfig(keep_counts_toward_consolidation=False,
                         weights=ScoreWeights(weight_version="v2-test",
                                              dimensions={"demand": 0.5, "consolidation": 0.2,
                                                          "feasibility": 0.2, "risk": -0.1}))
    tuned.cluster.min_metrics = 5
    changed = {r.key: r.value for r in assumption_register(tuned)}
    assert changed["keep_counts_toward_consolidation"] is False
    assert changed["cluster.min_metrics"] == 5
    assert changed["weights.dimensions.demand"] == 0.5
    assert config_version() != config_version(tuned)
    assert config_version() == config_version()            # deterministic
    delta = diff_registers(assumption_register(), assumption_register(tuned))
    assert {d["key"] for d in delta} >= {"keep_counts_toward_consolidation",
                                          "cluster.min_metrics", "weights.dimensions.demand"}
    assert default["cluster.min_metrics"] == 3


def test_value_assumptions_are_included_by_reference_not_copied():
    rows = [r for r in assumption_register() if r.category == "value"]
    assert rows, "the value rate card must appear in the one register"
    by_key = {r.key: r for r in rows}
    assert by_key["value.loaded_hourly_rate"].value == ValueAssumptions().loaded_hourly_rate
    assert "dpre/value/assumptions.py::ValueAssumptions" in by_key["value.loaded_hourly_rate"].implemented_in
    assert "illustrative" in by_key["value.loaded_hourly_rate"].description
    # A client-approved card changes the rows without changing this module.
    approved = ValueAssumptions(loaded_hourly_rate=120.0, basis="approved by cfo")
    rows2 = {r.key: r for r in assumption_register(value_assumptions=approved)}
    assert rows2["value.loaded_hourly_rate"].value == 120.0
    assert "approved by cfo" in rows2["value.loaded_hourly_rate"].description
    assert not [r for r in assumption_register(include_value=False) if r.category == "value"]


def test_every_reference_in_the_assumption_register_resolves():
    text = " ".join(f"{r.implemented_in} {' '.join(r.consumed_by)}"
                    for r in assumption_register())
    assert _references_resolve(text) == []


def test_assumption_register_snapshots_with_a_config_version(tmp_path):
    connection = sqlite3.connect(tmp_path / "reg.db")
    version = snapshot_assumptions(connection, "RUN-A")
    assert version.startswith("cfg-") and version == config_version()
    rows = assumptions_for_run(connection, "RUN-A")
    assert {r["config_version"] for r in rows} == {version}
    assert {r["key"] for r in rows} == {r.key for r in assumption_register()}
    # A different configuration snapshots under a different version.
    tuned = EngineConfig(keep_counts_toward_consolidation=False)
    other = snapshot_assumptions(connection, "RUN-B", config=tuned)
    assert other != version
    assert diff_registers(assumptions_for_run(connection, "RUN-A"),
                          assumptions_for_run(connection, "RUN-B"))[0]["key"] == \
        "keep_counts_toward_consolidation"
    assert register_hash(assumption_register()) == register_hash(assumption_register())


def test_the_assumptions_register_document_lists_every_key():
    doc = (ROOT / "docs" / "assumptions-register.md").read_text(encoding="utf-8")
    for row in assumption_register(include_value=False):
        assert f"`{row.key}`" in doc, row.key


# --------------------------------------------------------------------------
# Decision register (R-25)
# --------------------------------------------------------------------------

def test_the_decision_register_states_the_engine_position_and_honest_enforcement():
    rows = {r["ref"]: r for r in decision_register()}
    assert set(rows) == {f"D-0{i}" for i in range(1, 9)}
    assert all(r["status"] == "engine default" for r in rows.values())
    assert "12 months" in rows["D-01"]["position"]
    assert "Keep reports count" in rows["D-03"]["position"]
    assert rows["D-06"]["enforcement"] == decisions_module.RECORDED
    assert rows["D-07"]["enforcement"] == decisions_module.ASSUMED
    assert rows["D-05"]["enforcement"] == decisions_module.ASSUMED
    assert rows["D-08"]["enforcement"] == decisions_module.ENFORCED
    for row in rows.values():
        assert row["enforcement"] in decisions_module.ENFORCEMENT_LEVELS
        assert row["proposed_owner"] and row["enforcement_note"]
    assert "critic" in rows["D-06"]["enforcement_note"]
    tuned = EngineConfig(keep_counts_toward_consolidation=False)
    assert "excluded" in {r["ref"]: r for r in decision_register(tuned)}["D-03"]["position"]
    assert len(open_decisions(decision_register())) == 8


def test_decisions_are_logged_append_only(tmp_path):
    connection = sqlite3.connect(tmp_path / "dec.db")
    with pytest.raises(DecisionError):
        decide(connection, "D-99", "x", "someone", "because")
    with pytest.raises(DecisionError):
        decide(connection, "D-03", "Keep reports excluded", "", "because")
    with pytest.raises(DecisionError):
        decide(connection, "D-03", "Keep reports excluded", "r.lead", "")
    row = decide(connection, "D-03", "Keep reports excluded", "r.lead",
                 "a Keep report left untouched is not a win", decided_at="2026-09-10T09:00:00+00:00")
    assert row["log_id"] == 1
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("UPDATE DECISION_LOG SET position = 'changed' WHERE log_id = 1")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM DECISION_LOG WHERE log_id = 1")
    register = {r["ref"]: r for r in decision_register(connection=connection)}
    assert register["D-03"]["status"] == "decided"
    assert register["D-03"]["decided_by"] == "r.lead"
    assert register["D-03"]["position"] == "Keep reports excluded"
    assert register["D-01"]["status"] == "engine default"
    assert len(open_decisions(decision_register(connection=connection))) == 7
    # A run snapshots the register as it stood.
    snapshot_decisions(connection, "RUN-A", snapshot_at="2026-09-11T10:00:00+00:00")
    stored = {r["ref"]: r for r in decisions_for_run(connection, "RUN-A")}
    assert stored["D-03"]["decided_by"] == "r.lead" and stored["D-01"]["decided_by"] == ""
    assert len(decision_log(connection, "D-03")) == 1


def test_the_decision_register_document_covers_all_eight():
    doc = (ROOT / "docs" / "decision-register.md").read_text(encoding="utf-8")
    for spec in DECISIONS:
        assert spec.ref in doc and spec.enforcement in doc
    assert "recorded only" in doc


# --------------------------------------------------------------------------
# Controls matrix, RACI, framework mapping (R-27)
# --------------------------------------------------------------------------

def test_every_control_cites_code_and_tests_that_exist():
    rows = verify_controls(ROOT)
    missing = {r["control_id"]: r["missing"] for r in rows if not r["present"]}
    assert missing == {}, missing
    assert len(rows) == len(CONTROLS) >= 20
    for control in CONTROLS:
        assert control.kind in ("preventive", "detective")
        assert control.enforcing and control.proving_tests and control.test_procedure
        assert set(control.frameworks) == {"DMBOK", "DCAM", "COBIT"}
        assert control.owner in RACI_ROLES


def test_verify_controls_detects_a_dropped_function(tmp_path):
    """The static check must fail when a cited function disappears."""
    fake = tmp_path / "dpre" / "store.py"
    fake.parent.mkdir(parents=True)
    fake.write_text("def something_else():\n    pass\n", encoding="utf-8")
    rows = {r["control_id"]: r for r in verify_controls(tmp_path)}
    assert rows["C-01"]["present"] is False
    assert "dpre/store.py::save_candidates" in rows["C-01"]["missing"]


def test_raci_has_one_accountable_role_per_activity():
    rows = raci()
    assert len(rows) == len(RACI) >= 12
    for row in rows:
        letters = list(row["assignment"].values())
        assert letters.count("A") == 1, row["activity"]
        assert "R" in letters, row["activity"]
        assert set(row["assignment"]) <= set(RACI_ROLES)
        assert row["mechanism"]
    activities = " ".join(r["activity"] for r in rows)
    for ref in ("D-01", "D-03", "D-04", "D-06", "D-07", "D-08"):
        assert ref in activities


def test_framework_mapping_covers_every_control():
    mapping = framework_mapping()
    for framework in ("DMBOK", "DCAM", "COBIT"):
        covered = {cid for ids in mapping[framework].values() for cid in ids}
        assert covered == {c.control_id for c in CONTROLS}, framework
    assert any(key.startswith("APO14") for key in mapping["COBIT"])
    assert any(key.startswith("DSS06") for key in mapping["COBIT"])


def test_the_controls_matrix_document_lists_every_control_and_role():
    doc = (ROOT / "docs" / "controls-matrix.md").read_text(encoding="utf-8")
    for control in CONTROLS:
        assert control.control_id in doc, control.control_id
        for test in control.proving_tests:
            assert test in doc, test
    for role in RACI_ROLES:
        assert role.split(" (")[0] in doc
    assert "APO14" in doc and "DMBOK" in doc and "DCAM" in doc
    assert len(controls_matrix()) == len(CONTROLS)


# --------------------------------------------------------------------------
# Traceability to section 14 (R-40)
# --------------------------------------------------------------------------

def test_every_criterion_and_falsifier_has_a_feature_a_test_and_evidence():
    rows = traceability_matrix()
    assert {r["phase"] for r in rows} == {"1", "2", "3", "14.1"}
    kinds = {(r["phase"], r["kind"]) for r in rows}
    for phase in ("1", "2", "3"):
        assert (phase, "exit") in kinds and (phase, "falsifier") in kinds
    assert len([r for r in rows if r["phase"] == "14.1"]) == 7
    text = " ".join(f"{r['feature']} {' '.join(r['tests'])}" for r in rows)
    assert _references_resolve(text) == []
    for row in rows:
        assert row["evidence"] and row["provable"] in (
            traceability_module.PROVABLE_IN_CODE, traceability_module.PARTLY,
            traceability_module.FIELD_ONLY)


def test_grouping_verdicts_compute_the_phase_1_falsifier(stored):
    result, store = stored
    run_id = result.run_id
    metrics = list(result.canonical.metrics.values())[:30]
    with pytest.raises(VerdictError):
        record_grouping_verdict(store.connection, run_id, metrics[0].metric_id, "maybe", "s.a")
    with pytest.raises(VerdictError):
        record_grouping_verdict(store.connection, run_id, metrics[0].metric_id, "accept", "")
    with pytest.raises(VerdictError):
        record_grouping_verdict(store.connection, run_id, metrics[0].metric_id, "reject", "s.a")
    before = grouping_rejection_rate(store.connection, run_id)
    assert before["sampled"] == 0 and before["rate"] is None and before["falsified"] is None
    for index, metric in enumerate(metrics):
        verdict = "reject" if index < 3 else "accept"
        record_grouping_verdict(store.connection, run_id, metric.metric_id, verdict, "s.a",
                                reason="two different filters grouped" if verdict == "reject" else "",
                                fingerprint=metric.fingerprint, sample_id="pilot-30")
    rate = grouping_rejection_rate(store.connection, run_id)
    assert rate["sampled"] == 30 and rate["sample_complete"] is True
    assert rate["rejected"] == 3 and rate["rate"] == 0.1 and rate["falsified"] is False
    # A later verdict on the same metric supersedes the earlier one; the ledger keeps both.
    record_grouping_verdict(store.connection, run_id, metrics[0].metric_id, "accept", "s.b")
    assert grouping_rejection_rate(store.connection, run_id)["rejected"] == 2
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute("DELETE FROM GROUPING_VERDICT")
    # Nine rejections of thirty falsify Phase 1.
    for metric in metrics[3:10]:
        record_grouping_verdict(store.connection, run_id, metric.metric_id, "reject", "s.a",
                                reason="structural cousins merged")
    assert grouping_rejection_rate(store.connection, run_id)["falsified"] is True


def test_stage_2_metric_drift_is_measured_from_payload_history(stored):
    result, store = stored
    run_id = result.run_id
    proposed = [c for c in result.ranked() if c.status == "Proposed"]
    target, source = proposed[0], proposed[1]
    original = set(target.metric_ids)
    review(store, run_id, source.candidate_id, "Merge", "priya.silva",
           reason_code="same_decision", target_candidate_id=target.candidate_id)
    review(store, run_id, target.candidate_id, "Accept", "priya.silva",
           reason_code="retires_reports", usable_without_rework=True)
    drift = stage2_metric_drift(store, run_id)
    assert drift["accepted"] >= 1
    row = next(c for c in drift["candidates"] if c["candidate_id"] == target.candidate_id)
    assert row["original_metrics"] == len(original)
    assert row["current_metrics"] > len(original)
    assert 0 < row["drift"] < 1
    assert drift["threshold"] == 0.40


def test_phase_metrics_report_every_computable_criterion(stored):
    result, store = stored
    report = phase_metrics(store, result.run_id)
    keys = set(report["by_key"])
    for criterion in CRITERIA:
        if criterion.metric_key and criterion.metric_key != "citation_rate":
            assert criterion.metric_key in keys, criterion.ref
    by_key = report["by_key"]
    assert by_key["resolution_rate"]["met"] is True
    assert by_key["run_seconds"]["met"] is True and by_key["run_seconds"]["value"] < 1800
    assert by_key["usable_without_rework_rate"]["value"] == 1.0
    assert by_key["final_decisions"]["met"] is False           # far below 50 on a fresh run
    assert by_key["approved_weight_versions"]["met"] is False   # nothing approved yet
    with pytest.raises(KeyError):
        phase_metrics(store, "RUN-NOPE")


def test_the_question_bank_is_answered_with_citations(stored):
    result, store = stored
    assert len(QUESTION_BANK) == 20
    rate = citation_rate(store, result.run_id)
    assert rate["asked"] == 20
    assert rate["rate"] >= 0.9, rate["uncited"]


def test_the_traceability_document_lists_every_criterion():
    doc = (ROOT / "docs" / "traceability.md").read_text(encoding="utf-8")
    for criterion in CRITERIA:
        assert criterion.ref in doc, criterion.ref
    assert "GROUPING_VERDICT" in doc and "usable_without_rework" in doc
