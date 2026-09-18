"""Effort, dependencies, waves, RAID, status, sensitivity, stakeholders, critic readiness."""
from __future__ import annotations

import datetime as _dt
import sqlite3

import pytest

from dpre.config import EngineConfig, ScoreWeights
from dpre.ingest import ingest_automated
from dpre.narrate.critic import STANDING_CRITERION, readiness_checklist, split_findings
from dpre.pipeline import run_pipeline
from dpre.portfolio.stakeholders import ROLE_CONFLICT, ROLE_REPORT_OWNER, stakeholder_map
from dpre.programme import ProgrammeConfig, enrich_run
from dpre.programme.dependencies import (
    build_dependencies, dependency_graph, load_dependencies, save_dependencies,
)
from dpre.programme.effort import (
    DRIVER_RATES, SIZE_BANDS, effort_for_run, estimate_effort, load_efforts, save_efforts,
    size_for,
)
from dpre.programme.raid import build_raid, load_raid, raid_csv_rows, raid_for_candidate, save_raid
from dpre.programme.status import TARGETS, status_report
from dpre.programme.waves import load_waves, plan_waves, save_waves
from dpre.review import review
from dpre.score.sensitivity import load_sensitivity, rank_sensitivity, save_sensitivity
from dpre.store import Store
from dpre.value import ValueAssumptions, value_for_run

AS_OF = _dt.date(2026, 9, 17)


@pytest.fixture(scope="module")
def banking():
    return run_pipeline(ingest_automated("banking", as_of=AS_OF))


@pytest.fixture(scope="module")
def programme(banking):
    return enrich_run(banking)


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("programme") / "engine.db")
    result = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store, label="p1")
    enriched = enrich_run(result, store=store)
    yield result, store, enriched
    store.close()


# --------------------------------------------------------------------------
# critic readiness (R-53)
# --------------------------------------------------------------------------

def test_standing_findings_collapse_to_one_line_and_readiness_discriminates(banking):
    readiness = {}
    for candidate in banking.candidates:
        standing = [f for f in candidate.critique if f.criterion == STANDING_CRITERION]
        assert len(standing) <= 1
        if standing:
            assert standing[0].severity == "info"
            assert standing[0].finding.startswith("Standing until a human acts")
        split = split_findings(candidate)
        assert all(f.criterion != STANDING_CRITERION for f in split["specific"])
        checklist = candidate.narrative["readiness"]
        assert 0.0 <= checklist["percent"] <= 100.0
        assert checklist["outstanding_actions"] <= checklist["actions_in_scope"]
        assert {s["stage"] for s in checklist["stages"]} == {1, 2, 3, 5, 9}
        assert getattr(candidate, "_readiness") == checklist["percent"]
        readiness[candidate.candidate_id] = checklist["percent"]
    assert len(set(readiness.values())) > 1, "readiness must differ between candidates"


def test_readiness_counts_adjudications_per_conflict(banking):
    heavy = max(banking.candidates, key=lambda c: len(c.conflicts))
    light = min(banking.candidates, key=lambda c: len(c.conflicts))
    config = EngineConfig()
    heavy_list = readiness_checklist(heavy, banking.canonical, banking.graph, config)
    light_list = readiness_checklist(light, banking.canonical, banking.graph, config)
    assert heavy_list["adjudication_load"] == len(heavy.conflicts)
    assert heavy_list["actions_by_role"]["Domain steward"] > light_list["actions_by_role"].get(
        "Domain steward", 0)


# --------------------------------------------------------------------------
# effort (R-10)
# --------------------------------------------------------------------------

def test_effort_is_deterministic_versioned_and_evidenced(banking):
    first = effort_for_run(banking)
    second = effort_for_run(banking)
    assert {k: v.to_dict() for k, v in first.items()} == {k: v.to_dict() for k, v in second.items()}
    for candidate in banking.candidates:
        effort = first[candidate.candidate_id]
        assert effort.effort_version == "effort-1.0"
        assert effort.size in {s for s, _ in SIZE_BANDS} and effort.size == size_for(effort.points)
        assert {d.name for d in effort.drivers} == set(DRIVER_RATES)
        assert abs(sum(d.points for d in effort.drivers) - effort.points) < 0.11
        for driver in effort.drivers:
            assert driver.points == pytest.approx(driver.value * driver.rate, abs=0.01)
        assert set(effort.stage_weeks) == {1, 2, 3, 4, 5, 6}
        assert effort.total_weeks == pytest.approx(sum(effort.stage_weeks.values()), abs=0.1)
        assert effort.evidence, "an effort estimate must cite the rows behind its drivers"
        metric_driver = next(d for d in effort.drivers if d.name == "metric_count")
        assert metric_driver.value == len(candidate.metric_ids)


