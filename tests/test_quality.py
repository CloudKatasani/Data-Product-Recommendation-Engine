"""Run quality: measured detection, input DQ, remediation, stewardship, bias.

These are the tests that keep the engine honest about itself. The detection
scorecard in particular is only worth having if it can report a miss, so the
assertions here are about the measurement being real - right id spaces, misses
named, nothing silently scored zero - and not about the recall being high.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

import pytest

from dpre.ingest import ingest_automated
from dpre.pipeline import run_pipeline
from dpre.quality import bias_register, detection_scorecard, dq_scorecard, remediation_plan
from dpre.quality.bias import BIASES, bias_summary
from dpre.quality.detection import (METHODS, detection_summary, load_detection_scorecard,
                                    save_detection_scorecard)
from dpre.quality.remediation import (load_remediation_plan, remediation_summary,
                                      save_remediation_plan, write_remediation_plan_csv)
from dpre.quality.stewardship import (CONFIRMED_SOURCES, load_stewardship_requests,
                                      save_stewardship_requests, steward_state,
                                      stewardship_register, stewardship_summary)

AS_OF = _dt.date(2026, 9, 17)


@pytest.fixture(scope="module")
def utility():
    ingest = ingest_automated("utility", as_of=AS_OF)
    return run_pipeline(ingest), ingest


# --------------------------------------------------------------------------
# Detection (R-19)
# --------------------------------------------------------------------------

def test_every_planted_class_is_measured_against_the_run(utility):
    result, ingest = utility
    rows = detection_scorecard(result, planted=ingest.bundle.planted_defects)
    planted_classes = {d["defect_class"] for d in ingest.bundle.planted_defects}
    assert {r["defect_class"] for r in rows} == planted_classes
    # A class with no detection rule is reported as not measurable. Scoring it
    # zero would blame the engine for a gap in the scorecard.
    for row in rows:
        if row["method"] == "not measurable":
            assert row["recall"] is None
        else:
            assert row["method"] in METHODS.values()
            assert 0.0 <= row["recall"] <= 1.0
            assert row["detected"] <= row["planted"]


def test_the_scorecard_names_what_was_missed(utility):
    result, ingest = utility
    rows = detection_scorecard(result, planted=ingest.bundle.planted_defects)
    for row in rows:
        if row["recall"] is None:
            continue
        # Every defect is either a hit or a named miss: the two must add up, or
        # a defect vanished from the count.
        assert row["detected"] + len(row["missed"]) == row["planted"], row["defect_class"]


def test_detection_recall_is_high_enough_to_be_a_claim(utility):
    """The specification says the detection rate is measurable. This is the
    measurement; a regression in a detection rule shows up here first."""
    result, ingest = utility
    summary = detection_summary(detection_scorecard(result,
                                                    planted=ingest.bundle.planted_defects))
    assert summary["planted"] >= 20
    assert summary["recall"] >= 0.80, summary


def test_a_client_estate_has_nothing_planted_and_says_so(utility):
    """No planted list means no scorecard, rather than a perfect score."""
    result, _ = utility
    assert detection_scorecard(result, planted=[]) == []


def test_the_scorecard_persists_and_reads_back(utility, tmp_path):
    result, ingest = utility
    rows = detection_scorecard(result, planted=ingest.bundle.planted_defects)
    connection = sqlite3.connect(tmp_path / "d.db")
    save_detection_scorecard(connection, "RUN-1", rows)
    save_detection_scorecard(connection, "RUN-1", rows)          # idempotent
    loaded = load_detection_scorecard(connection, "RUN-1")
    assert len(loaded) == len(rows)
    assert {r["defect_class"] for r in loaded} == {r["defect_class"] for r in rows}
    assert all(isinstance(r["missed"], list) for r in loaded)


# --------------------------------------------------------------------------
# Remediation (R-46)
# --------------------------------------------------------------------------

def test_gaps_become_units_with_an_owner_and_a_priority(utility):
    result, _ = utility
    rows = remediation_plan(result)
    assert rows
    for row in rows:
        assert row["owner_role"] and row["action"]
        assert row["priority"] in ("high", "medium", "low")
        assert row["rows_affected"] >= 1
    # Ranked, and ranked by something: the first unit outranks the last.
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0]["usage_at_stake"] >= rows[-1]["usage_at_stake"]


def test_definition_gaps_are_grouped_into_work_a_steward_can_sit_down_and_do(utility):
    """One ticket per undefined column is five hundred tickets nobody opens."""
    result, _ = utility
    rows = remediation_plan(result)
    definitions = [r for r in rows if r["unit_type"] == "definition"]
    assert definitions
    undefined = sum(1 for c in result.graph.columns.values()
                    if not (c.definition or "").strip())
    assert len(definitions) < undefined
    assert sum(r["rows_affected"] for r in definitions) == undefined


def test_a_unit_that_keeps_appearing_is_marked_carried(utility):
    result, _ = utility
    first = remediation_plan(result)
    second = remediation_plan(result, previous=first)
    assert all(r["status"] == "new" for r in first)
    assert all(r["status"] == "carried" for r in second)
    # The same gap keeps the same id across runs, which is what makes
    # run-over-run status possible at all.
    assert {r["unit_id"] for r in first} == {r["unit_id"] for r in second}


def test_the_plan_persists_and_exports(utility, tmp_path):
    result, _ = utility
    rows = remediation_plan(result)
    connection = sqlite3.connect(tmp_path / "r.db")
    save_remediation_plan(connection, "RUN-1", rows)
    loaded = load_remediation_plan(connection, "RUN-1")
    assert len(loaded) == len(rows) and loaded[0]["rank"] == 1

    path = write_remediation_plan_csv(rows, tmp_path / "plan.csv")
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("rank,priority,unit_type,subject,label,owner_role,action")
    summary = remediation_summary(rows)
    assert summary["units"] == len(rows) and summary["by_owner_role"]


# --------------------------------------------------------------------------
# Stewardship (R-44)
# --------------------------------------------------------------------------

def test_a_report_owner_is_never_counted_as_a_steward(utility):
    """Ownership and stewardship are different jobs (R-44). A name inferred
    from a report owner is a suggestion nobody has agreed to."""
    result, _ = utility
    rows = stewardship_register(result)
    for row in rows:
        assert row["state"] in ("suggested", "unassigned")
        assert row["suggestion_source"] not in CONFIRMED_SOURCES
        assert row["ask"].endswith("?"), row["metric_id"]
    suggested = [r for r in rows if r["state"] == "suggested"]
    if suggested:
        assert all("report" in r["suggestion_source"].lower() or r["confidence"] < 0.70
                   for r in suggested)


def test_the_register_and_the_estate_kpi_agree_on_confirmed(utility):
    """Two places compute steward coverage; they must not disagree."""
    from dpre.portfolio.benchmark import estate_kpis

    result, _ = utility
    summary = stewardship_summary(stewardship_register(result), result)
    kpis = estate_kpis(result)
    assert abs(summary["confirmed_share"] - kpis["confirmed_steward_coverage"]) < 0.01


def test_steward_state_reads_the_source_not_the_field(utility):
    result, _ = utility
    metrics = list(result.canonical.metrics.values())
    confirmed = [m for m in metrics if steward_state(m) == "confirmed"]
    assert confirmed
    assert all(m.steward_source in CONFIRMED_SOURCES for m in confirmed)


def test_stewardship_requests_persist(utility, tmp_path):
    result, _ = utility
    rows = stewardship_register(result)
    connection = sqlite3.connect(tmp_path / "s.db")
    save_stewardship_requests(connection, "RUN-1", rows)
    loaded = load_stewardship_requests(connection, "RUN-1")
    assert len(loaded) == len(rows)
    assert all(isinstance(r["reports"], list) for r in loaded)


# --------------------------------------------------------------------------
# Bias (R-49)
# --------------------------------------------------------------------------

def test_the_bias_register_names_mechanism_direction_and_mitigation():
    rows = bias_register()
    assert len(rows) >= 6
    for row in rows:
        assert row["id"].startswith("BIAS-")
        assert row["direction"] in ("under-ranks", "over-ranks", "under-detects")
        assert row["severity"] in ("material", "moderate", "minor")
        # A mitigation that cannot be pointed at in the code is a slide, not a
        # control, so every entry names where it lives.
        assert "/" in row["where"] and ".py" in row["where"] or ".md" in row["where"]
        assert len(row["mechanism"]) > 40 and len(row["affects"]) > 40


def test_an_unmitigated_bias_is_admitted_rather_than_dressed_up():
    summary = bias_summary(bias_register())
    assert summary["entries"] == len(BIASES)
    # Incumbency in demand has no mitigation in the score, on purpose. If that
    # entry ever claims one, it should be because the code gained it.
    assert "BIAS-02" in summary["unmitigated"]


def test_the_register_quantifies_itself_against_a_real_run(utility):
    result, _ = utility
    rows = bias_register(result)
    assert all(row["observed"] for row in rows)
    opaque = next(r for r in rows if r["id"] == "BIAS-04")
    assert str(sum(1 for m in result.canonical.metrics.values() if m.opaque)) in opaque["observed"]


# --------------------------------------------------------------------------
# Input data quality (R-35)
# --------------------------------------------------------------------------

def test_the_dq_scorecard_covers_every_input_and_dimension(utility):
    _, ingest = utility
    rows = dq_scorecard(ingest.bundle, as_of=AS_OF)
    assert rows
    dimensions = {r["dimension"] for r in rows}
    assert {"uniqueness", "validity", "completeness", "timeliness"} <= dimensions
    for row in rows:
        assert row["result"] in ("pass", "warn", "fail")
        assert row["rows_checked"] >= 0
        assert row["rows_failed"] <= row["rows_checked"]


# --------------------------------------------------------------------------
# The Assessor in the pipeline
# --------------------------------------------------------------------------

def test_the_pipeline_runs_the_assessor_and_persists_what_it_found(tmp_path):
    """The ninth agent. Its findings reach the manifest and the store, so a
    reviewer can ask what the engine was fed without re-running anything."""
    from dpre.store import Store
    from dpre.quality.detection import load_detection_scorecard
    from dpre.quality.dq import load_dq_scorecard
    from dpre.quality.remediation import load_remediation_plan
    from dpre.quality.stewardship import load_stewardship_requests

    store = Store(tmp_path / "assess.db")
    result = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store,
                          label="assess")
    agents = [entry["agent"] for entry in result.manifest.agent_log]
    assert agents[-1] == "Assessor"

    quality = result.manifest.stats["quality"]
    assert quality["dq"]["rules"] > 0
    assert quality["detection"]["recall"] >= 0.80
    assert quality["remediation"]["units"] > 0
    assert quality["stewardship"]["requests"] >= 0
    assert quality["bias"]["entries"] >= 6

    connection = store.connection
    assert load_dq_scorecard(connection, result.run_id)
    assert load_detection_scorecard(connection, result.run_id)
    assert load_remediation_plan(connection, result.run_id)
    assert load_stewardship_requests(connection, result.run_id)
    store.close()


def test_a_failing_input_rule_becomes_a_run_warning(tmp_path):
    """A data-quality failure bounds what the run may claim, so it travels with
    the run rather than sitting in a table nobody opens."""
    from dpre.store import Store

    store = Store(tmp_path / "warn.db")
    result = run_pipeline(ingest_automated("banking", as_of=AS_OF), store=store)
    quality = result.manifest.stats["quality"]
    failing = [r for r in quality["dq"]["rows"] if r["result"] == "fail"]
    for row in failing:
        assert any(row["rule_id"] in warning for warning in result.manifest.warnings)
    high = quality["remediation"]["by_priority"].get("high", 0)
    if high:
        assert any("high-priority lineage gaps" in w for w in result.manifest.warnings)
    store.close()
