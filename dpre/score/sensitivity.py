"""Rank sensitivity: how much of the ordering survives disagreement about weights.

Open decision D-04 makes the dimension weights contestable, so the backlog must
say which positions hold under reasonable disagreement (review finding R-21).
Two perturbation families, both deterministic:

* one-at-a-time: each dimension weight moved by +/-20%, the others untouched;
* a seeded Dirichlet-like draw of the three positive weights around their base
  proportions (gamma variates, normalised), with the risk weight jittered by
  +/-20%, thirty-two samples.

Only the stored dimension scores are re-weighted; features are never rescored,
so the analysis is a pure function of the run and the seed.
"""
from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import ScoreWeights
from ..models import Candidate

DIMENSIONS = ("demand", "consolidation", "feasibility", "risk")
SENSITIVITY_VERSION = "sensitivity-1.0"

SCHEMA = """
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_RANK_RANGE (
    run_id TEXT, candidate_id TEXT, weight_version TEXT, rank_base INTEGER,
    rank_low INTEGER, rank_high INTEGER, robust_top_n INTEGER, tie_group INTEGER,
    rank_by_dimension TEXT, composite REAL, samples INTEGER, seed INTEGER,
    PRIMARY KEY (run_id, candidate_id)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass
class RankRange:
    candidate_id: str
    composite: float
    rank_base: int
    rank_low: int                        # best position seen (1 = top)
    rank_high: int                       # worst position seen
    robust_top_n: bool
    tie_group: int
    rank_by_dimension: dict[str, int] = field(default_factory=dict)
    positions_seen: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rank_sensitivity(candidates: list[Candidate], weights: ScoreWeights | dict | None = None,
                     top_n: int = 10, samples: int = 32, seed: int = 17,
                     perturbation: float = 0.20, tie_width: float = 1.0) -> dict[str, Any]:
    """Rank range per candidate plus summary statistics of the perturbation."""
    base_weights = _dimension_weights(weights)
    scored = [c for c in candidates if c.score is not None]
    if not scored:
        return {"version": SENSITIVITY_VERSION, "candidates": {}, "summary": {}}

    # The base order is the backlog as the reviewer sees it: the stored composite,
    # stable on the candidates' order for exact ties. Every perturbed order breaks
    # its ties by base rank so a tie can never look like a move.
    base_order = [c.candidate_id for c in sorted(scored, key=lambda c: -c.score.composite)]
    base_rank = {cid: i + 1 for i, cid in enumerate(base_order)}
    low = dict(base_rank)
    high = dict(base_rank)
    vectors = list(_one_at_a_time(base_weights, perturbation))
    vectors += list(_dirichlet_like(base_weights, samples, seed, perturbation))

    top_base = set(base_order[:top_n])
    min_overlap = 1.0
    min_tau = 1.0
    for vector in vectors:
        order = _order(scored, vector, base_rank)
        for position, cid in enumerate(order, start=1):
            low[cid] = min(low[cid], position)
            high[cid] = max(high[cid], position)
        overlap = len(top_base & set(order[:top_n])) / float(min(top_n, len(order)))
        min_overlap = min(min_overlap, overlap)
        min_tau = min(min_tau, _kendall_tau(base_order, order))

    by_dimension = {
        dimension: {cid: i + 1 for i, cid in enumerate(
            _order(scored, {dimension: 1.0}, base_rank))}
        for dimension in DIMENSIONS
    }
    groups = _tie_groups(scored, base_order, tie_width)

    out: dict[str, RankRange] = {}
    for candidate in scored:
        cid = candidate.candidate_id
        out[cid] = RankRange(
            candidate_id=cid,
            composite=candidate.score.composite,
            rank_base=base_rank[cid],
            rank_low=low[cid],
            rank_high=high[cid],
            robust_top_n=high[cid] <= top_n,
            tie_group=groups[cid],
            rank_by_dimension={d: by_dimension[d][cid] for d in DIMENSIONS},
            positions_seen=high[cid] - low[cid] + 1,
        )
    return {
        "version": SENSITIVITY_VERSION,
        "weight_version": getattr(weights, "weight_version", "") if weights is not None else "",
        "top_n": top_n,
        "samples": len(vectors),
        "seed": seed,
        "perturbation": perturbation,
        "tie_width": tie_width,
        "candidates": {cid: r.to_dict() for cid, r in out.items()},
        "summary": {
            "robust_top_n": sorted(cid for cid, r in out.items() if r.robust_top_n),
            "robust_top_n_count": sum(1 for r in out.values() if r.robust_top_n),
            "min_top_n_overlap": round(min_overlap, 4),
            "min_kendall_tau": round(min_tau, 4),
            "tie_groups": _group_members(groups, base_order),
            "widest_range": max((r.positions_seen for r in out.values()), default=0),
        },
    }


# --------------------------------------------------------------------------

def _dimension_weights(weights: ScoreWeights | dict | None) -> dict[str, float]:
    if weights is None:
        return dict(ScoreWeights().dimensions)
    if isinstance(weights, dict):
        return {d: float(weights.get(d, 0.0)) for d in DIMENSIONS}
    return {d: float(weights.dimensions.get(d, 0.0)) for d in DIMENSIONS}


def _composite(candidate: Candidate, weights: dict[str, float]) -> float:
    score = candidate.score
    return round(
        weights.get("demand", 0.0) * score.demand
        + weights.get("consolidation", 0.0) * score.consolidation
        + weights.get("feasibility", 0.0) * score.feasibility
        + weights.get("risk", 0.0) * score.risk, 4)


def _order(candidates: list[Candidate], weights: dict[str, float],
           base_rank: dict[str, int]) -> list[str]:
    return [c.candidate_id for c in sorted(
        candidates, key=lambda c: (-_composite(c, weights), base_rank[c.candidate_id]))]


def _one_at_a_time(base: dict[str, float], perturbation: float):
    for dimension in DIMENSIONS:
        for direction in (1.0, -1.0):
            vector = dict(base)
            vector[dimension] = base[dimension] * (1.0 + direction * perturbation)
            yield vector


def _dirichlet_like(base: dict[str, float], samples: int, seed: int, perturbation: float):
    """Gamma draws around the base proportions of the positive weights.

    A Dirichlet with concentration K gives share s a relative standard
    deviation of sqrt((1 - s) / (K s)); K is set so that no positive weight
    moves by more than about the one-at-a-time perturbation on a typical draw.
    The total of the positive weights is preserved.
    """
    rng = random.Random(seed)
    positive = [d for d in DIMENSIONS if base[d] > 0]
    total = sum(base[d] for d in positive) or 1.0
    shares = {d: base[d] / total for d in positive}
    concentration = max((1.0 - s) / (s * perturbation ** 2) for s in shares.values())
    for _ in range(samples):
        draws = {d: rng.gammavariate(max(1e-6, concentration * shares[d]), 1.0)
                 for d in positive}
        norm = sum(draws.values()) or 1.0
        vector = {d: total * draws[d] / norm for d in positive}
        for dimension in DIMENSIONS:
            if dimension not in vector:
                vector[dimension] = base[dimension] * rng.uniform(1.0 - perturbation,
                                                                  1.0 + perturbation)
        yield vector


def _kendall_tau(a: list[str], b: list[str]) -> float:
    position = {cid: i for i, cid in enumerate(b)}
    n = len(a)
    if n < 2:
        return 1.0
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if position[a[i]] < position[a[j]]:
                concordant += 1
            else:
                discordant += 1
    return (concordant - discordant) / float(concordant + discordant)


def _tie_groups(candidates: list[Candidate], order: list[str], width: float) -> dict[str, int]:
    """Consecutive candidates within ``width`` composite points of the group's leader."""
    composite = {c.candidate_id: c.score.composite for c in candidates}
    groups: dict[str, int] = {}
    group = 0
    leader = None
    for cid in order:
        if leader is None or composite[leader] - composite[cid] > width:
            group += 1
            leader = cid
        groups[cid] = group
    return groups


def _group_members(groups: dict[str, int], order: list[str]) -> list[list[str]]:
    members: dict[int, list[str]] = {}
    for cid in order:
        members.setdefault(groups[cid], []).append(cid)
    return [ids for _, ids in sorted(members.items()) if len(ids) > 1]


def save_sensitivity(connection: sqlite3.Connection, run_id: str, analysis: dict) -> None:
    ensure_schema(connection)
    for cid, row in analysis.get("candidates", {}).items():
        connection.execute(
            "INSERT OR REPLACE INTO DP_CANDIDATE_RANK_RANGE VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, cid, analysis.get("weight_version", ""), row["rank_base"], row["rank_low"],
             row["rank_high"], int(row["robust_top_n"]), row["tie_group"],
             json.dumps(row["rank_by_dimension"]), row["composite"], analysis.get("samples", 0),
             analysis.get("seed", 0)))
    connection.commit()


def load_sensitivity(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM DP_CANDIDATE_RANK_RANGE WHERE run_id = ? "
                              "ORDER BY rank_base", (run_id,)).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["rank_by_dimension"] = json.loads(item["rank_by_dimension"] or "{}")
        item["robust_top_n"] = bool(item["robust_top_n"])
        out.append(item)
    return out
