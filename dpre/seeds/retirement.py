"""DPF Stage 12 seed: the retirement list (specification section 9.2).

The action per report is disposition-aware (review findings R-23 and R-36):
a Retire report is retired on publication, a Merge report is merged and
retired, a Migrate report is rebuilt on the product, a Keep report is
re-pointed at the product and retained, and a decision-critical or regulatory
report is held whatever its disposition. A Keep report is never told it will
be "retired on publication": Keep is the programme's own decision that the
report stays.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import Candidate, KnowledgeGraph
from ..portfolio.views import HOLD_ACTION, is_hold, is_retirable, retirement_action
from ..util.tabular import write_csv
from .provenance import run_provenance

COLUMNS = (
    "report_id", "report_name", "business_unit", "owner", "disposition", "coverage",
    "users", "last_run", "action", "retirable", "hold_reason", "notify_by", "cut_over_date",
    "run_id", "synthetic", "generation_id",
)


def retirement_list(candidate: Candidate, graph: KnowledgeGraph | None = None,
                    provenance: dict[str, Any] | None = None) -> list[dict]:
    provenance = provenance or run_provenance(candidate, graph)
    rows = []
    for report in candidate.reports:
        if report.coverage <= 0:
            continue
        record = graph.reports.get(report.report_id) if graph is not None else None
        hold = is_hold(record, report.report_name)
        rows.append({
            "report_id": report.report_id,
            "report_name": report.report_name,
            "business_unit": report.business_unit,
            "owner": report.owner or "UNKNOWN",
            "disposition": report.disposition,
            "coverage": report.coverage,
            "users": report.users,
            "last_run": report.last_run,
            "action": retirement_action(report, hold),
            "retirable": "Y" if is_retirable(report, hold) else "N",
            "hold_reason": _hold_reason(record, report.report_name, hold),
            "notify_by": "TO BE SET",
            "cut_over_date": "TO BE SET",
            "run_id": provenance["run_id"],
            "synthetic": "TRUE" if provenance["synthetic"] else "FALSE",
            "generation_id": provenance["generation_id"],
        })
    rows.sort(key=lambda r: (-r["coverage"], -r["users"], r["report_id"]))
    return rows


def _hold_reason(record, name: str, hold: bool) -> str:
    if not hold:
        return ""
    if record is not None and record.decision_critical:
        return "marked decision-critical by a reviewer"
    return "regulatory or statutory filing by name"


def write_retirement_list(candidate: Candidate, path: str | Path,
                          graph: KnowledgeGraph | None = None) -> Path:
    return write_csv(path, retirement_list(candidate, graph), list(COLUMNS))


__all__ = ["COLUMNS", "HOLD_ACTION", "retirement_list", "write_retirement_list"]