def test_more_conflicts_and_systems_mean_more_effort(banking):
    efforts = effort_for_run(banking)
    heavy = max(banking.candidates, key=lambda c: (len(c.conflicts), len(c.metric_ids)))
    light = min(banking.candidates, key=lambda c: (len(c.conflicts), len(c.metric_ids)))
    assert efforts[heavy.candidate_id].points > efforts[light.candidate_id].points
    assert efforts[heavy.candidate_id].stage_weeks[6] > efforts[light.candidate_id].stage_weeks[6]


def test_efforts_persist(tmp_path, banking):
    efforts = effort_for_run(banking)
    connection = sqlite3.connect(tmp_path / "e.db")
    save_efforts(connection, banking.run_id, efforts)
    save_efforts(connection, banking.run_id, efforts)
    rows = load_efforts(connection, banking.run_id)
    assert len(rows) == len(efforts)
    assert rows[0]["points"] >= rows[-1]["points"]
    assert rows[0]["drivers"] and rows[0]["evidence"]
    connection.close()


# --------------------------------------------------------------------------
# dependencies (R-11)
# --------------------------------------------------------------------------

def test_dependencies_cover_candidates_entity_masters_successors_and_stewards(banking):
    rows = build_dependencies(banking)
    types = {r.type for r in rows}
    assert {"candidate", "entity_master", "successor_system", "steward_adjudication"} <= types
    ids = {c.candidate_id for c in banking.candidates}
    masters = {c.candidate_id for c in banking.candidates if c.origin == "entity_master"}
    hub_tables = {s.table_fqn for c in banking.candidates if c.origin == "entity_master"
                  for s in c.sources}
    for row in rows:
        assert row.owner_role and row.candidate_id in ids
        if row.type == "candidate":
            assert row.target in ids and row.status != "MISSING"
        if row.type == "entity_master":
            assert row.target in masters
    # every non-master candidate that reads a hub table depends on the master
    for candidate in banking.candidates:
        if candidate.origin == "entity_master":
            continue
        if {s.table_fqn for s in candidate.sources} & hub_tables:
            deps = [r for r in rows if r.candidate_id == candidate.candidate_id
                    and r.type in ("entity_master", "candidate") and r.target in masters]
            assert deps, candidate.candidate_id
    blocked = [c for c in banking.candidates if c.status == "Blocked"]
    assert blocked
    successor = [r for r in rows if r.type == "successor_system"
                 and r.candidate_id == blocked[0].candidate_id]
    assert successor and successor[0].due_hint == "2027-03-31" and successor[0].status == "BLOCKING"
    assert build_dependencies(banking) == rows


def test_dependencies_persist(tmp_path, banking):
    rows = build_dependencies(banking)
    connection = sqlite3.connect(tmp_path / "d.db")
    save_dependencies(connection, banking.run_id, rows)
    save_dependencies(connection, banking.run_id, rows)
    assert len(load_dependencies(connection, banking.run_id)) == len(rows)
    composite = next(c for c in banking.candidates if c.origin == "composite")
    mine = load_dependencies(connection, banking.run_id, composite.candidate_id)
    assert all(r["candidate_id"] == composite.candidate_id or r["target"] == composite.candidate_id
               for r in mine)
    connection.close()


# --------------------------------------------------------------------------
# waves (R-10)
# --------------------------------------------------------------------------

def test_waves_never_place_a_dependent_before_its_dependency(banking, programme):
    plan = programme["waves"]
    wave_of = plan["wave_of"]
    edges = dependency_graph(build_dependencies(banking))
    unscheduled = {u["candidate_id"] for u in plan["unscheduled"]}
    for cid, targets in edges.items():
        if cid in unscheduled:
            continue
        for target in targets:
            assert target in wave_of, f"{cid} scheduled before {target} was"
            assert wave_of[target] < wave_of[cid]
    scheduled = {c["candidate_id"] for w in plan["waves"] for c in w["candidates"]}
    assert scheduled.isdisjoint(unscheduled)
    assert scheduled | unscheduled == {c.candidate_id for c in banking.candidates}


