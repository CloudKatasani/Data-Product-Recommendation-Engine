"""Wave planning: a dependency-respecting roadmap under capacity (R-10, R-11).

A council funds waves, not a list. Candidates are ordered topologically over
the candidate and entity-master dependency edges, then each wave is filled
greedily by value per effort point, never placing a dependent before the wave
after its dependency, and never exceeding the products or points a wave can
carry. Blocked candidates, and anything that depends on one, are held in an
unscheduled bucket with the reason and the date that would release them.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import Candidate
from .dependencies import DependencyRow, dependency_graph

SCHEMA = """
CREATE TABLE IF NOT EXISTS DP_WAVE (
    run_id TEXT, wave INTEGER, position INTEGER, candidate_id TEXT, size TEXT, points REAL,
    annual_benefit REAL, rationale TEXT, starts_week INTEGER, ends_week INTEGER,
    config TEXT,
    PRIMARY KEY (run_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS DP_WAVE_UNSCHEDULED (
    run_id TEXT, candidate_id TEXT, reason TEXT, release_hint TEXT,
    PRIMARY KEY (run_id, candidate_id)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass
class ProgrammeConfig:
    """Capacity a council can contest; stored with every plan it produced."""

    products_per_wave: int = 3
    wave_length_weeks: int = 12
    max_points_per_wave: float = 120.0
    max_waves: int = 12
    include_exploratory: bool = True     # Exploratory candidates are planned, flagged
    version: str = "programme-1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WaveEntry:
    candidate_id: str
    name: str
    status: str
    origin: str
    size: str
    points: float
    annual_benefit: float
    value_per_point: float
    readiness: float
    rationale: str
    depends_on: list[str] = field(default_factory=list)


def plan_waves(result: Any, efforts: dict[str, Any], values: dict[str, Any],
               config: ProgrammeConfig | None = None,
               dependencies: list[DependencyRow] | None = None) -> dict[str, Any]:
    """The roadmap. ``values`` maps candidate_id to a CandidateValue or its dict."""
    config = config or ProgrammeConfig()
    candidates: list[Candidate] = result.candidates
    by_id = {c.candidate_id: c for c in candidates}
    if dependencies is None:
        from .dependencies import build_dependencies
        dependencies = build_dependencies(result)
    edges = dependency_graph(dependencies)
    for cid in by_id:
        edges.setdefault(cid, [])
    # Edges to candidates outside the run cannot be waited on.
    edges = {cid: [t for t in targets if t in by_id] for cid, targets in edges.items()}

    unscheduled: dict[str, dict] = {}
    for candidate in candidates:
        if candidate.status == "Blocked":
            unscheduled[candidate.candidate_id] = {
                "reason": _blocked_reason(candidate),
                "release_hint": _release_hint(candidate, dependencies),
            }
        elif candidate.status == "Exploratory" and not config.include_exploratory:
            unscheduled[candidate.candidate_id] = {
                "reason": "Exploratory: a reviewer must confirm a consumer (G1) or lineage (G2) "
                          "before it is planned", "release_hint": ""}
    # Anything downstream of an unschedulable candidate is unschedulable too.
    changed = True
    while changed:
        changed = False
        for cid, targets in edges.items():
            if cid in unscheduled:
                continue
            blocked_by = [t for t in targets if t in unscheduled]
            if blocked_by:
                unscheduled[cid] = {
                    "reason": f"depends on unscheduled {', '.join(sorted(blocked_by))}",
                    "release_hint": unscheduled[blocked_by[0]].get("release_hint", ""),
                }
                changed = True

    remaining = {cid for cid in by_id if cid not in unscheduled}
    placed: dict[str, int] = {}
    waves: list[dict] = []
    coverage_total = sum(m.usage_weight for m in result.canonical.metrics.values()) or 1.0
    covered_metrics: set[str] = set()
    cumulative_benefit = 0.0
    cumulative_reports = 0
    wave_number = 0
    attribution = _attributed_reports(candidates)

    while remaining and wave_number < config.max_waves:
        wave_number += 1
        eligible = [cid for cid in remaining
                    if all(placed.get(t, 10 ** 6) < wave_number for t in edges[cid])]
        if not eligible:
            for cid in sorted(remaining):
                unscheduled[cid] = {"reason": "dependency cycle or unplaceable dependency",
                                    "release_hint": ""}
            remaining.clear()
            break
        ranked = sorted(eligible, key=lambda cid: _priority(by_id[cid], efforts, values))
        entries: list[WaveEntry] = []
        points_used = 0.0
        for cid in ranked:
            effort = efforts.get(cid)
            points = float(getattr(effort, "points", 0.0) if effort else 0.0)
            if len(entries) >= config.products_per_wave:
                break
            if entries and points_used + points > config.max_points_per_wave:
                continue
            candidate = by_id[cid]
            benefit = _benefit(values.get(cid))
            entries.append(WaveEntry(
                candidate_id=cid, name=candidate.proposed_name, status=candidate.status,
                origin=candidate.origin, size=getattr(effort, "size", "n/a"),
                points=points, annual_benefit=benefit,
                value_per_point=round(benefit / points, 2) if points else 0.0,
                readiness=float(getattr(candidate, "_readiness", 0.0)),
                rationale=_rationale(candidate, effort, benefit, edges[cid], placed),
                depends_on=list(edges[cid]),
            ))
            points_used += points
            placed[cid] = wave_number
        for entry in entries:
            remaining.discard(entry.candidate_id)
            covered_metrics.update(by_id[entry.candidate_id].metric_ids)
            cumulative_benefit += entry.annual_benefit
            cumulative_reports += attribution.get(entry.candidate_id, 0)
        coverage = sum(result.canonical.metrics[m].usage_weight for m in covered_metrics
                       if m in result.canonical.metrics) / coverage_total
        waves.append({
            "wave": wave_number,
            "starts_week": (wave_number - 1) * config.wave_length_weeks + 1,
            "ends_week": wave_number * config.wave_length_weeks,
            "candidates": [asdict(e) for e in entries],
            "points": round(points_used, 1),
            "annual_benefit": round(sum(e.annual_benefit for e in entries), 2),
            "cumulative_annual_benefit": round(cumulative_benefit, 2),
            "cumulative_coverage": round(coverage, 4),
            "cumulative_reports_retired": cumulative_reports,
            "longest_stage_path_weeks": max(
                (float(getattr(efforts.get(e.candidate_id), "total_weeks", 0.0))
                 for e in entries), default=0.0),
        })
    for cid in sorted(remaining):
        unscheduled[cid] = {"reason": f"beyond the {config.max_waves}-wave horizon",
                            "release_hint": "raise max_waves or capacity"}

    return {
        "config": config.to_dict(),
        "waves": waves,
        "unscheduled": [{"candidate_id": cid, "name": by_id[cid].proposed_name,
                         "status": by_id[cid].status, **info}
                        for cid, info in sorted(unscheduled.items())],
        "wave_of": placed,
        "quadrants": value_effort_quadrants(candidates, efforts, values),
    }


# --------------------------------------------------------------------------

def _priority(candidate: Candidate, efforts: dict, values: dict) -> tuple:
    """Highest value per effort point first; then composite; then id for determinism."""
    effort = efforts.get(candidate.candidate_id)
    points = float(getattr(effort, "points", 0.0) if effort else 0.0)
    benefit = _benefit(values.get(candidate.candidate_id))
    ratio = benefit / points if points else benefit
    composite = candidate.score.composite if candidate.score else 0.0
    return (-round(ratio, 6), -composite, candidate.candidate_id)


def _benefit(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, dict):
        return float(value.get("attributed_annual_benefit", 0.0))
    return float(getattr(value, "attributed_annual_benefit", 0.0))


def _rationale(candidate: Candidate, effort: Any, benefit: float, deps: list[str],
               placed: dict[str, int]) -> str:
    parts = [f"{getattr(effort, 'size', 'n/a')} ({getattr(effort, 'points', 0)} points)",
             f"benefit {benefit:,.0f}/yr"]
    if deps:
        parts.append("after " + ", ".join(f"{d} (wave {placed[d]})" for d in deps))
    if candidate.status == "Exploratory":
        parts.append("Exploratory: needs a reviewer to confirm a consumer before build starts")
    readiness = getattr(candidate, "_readiness", None)
    if readiness is not None:
        parts.append(f"DPF readiness {readiness:.0f}%")
    return "; ".join(parts)


def _blocked_reason(candidate: Candidate) -> str:
    for gate in (candidate.score.gates if candidate.score else []):
        if gate.gate == "G4" and not gate.passed:
            return f"Blocked by G4: {gate.detail}"
    return "Blocked"


def _release_hint(candidate: Candidate, dependencies: list[DependencyRow]) -> str:
    for row in dependencies:
        if row.candidate_id == candidate.candidate_id and row.type == "successor_system":
            return (f"not schedulable until a successor for {row.target} is mapped"
                    + (f" (sunset {row.due_hint})" if row.due_hint else ""))
    return ""


def _attributed_reports(candidates: list[Candidate]) -> dict[str, int]:
    from ..portfolio.views import attribute_reports
    counts: dict[str, int] = {}
    for _report, cid in attribute_reports(candidates).items():
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def value_effort_quadrants(candidates: list[Candidate], efforts: dict, values: dict) -> dict:
    """The 2x2 a council draws on the whiteboard: quick wins, big bets, fill-ins, avoid."""
    rows = []
    for candidate in candidates:
        effort = efforts.get(candidate.candidate_id)
        rows.append((candidate.candidate_id,
                     float(getattr(effort, "points", 0.0) if effort else 0.0),
                     _benefit(values.get(candidate.candidate_id))))
    if not rows:
        return {}
    points_median = sorted(r[1] for r in rows)[len(rows) // 2]
    benefit_median = sorted(r[2] for r in rows)[len(rows) // 2]
    quadrants: dict[str, list[str]] = {"quick_wins": [], "big_bets": [], "fill_ins": [],
                                       "reconsider": []}
    for cid, points, benefit in sorted(rows):
        high_value = benefit >= benefit_median and benefit > 0
        low_effort = points <= points_median
        key = ("quick_wins" if high_value and low_effort else
               "big_bets" if high_value else
               "fill_ins" if low_effort else "reconsider")
        quadrants[key].append(cid)
    return {"points_median": points_median, "benefit_median": benefit_median, **quadrants}


def save_waves(connection: sqlite3.Connection, run_id: str, plan: dict) -> None:
    ensure_schema(connection)
    connection.execute("DELETE FROM DP_WAVE WHERE run_id = ?", (run_id,))
    connection.execute("DELETE FROM DP_WAVE_UNSCHEDULED WHERE run_id = ?", (run_id,))
    config = json.dumps(plan["config"])
    for wave in plan["waves"]:
        for position, entry in enumerate(wave["candidates"], start=1):
            connection.execute(
                "INSERT INTO DP_WAVE VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, wave["wave"], position, entry["candidate_id"], entry["size"],
                 entry["points"], entry["annual_benefit"], entry["rationale"],
                 wave["starts_week"], wave["ends_week"], config))
    for row in plan["unscheduled"]:
        connection.execute("INSERT INTO DP_WAVE_UNSCHEDULED VALUES (?,?,?,?)",
                           (run_id, row["candidate_id"], row["reason"], row["release_hint"]))
    connection.commit()


def load_waves(connection: sqlite3.Connection, run_id: str) -> dict:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = [dict(r) for r in connection.execute(
        "SELECT * FROM DP_WAVE WHERE run_id = ? ORDER BY wave, position", (run_id,)).fetchall()]
    unscheduled = [dict(r) for r in connection.execute(
        "SELECT * FROM DP_WAVE_UNSCHEDULED WHERE run_id = ? ORDER BY candidate_id",
        (run_id,)).fetchall()]
    return {"waves": rows, "unscheduled": unscheduled}
