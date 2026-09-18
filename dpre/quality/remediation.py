"""Lineage gaps as a plan somebody can work, not a list somebody can read
(review finding R-46).

The gap register named what was missing and stopped there. A list of four
hundred unresolved lineage rows is not actionable: nobody owns it, nothing says
which twenty to fix first, and the next run produces the same list with no way
to tell what moved.

This module turns gaps into remediation units. A unit groups the rows that one
person fixes in one action - a missing table in the catalog export, a column
with no definition, a metric with no steward - and carries an owner role, a
priority derived from the usage behind it, the effort shape, and a status that
survives into the next run. Priority is usage-weighted because fixing lineage
for a report nobody runs buys nothing.

Nothing here decides anything. The owner roles are roles, never names, and the
plan is a proposal a steward accepts or rejects like any other.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from ..util.ids import stable_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS REMEDIATION_UNIT (
    run_id TEXT, unit_id TEXT, unit_type TEXT, subject TEXT, owner_role TEXT,
    priority TEXT, rank INTEGER, rows_affected INTEGER, usage_at_stake REAL,
    reports TEXT, detail TEXT, action TEXT, status TEXT, first_seen_run TEXT,
    PRIMARY KEY (run_id, unit_id)
);
"""

#: Which role fixes which kind of gap. A catalog export that is missing tables
#: is not a steward's problem, and a definition nobody wrote is not an
#: administrator's.
OWNER_ROLES = {
    "catalog_table": "Catalog admin",
    "catalog_column": "Catalog admin",
    "definition": "Domain steward",
    "steward": "Domain owner",
    "opaque_metric": "Report owner",
    "sunset_source": "Source system owner",
}

ACTIONS = {
    "catalog_table": "Extend the catalog export to include this table, then re-run",
    "catalog_column": "Extend the catalog export to include this column, then re-run",
    "definition": "Write a business definition for this column in the catalog",
    "steward": "Assign a steward to this metric's business term",
    "opaque_metric": "Supply the calculation in words so it can be fingerprinted",
    "sunset_source": "Map the successor system for this source, or confirm the retirement",
}

#: Share of the run's total usage at stake, above which a unit is high priority.
HIGH_PRIORITY_SHARE = 0.05
MEDIUM_PRIORITY_SHARE = 0.01


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def _usage_of_report(graph: Any, report_id: str) -> float:
    """Runs in the last twelve months.

    The scorer's usage weight is a percentile within a tool and is not on the
    graph record. Raw runs are the honest proxy here: this is a work queue, and
    ordering it by how often the affected reports actually run is what makes the
    top of it worth doing first.
    """
    report = graph.reports.get(report_id)
    return float(getattr(report, "run_count_12m", 0) or 0) if report else 0.0