def test_blocked_candidates_are_unscheduled_with_the_sunset_reason(programme):
    blocked = [u for u in programme["waves"]["unscheduled"] if u["status"] == "Blocked"]
    assert blocked
    assert "G4" in blocked[0]["reason"] and "LEGACY_LOANS" in blocked[0]["reason"]
    assert "2027-03-31" in blocked[0]["release_hint"]


def test_waves_respect_capacity_and_accumulate(banking, programme):
    plan = programme["waves"]
    config = ProgrammeConfig()
    previous_benefit = 0.0
    previous_coverage = 0.0
    for wave in plan["waves"]:
        assert 1 <= len(wave["candidates"]) <= config.products_per_wave
        if len(wave["candidates"]) > 1:
            assert wave["points"] <= config.max_points_per_wave + 1e-6
        assert wave["cumulative_annual_benefit"] >= previous_benefit
        assert wave["cumulative_coverage"] >= previous_coverage
        previous_benefit = wave["cumulative_annual_benefit"]
        previous_coverage = wave["cumulative_coverage"]
        assert wave["ends_week"] - wave["starts_week"] + 1 == config.wave_length_weeks
        for entry in wave["candidates"]:
            assert entry["rationale"] and entry["size"]
    assert set(plan["quadrants"]) >= {"quick_wins", "big_bets", "fill_ins", "reconsider"}


def test_wave_planning_is_deterministic_and_config_driven(banking):
    efforts = effort_for_run(banking)
    values = value_for_run(banking, ValueAssumptions(), efforts)["candidates"]
    first = plan_waves(banking, efforts, values, ProgrammeConfig())
    second = plan_waves(banking, efforts, values, ProgrammeConfig())
    assert first == second
    wide = plan_waves(banking, efforts, values,
                      ProgrammeConfig(products_per_wave=6, max_points_per_wave=10_000))
    assert len(wide["waves"]) < len(first["waves"])
    strict = plan_waves(banking, efforts, values, ProgrammeConfig(include_exploratory=False))
    assert all(u["status"] != "Exploratory" or "Exploratory" in u["reason"] or "depends" in u["reason"]
               for u in strict["unscheduled"])
    assert any(u["status"] == "Exploratory" for u in strict["unscheduled"])


def test_waves_persist(tmp_path, banking, programme):
    connection = sqlite3.connect(tmp_path / "w.db")
    save_waves(connection, banking.run_id, programme["waves"])
    save_waves(connection, banking.run_id, programme["waves"])
    loaded = load_waves(connection, banking.run_id)
    assert len(loaded["waves"]) == sum(len(w["candidates"]) for w in programme["waves"]["waves"])
    assert len(loaded["unscheduled"]) == len(programme["waves"]["unscheduled"])
    connection.close()


# --------------------------------------------------------------------------
# RAID (R-39)
# --------------------------------------------------------------------------

def test_raid_rows_carry_type_owner_severity_and_evidence(banking, programme):
    rows = build_raid(banking, value_assumption_version=ValueAssumptions().version)
    assert [r.to_dict() for r in rows] == programme["raid"]
    assert {r.type for r in rows} == {"Risk", "Assumption", "Issue", "Dependency"}
    ids = {c.candidate_id for c in banking.candidates}
    for row in rows:
        assert row.id.startswith("RAID-") and row.owner_role and row.severity in ("high", "medium", "low")
        assert set(row.candidate_ids) <= ids
        assert row.status in ("OPEN", "BLOCKING", "Proposed", "Exploratory", "Blocked")
    assert len({r.id for r in rows}) == len(rows)
    sunset = [r for r in rows if r.type == "Risk" and "Sunset source" in r.title]
    assert sunset and sunset[0].due_hint == "2027-03-31" and sunset[0].owner_role == "Catalog admin"
    spec_rows = [r for r in rows if r.source.startswith("spec 15.1")]
    assert len(spec_rows) == 7, "all seven 15.1 risks are instantiated with run values"
    assert any("%" in r.title for r in spec_rows)
    assumptions = [r for r in rows if r.type == "Assumption"]
    assert any("AI-drafted decision-register" in r.title for r in assumptions)
    issues = [r for r in rows if r.type == "Issue"]
    assert any(r.title.startswith("Open conflict") for r in issues)
    assert any("quarantined" in r.title for r in issues)


