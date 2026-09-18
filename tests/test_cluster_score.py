"""Clustering, classification and scoring (specification sections 6, 7 and 8)."""
from __future__ import annotations

from dpre.cluster.louvain import Graph, louvain, modularity
from dpre.config import (
    DIMENSION_WEIGHTS, GATE_GRAIN_AMBIGUITY_MAX, GATE_LINEAGE_FLOOR, STATUS_ORDER,
)


def test_louvain_separates_two_cliques():
    graph = Graph()
    for a, b in [("a", "b"), ("a", "c"), ("b", "c"), ("d", "e"), ("d", "f"), ("e", "f")]:
        graph.add_edge(a, b, 1.0)
    graph.add_edge("c", "d", 0.05)
    communities = louvain(graph, resolution=1.0)
    assert communities["a"] == communities["b"] == communities["c"]
    assert communities["d"] == communities["e"] == communities["f"]
    assert communities["a"] != communities["d"]
    assert modularity(graph, communities) > 0.3


def test_louvain_is_deterministic():
    graph = Graph()
    for i in range(12):
        graph.add_edge(f"n{i}", f"n{(i + 1) % 12}", 1.0)
    assert louvain(graph, seed=17) == louvain(graph, seed=17)


def test_every_candidate_has_one_grain(clustered):
    for candidate in clustered.candidates:
        assert candidate.grain, candidate.candidate_id


def test_grain_split_keeps_the_parent_link(clustered):
    children = [c for c in clustered.candidates if c.origin == "grain_child"]
    if not children:
        return
    ids = {c.candidate_id for c in clustered.candidates}
    for child in children:
        assert child.parent_candidate_id in ids
        parent = next(c for c in clustered.candidates if c.candidate_id == child.parent_candidate_id)
        assert child.candidate_id in parent.child_candidate_ids
        assert child.grain != parent.grain


def test_composites_depend_on_the_candidates_they_span(clustered):
    composites = [c for c in clustered.candidates if c.origin == "composite"]
    assert composites, "a business unit spanning several communities must seed a composite"
    ids = {c.candidate_id for c in clustered.candidates}
    for composite in composites:
        assert composite.tier == "Consumer-aligned"
        assert len(composite.depends_on) >= 2
        assert all(dependency in ids for dependency in composite.depends_on)


def test_entity_masters_are_detected(clustered):
    masters = [c for c in clustered.candidates if c.origin == "entity_master"]
    assert masters, "a conformed dimension used across communities must be an Entity Master"
    for master in masters:
        assert master.archetype == "Entity Master"


def test_metrics_without_resolved_lineage_are_data_gaps_not_candidates(clustered):
    """Section 6.4: a metric whose edges all quarantined is listed as a data gap."""
    assert clustered.data_gap_metrics
    clustered_metrics = {m for c in clustered.candidates for m in c.metric_ids}
    assert not (set(clustered.data_gap_metrics) & clustered_metrics)


def test_resolution_sweep_is_recorded_for_the_reviewer(clustered):
    sweep = clustered.stats["resolution_sweep"]
    assert len(sweep) == 3
    assert sum(1 for row in sweep if row["selected"]) == 1


def test_scores_are_bounded_and_composite_follows_the_weights(clustered):
    weights = DIMENSION_WEIGHTS
    for candidate in clustered.candidates:
        score = candidate.score
        assert score is not None
        for value in (score.demand, score.consolidation, score.feasibility, score.risk):
            assert 0.0 <= value <= 100.0
        expected = (weights["demand"] * score.demand
                    + weights["consolidation"] * score.consolidation
                    + weights["feasibility"] * score.feasibility
                    + weights["risk"] * score.risk)
        assert abs(expected - score.composite) < 0.15


def test_every_feature_resolves_to_a_number(clustered):
    """The null rule: a metadata gap can never make a candidate look better."""
    for candidate in clustered.candidates:
        for feature in candidate.score.features:
            assert feature.value is not None and feature.normalized is not None
            assert 0.0 <= feature.normalized <= 1.0


def test_every_score_carries_evidence(clustered):
    for candidate in clustered.candidates:
        assert candidate.evidence, candidate.candidate_id
        features_with_evidence = {row.feature for row in candidate.evidence}
        assert "usage_weight" in features_with_evidence


def test_hard_gates_cap_the_status(clustered):
    for candidate in clustered.candidates:
        gates = {gate.gate: gate for gate in candidate.score.gates}
        assert set(gates) == {"G1", "G2", "G3", "G4"}
        if not gates["G4"].passed:
            assert candidate.status == "Blocked"
        elif not all(gate.passed for gate in gates.values()):
            assert candidate.status == "Exploratory"
        else:
            assert candidate.status == "Proposed"


def test_gate_thresholds_match_the_specification(clustered):
    for candidate in clustered.candidates:
        lineage = next(f.value for f in candidate.score.features
                       if f.feature == "lineage_completeness")
        ambiguity = next(f.value for f in candidate.score.features
                         if f.feature == "grain_ambiguity")
        gates = {gate.gate: gate.passed for gate in candidate.score.gates}
        assert gates["G2"] == (lineage >= GATE_LINEAGE_FLOOR)
        assert gates["G3"] == (ambiguity <= GATE_GRAIN_AMBIGUITY_MAX)


def test_sunset_source_blocks_the_candidate(clustered):
    blocked = [c for c in clustered.candidates if c.status == "Blocked"]
    assert blocked, "the planted sunset system must block at least one candidate"
    for candidate in blocked:
        gate = next(g for g in candidate.score.gates if g.gate == "G4")
        assert not gate.passed and "sunset" in gate.detail


def test_engine_never_proposes_past_proposed(clustered):
    limit = STATUS_ORDER.index("Proposed")
    for candidate in clustered.candidates:
        assert STATUS_ORDER.index(candidate.status) <= limit


def test_archetypes_and_tiers_come_from_the_documented_sets(clustered):
    from dpre.score.classify import ARCHETYPES, TIERS
    for candidate in clustered.candidates:
        assert candidate.archetype in ARCHETYPES
        assert candidate.tier in TIERS
        assert 0.0 <= candidate.archetype_confidence <= 1.0
        if candidate.archetype_confidence < 0.6:
            assert candidate.archetype_runner_up


def test_source_aligned_means_one_system(clustered):
    for candidate in clustered.candidates:
        if candidate.tier == "Source-aligned":
            systems = {s.system for s in candidate.sources if s.system}
            assert len(systems) <= 1
