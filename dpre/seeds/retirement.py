"""DPF Stage 12 seed: the retirement list."""
from __future__ import annotations

from pathlib import Path

from ..models import Candidate
from ..util.tabular import write_csv

COLUMNS = (
    "report_id", "report_name", "business_unit", "owner", "disposition", "coverage",
    "users", "last_run", "action", "notify_by", "cut_over_date",
)


def retirement_list(candidate: Candidate) -> list[dict]:
    rows = []
    for report in candidate.reports:
        if report.coverage <= 0:
            continue
        action = ("retire on publication" if report.coverage >= 1.0
                  else "partially covered - review before retirement")
        rows.append({
            "report_id": report.report_id,
            "report_name": report.report_name,
            "business_unit": report.business_unit,
            "owner": report.owner or "UNKNOWN",
            "disposition": report.disposition,
            "coverage": report.coverage,
            "users": report.users,
            "last_run": report.last_run,
            "action": action,
            "notify_by": "TO BE SET",
            "cut_over_date": "TO BE SET",
        })
    rows.sort(key=lambda r: (-r["coverage"], -r["users"]))
    return rows


def write_retirement_list(candidate: Candidate, path: str | Path) -> Path:
    return write_csv(path, retirement_list(candidate), list(COLUMNS))
