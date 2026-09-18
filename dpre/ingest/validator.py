"""Ingest validation (specification section 3, "Validation on load").

Row counts are reconciled to the source extract, required fields gate the run,
synthetic and real extracts may never be mixed, and every row is stamped with an
as-of date so recommendations carry one.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from ..config import QUALITY_GATES
from ..models import ExtractBundle


@dataclass
class ValidationIssue:
    severity: str          # error | warning | info
    code: str
    message: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code,
                "message": self.message, "detail": self.detail}


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    reconciliation: list[dict] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: str, code: str, message: str, detail: str = "") -> None:
        self.issues.append(ValidationIssue(severity, code, message, detail))

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "counts": self.counts,
            "reconciliation": self.reconciliation,
            "issues": [i.to_dict() for i in self.issues],
        }


def validate(bundle: ExtractBundle, expected_counts: dict[str, int] | None = None) -> ValidationReport:
    report = ValidationReport()
    report.counts = {
        "reports": len(bundle.reports),
        "kpi_rows": len(bundle.kpis),
        "catalog_columns": len(bundle.columns),
        "catalog_lineage": len(bundle.lineage),
        "glossary_terms": len(bundle.glossary),
    }

    if not bundle.kpis:
        report.add("error", "NO_KPI_LINEAGE",
                   "No KPI lineage rows were ingested",
                   "The KPI lineage extract is the one input the engine cannot run without.")
    if not bundle.reports:
        report.add("warning", "NO_REPORT_INVENTORY",
                   "No report inventory was ingested",
                   "Demand and retirement impact will be scored from lineage alone and will "
                   "be weak; supply the rationalization report to fix this.")
    if not bundle.columns:
        report.add("warning", "NO_CATALOG",
                   "No catalog metadata was ingested",
                   "Every lineage row will quarantine, so candidates will be capped at "
                   "Exploratory by gate G2.")

    # Synthetic and real extracts may never be mixed in one run (section 17.3).
    synthetic_flags = {r.synthetic for r in bundle.reports} | {k.synthetic for k in bundle.kpis}
    if len(synthetic_flags) > 1:
        report.add("error", "MIXED_SYNTHETIC",
                   "Synthetic and real extracts cannot be mixed in one run",
                   "Every row in a run must carry the same synthetic flag.")

    # Referential integrity between the two Cognos extracts.
    report_ids = {r.report_id for r in bundle.reports}
    if report_ids:
        orphans = sorted({k.report_id for k in bundle.kpis if k.report_id not in report_ids})
        if orphans:
            severity = "error" if len(orphans) > len(report_ids) else "warning"
            report.add(severity, "ORPHAN_KPI_REPORTS",
                       f"{len(orphans)} KPI rows reference a report that is not in the inventory",
                       ", ".join(orphans[:10]))

    missing_expression = [k.kpi_id for k in bundle.kpis if not k.calculation_expression]
    if missing_expression:
        report.add("warning", "MISSING_EXPRESSION",
                   f"{len(missing_expression)} KPI rows carry no calculation expression",
                   "These are treated as opaque and carry a feasibility penalty.")

    missing_reference = [k.kpi_id for k in bundle.kpis if not (k.table and k.column)]
    if missing_reference:
        report.add("warning", "MISSING_COLUMN_REFERENCE",
                   f"{len(missing_reference)} KPI rows carry no table or column reference",
                   "These quarantine with reason code MISSING_REFERENCE.")

    # Usage statistics availability (section 15.3 assumption).
    if bundle.reports:
        with_usage = [r for r in bundle.reports if r.run_count_12m > 0]
        if len(with_usage) < 0.5 * len(bundle.reports):
            report.add("warning", "SPARSE_USAGE",
                       "More than half the reports carry no 12-month run count",
                       "Demand scores will be dominated by the reports that do.")
        undated = [r for r in bundle.reports if r.last_run_date is None]
        if undated:
            report.add("warning", "MISSING_LAST_RUN",
                       f"{len(undated)} reports carry no last-run date",
                       "Recency decay defaults to the oldest bucket for these, which lowers "
                       "their demand score rather than raising it.")

    # Row-count reconciliation against the extract manifest (tolerance 0.5%).
    tolerance = QUALITY_GATES["ingest_reconciliation_tolerance"]
    for key, expected in (expected_counts or {}).items():
        actual = report.counts.get(key)
        if actual is None or not expected:
            continue
        drift = abs(actual - expected) / float(expected)
        row = {"input": key, "expected": expected, "actual": actual, "drift": round(drift, 5),
               "within_tolerance": drift <= tolerance}
        report.reconciliation.append(row)
        if not row["within_tolerance"]:
            report.add("error", "RECONCILIATION_FAILED",
                       f"{key}: ingested {actual} rows against a manifest of {expected}",
                       f"Drift {drift:.2%} exceeds the {tolerance:.2%} tolerance.")

    return report


def stamp_as_of(bundle: ExtractBundle, as_of: _dt.date | None = None) -> ExtractBundle:
    """Stamp the extract date on the bundle so recommendations carry an as-of date."""
    bundle.as_of_date = as_of or bundle.as_of_date or _dt.date.today()
    return bundle