def test_raid_persists_and_filters_by_candidate(tmp_path, banking):
    rows = build_raid(banking, value_assumption_version="va-1.0-illustrative")
    assert any("va-1.0-illustrative" in r.title for r in rows if r.type == "Assumption")
    connection = sqlite3.connect(tmp_path / "r.db")
    save_raid(connection, banking.run_id, rows)
    save_raid(connection, banking.run_id, rows)
    assert len(load_raid(connection, banking.run_id)) == len(rows)
    assert all(r["type"] == "Risk" for r in load_raid(connection, banking.run_id, "Risk"))
    blocked = next(c for c in banking.candidates if c.status == "Blocked")
    mine = raid_for_candidate(rows, blocked.candidate_id)
    assert any("Sunset" in r.title for r in mine)
    csv_rows = raid_csv_rows(rows)
    assert csv_rows[0]["candidate_ids"] is not None and "id" in csv_rows[0]
    connection.close()


# --------------------------------------------------------------------------
# sensitivity (R-21)
# --------------------------------------------------------------------------

def test_rank_ranges_contain_the_base_rank_and_are_deterministic(banking):
    analysis = rank_sensitivity(banking.candidates, banking.config.weights)
    assert analysis == rank_sensitivity(banking.candidates, banking.config.weights)
    base_order = [c.candidate_id for c in banking.ranked()]
    for position, cid in enumerate(base_order, start=1):
        row = analysis["candidates"][cid]
        assert row["rank_base"] == position
        assert row["rank_low"] <= row["rank_base"] <= row["rank_high"]
        assert set(row["rank_by_dimension"]) == {"demand", "consolidation", "feasibility", "risk"}
        assert row["robust_top_n"] == (row["rank_high"] <= analysis["top_n"])
    assert analysis["samples"] == 8 + 32
    assert 0.0 <= analysis["summary"]["min_kendall_tau"] <= 1.0
    assert 0.0 < analysis["summary"]["min_top_n_overlap"] <= 1.0


def test_tied_candidates_share_a_tie_group(banking):
    analysis = rank_sensitivity(banking.candidates, banking.config.weights)
    composite = {cid: row["composite"] for cid, row in analysis["candidates"].items()}
    for group in analysis["summary"]["tie_groups"]:
        assert len(group) > 1
        assert max(composite[c] for c in group) - min(composite[c] for c in group) <= 1.0 + 1e-6
        assert len({analysis["candidates"][c]["tie_group"] for c in group}) == 1
    ranked = banking.ranked()
    for a, b in zip(ranked, ranked[1:]):
        if abs(a.score.composite - b.score.composite) < 1e-9:
            assert (analysis["candidates"][a.candidate_id]["tie_group"]
                    == analysis["candidates"][b.candidate_id]["tie_group"])


def test_rank_under_each_single_dimension_follows_that_dimension_score(banking):
    analysis = rank_sensitivity(banking.candidates, banking.config.weights)
    base_rank = {cid: row["rank_base"] for cid, row in analysis["candidates"].items()}
    for dimension in ("demand", "consolidation", "feasibility", "risk"):
        expected = sorted(banking.candidates,
                          key=lambda c: (-getattr(c.score, dimension), base_rank[c.candidate_id]))
        for position, candidate in enumerate(expected, start=1):
            assert analysis["candidates"][candidate.candidate_id]["rank_by_dimension"][
                dimension] == position
    # a weight vector that ignores everything but demand widens the range exactly
    # where demand disagrees with the composite, and never loses the base rank
    demand_only = ScoreWeights(weight_version="demand", dimensions={
        "demand": 1.0, "consolidation": 0.0, "feasibility": 0.0, "risk": 0.0})
    tilted = rank_sensitivity(banking.candidates, demand_only)
    for cid, row in tilted["candidates"].items():
        assert row["rank_low"] <= row["rank_base"] <= row["rank_high"]
        assert row["rank_low"] <= row["rank_by_dimension"]["demand"] <= row["rank_high"]


