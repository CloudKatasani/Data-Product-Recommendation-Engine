"""Portfolio roll-ups (specification section 9.4).

Three views alongside the individual records: the coverage curve, the retirement
map and the conflict heat map. On top of them sits the report attribution that
every estate-level number depends on: a report fully covered by several
candidates counts once, for its *primary* candidate, so the portfolio total is
the number of reports that actually go away rather than the sum of overlapping
per-candidate claims (review finding R-23).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, CandidateReport, KnowledgeGraph, ReportRecord

# A report whose name says it is a filing is held back from retirement whatever
# its disposition: a regulatory return cannot be retired on publication of a
# data product. Whole words only: a retail "Returns Analysis" is stock coming
# back, not a return to a regulator.
REGULATORY_PATTERN = re.compile(
    r"\b(Regulatory|Statutory|Filing|Submission)\b|\b(Compliance|Statutory|Regulatory)\s+Return\b")

# Stage 12 action by disposition (R-23). Keep is the programme's own decision
# that the report stays; it is re-pointed to the product, never retired.
DISPOSITION_ACTION = {
    "retire": "retire on publication",
    "merge": "merge and retire",
    "migrate": "rebuild on the product",
    "keep": "re-point source, report retained",
}
HOLD_ACTION = "hold: decision-critical or regulatory"
RETIRING_DISPOSITIONS = ("retire", "merge", "migrate")


def portfolio_views(candidates: list[Candidate], result: CanonicalizationResult,
                    graph: KnowledgeGraph) -> dict:
    attribution = attribute_reports(candidates)
    return {
        "coverage_curve": coverage_curve(candidates, result),
        "retirement_map": retirement_map(candidates, graph, attribution),
        "estate_retirement": estate_retirement(candidates, graph, attribution),
        "conflict_heat_map": conflict_heat_map(result, candidates),
        "archetype_mix": dict(Counter(c.archetype for c in candidates)),
        "tier_mix": dict(Counter(c.tier for c in candidates)),
        "status_mix": dict(Counter(c.status for c in candidates)),
        "domain_mix": dict(Counter(c.domain or "Unassigned" for c in candidates)),
    }


# --------------------------------------------------------------------------
# Attribution (R-23)
# --------------------------------------------------------------------------

def _composite(candidate: Candidate) -> float:
    return candidate.score.composite if candidate.score else 0.0


def attribute_reports(candidates: list[Candidate]) -> dict[str, str]:
    """Map each fully covered report to exactly one primary candidate.

    The primary candidate is the one with the highest composite among those
    covering every metric of the report; ties break on candidate id so the
    attribution is reproducible. Composites and entity masters compete on the
    same footing: a reviewer sees the same primary whichever card is open.
    """
    best: dict[str, tuple[float, str]] = {}
    for candidate in candidates:
        key = (_composite(candidate), candidate.candidate_id)
        for report in candidate.reports:
            if report.coverage < 1.0:
                continue
            current = best.get(report.report_id)
            # Higher composite wins; on an exact tie the smaller id wins.
            if current is None or (key[0], -_id_rank(key[1])) > (current[0], -_id_rank(current[1])):
                best[report.report_id] = key
    return {report_id: key[1] for report_id, key in best.items()}


def _id_rank(candidate_id: str) -> int:
    return int.from_bytes(candidate_id.encode("utf-8"), "big")


def is_hold(report: ReportRecord | None, name: str = "") -> bool:
    """A decision-critical or regulatory report is never 'retired on publication'."""
    if report is not None:
        if report.decision_critical:
            return True
        name = name or report.report_name
    return bool(REGULATORY_PATTERN.search(name or ""))


def retirement_action(report: CandidateReport, hold: bool) -> str:
    """Stage 12 action for one covered report, disposition-aware (R-23)."""
    if report.coverage < 1.0:
        return "partially covered - review before retirement"
    if hold:
        return HOLD_ACTION
    return DISPOSITION_ACTION.get((report.disposition or "keep").lower(),
                                  DISPOSITION_ACTION["keep"])


def is_retirable(report: CandidateReport, hold: bool) -> bool:
    return (report.coverage >= 1.0 and not hold
            and (report.disposition or "keep").lower() in RETIRING_DISPOSITIONS)


def candidate_attribution(candidate: Candidate, graph: KnowledgeGraph,
                          attribution: dict[str, str]) -> dict:
    """Which of this candidate's fully covered reports it actually owns."""
    attributable: list[str] = []
    also_covered: list[str] = []
    retirable: list[str] = []
    holds: list[str] = []
    by_disposition: Counter = Counter()
    for report in candidate.reports:
        if report.coverage < 1.0:
            continue
        primary = attribution.get(report.report_id) == candidate.candidate_id
        hold = is_hold(graph.reports.get(report.report_id), report.report_name)
        if primary:
            attributable.append(report.report_id)
            if hold:
                holds.append(report.report_id)
            elif is_retirable(report, hold):
                retirable.append(report.report_id)
                by_disposition[(report.disposition or "Keep").title()] += 1
        else:
            also_covered.append(report.report_id)
    packages = package_impact(candidate, graph, attribution)
    return {
        "reports_attributable": len(attributable),
        "also_covered_by_others": len(also_covered),
        "reports_retirable_attributable": len(retirable),
        "reports_on_hold": len(holds),
        "attributable_report_ids": sorted(attributable),
        "also_covered_report_ids": sorted(also_covered),
        "retirable_report_ids": sorted(retirable),
        "hold_report_ids": sorted(holds),
        "retirable_by_disposition": dict(sorted(by_disposition.items())),
        "packages_fully_retirable": packages["fully_retirable"],
        "packages_partially_affected": packages["partially_affected"],
    }


