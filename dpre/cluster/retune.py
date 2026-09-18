"""Coverage-driven resolution retune without side effects (R-06, R-32).

Section 13.2 says that when the top twenty candidates cover less than half of
usage-weighted consumption, the clustering resolution is re-tuned before
publication. The shipped pipeline swept the resolution and then assigned the
winner to the caller's configuration object - in the server that object is
shared by every later run, so a retune in run N silently changed run N+1 and no
manifest recorded it. It also evaluated the coverage gate on the maximised
value, so "passed" hid "tuned to pass".

Here the sweep works on a deep copy of the configuration and returns:

* the coverage before and after, and the resolution actually used, so the gate
  can say "passed after retune" and the manifest can record the effective
  configuration;
* the full sweep table (resolution, coverage, candidates), for the Runs tab;
* the effective configuration copy, which the pipeline should use for the
  rest of the run and snapshot into the manifest.

The caller's configuration is never mutated.
"""
from __future__ import annotations

import copy
import datetime as _dt
from dataclasses import dataclass, field

from ..canonicalize.grouping import CanonicalizationResult
from ..config import QUALITY_GATES, EngineConfig
from ..models import Candidate, KnowledgeGraph
from ..score import apply_classification, score_candidates
from .generator import ClusterResult, generate_candidates


@dataclass
class RetuneOutcome:
    coverage_before: float
    coverage_after: float
    original_resolution: float
    selected_resolution: float
    retuned: bool
    cluster: ClusterResult
    config: EngineConfig
    sweep: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "coverage_before": self.coverage_before, "coverage_after": self.coverage_after,
            "original_resolution": self.original_resolution,
            "selected_resolution": self.selected_resolution, "retuned": self.retuned,
            "sweep": list(self.sweep),
        }


def usage_coverage(candidates: list[Candidate], canonical: CanonicalizationResult,
                   top_n: int) -> float:
    """Share of usage-weighted KPI consumption covered by the top N candidates."""
    total = sum(m.usage_weight for m in canonical.metrics.values())
    if total <= 0:
        return 0.0
    ranked = sorted(candidates, key=lambda c: -(c.score.composite if c.score else 0.0))[:top_n]
    covered: set[str] = set()
    for candidate in ranked:
        covered.update(candidate.metric_ids)
    return round(sum(canonical.metrics[m].usage_weight for m in covered if m in canonical.metrics)
                 / total, 4)


def retune_for_coverage(cluster: ClusterResult, canonical: CanonicalizationResult,
                        graph: KnowledgeGraph, config: EngineConfig, run_id: str,
                        as_of: _dt.date, top_n: int | None = None,
                        floor: float | None = None) -> RetuneOutcome:
    """Sweep the resolution if coverage is below the floor; never touch ``config``."""
    top_n = top_n or QUALITY_GATES["coverage_top_n"]
    floor = QUALITY_GATES["coverage_floor"] if floor is None else floor
    effective = copy.deepcopy(config)
    original = config.cluster.resolution
    before = usage_coverage(cluster.candidates, canonical, top_n)
    sweep = [{"resolution": original, "coverage": before, "candidates": len(cluster.candidates),
              "selected": True, "original": True}]
    if before >= floor:
        return RetuneOutcome(before, before, original, original, False, cluster, effective, sweep)

    best = (before, cluster, original)
    for resolution in config.cluster.resolution_sweep:
        if abs(resolution - original) < 1e-9:
            continue
        trial = copy.deepcopy(config)
        trial.cluster.resolution = resolution
        retried = generate_candidates(canonical, graph, trial, run_id, as_of)
        apply_classification(retried.candidates, canonical, graph)
        score_candidates(retried.candidates, canonical, graph, trial, as_of)
        coverage = usage_coverage(retried.candidates, canonical, top_n)
        sweep.append({"resolution": resolution, "coverage": coverage,
                      "candidates": len(retried.candidates), "selected": False,
                      "original": False})
        if coverage > best[0]:
            best = (coverage, retried, resolution)
    for row in sweep:
        row["selected"] = abs(row["resolution"] - best[2]) < 1e-9
    effective.cluster.resolution = best[2]
    return RetuneOutcome(before, best[0], original, best[2], best[2] != original,
                         best[1], effective, sweep)


def coverage_gate(outcome: RetuneOutcome, top_n: int | None = None,
                  floor: float | None = None) -> dict:
    """The coverage gate with both values shown, marked when it passed after retune."""
    top_n = top_n or QUALITY_GATES["coverage_top_n"]
    floor = QUALITY_GATES["coverage_floor"] if floor is None else floor
    passed = outcome.coverage_after >= floor
    state = "pass" if passed else "fail"
    if passed and outcome.retuned:
        state = "passed after retune"
    return {
        "gate": "coverage_sanity",
        "passed": passed,
        "state": state,
        "assessed": True,
        "value": outcome.coverage_after,
        "value_before_retune": outcome.coverage_before,
        "resolution": outcome.selected_resolution,
        "resolution_before_retune": outcome.original_resolution,
        "retuned": outcome.retuned,
        "sweep": list(outcome.sweep),
        "threshold": floor,
        "detail": (f"usage-weighted KPI consumption covered by the top {top_n} candidates"
                   + (f"; {outcome.coverage_before:.1%} at resolution "
                      f"{outcome.original_resolution} before retune, "
                      f"{outcome.coverage_after:.1%} at {outcome.selected_resolution} after"
                      if outcome.retuned else "")),
    }
