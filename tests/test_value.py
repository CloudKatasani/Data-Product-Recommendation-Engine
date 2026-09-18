"""Value model, assumptions, portfolio attribution and the estate benchmark (R-08, R-23, R-48)."""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3

import pytest

from dpre.ingest import ingest_automated
from dpre.models import CandidateReport, ReportRecord
from dpre.pipeline import run_pipeline
from dpre.portfolio.benchmark import (
    REFERENCE_BANDS, TARGETS_14_2, benchmark_run, compare_to_bands, estate_kpis,
    load_benchmark, save_benchmark,
)
from dpre.portfolio.views import (
    DISPOSITION_ACTION, HOLD_ACTION, attribute_reports, candidate_attribution,
    estate_retirement, is_hold, retirement_action,
)
from dpre.programme.effort import effort_for_run
from dpre.value import (
    ValueAssumptions, approve_assumptions, load_assumptions, load_values, save_assumptions,
    save_values, value_for_run, value_sentence,
)
from dpre.value.model import CandidateValue, ValueComponent, attribute_conflicts, compute_value

AS_OF = _dt.date(2026, 9, 17)


@pytest.fixture(scope="module")
def banking():
    return run_pipeline(ingest_automated("banking", as_of=AS_OF))


@pytest.fixture(scope="module")
def valued(banking):
    efforts = effort_for_run(banking)
    return value_for_run(banking, ValueAssumptions(), efforts), efforts


# --------------------------------------------------------------------------
# assumptions (R-08)
# --------------------------------------------------------------------------

def test_default_assumptions_are_labelled_illustrative():
    assumptions = ValueAssumptions()
    assert "illustrative" in assumptions.basis
    assert assumptions.version.endswith("illustrative")
    assert set(assumptions.maintenance_hours_per_report) == {"low", "medium", "high"}
    assert assumptions.complexity_band(0.1) == "low"
    assert assumptions.complexity_band(0.5) == "medium"
    assert assumptions.complexity_band(0.9) == "high"


def test_assumptions_round_trip_through_dict_and_table(tmp_path):
    assumptions = ValueAssumptions(version="va-test", loaded_hourly_rate=120.0)
    assert ValueAssumptions.from_dict(json.loads(json.dumps(assumptions.to_dict()))) == assumptions
    connection = sqlite3.connect(tmp_path / "va.db")
    save_assumptions(connection, assumptions)
    save_assumptions(connection, assumptions)                      # idempotent
    loaded = load_assumptions(connection, "va-test")
    assert loaded == assumptions
    assert load_assumptions(connection) is not None
    with pytest.raises(PermissionError):
        approve_assumptions(connection, assumptions, "", "2026-10-01")
    approved = approve_assumptions(connection, assumptions, "cfo.office", "2026-10-01")
    assert approved.approved_by == "cfo.office" and "illustrative" not in approved.basis
    assert load_assumptions(connection, "va-test").approved_by == "cfo.office"
    connection.close()


# --------------------------------------------------------------------------
# attribution and holds (R-23)
# --------------------------------------------------------------------------

def test_each_fully_covered_report_has_exactly_one_primary_candidate(banking):
    attribution = attribute_reports(banking.candidates)
    claimed = sum(1 for c in banking.candidates for r in c.reports if r.coverage >= 1.0)
    assert len(attribution) < claimed, "the raw sum double-counts across candidates"
    by_id = {c.candidate_id: c for c in banking.candidates}
    for report_id, candidate_id in attribution.items():
        winner = by_id[candidate_id]
        assert any(r.report_id == report_id and r.coverage >= 1.0 for r in winner.reports)
        for other in banking.candidates:
            if any(r.report_id == report_id and r.coverage >= 1.0 for r in other.reports):
                assert other.score.composite <= winner.score.composite
    total_attributable = sum(
        candidate_attribution(c, banking.graph, attribution)["reports_attributable"]
        for c in banking.candidates)
    assert total_attributable == len(attribution)


def test_estate_figure_is_deduplicated_and_disposition_aware(banking):
    estate = banking.portfolio["estate_retirement"]
    assert estate["reports_fully_covered_claimed"] > estate["reports_fully_covered_distinct"]
    assert (estate["reports_retirable"] + estate["reports_on_hold"]
            + estate["reports_keep_repointed"] == estate["reports_fully_covered_distinct"])
    assert "Keep" not in estate["retirable_by_disposition"]
    assert estate["reports_on_hold"] >= 1, "planted regulatory reports must be held"
    assert not (set(estate["packages_fully_retirable"])
                & set(estate["packages_partially_affected"]))
    for row in banking.portfolio["retirement_map"]:
        assert row["reports_attributable"] + row["also_covered_by_others"] == row["fully_covered"]
        assert row["reports_retirable_attributable"] <= row["reports_attributable"]


