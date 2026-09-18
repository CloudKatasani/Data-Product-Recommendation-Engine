"""Usage-weighted demand primitives (specification sections 8.1 and 11.2).

    usage_weight = sum over reports of ln(1 + run_count_12m) * distinct_users
                   * recency_decay,  recency = 0.5 ^ (months_since_last_run / 6)

Power BI view counts and Cognos run counts are normalized to a within-tool
percentile first, so a tool with heavier logging does not dominate the ranking
(section 16.2).
"""
from __future__ import annotations

import datetime as _dt
import math

from .config import DECISION_CRITICAL_USAGE_FLOOR, RECENCY_HALF_LIFE_MONTHS
from .models import ReportRecord

CADENCE_MONTHS = {"daily": 1, "weekly": 1, "monthly": 1, "quarterly": 3, "annual": 12}


def months_since(last_run: _dt.date | None, as_of: _dt.date) -> float:
    if last_run is None:
        return 24.0            # unknown recency is scored as stale, never as fresh
    delta_days = (as_of - last_run).days
    return max(0.0, delta_days / 30.44)


def recency_decay(last_run: _dt.date | None, as_of: _dt.date,
                  half_life_months: float = RECENCY_HALF_LIFE_MONTHS) -> float:
    return 0.5 ** (months_since(last_run, as_of) / half_life_months)


def tool_percentiles(reports: list[ReportRecord]) -> dict[str, float]:
    """Percentile rank of each report's run count within its own tool."""
    by_tool: dict[str, list[ReportRecord]] = {}
    for report in reports:
        by_tool.setdefault(report.tool or "cognos", []).append(report)
    out: dict[str, float] = {}
    for group in by_tool.values():
        ordered = sorted(group, key=lambda r: r.run_count_12m)
        n = len(ordered)
        for i, report in enumerate(ordered):
            out[report.report_id] = (i + 1) / n if n else 0.5
    return out


def report_usage_weight(report: ReportRecord, as_of: _dt.date,
                        percentile: float | None = None,
                        half_life_months: float = RECENCY_HALF_LIFE_MONTHS) -> float:
    """Usage weight of one report, with the cross-tool parity adjustment applied."""
    runs = max(0, report.run_count_12m)
    users = max(1, report.distinct_users_12m)
    base = math.log1p(runs) * users
    decay = recency_decay(report.last_run_date, as_of, half_life_months)
    weight = base * decay
    if percentile is not None:
        # Anchor on the within-tool percentile so a chattier tool cannot win on
        # logging volume alone; 2 * percentile keeps the median report at 1.0.
        weight *= max(0.2, min(2.0, 2.0 * percentile))
    if report.decision_critical:
        weight = max(weight, DECISION_CRITICAL_USAGE_FLOOR * base)
    return round(weight, 4)


def report_weights(reports: list[ReportRecord], as_of: _dt.date,
                   half_life_months: float = RECENCY_HALF_LIFE_MONTHS,
                   normalize_tools: bool = True) -> dict[str, float]:
    percentiles = tool_percentiles(reports) if normalize_tools else {}
    return {
        r.report_id: report_usage_weight(r, as_of, percentiles.get(r.report_id), half_life_months)
        for r in reports
    }


def scheduled_share(reports: list[ReportRecord]) -> float:
    if not reports:
        return 0.0
    return sum(1 for r in reports if r.schedule_flag) / len(reports)


def dominant_cadence(reports: list[ReportRecord]) -> str:
    counts: dict[str, int] = {}
    for report in reports:
        cadence = (report.schedule_frequency or "").strip().title()
        if cadence:
            counts[cadence] = counts.get(cadence, 0) + 1
    if not counts:
        return "Ad hoc"
    return max(counts.items(), key=lambda kv: kv[1])[0]
