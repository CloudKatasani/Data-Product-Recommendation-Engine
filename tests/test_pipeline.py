"""The pipeline itself: agents, gates, manifests and every industry end to end."""
from __future__ import annotations

import datetime as _dt

import pytest

from dpre.config import QUALITY_GATES, EngineConfig
from dpre.ingest import ingest_automated
from dpre.pipeline import run_pipeline
from dpre.store import Store
from dpre.synth import INDUSTRY_KEYS

AS_OF = _dt.date(2026, 9, 17)


def test_the_seven_agents_all_report(run):
    result, _store = run
    agents = [entry["agent"] for entry in result.manifest.agent_log]
    assert agents == ["Ingestor", "Resolver", "Canonicalizer", "Clusterer", "Scorer",
                      "Narrator", "Critic"]
    assert all(entry["seconds"] >= 0 for entry in result.manifest.agent_log)
    assert all(entry["note"] for entry in result.manifest.agent_log)


def test_the_manifest_records_what_a_replay_needs(run):
    result, _store = run
    manifest = result.manifest
    assert manifest.run_id and manifest.as_of_date == AS_OF.isoformat()
    assert manifest.weight_version and manifest.parser_version
    assert manifest.generation_id
    assert manifest.finished_at >= manifest.started_at


def test_the_store_holds_the_documented_reco_tables(run):
    result, store = run
    tables = {row["name"] for row in store.query(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"DP_CANDIDATE", "DP_CANDIDATE_METRIC", "DP_CANDIDATE_SOURCE",
            "DP_CANDIDATE_CONSUMER", "DP_CANDIDATE_REPORT", "DP_CANDIDATE_SCORE",
            "DP_CANDIDATE_EVIDENCE", "DP_CANDIDATE_CRITIQUE", "KPI_CANONICAL",
            "KPI_VARIANT", "KPI_CONFLICT", "REVIEW_DECISION", "SCORE_WEIGHT"} <= tables
    assert store.candidates(result.run_id)
    assert store.metrics(result.run_id)
    assert store.weights(result.manifest.weight_version)


def test_stability_is_measured_against_the_previous_run(tmp_path):
    store = Store(tmp_path / "stability.db")
    first = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store)
    second = run_pipeline(ingest_automated("utility", as_of=AS_OF), store=store,
                          previous_run_id=first.run_id)
    gate = next(g for g in second.manifest.quality_gates if g["gate"] == "stability")
    assert gate["passed"]
    assert gate["value"] >= QUALITY_GATES["stability_floor"]
    assert first.run_id in gate["detail"]
    store.close()


def test_a_run_with_no_catalog_publishes_only_the_gap_list(tmp_path):
    """Below the resolution floor the run is held back rather than published."""
    ingest = ingest_automated("utility", as_of=AS_OF)
    ingest.bundle.columns = []
    ingest.bundle.lineage = []
    store = Store(tmp_path / "nocatalog.db")
    result = run_pipeline(ingest, store=store)
    gate = next(g for g in result.manifest.quality_gates if g["gate"] == "resolution_rate")
    assert not gate["passed"]
    assert result.manifest.published is False
    assert store.run(result.run_id)["published"] == 0
    assert result.graph.quarantine
    store.close()


def test_configuration_changes_change_the_ranking(tmp_path):
    ingest = ingest_automated("utility", as_of=AS_OF)
    baseline = run_pipeline(ingest, config=EngineConfig())

    weighted = EngineConfig()
    weighted.weights.weight_version = "v-test-demand-only"
    weighted.weights.dimensions = {"demand": 1.0, "consolidation": 0.0,
                                   "feasibility": 0.0, "risk": 0.0}
    tilted = run_pipeline(ingest_automated("utility", as_of=AS_OF), config=weighted)
    assert tilted.manifest.weight_version == "v-test-demand-only"
    for candidate in tilted.candidates:
        assert abs(candidate.score.composite - candidate.score.demand) < 0.15
    assert ([c.candidate_id for c in baseline.ranked()]
            != [c.candidate_id for c in tilted.ranked()])


@pytest.mark.parametrize("industry", INDUSTRY_KEYS)
def test_every_industry_runs_clean(industry):
    result = run_pipeline(ingest_automated(industry, as_of=AS_OF))
    failed = [gate["gate"] for gate in result.manifest.quality_gates if not gate["passed"]]
    assert not failed, f"{industry} failed {failed}"
    assert result.manifest.published
    assert result.candidates
    assert any(c.status == "Proposed" for c in result.candidates)
    assert result.canonical.conflicts
    for candidate in result.candidates:
        assert candidate.purpose and candidate.evidence and candidate.score
        assert candidate.decisions_drafted or candidate.status != "Proposed"


def test_a_full_estate_run_is_quick():
    """Section 14: a full-estate run completes in minutes, not hours."""
    import time
    start = time.time()
    run_pipeline(ingest_automated("generic", as_of=AS_OF))
    assert time.time() - start < 60