def test_holds_and_actions_follow_the_disposition():
    regulatory = ReportRecord("R1", "Regulatory Return (Finance)")
    returns = ReportRecord("R2", "Returns Analysis Daily")
    critical = ReportRecord("R3", "Cash Position", decision_critical=True)
    assert is_hold(regulatory) and is_hold(critical) and not is_hold(returns)
    for disposition, action in DISPOSITION_ACTION.items():
        report = CandidateReport("R", "r", 1.0, disposition.title(), 3, "")
        assert retirement_action(report, hold=False) == action
    assert retirement_action(CandidateReport("R", "r", 1.0, "Retire", 3, ""), hold=True) == HOLD_ACTION
    assert retirement_action(CandidateReport("R", "r", 0.5, "Retire", 3, ""), hold=False).startswith(
        "partially covered")


# --------------------------------------------------------------------------
# value model (R-08)
# --------------------------------------------------------------------------

def test_every_value_component_is_backed_by_evidence_rows_that_sum(valued, banking):
    values, _ = valued
    assumptions = ValueAssumptions()
    for candidate in banking.candidates:
        value = values["candidates"][candidate.candidate_id]
        evidence_by_feature = {}
        for row in value["evidence"]:
            evidence_by_feature.setdefault(row["feature"], []).append(row)
        for component in value["components"]:
            rows = evidence_by_feature.get(f"value:{_feature_of(component['name'])}", [])
            assert len(rows) == component["driver_count"], component["name"]
            if component["name"] == "conflict_reconciliation":
                expected = (component["driver_count"]
                            * assumptions.reconciliation_hours_per_conflict_per_period
                            * assumptions.periods_per_year * assumptions.loaded_hourly_rate)
                assert abs(component["attributed_benefit"] - expected) < 0.01
            assert component["attributed_benefit"] <= component["annual_benefit"] + 1e-6
        assert abs(sum(c["attributed_benefit"] for c in value["components"])
                   - value["attributed_annual_benefit"]) < 0.01
        assert value["basis"] == assumptions.basis
        assert value["assumption_version"] == assumptions.version


def _feature_of(component: str) -> str:
    return {"conflict_reconciliation": "reconciliation",
            "mis_decision_avoidance": "mis_decision"}.get(component, component)


def test_portfolio_totals_count_each_report_and_conflict_once(valued, banking):
    values, _ = valued
    portfolio = values["portfolio"]
    assert portfolio["attributed_annual_benefit"] < portfolio["standalone_sum_annual_benefit"]
    assert abs(sum(portfolio["by_component"].values())
               - portfolio["attributed_annual_benefit"]) < 0.01
    assert portfolio["reports_counted_once"] == len(attribute_reports(banking.candidates))
    assert portfolio["conflicts_counted_once"] == len(attribute_conflicts(banking.candidates))
    retirement_reports = [rid for v in values["candidates"].values()
                          for c in v["components"] if c["name"] == "report_retirement"
                          for rid in c["items"]]
    assert len(retirement_reports) == len(set(retirement_reports))


def test_keep_and_regulatory_reports_release_no_retirement_benefit(valued, banking):
    values, _ = valued
    for candidate in banking.candidates:
        component = next(c for c in values["candidates"][candidate.candidate_id]["components"]
                         if c["name"] == "report_retirement")
        for report_id in component["items"]:
            report = next(r for r in candidate.reports if r.report_id == report_id)
            assert report.disposition.lower() != "keep"
            assert not is_hold(banking.graph.reports[report_id])


def test_build_cost_payback_and_npv_follow_the_effort_and_the_rates(valued, banking):
    values, efforts = valued
    assumptions = ValueAssumptions()
    for candidate in banking.candidates:
        value = values["candidates"][candidate.candidate_id]
        effort = efforts[candidate.candidate_id]
        assert abs(value["build_cost"] - effort.points * assumptions.build_cost_per_effort_point) < 0.01
        benefit = value["attributed_annual_benefit"]
        if benefit > 0 and value["build_cost"] > 0:
            assert abs(value["payback_months"] - 12 * value["build_cost"] / benefit) < 0.1
        else:
            assert value["payback_months"] is None
        expected = -value["build_cost"] + sum(
            benefit * (assumptions.ramp_year1_share if y == 1 else 1.0)
            / (1 + assumptions.discount_rate) ** y for y in range(1, 4))
        assert abs(value["npv_3y"] - expected) < 0.05