def test_sensitivity_persists(tmp_path, banking):
    analysis = rank_sensitivity(banking.candidates, banking.config.weights)
    connection = sqlite3.connect(tmp_path / "s.db")
    save_sensitivity(connection, banking.run_id, analysis)
    save_sensitivity(connection, banking.run_id, analysis)
    rows = load_sensitivity(connection, banking.run_id)
    assert [r["rank_base"] for r in rows] == list(range(1, len(banking.candidates) + 1))
    assert all(r["rank_low"] <= r["rank_base"] <= r["rank_high"] for r in rows)
    connection.close()


# --------------------------------------------------------------------------
# stakeholders (R-45)
# --------------------------------------------------------------------------

def test_stakeholder_map_rolls_up_by_person_and_business_unit(banking, programme):
    stakeholders = programme["stakeholders"]
    assert stakeholders == stakeholder_map(banking)
    people = stakeholders["people"]
    assert people == sorted(people, key=lambda p: (-p["load"], p["person"]))
    stewards = [p for p in people if ROLE_CONFLICT in p["roles"]]
    assert stewards and stakeholders["bottlenecks"][0]["conflicts_to_adjudicate"] >= 1
    open_conflicts = [c for c in banking.canonical.conflicts if c.resolution_status == "OPEN"]
    assert sum(p["conflicts_to_adjudicate"] for p in stewards) == len(
        {c.conflict_id for c in open_conflicts
         if any(c.conflict_id in cand.conflicts for cand in banking.candidates)})
    owners = [p for p in people if ROLE_REPORT_OWNER in p["roles"]]
    notified = sum(p["reports_to_be_notified"] for p in owners)
    assert notified == banking.portfolio["estate_retirement"]["reports_fully_covered_distinct"]
    assert stakeholders["report_owners_unmapped_to_catalog"]
    units = stakeholders["business_units"]
    assert units and all(u["reports_attributable"] >= u["reports_retirable"] for u in units)


# --------------------------------------------------------------------------
# enrich_run and persistence
# --------------------------------------------------------------------------

def test_enrich_run_is_deterministic_and_touches_no_status(banking, programme):
    again = enrich_run(banking)
    assert again["value"] == programme["value"]
    assert again["waves"] == programme["waves"]
    assert again["sensitivity"] == programme["sensitivity"]
    assert again["raid"] == programme["raid"]
    for candidate in banking.candidates:
        assert candidate.status in ("Blocked", "Exploratory", "Proposed")
        assert getattr(candidate, "_effort")["size"]
        assert "value_hypothesis_quantified" in candidate.narrative
        assert "illustrative" in candidate.narrative["value_hypothesis_quantified"]


def test_enrich_run_persists_to_the_programme_tables(stored):
    result, store, enriched = stored
    tables = {row["name"] for row in store.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"VALUE_ASSUMPTION", "DP_CANDIDATE_VALUE", "DP_CANDIDATE_EFFORT",
            "DP_CANDIDATE_DEPENDENCY", "DP_WAVE", "DP_WAVE_UNSCHEDULED", "RAID",
            "DP_CANDIDATE_RANK_RANGE", "RUN_BENCHMARK"} <= tables
    n = len(result.candidates)
    for table in ("DP_CANDIDATE_VALUE", "DP_CANDIDATE_EFFORT", "DP_CANDIDATE_RANK_RANGE"):
        assert store.query(f"SELECT COUNT(*) AS n FROM {table} WHERE run_id = ?",
                           (result.run_id,))[0]["n"] == n
    assert store.query("SELECT COUNT(*) AS n FROM RAID WHERE run_id = ?",
                       (result.run_id,))[0]["n"] == len(enriched["raid"])
    assert store.query("SELECT version FROM VALUE_ASSUMPTION")[0]["version"] == "va-1.0-illustrative"
    # propose-only: nothing in the store moved past Proposed
    statuses = {r["status"] for r in store.candidates(result.run_id)}
    assert statuses <= {"Blocked", "Exploratory", "Proposed"}