def package_impact(candidate: Candidate, graph: KnowledgeGraph,
                   attribution: dict[str, str]) -> dict:
    """Packages and semantic models this candidate would empty out (O3 objective).

    A package is fully retirable only when every report in it is retirable and
    attributed to this candidate; that is where licence and refresh cost sit.
    """
    reports_by_package: dict[str, set[str]] = defaultdict(set)
    for report in graph.reports.values():
        if report.semantic_container:
            reports_by_package[report.semantic_container].add(report.report_id)
    retirable_here = {
        r.report_id for r in candidate.reports
        if attribution.get(r.report_id) == candidate.candidate_id
        and is_retirable(r, is_hold(graph.reports.get(r.report_id), r.report_name))
    }
    fully: list[str] = []
    partially: list[str] = []
    for package, members in sorted(reports_by_package.items()):
        touched = members & retirable_here
        if not touched:
            continue
        (fully if touched == members else partially).append(package)
    return {"fully_retirable": fully, "partially_affected": partially}


def estate_retirement(candidates: list[Candidate], graph: KnowledgeGraph,
                      attribution: dict[str, str] | None = None) -> dict:
    """De-duplicated, disposition-aware retirement figure for the whole estate."""
    attribution = attribution if attribution is not None else attribute_reports(candidates)
    by_id: dict[str, Candidate] = {c.candidate_id: c for c in candidates}
    by_disposition: Counter = Counter()
    retirable = 0
    holds = 0
    keep_repointed = 0
    users_retired = 0
    for report_id, candidate_id in sorted(attribution.items()):
        candidate = by_id[candidate_id]
        row = next(r for r in candidate.reports if r.report_id == report_id)
        hold = is_hold(graph.reports.get(report_id), row.report_name)
        if hold:
            holds += 1
        elif is_retirable(row, hold):
            retirable += 1
            users_retired += row.users
            by_disposition[(row.disposition or "Keep").title()] += 1
        else:
            keep_repointed += 1
    packages_full: set[str] = set()
    packages_partial: set[str] = set()
    for candidate in candidates:
        impact = package_impact(candidate, graph, attribution)
        packages_full.update(impact["fully_retirable"])
        packages_partial.update(impact["partially_affected"])
    packages_partial -= packages_full
    return {
        "reports_in_estate": len(graph.reports),
        "reports_fully_covered_distinct": len(attribution),
        "reports_fully_covered_claimed": sum(
            1 for c in candidates for r in c.reports if r.coverage >= 1.0),
        "reports_retirable": retirable,
        "reports_on_hold": holds,
        "reports_keep_repointed": keep_repointed,
        "users_on_retirable_reports": users_retired,
        "retirable_by_disposition": dict(sorted(by_disposition.items())),
        "packages_in_estate": len({r.semantic_container for r in graph.reports.values()
                                   if r.semantic_container}),
        "packages_fully_retirable": sorted(packages_full),
        "packages_partially_affected": sorted(packages_partial),
    }


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------

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


def retirement_map(candidates: list[Candidate], graph: KnowledgeGraph,
                   attribution: dict[str, str] | None = None) -> list[dict]:
    """Reports by candidate by disposition, with the users affected and the
    attributed (de-duplicated) figures beside the raw coverage counts."""
    attribution = attribution if attribution is not None else attribute_reports(candidates)
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
        row = {
            "candidate_id": candidate.candidate_id,
            "candidate": candidate.proposed_name,
            "status": candidate.status,
            "fully_covered": full,
            "partially_covered": partial,
            "users_affected": sum(b["users"] for b in by_disposition.values()),
            "by_disposition": {k: v for k, v in sorted(by_disposition.items())},
            "owners_to_notify": sorted({r.owner for r in candidate.reports
                                        if r.coverage >= 1.0 and r.owner})[:20],
        }
        attributed = candidate_attribution(candidate, graph, attribution)
        row.update({k: v for k, v in attributed.items() if not k.endswith("_ids")})
        out.append(row)
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
