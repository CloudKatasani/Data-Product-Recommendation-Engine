"""Usage-weighted demand with cadence-aware recency and true tool parity (R-49).

The specification's demand formula (section 8.1) is

    usage_weight = sum over reports of ln(1 + runs_12m) x distinct_users x recency,
    recency = 0.5 ^ (months_since_last_run / half_life)

Two biases in the shipped implementation were measurable and untested:

* Recency ignored cadence. A fixed six-month half-life scores an annual report
  last run eleven months ago at 0.28 of its weight and a quarterly one five
  months ago at 0.56, although both ran exactly when they were supposed to.
  Here the half-life is ``max(configured, 2 x cadence months)``, so a report
  is only "stale" once it has missed two of its own cycles, and the half-life
  actually applied is recorded per report for the evidence row.
* Tool parity was partial. Section 16.2 says counts are normalised to a
  percentile *within the tool* before they enter the score; the shipped code
  multiplied the raw ``ln(1 + runs)`` by a clamped percentile factor, so a tool
  with heavier logging still won through the base term. Here a report's run
  count is replaced by the pooled-estate run count at the same within-tool
  percentile (quantile matching), which makes the run-count term of every tool
  follow one distribution. On a single-tool estate the mapping is the identity.

Everything is a pure function of the reports, the as-of date and the
configuration; the applied half-life and percentile are returned so the
usage_weight evidence can show them (R-22).
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass

from ..config import DECISION_CRITICAL_USAGE_FLOOR, RECENCY_HALF_LIFE_MONTHS
from ..models import ReportRecord
from ..usage import months_since

# Months between two scheduled runs, by the first word of the schedule text.
CADENCE_MONTHS = {
    "hourly": 1, "daily": 1, "weekly": 1, "fortnightly": 1, "biweekly": 1,
    "monthly": 1, "quarterly": 3, "half": 6, "semi": 6, "semiannual": 6,
    "annual": 12, "annually": 12, "yearly": 12,
}


@dataclass
class ReportDemand:
    report_id: str
    weight: float
    base: float                 # ln(1 + parity-adjusted runs) x users, before decay
    decay: float
    half_life_months: float
    cadence: str
    percentile: float           # within-tool percentile of the raw run count
    raw_runs: int
    adjusted_runs: float        # pooled-estate run count at the same percentile
    users: int
    months_since_run: float
    floored: bool               # the decision-critical floor applied (section 15.1)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def cadence_months(frequency: str | None) -> int:
    """Months per cycle from a schedule string such as ``Weekly Mon 07:00``."""
    head = (frequency or "").strip().lower().replace("-", " ").split(" ")[0]
    return CADENCE_MONTHS.get(head, 1)


def half_life_for(report: ReportRecord,
                  configured: float = RECENCY_HALF_LIFE_MONTHS) -> float:
    """A report is stale only once it has missed two of its own cycles."""
    cadence = cadence_months(report.schedule_frequency) if report.schedule_flag else 1
    return float(max(configured, 2 * cadence))


def within_tool_percentiles(reports: list[ReportRecord]) -> dict[str, float]:
    """Percentile rank of each report's run count within its own tool."""
    by_tool: dict[str, list[ReportRecord]] = {}
    for report in reports:
        by_tool.setdefault(report.tool or "cognos", []).append(report)
    out: dict[str, float] = {}
    for group in by_tool.values():
        ordered = sorted(group, key=lambda r: (r.run_count_12m, r.report_id))
        n = len(ordered)
        for i, report in enumerate(ordered):
            out[report.report_id] = (i + 1) / n if n else 0.5
    return out


def parity_adjusted_runs(reports: list[ReportRecord],
                         percentiles: dict[str, float]) -> dict[str, float]:
    """Quantile-match every tool's run counts onto the pooled distribution."""
    pooled = sorted(max(0, r.run_count_12m) for r in reports)
    if not pooled:
        return {}
    last = len(pooled) - 1
    out: dict[str, float] = {}
    for report in reports:
        p = percentiles.get(report.report_id, 0.5)
        out[report.report_id] = float(pooled[min(last, max(0, round(p * last)))])
    return out


def report_demand(reports: list[ReportRecord], as_of: _dt.date,
                  half_life_months: float = RECENCY_HALF_LIFE_MONTHS,
                  normalize_tools: bool = True) -> dict[str, ReportDemand]:
    """Every report's usage weight with the terms that produced it."""
    percentiles = within_tool_percentiles(reports)
    tools = {r.tool or "cognos" for r in reports}
    adjusted = (parity_adjusted_runs(reports, percentiles)
                if normalize_tools and len(tools) > 1 else
                {r.report_id: float(max(0, r.run_count_12m)) for r in reports})
    out: dict[str, ReportDemand] = {}
    for report in reports:
        runs = adjusted.get(report.report_id, float(max(0, report.run_count_12m)))
        users = max(1, report.distinct_users_12m)
        base = math.log1p(runs) * users
        half_life = half_life_for(report, half_life_months)
        months = months_since(report.last_run_date, as_of)
        decay = 0.5 ** (months / half_life)
        weight = base * decay
        floored = False
        if report.decision_critical and weight < DECISION_CRITICAL_USAGE_FLOOR * base:
            weight = DECISION_CRITICAL_USAGE_FLOOR * base
            floored = True
        out[report.report_id] = ReportDemand(
            report_id=report.report_id, weight=round(weight, 4), base=round(base, 4),
            decay=round(decay, 4), half_life_months=half_life,
            cadence=(report.schedule_frequency or "").strip() if report.schedule_flag else "",
            percentile=round(percentiles.get(report.report_id, 0.5), 4),
            raw_runs=max(0, report.run_count_12m), adjusted_runs=runs, users=users,
            months_since_run=round(months, 2), floored=floored,
        )
    return out


def report_weights(reports: list[ReportRecord], as_of: _dt.date,
                   half_life_months: float = RECENCY_HALF_LIFE_MONTHS,
                   normalize_tools: bool = True) -> dict[str, float]:
    """``{report_id: usage_weight}``, the shape every consumer of demand expects."""
    return {rid: d.weight for rid, d in
            report_demand(reports, as_of, half_life_months, normalize_tools).items()}


def median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
