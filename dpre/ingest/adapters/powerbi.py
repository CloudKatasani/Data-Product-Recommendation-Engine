"""Power BI adapter (specification section 16).

Two rules matter here. A measure defined once in a semantic model and used by
twenty reports is one KPI node with twenty report edges, not twenty KPIs. And
usage is normalized to a within-tool percentile before it meets Cognos runs, so
a tool with heavier logging cannot dominate the ranking.
"""
from __future__ import annotations

from ...models import KpiRecord, ReportRecord
from ...util import tabular
from .base import FieldReader, normalize_disposition


def adapt_reports(records: list[dict], mapping: dict[str, str] | None = None,
                  source_file: str = "") -> tuple[list[ReportRecord], FieldReader]:
    columns = list(records[0].keys()) if records else []
    reader = FieldReader("powerbi_inventory", columns, mapping)
    out: list[ReportRecord] = []
    for record in records:
        report_id = reader.text(record, "report_id")
        if not report_id:
            continue
        workspace = reader.text(record, "workspace")
        schedule = reader.text(record, "refresh_schedule")
        out.append(ReportRecord(
            report_id=report_id,
            report_name=reader.text(record, "report_name", report_id),
            folder_path=f"/{workspace}" if workspace else "",
            tool="powerbi",
            semantic_container=reader.text(record, "semantic_model"),
            workspace=workspace,
            owner=reader.text(record, "owner"),
            business_unit=reader.text(record, "business_unit", "Unassigned"),
            run_count_90d=reader.integer(record, "view_count_90d"),
            run_count_12m=reader.integer(record, "view_count_12m"),
            distinct_users_12m=reader.integer(record, "distinct_users_12m"),
            last_run_date=reader.date(record, "last_viewed_date"),
            schedule_flag=bool(schedule),
            schedule_frequency=schedule,
            disposition=normalize_disposition(reader.text(record, "disposition", "Keep")),
            complexity_score=0.0,
            synthetic=tabular.as_bool(record.get("synthetic"), False),
            generation_id=tabular.as_str(record.get("generation_id")),
            source_file=source_file,
        ))
    return out, reader


def adapt_measures(records: list[dict], mapping: dict[str, str] | None = None,
                   source_file: str = "") -> tuple[list[KpiRecord], FieldReader]:
    columns = list(records[0].keys()) if records else []
    reader = FieldReader("powerbi_measure_lineage", columns, mapping)
    out: list[KpiRecord] = []
    for record in records:
        measure_id = reader.text(record, "measure_id")
        if not measure_id:
            continue
        # A model-scoped measure belongs to the semantic model and is used by
        # every report on it (section 16.2), so it carries no report of its
        # own. Dropping those rows silently discarded the shared measures -
        # exactly the ones most worth consolidating. ingest_manual attaches
        # them to their model's reports once the inventory is also loaded.
        report_id = reader.text(record, "report_id")
        scope = reader.text(record, "measure_scope", "").lower()
        if not scope:
            scope = "report" if report_id else "model"
        kpi = KpiRecord(
            kpi_id=measure_id,
            kpi_label=reader.text(record, "measure_name", measure_id),
            report_id=report_id,
            semantic_container=reader.text(record, "semantic_model"),
            query_subject=reader.text(record, "model_table"),
            query_item=reader.text(record, "model_column"),
            calculation_expression=reader.text(record, "dax_expression"),
            expression_language="dax",
            aggregation_type=reader.text(record, "aggregation_type"),
            filter_expression=reader.text(record, "filter_expression"),
            source_system=reader.text(record, "source_system"),
            database=reader.text(record, "source_database"),
            schema=reader.text(record, "source_schema"),
            table=reader.text(record, "source_table"),
            column=reader.text(record, "source_column"),
            measure_scope="model" if scope.startswith("model") else "report",
            tool="powerbi",
            synthetic=tabular.as_bool(record.get("synthetic"), False),
            generation_id=tabular.as_str(record.get("generation_id")),
            source_file=source_file,
        )
        role = reader.text(record, "column_role", "")
        if role:
            setattr(kpi, "_column_role", role.lower())
        storage = reader.text(record, "storage_mode", "")
        if storage:
            setattr(kpi, "_storage_mode", storage)
        out.append(kpi)
    return out, reader


def normalize_usage_across_tools(reports: list[ReportRecord]) -> dict[str, float]:
    """Percentile-rank run counts within each tool (section 16.2, usage parity).

    Returns ``{report_id: percentile}``; the demand feature multiplies raw runs
    by the ratio between the report's within-tool percentile and the estate
    median, so a tool with heavier logging does not dominate the ranking.
    """
    by_tool: dict[str, list[ReportRecord]] = {}
    for report in reports:
        by_tool.setdefault(report.tool or "cognos", []).append(report)
    percentiles: dict[str, float] = {}
    for tool, group in by_tool.items():
        ordered = sorted(group, key=lambda r: r.run_count_12m)
        n = len(ordered)
        for i, report in enumerate(ordered):
            percentiles[report.report_id] = (i + 1) / n if n else 0.0
    return percentiles
