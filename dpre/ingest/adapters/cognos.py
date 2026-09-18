"""Cognos adapter: rationalization report and KPI lineage report."""
from __future__ import annotations

from ...models import KpiRecord, ReportRecord
from ...util import tabular
from .base import FieldReader, normalize_disposition


def adapt_reports(records: list[dict], mapping: dict[str, str] | None = None,
                  source_file: str = "") -> tuple[list[ReportRecord], FieldReader]:
    columns = list(records[0].keys()) if records else []
    reader = FieldReader("cognos_rationalization", columns, mapping)
    out: list[ReportRecord] = []
    for record in records:
        report_id = reader.text(record, "report_id")
        if not report_id:
            continue
        out.append(ReportRecord(
            report_id=report_id,
            report_name=reader.text(record, "report_name", report_id),
            folder_path=reader.text(record, "folder_path"),
            tool="cognos",
            semantic_container=reader.text(record, "fm_package"),
            owner=reader.text(record, "owner"),
            business_unit=reader.text(record, "business_unit", "Unassigned"),
            run_count_90d=reader.integer(record, "run_count_90d"),
            run_count_12m=reader.integer(record, "run_count_12m"),
            distinct_users_12m=reader.integer(record, "distinct_users_12m"),
            last_run_date=reader.date(record, "last_run_date"),
            schedule_flag=reader.boolean(record, "schedule_flag"),
            schedule_frequency=reader.text(record, "schedule_frequency"),
            disposition=normalize_disposition(reader.text(record, "disposition", "Keep")),
            redundancy_cluster_id=reader.text(record, "redundancy_cluster_id"),
            complexity_score=reader.number(record, "complexity_score"),
            decision_critical=reader.boolean(record, "decision_critical"),
            synthetic=tabular.as_bool(record.get("synthetic"), False),
            generation_id=tabular.as_str(record.get("generation_id")),
            source_file=source_file,
        ))
    return out, reader


def adapt_kpis(records: list[dict], mapping: dict[str, str] | None = None,
               source_file: str = "") -> tuple[list[KpiRecord], FieldReader]:
    columns = list(records[0].keys()) if records else []
    reader = FieldReader("cognos_kpi_lineage", columns, mapping)
    out: list[KpiRecord] = []
    for record in records:
        kpi_id = reader.text(record, "kpi_id")
        report_id = reader.text(record, "report_id")
        if not kpi_id or not report_id:
            continue
        language = reader.text(record, "expression_language", "cognos").lower()
        out.append(KpiRecord(
            kpi_id=kpi_id,
            kpi_label=reader.text(record, "kpi_label", kpi_id),
            report_id=report_id,
            semantic_container=reader.text(record, "fm_package"),
            query_subject=reader.text(record, "query_subject"),
            query_item=reader.text(record, "query_item"),
            calculation_expression=reader.text(record, "calculation_expression"),
            expression_language="dax" if language.startswith("dax") else "cognos",
            aggregation_type=reader.text(record, "aggregation_type"),
            filter_expression=reader.text(record, "filter_expression"),
            source_system=reader.text(record, "source_system"),
            database=reader.text(record, "database"),
            schema=reader.text(record, "schema"),
            table=reader.text(record, "table"),
            column=reader.text(record, "column"),
            usage_rank=reader.integer(record, "usage_rank"),
            cross_report_count=reader.integer(record, "cross_report_count"),
            measure_scope=reader.text(record, "measure_scope", "report"),
            tool="cognos",
            synthetic=tabular.as_bool(record.get("synthetic"), False),
            generation_id=tabular.as_str(record.get("generation_id")),
            source_file=source_file,
        ))
        role = reader.text(record, "column_role", "")
        if role:
            out[-1].measure_scope = out[-1].measure_scope or "report"
            setattr(out[-1], "_column_role", role.lower())
    return out, reader