# --------------------------------------------------------------------------
# status report (R-13)
# --------------------------------------------------------------------------

def test_status_report_computes_the_14_2_measures_with_rag(stored):
    result, store, _ = stored
    before = status_report(store, result.run_id)
    keys = [m["key"] for m in before["measures"]]
    assert keys == list(TARGETS)
    by_key = {m["key"]: m for m in before["measures"]}
    assert by_key["usage_coverage_accepted"]["actual"] == 0.0
    assert by_key["usage_coverage_accepted"]["rag"] == "red"
    assert by_key["hours_accept_to_charter_ratio"]["rag"] == "grey" and by_key[
        "hours_accept_to_charter_ratio"]["note"]
    assert by_key["metrics_with_confirmed_steward_share"]["rag"] == "grey"
    assert before["acceptance"]["overall"] is None
    assert before["queues"]["blocked"] and "sunset" in before["queues"]["blocked"][0]["reason"]

    candidates = [c for c in store.candidates(result.run_id) if c["status"] == "Proposed"]
    review(store, result.run_id, candidates[0]["candidate_id"], "Accept", "priya.silva",
           reason_code="value_clear")
    review(store, result.run_id, candidates[1]["candidate_id"], "Accept", "amit.rao",
           reason_code="retires_reports")
    review(store, result.run_id, candidates[2]["candidate_id"], "Defer", "priya.silva",
           reason_code="capacity", note="next quarter")
    review(store, result.run_id, candidates[3]["candidate_id"], "Reject", "amit.rao",
           reason_code="too_small")
    store.resolve_conflict(result.run_id, result.canonical.conflicts[0].conflict_id,
                           "RESOLVED_A", "rosa.adeyemi", note="side A is the certified basis")

    after = status_report(store, result.run_id)
    by_key = {m["key"]: m for m in after["measures"]}
    assert by_key["usage_coverage_accepted"]["actual"] > 0.0
    assert by_key["usage_coverage_accepted"]["context"]["accepted_candidates"] == 2
    assert by_key["reports_on_retirement_list_with_owner"]["actual"] > 0
    assert by_key["reports_on_retirement_list_with_owner"]["target"] == 500
    assert by_key["metrics_with_confirmed_steward_share"]["rag"] in ("green", "amber", "red")
    assert by_key["conflicts_adjudicated"]["actual"] == 1
    by_type = after["decisions"]["by_type"]
    assert {k: v for k, v in by_type.items() if k != "Override"} == {
        "Accept": 2, "Defer": 1, "Reject": 1}
    by_reviewer = after["decisions"]["by_reviewer"]
    assert by_reviewer["amit.rao"] == 2 and by_reviewer["priya.silva"] == 2
    deferred = after["queues"]["deferred"]
    assert deferred and deferred[0]["reason"] == "capacity" and deferred[0]["revisit_hint"]
    assert after["acceptance"]["overall"] == 0.5
    reviewers = {r["key"]: r for r in after["acceptance"]["by_reviewer"]}
    assert reviewers["priya.silva"]["acceptance_rate"] == 0.5
    assert after["acceptance"]["by_domain"]
    assert after["time_to_first_decision"]["candidates_with_a_decision"] == 4
    assert after["time_to_first_decision"]["median_hours"] is not None
    assert after["time_to_first_decision"]["p90_hours"] >= after["time_to_first_decision"]["median_hours"]
    stewards = after["open_conflicts_by_steward"]
    assert sum(s["adjudicated"] for s in stewards) == 1
    assert any(p["met"] for p in after["phase_exit"])
    assert next(p for p in after["phase_exit"] if "2 candidates Accepted" in p["criterion"])["met"]


def test_status_report_compares_with_the_previous_run(stored):
    result, store, _ = stored
    second = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store,
                          previous_run_id=result.run_id, label="p2")
    report = status_report(store, second.run_id, previous_run_id=result.run_id)
    assert report["previous_run_id"] == result.run_id
    assert report["delta"]["previous_run_id"] == result.run_id
    assert "candidates_by_status" in report["delta"]["changes"]
    assert report["decisions"]["since"] == store.run(result.run_id)["finished_at"]
    assert report["decisions"]["total"] == 0
    with pytest.raises(KeyError):
        status_report(store, "no-such-run")