def remediation_plan(result: Any, previous: list[dict] | None = None) -> list[dict]:
    """Group this run's gaps into units, ranked by the usage behind them.

    ``previous`` is the plan from the run before, used only to carry a unit's
    first-seen run forward and to mark what is new. A unit that keeps appearing
    is the useful signal: it says the fix was never made.
    """
    graph, canonical = result.graph, result.canonical
    seen_before = {row["unit_id"]: row for row in (previous or [])}

    units: dict[tuple[str, str], dict] = {}

    def unit(unit_type: str, subject: str) -> dict:
        key = (unit_type, subject)
        if key not in units:
            units[key] = {
                "unit_id": stable_id("REM", unit_type, subject),
                "unit_type": unit_type, "subject": subject,
                "owner_role": OWNER_ROLES.get(unit_type, "Domain steward"),
                "action": ACTIONS.get(unit_type, ""),
                "rows_affected": 0, "usage_at_stake": 0.0,
                "reports": set(), "detail": "",
            }
        return units[key]

    # Quarantined lineage: grouped by the catalog object that was missing, so
    # one missing table is one unit and not two hundred rows.
    for row in graph.quarantine:
        reference = getattr(row, "raw_reference", "") or ""
        reason = getattr(row, "reason_code", "") or ""
        if reason == "NO_CATALOG_TABLE":
            subject = ".".join(reference.split(".")[:4]) or reference
            entry = unit("catalog_table", subject)
        elif reason in ("NO_CATALOG_COLUMN", "MISSING_REFERENCE", "LOW_CONFIDENCE"):
            entry = unit("catalog_column", reference)
        else:
            continue
        entry["rows_affected"] += 1
        entry["detail"] = getattr(row, "detail", "") or entry["detail"]
        report_id = getattr(row, "_report_id", "") or ""
        if report_id:
            entry["reports"].add(report_id)
            entry["usage_at_stake"] += _usage_of_report(graph, report_id)

    # Definitions are grouped by table, not by column. One row per undefined
    # column is five hundred tickets nobody opens; one row per table is the unit
    # a steward actually works, because they sit down with the table owner once
    # and define its columns together.
    undefined: dict[str, list[str]] = defaultdict(list)
    for column in getattr(graph, "columns", {}).values():
        fqn = getattr(column, "column_fqn", "") or ""
        if fqn and not (getattr(column, "definition", "") or "").strip():
            undefined[getattr(column, "table_fqn", "") or fqn].append(
                getattr(column, "column_name", "") or fqn)
    for table_fqn, columns in undefined.items():
        entry = unit("definition", table_fqn)
        entry["rows_affected"] += len(columns)
        entry["detail"] = (f"{len(columns)} columns with no business definition: "
                           + ", ".join(sorted(columns)[:8])
                           + (", ..." if len(columns) > 8 else ""))

    for metric in canonical.metrics.values():
        steward = (getattr(metric, "steward_id", "") or "").strip()
        source = (getattr(metric, "steward_source", "") or "").lower()
        if not steward or "suggestion" in source or "report owner" in source:
            entry = unit("steward", metric.metric_id)
            entry["rows_affected"] += 1
            entry["detail"] = (f"{metric.canonical_name} has no confirmed steward"
                               if not steward else
                               f"{metric.canonical_name} has a suggested owner, not a steward")
            entry["reports"] |= set(getattr(metric, "report_ids", []) or [])
            for report_id in getattr(metric, "report_ids", []) or []:
                entry["usage_at_stake"] += _usage_of_report(graph, report_id)
        if getattr(metric, "opaque", False):
            entry = unit("opaque_metric", metric.metric_id)
            entry["rows_affected"] += 1
            entry["detail"] = f"{metric.canonical_name} could not be parsed into a shape"
            entry["reports"] |= set(getattr(metric, "report_ids", []) or [])
            for report_id in getattr(metric, "report_ids", []) or []:
                entry["usage_at_stake"] += _usage_of_report(graph, report_id)

    for table in getattr(graph, "tables", {}).values():
        lifecycle = (getattr(table, "lifecycle_status", "") or "").lower()
        if lifecycle in ("sunset", "deprecated"):
            entry = unit("sunset_source", getattr(table, "table_fqn", "") or "")
            entry["rows_affected"] += 1
            entry["detail"] = f"lifecycle {lifecycle}, successor not mapped"

    total_usage = sum(float(getattr(r, "run_count_12m", 0) or 0)
                      for r in graph.reports.values()) or 1.0

    rows = []
    for entry in units.values():
        share = entry["usage_at_stake"] / total_usage
        if share >= HIGH_PRIORITY_SHARE:
            priority = "high"
        elif share >= MEDIUM_PRIORITY_SHARE or entry["rows_affected"] >= 10:
            priority = "medium"
        else:
            priority = "low"
        before = seen_before.get(entry["unit_id"])
        rows.append({
            **entry,
            "reports": sorted(entry["reports"]),
            "usage_at_stake": round(entry["usage_at_stake"], 2),
            "usage_share": round(share, 4),
            "priority": priority,
            "status": "carried" if before else "new",
            "first_seen_run": (before or {}).get("first_seen_run", "") or "",
        })

    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (order[r["priority"]], -r["usage_at_stake"], r["unit_type"],
                             r["subject"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


def remediation_summary(rows: list[dict]) -> dict:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["priority"]] += 1
    return {
        "units": len(rows),
        "by_priority": dict(counts),
        "by_owner_role": dict(_count(r["owner_role"] for r in rows)),
        "carried_from_previous": sum(1 for r in rows if r["status"] == "carried"),
        "usage_at_stake": round(sum(r["usage_at_stake"] for r in rows), 2),
    }


def _count(values) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for value in values:
        out[value] += 1
    return dict(out)


def save_remediation_plan(connection: sqlite3.Connection, run_id: str,
                          rows: list[dict]) -> None:
    ensure_schema(connection)
    connection.executemany(
        "INSERT OR REPLACE INTO REMEDIATION_UNIT (run_id, unit_id, unit_type, subject, "
        "owner_role, priority, rank, rows_affected, usage_at_stake, reports, detail, action, "
        "status, first_seen_run) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(run_id, r["unit_id"], r["unit_type"], r["subject"], r["owner_role"], r["priority"],
          r["rank"], r["rows_affected"], r["usage_at_stake"], json.dumps(r["reports"]),
          r["detail"], r["action"], r["status"], r["first_seen_run"] or run_id)
         for r in rows])
    connection.commit()


def load_remediation_plan(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = [dict(r) for r in connection.execute(
        "SELECT * FROM REMEDIATION_UNIT WHERE run_id = ? ORDER BY rank", (run_id,)).fetchall()]
    for row in rows:
        row["reports"] = json.loads(row["reports"] or "[]")
    return rows


def write_remediation_plan_csv(rows: list[dict], path) -> "Path":
    """The plan as a CSV a catalog team can work through and hand back."""
    from pathlib import Path

    from ..util.tabular import write_csv

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fields = ["rank", "priority", "unit_type", "subject", "owner_role", "action",
              "rows_affected", "usage_at_stake", "usage_share", "status", "detail"]
    write_csv(target, [{k: row.get(k, "") for k in fields} for row in rows], fields)
    return target
