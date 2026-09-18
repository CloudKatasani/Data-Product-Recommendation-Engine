"""Data-quality scorecard on the input extracts (review finding R-35).

A CDO wants to see the quality of what the engine was fed, by input and DQ
dimension, before trusting a ranking derived from it. The shipped validator
checked presence, orphan report ids, missing references and sparse usage; a
duplicated report row, a disposition of "Whatever", a negative run count, a
90-day count above the 12-month count, a last run in 2031 and an aggregation of
"SUMMARISE" produced no issue at all, and the duplicate was dropped silently.

One row per (input, dimension, rule): rows checked, rows failed, sample ids, the
threshold and a result. Dimensions follow the usual DQ vocabulary -
uniqueness, validity, consistency, completeness, timeliness, accuracy - so the
scorecard reads like the ones a governance office already publishes. Nothing
here blocks a run; the rows go on the manifest and into ``DQ_SCORECARD``.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from collections import Counter

from ..models import ExtractBundle

SCHEMA = """
CREATE TABLE IF NOT EXISTS DQ_SCORECARD (
    run_id TEXT, input TEXT, dimension TEXT, rule_id TEXT, description TEXT,
    rows_checked INTEGER, rows_failed INTEGER, failure_share REAL, sample_ids TEXT,
    threshold REAL, result TEXT,
    PRIMARY KEY (run_id, input, rule_id)
);
"""

DISPOSITIONS = {"keep", "merge", "retire", "migrate"}
AGGREGATIONS = {"", "sum", "avg", "average", "count", "count distinct", "distinct count",
                "max", "min", "ratio", "median", "stddev", "variance", "percentile", "aggregate",
                "none", "calculated"}
CLASSIFICATIONS = {"", "public", "internal", "confidential", "restricted"}
LIFECYCLES = {"", "active", "sunset", "deprecated", "retired"}
FREQUENCY_HEADS = {"", "hourly", "daily", "weekly", "fortnightly", "monthly", "quarterly",
                   "annual", "annually", "yearly", "half", "semi", "ad", "adhoc", "on"}
SAMPLE = 10


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def _rule(input_name: str, dimension: str, rule_id: str, description: str,
          checked: int, failed: list[str], threshold: float = 0.0,
          warn_only: bool = False) -> dict:
    share = round(len(failed) / checked, 4) if checked else 0.0
    if not failed:
        result = "pass"
    elif share <= threshold:
        result = "warn"
    else:
        result = "warn" if warn_only else "fail"
    return {"input": input_name, "dimension": dimension, "rule_id": rule_id,
            "description": description, "rows_checked": checked, "rows_failed": len(failed),
            "failure_share": share, "sample_ids": sorted(failed)[:SAMPLE],
            "threshold": threshold, "result": result}


def dq_scorecard(bundle: ExtractBundle, as_of: _dt.date | None = None) -> list[dict]:
    as_of = as_of or bundle.as_of_date
    rows: list[dict] = []
    reports, kpis, columns, glossary = bundle.reports, bundle.kpis, bundle.columns, bundle.glossary

    # ---- report inventory ---------------------------------------------
    if reports:
        ids = Counter(r.report_id for r in reports)
        rows.append(_rule("reports", "uniqueness", "RPT-01", "report_id is unique",
                          len(reports), [i for i, n in ids.items() if n > 1]))
        rows.append(_rule("reports", "validity", "RPT-02",
                          "disposition is one of Keep, Merge, Retire, Migrate", len(reports),
                          [r.report_id for r in reports
                           if (r.disposition or "").strip().lower() not in DISPOSITIONS]))
        rows.append(_rule("reports", "validity", "RPT-03", "run and user counts are non-negative",
                          len(reports),
                          [r.report_id for r in reports
                           if min(r.run_count_90d, r.run_count_12m, r.distinct_users_12m) < 0]))
        rows.append(_rule("reports", "consistency", "RPT-04",
                          "run_count_90d does not exceed run_count_12m", len(reports),
                          [r.report_id for r in reports if r.run_count_90d > r.run_count_12m]))
        rows.append(_rule("reports", "timeliness", "RPT-05",
                          "last_run_date is not after the as-of date", len(reports),
                          [r.report_id for r in reports
                           if r.last_run_date and r.last_run_date > as_of]))
        rows.append(_rule("reports", "completeness", "RPT-06", "last_run_date present",
                          len(reports), [r.report_id for r in reports if r.last_run_date is None],
                          threshold=0.05, warn_only=True))
        rows.append(_rule("reports", "completeness", "RPT-07", "owner and business unit present",
                          len(reports),
                          [r.report_id for r in reports if not (r.owner and r.business_unit)],
                          threshold=0.05, warn_only=True))
        rows.append(_rule("reports", "consistency", "RPT-08",
                          "a scheduled report names a recognisable frequency", len(reports),
                          [r.report_id for r in reports if r.schedule_flag and
                           (r.schedule_frequency or "").strip().lower().split(" ")[0]
                           not in FREQUENCY_HEADS], threshold=0.05, warn_only=True))
        rows.append(_rule("reports", "consistency", "RPT-09",
                          "a report with runs in 90 days has a last run within 90 days",
                          len(reports),
                          [r.report_id for r in reports if r.run_count_90d > 0 and r.last_run_date
                           and (as_of - r.last_run_date).days > 90], threshold=0.02,
                          warn_only=True))

    # ---- KPI lineage ---------------------------------------------------
    if kpis:
        keys = Counter((k.kpi_id, k.report_id, k.raw_reference) for k in kpis)
        rows.append(_rule("kpi_lineage", "uniqueness", "KPI-01",
                          "(kpi_id, report_id, reference) is unique", len(kpis),
                          [f"{k[0]}@{k[1]}" for k, n in keys.items() if n > 1]))
        rows.append(_rule("kpi_lineage", "validity", "KPI-02",
                          "aggregation_type is a recognised aggregation", len(kpis),
                          [k.kpi_id for k in kpis
                           if (k.aggregation_type or "").strip().lower() not in AGGREGATIONS]))
        rows.append(_rule("kpi_lineage", "completeness", "KPI-03", "calculation_expression present",
                          len(kpis), [k.kpi_id for k in kpis if not k.calculation_expression],
                          threshold=0.05, warn_only=True))
        rows.append(_rule("kpi_lineage", "completeness", "KPI-04",
                          "table and column reference present", len(kpis),
                          [k.kpi_id for k in kpis if not (k.table and k.column)],
                          threshold=0.02, warn_only=True))
        report_ids = {r.report_id for r in reports}
        if report_ids:
            rows.append(_rule("kpi_lineage", "consistency", "KPI-05",
                              "report_id exists in the report inventory", len(kpis),
                              [k.kpi_id for k in kpis if k.report_id not in report_ids],
                              threshold=0.02, warn_only=True))
        # Declared reuse against derived reuse (section 3.2 cross-check): the
        # reports that carry the same label, counted from the lineage itself.
        derived: dict[str, set[str]] = {}
        for k in kpis:
            derived.setdefault((k.kpi_label or "").strip().lower(), set()).add(k.report_id)
        declared = [k for k in kpis if k.cross_report_count > 0]
        if declared:
            rows.append(_rule("kpi_lineage", "accuracy", "KPI-06",
                              "declared cross_report_count agrees with derived reuse (+/- 25%)",
                              len(declared),
                              [k.kpi_id for k in declared
                               if abs(k.cross_report_count
                                      - len(derived.get((k.kpi_label or "").strip().lower(), ())))
                               > max(1, 0.25 * k.cross_report_count)], threshold=0.1,
                              warn_only=True))
        rows.append(_rule("kpi_lineage", "validity", "KPI-07", "usage_rank is non-negative",
                          len(kpis), [k.kpi_id for k in kpis if k.usage_rank < 0]))

    # ---- catalog -------------------------------------------------------
    if columns:
        fqns = Counter(c.column_fqn for c in columns)
        rows.append(_rule("catalog", "uniqueness", "CAT-01", "column_fqn is unique",
                          len(columns), [f for f, n in fqns.items() if n > 1]))
        rows.append(_rule("catalog", "validity", "CAT-02",
                          "classification is Public, Internal, Confidential or Restricted",
                          len(columns), [c.column_fqn for c in columns
                                         if (c.classification or "").strip().lower()
                                         not in CLASSIFICATIONS]))
        rows.append(_rule("catalog", "validity", "CAT-03", "lifecycle_status is a known value",
                          len(columns), [c.column_fqn for c in columns
                                         if (c.lifecycle_status or "").strip().lower()
                                         not in LIFECYCLES]))
        rows.append(_rule("catalog", "validity", "CAT-04", "quality_score within 0-1 or 0-100",
                          len(columns), [c.column_fqn for c in columns
                                         if c.quality_score < 0 or c.quality_score > 100]))
        rows.append(_rule("catalog", "completeness", "CAT-05", "business_term present",
                          len(columns), [c.column_fqn for c in columns if not c.business_term],
                          threshold=0.30, warn_only=True))
        rows.append(_rule("catalog", "completeness", "CAT-06", "data_steward present",
                          len(columns), [c.column_fqn for c in columns if not c.data_steward],
                          threshold=0.15, warn_only=True))
        rows.append(_rule("catalog", "completeness", "CAT-07", "data_domain present",
                          len(columns), [c.column_fqn for c in columns if not c.data_domain],
                          threshold=0.05, warn_only=True))
        rows.append(_rule("catalog", "consistency", "CAT-08",
                          "a sunset table names a sunset date", len(columns),
                          [c.column_fqn for c in columns
                           if c.lifecycle_status == "sunset" and not c.sunset_date],
                          threshold=1.0, warn_only=True))
        rows.append(_rule("catalog", "completeness", "CAT-09", "certification_status present",
                          len(columns),
                          [c.column_fqn for c in columns if not c.certification_status],
                          threshold=0.5, warn_only=True))
        rows.append(_rule("catalog", "consistency", "CAT-10",
                          "a PII column is classified above Internal", len(columns),
                          [c.column_fqn for c in columns if c.pii_flag and
                           (c.classification or "").strip().lower() in ("", "public", "internal")],
                          threshold=0.0, warn_only=True))

    if glossary:
        terms = Counter((t.term or "").strip().lower() for t in glossary)
        rows.append(_rule("glossary", "uniqueness", "GLO-01", "term is unique", len(glossary),
                          [t for t, n in terms.items() if n > 1]))
        ids = Counter(t.term_id for t in glossary)
        rows.append(_rule("glossary", "uniqueness", "GLO-02", "term_id is unique", len(glossary),
                          [t for t, n in ids.items() if n > 1]))
        rows.append(_rule("glossary", "completeness", "GLO-03", "definition present",
                          len(glossary), [t.term_id for t in glossary if not t.definition],
                          threshold=0.1, warn_only=True))
        rows.append(_rule("glossary", "completeness", "GLO-04", "steward present", len(glossary),
                          [t.term_id for t in glossary if not t.steward], threshold=0.15,
                          warn_only=True))
        rows.append(_rule("glossary", "completeness", "GLO-05", "lifecycle status present",
                          len(glossary), [t.term_id for t in glossary if not t.status],
                          threshold=0.1, warn_only=True))
    return rows


def dq_summary(rows: list[dict]) -> dict:
    by_result = Counter(r["result"] for r in rows)
    by_input: dict[str, dict] = {}
    for row in rows:
        entry = by_input.setdefault(row["input"], {"rules": 0, "pass": 0, "warn": 0, "fail": 0})
        entry["rules"] += 1
        entry[row["result"]] += 1
    return {"rules": len(rows), "pass": by_result.get("pass", 0),
            "warn": by_result.get("warn", 0), "fail": by_result.get("fail", 0),
            "by_input": by_input,
            "failed_rules": [f"{r['input']}/{r['rule_id']}" for r in rows if r["result"] == "fail"]}


def save_dq_scorecard(connection: sqlite3.Connection, run_id: str, rows: list[dict]) -> None:
    ensure_schema(connection)
    connection.executemany(
        "INSERT OR REPLACE INTO DQ_SCORECARD VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(run_id, r["input"], r["dimension"], r["rule_id"], r["description"], r["rows_checked"],
          r["rows_failed"], r["failure_share"], json.dumps(r["sample_ids"]), r["threshold"],
          r["result"]) for r in rows])
    connection.commit()