def test_value_is_deterministic_and_sensitive_to_the_rate_card(banking):
    efforts = effort_for_run(banking)
    first = value_for_run(banking, ValueAssumptions(), efforts)
    second = value_for_run(banking, ValueAssumptions(), efforts)
    assert first == second
    doubled = ValueAssumptions(version="va-double", loaded_hourly_rate=190.0)
    tilted = value_for_run(banking, doubled, efforts)
    assert tilted["assumption_version"] == "va-double"
    assert (tilted["portfolio"]["by_component"]["report_retirement"]
            == pytest.approx(2 * first["portfolio"]["by_component"]["report_retirement"]))


def test_size_based_build_cost_is_an_alternative_method(banking):
    efforts = effort_for_run(banking)
    assumptions = ValueAssumptions(build_cost_method="size")
    candidate = banking.ranked()[0]
    value = compute_value(candidate, banking.graph, {}, assumptions,
                          efforts[candidate.candidate_id])
    assert value.build_cost == assumptions.build_cost_by_size[efforts[candidate.candidate_id].size]
    assert "T-shirt" in value.build_basis


def test_values_persist_and_reload(tmp_path, valued, banking):
    values, _ = valued
    connection = sqlite3.connect(tmp_path / "v.db")
    save_values(connection, banking.run_id, values["candidates"])
    save_values(connection, banking.run_id, values["candidates"])         # idempotent
    rows = load_values(connection, banking.run_id)
    assert len(rows) == len(banking.candidates)
    assert rows[0]["attributed_annual_benefit"] >= rows[-1]["attributed_annual_benefit"]
    assert rows[0]["components"] and rows[0]["evidence"]
    connection.close()


def test_value_sentence_names_the_assumption_version(valued, banking):
    values, _ = valued
    payload = values["candidates"][banking.ranked()[0].candidate_id]
    value = CandidateValue(
        candidate_id=payload["candidate_id"], assumption_version=payload["assumption_version"],
        currency=payload["currency"],
        components=[ValueComponent(**c) for c in payload["components"]],
        gross_annual_benefit=payload["gross_annual_benefit"],
        attributed_annual_benefit=payload["attributed_annual_benefit"],
        build_cost=payload["build_cost"], build_basis=payload["build_basis"],
        payback_months=payload["payback_months"], npv_3y=payload["npv_3y"],
        evidence=[], basis=payload["basis"])
    sentence = value_sentence(value)
    assert "va-1.0-illustrative" in sentence and "illustrative" in sentence
    assert "NPV" in sentence and "reports retired" in sentence


# --------------------------------------------------------------------------
# benchmark (R-48)
# --------------------------------------------------------------------------

def test_estate_kpis_are_shares_and_a_synthetic_run_sits_inside_the_bands(banking):
    kpis = estate_kpis(banking)
    assert set(REFERENCE_BANDS) <= set(kpis)
    for kpi, value in kpis.items():
        assert 0.0 <= value <= 1.0, kpi
    comparison = compare_to_bands(kpis)
    assert {row["kpi"] for row in comparison} == set(REFERENCE_BANDS)
    positions = {row["kpi"]: row["position"] for row in comparison}
    assert all(position in ("within", "edge") for position in positions.values()), positions
    assert not any(row["outlier"] for row in comparison)
    for row in comparison:
        if row["kpi"] in TARGETS_14_2:
            assert row["target"] == TARGETS_14_2[row["kpi"]]
            assert row["meets_target"] is not None


def test_bands_flag_a_worse_estate_in_the_right_direction():
    kpis = {kpi: band["median"] for kpi, band in REFERENCE_BANDS.items()}
    kpis["conflict_density"] = REFERENCE_BANDS["conflict_density"]["high"] + 0.5
    kpis["lineage_completeness"] = 0.3
    rows = {row["kpi"]: row for row in compare_to_bands(kpis)}
    assert rows["conflict_density"]["outlier"] and rows["conflict_density"]["direction"] == "worse"
    assert rows["lineage_completeness"]["outlier"] and rows["lineage_completeness"]["direction"] == "worse"
    assert rows["lineage_completeness"]["meets_target"] is False
    assert not rows["duplication_ratio"]["outlier"]


def test_benchmark_persists_per_run(tmp_path, banking):
    benchmark = benchmark_run(banking)
    connection = sqlite3.connect(tmp_path / "b.db")
    save_benchmark(connection, banking.run_id, benchmark)
    save_benchmark(connection, banking.run_id, benchmark)
    loaded = load_benchmark(connection, banking.run_id)
    assert loaded["kpis"] == benchmark["kpis"]
    assert loaded["outliers"] == benchmark["outliers"] == []
    assert load_benchmark(connection, "no-such-run") is None
    connection.close()
