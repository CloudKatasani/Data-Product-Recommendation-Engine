"""Portfolio roll-ups (specification section 9.4).

Three views alongside the individual records: the coverage curve, the retirement
map and the conflict heat map.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, KnowledgeGraph


def portfolio_views(candidates: list[Candidate], result: CanonicalizationResult,
                    graph: KnowledgeGraph) -> dict:
    return {
        "coverage_curve": coverage_curve(candidates, result),
        "retirement_map": retirement_map(candidates, graph),
        "conflict_heat_map": conflict_heat_map(result, candidates),
        "archetype_mix": dict(Counter(c.archetype for c in candidates)),
        "tier_mix": dict(Counter(c.tier for c in candidates)),
        "status_mix": dict(Counter(c.status for c in candidates)),
        "domain_mix": dict(Counter(c.domain or "Unassigned" for c in candidates)),
    }


def coverage_curve(candidates: list[Candidate], result: CanonicalizationResult) -> list[dict]:
    """Cumulative usage-weighted KPI consumption covered by the top N candidates."""
    total = sum(m.usage_weight for m in result.metrics.values()) or 1.0
    ranked = sorted(candidates, key=lambda c: -(c.score.composite if c.score else 0.0))
    covered: set[str] = set()
    curve = []
    for index, candidate in enumerate(ranked, start=1):
        covered.update(candidate.metric_ids)
        weight = sum(result.metrics[m].usage_weight for m in covered if m in result.metrics)
        curve.append({
            "n": index,
            "candidate_id": candidate.candidate_id,
            "candidate": candidate.proposed_name,
            "composite": candidate.score.composite if candidate.score else 0.0,
            "metrics_covered": len(covered),
            "cumulative_coverage": round(weight / total, 4),
        })
    return curve


def retirement_map(candidates: list[Candidate], graph: KnowledgeGraph) -> list[dict]:
    """Reports by candidate by disposition, with the users affected."""
    out = []
    for candidate in sorted(candidates,
                            key=lambda c: -(c.score.composite if c.score else 0.0)):
        by_disposition: dict[str, dict] = defaultdict(lambda: {"reports": 0, "users": 0})
        full = 0
        partial = 0
        for report in candidate.reports:
            if report.coverage <= 0:
                continue
            bucket = by_disposition[report.disposition or "Keep"]
            bucket["reports"] += 1
            bucket["users"] += report.users
            if report.coverage >= 1.0:
                full += 1
            else:
                partial += 1
        out.append({
            "candidate_id": candidate.candidate_id,
            "candidate": candidate.proposed_name,
            "status": candidate.status,
            "fully_covered": full,
            "partially_covered": partial,
            "users_affected": sum(b["users"] for b in by_disposition.values()),
            "by_disposition": {k: v for k, v in sorted(by_disposition.items())},
            "owners_to_notify": sorted({r.owner for r in candidate.reports
                                        if r.coverage >= 1.0 and r.owner})[:20],
        })
    return out


def conflict_heat_map(result: CanonicalizationResult, candidates: list[Candidate]) -> list[dict]:
    """Canonical metrics by number of competing definitions and usage at stake."""
    competing: dict[str, set[str]] = defaultdict(set)
    at_stake: dict[str, float] = defaultdict(float)
    label_of: dict[str, str] = {}
    for conflict in result.conflicts:
        key = conflict.label.lower()
        label_of[key] = conflict.label
        competing[key].add(conflict.metric_id_a)
        competing[key].add(conflict.metric_id_b)
        at_stake[key] = max(at_stake[key], conflict.usage_weight_a + conflict.usage_weight_b)

    candidate_of: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        for conflict_id in candidate.conflicts:
            candidate_of[conflict_id].append(candidate.candidate_id)

    out = []
    for key, metric_ids in competing.items():
        conflicts = [c for c in result.conflicts if c.label.lower() == key]
        out.append({
            "label": label_of[key],
            "competing_definitions": len(metric_ids),
            "conflicts": len(conflicts),
            "usage_at_stake": round(at_stake[key], 2),
            "patterns": sorted({c.pattern for c in conflicts}),
            "stewards": sorted({c.steward_id for c in conflicts if c.steward_id}),
            "open": sum(1 for c in conflicts if c.resolution_status == "OPEN"),
            "candidates": sorted({cid for c in conflicts for cid in candidate_of.get(c.conflict_id, [])}),
            "metric_ids": sorted(metric_ids),
        })
    out.sort(key=lambda row: (-row["usage_at_stake"], -row["competing_definitions"]))
    return out
