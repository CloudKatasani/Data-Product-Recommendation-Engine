"""Renders a generated pack into the review workbook of specification 17.1.

The tabs double as the ingestion contract: if a real Cognos, Power BI or
catalog extract can be mapped onto these columns, the engine runs unchanged.
"""
from __future__ import annotations

from pathlib import Path

from ..models import ExtractBundle
from ..util.xlsx import write_workbook

TAB_ORDER = (
    "README",
    "Cognos_Rationalization",
    "Cognos_KPI_Lineage",
    "PowerBI_Inventory",
    "PowerBI_Measure_Lineage",
    "Collibra_Metadata",
    "Collibra_Lineage",
    "Alation_Metadata",
    "Business_Glossary",
    "Planted_Defects",
)

COGNOS_RATIONALIZATION_COLUMNS = (
    "report_id", "report_name", "folder_path", "fm_package", "owner", "business_unit",
    "run_count_90d", "run_count_12m", "distinct_users_12m", "last_run_date",
    "schedule_flag", "schedule_frequency", "disposition", "redundancy_cluster_id",
    "complexity_score", "synthetic", "generation_id",
)
COGNOS_KPI_COLUMNS = (
    "kpi_id", "kpi_label", "report_id", "fm_package", "query_subject", "query_item",
    "calculation_expression", "aggregation_type", "filter_expression", "column_role",
    "source_system", "database", "schema", "table", "column", "usage_rank",
    "cross_report_count", "synthetic", "generation_id",
)
POWERBI_INVENTORY_COLUMNS = (
    "report_id", "report_name", "workspace", "app_name", "semantic_model", "storage_mode",
    "owner", "business_unit", "view_count_90d", "view_count_12m", "distinct_users_12m",
    "last_viewed_date", "refresh_schedule", "disposition", "certified", "synthetic",
    "generation_id",
)
POWERBI_MEASURE_COLUMNS = (
    "measure_id", "measure_name", "measure_scope", "semantic_model", "workspace",
    "report_id", "model_table", "model_column", "dax_expression", "filter_expression",
    "aggregation_type", "column_role", "source_system", "source_database", "source_schema",
    "source_table", "source_column", "storage_mode", "synthetic", "generation_id",
)
COLLIBRA_METADATA_COLUMNS = (
    "system", "database", "schema", "table", "column", "full_name", "business_term",
    "definition", "data_domain", "sub_domain", "data_owner", "data_steward",
    "classification", "pii_flag", "data_type", "nullable", "primary_key", "foreign_key",
    "system_of_record", "certification_status", "quality_score", "lifecycle_status",
    "sunset_date", "successor_system", "row_count", "synthetic",
)
COLLIBRA_LINEAGE_COLUMNS = (
    "level", "src_system", "src_database", "src_schema", "src_table", "src_column",
    "tgt_system", "tgt_database", "tgt_schema", "tgt_table", "tgt_column", "transformation",
)
ALATION_METADATA_COLUMNS = (
    "ds_name", "db_name", "schema_name", "table_name", "column_name", "title",
    "description", "custom_field_domain", "custom_field_sub_domain", "steward",
    "data_owner", "sensitivity_label", "pii", "type_name", "is_nullable", "is_pk",
    "fk_target", "source_of_record", "endorsement", "data_health_score",
    "lifecycle_stage", "row_estimate",
)
GLOSSARY_COLUMNS = ("term_id", "term", "definition", "domain", "sub_domain", "steward", "status")
PLANTED_DEFECT_COLUMNS = (
    "defect_id", "defect_class", "defect_name", "how_planted", "expected_detection",
    "affected_objects", "affected_count", "detail",
)


def build_tabs(generator, bundle: ExtractBundle) -> dict[str, list[dict]]:
    """Build every tab as a list of dict rows, in the section 17.1 order."""
    manifest = bundle.manifest
    readme = _readme_rows(generator, manifest)

    cognos_reports = [
        {
            "report_id": r.report_id, "report_name": r.report_name, "folder_path": r.folder_path,
            "fm_package": r.semantic_container, "owner": r.owner, "business_unit": r.business_unit,
            "run_count_90d": r.run_count_90d, "run_count_12m": r.run_count_12m,
            "distinct_users_12m": r.distinct_users_12m,
            "last_run_date": r.last_run_date.isoformat() if r.last_run_date else "",
            "schedule_flag": "Y" if r.schedule_flag else "N",
            "schedule_frequency": r.schedule_frequency, "disposition": r.disposition,
            "redundancy_cluster_id": r.redundancy_cluster_id,
            "complexity_score": r.complexity_score, "synthetic": True,
            "generation_id": r.generation_id,
        }
        for r in bundle.reports if r.tool == "cognos"
    ]

    kpi_rows = []
    for row in generator._kpi_rows:
        fqn = row["column_fqn"]
        system, database, schema, table, column = fqn.split(".", 4)
        kpi_rows.append({
            "kpi_id": row["kpi_id"], "kpi_label": row["kpi_label"], "report_id": row["report_id"],
            "fm_package": row["fm_package"], "query_subject": row["query_subject"],
            "query_item": row["query_item"],
            "calculation_expression": row["calculation_expression"],
            "aggregation_type": row["aggregation_type"],
            "filter_expression": row["filter_expression"],
            "column_role": row["column_role"], "source_system": system, "database": database,
            "schema": schema, "table": table, "column": row.get("alias_column") or column,
            "usage_rank": row["usage_rank"], "cross_report_count": row["cross_report_count"],
            "synthetic": True, "generation_id": generator.generation_id,
        })

    pbi_reports = [
        {**r, "synthetic": True, "generation_id": generator.generation_id}
        for r in generator._pbi_reports
    ]
    pbi_measures = [
        {
            "measure_id": row["measure_id"], "measure_name": row["measure_name"],
            "measure_scope": row["measure_scope"], "semantic_model": row["semantic_model"],
            "workspace": row["workspace"], "report_id": row["report_id"],
            "model_table": row["model_table"], "model_column": row["model_column"],
            "dax_expression": row["dax_expression"],
            "filter_expression": row["filter_expression"],
            "aggregation_type": row["aggregation_type"], "column_role": row["column_role"],
            "source_system": row["source_system"], "source_database": row["source_database"],
            "source_schema": row["source_schema"], "source_table": row["source_table"],
            "source_column": row.get("alias_column") or row["source_column"],
            "storage_mode": row["storage_mode"], "synthetic": True,
            "generation_id": generator.generation_id,
        }
        for row in generator._pbi_measure_rows
    ]

    collibra = [
        {
            "system": c.system, "database": c.database, "schema": c.schema, "table": c.table,
            "column": c.column, "full_name": c.column_fqn, "business_term": c.business_term,
            "definition": c.definition, "data_domain": c.data_domain, "sub_domain": c.sub_domain,
            "data_owner": c.data_owner, "data_steward": c.data_steward,
            "classification": c.classification, "pii_flag": "Y" if c.pii_flag else "N",
            "data_type": c.data_type, "nullable": "Y" if c.nullable else "N",
            "primary_key": "Y" if c.primary_key else "N", "foreign_key": c.foreign_key,
            "system_of_record": "Y" if c.system_of_record else "N",
            "certification_status": c.certification_status, "quality_score": c.quality_score,
            "lifecycle_status": c.lifecycle_status, "sunset_date": c.sunset_date,
            "successor_system": c.successor_system, "row_count": c.row_count, "synthetic": True,
        }
        for c in bundle.columns
    ]

    alation = [
        {
            "ds_name": c.system, "db_name": c.database, "schema_name": c.schema,
            "table_name": c.table, "column_name": c.column,
            "title": c.business_term, "description": c.definition,
            "custom_field_domain": c.data_domain, "custom_field_sub_domain": c.sub_domain,
            "steward": c.data_steward, "data_owner": c.data_owner,
            "sensitivity_label": c.classification, "pii": "true" if c.pii_flag else "false",
            "type_name": c.data_type, "is_nullable": "true" if c.nullable else "false",
            "is_pk": "true" if c.primary_key else "false", "fk_target": c.foreign_key,
            "source_of_record": "true" if c.system_of_record else "false",
            "endorsement": c.certification_status, "data_health_score": c.quality_score,
            "lifecycle_stage": c.lifecycle_status, "row_estimate": c.row_count,
        }
        for c in bundle.columns
    ]

    lineage = [
        {
            "level": e.level, "src_system": e.src_system, "src_database": e.src_database,
            "src_schema": e.src_schema, "src_table": e.src_table, "src_column": e.src_column,
            "tgt_system": e.tgt_system, "tgt_database": e.tgt_database,
            "tgt_schema": e.tgt_schema, "tgt_table": e.tgt_table, "tgt_column": e.tgt_column,
            "transformation": e.transformation,
        }
        for e in bundle.lineage
    ]

    glossary = [
        {
            "term_id": t.term_id, "term": t.term, "definition": t.definition,
            "domain": t.domain, "sub_domain": t.sub_domain, "steward": t.steward,
            "status": t.status,
        }
        for t in bundle.glossary
    ]

    return {
        "README": readme,
        "Cognos_Rationalization": cognos_reports,
        "Cognos_KPI_Lineage": kpi_rows,
        "PowerBI_Inventory": pbi_reports,
        "PowerBI_Measure_Lineage": pbi_measures,
        "Collibra_Metadata": collibra,
        "Collibra_Lineage": lineage,
        "Alation_Metadata": alation,
        "Business_Glossary": glossary,
        "Planted_Defects": bundle.planted_defects,
    }


def _readme_rows(generator, manifest: dict) -> list[dict]:
    pack = generator.pack
    rows = [
        {"item": "Pack", "value": f"{pack.label} synthetic extract pack"},
        {"item": "Specification", "value": "Data Product Recommendation Engine, section 17"},
        {"item": "Industry key", "value": pack.key},
        {"item": "Random seed", "value": generator.seed},
        {"item": "Generation id", "value": generator.generation_id},
        {"item": "As-of date", "value": manifest["as_of_date"]},
        {"item": "Synthetic", "value": "TRUE - every row carries synthetic = TRUE"},
        {"item": "Warning", "value": "The Ingestor refuses to mix synthetic and real extracts in one run."},
        {"item": "", "value": ""},
        {"item": "Volumes", "value": ""},
    ]
    for key in ("cognos_reports", "cognos_kpi_rows", "powerbi_reports", "powerbi_measure_rows",
                "catalog_tables", "catalog_columns", "catalog_lineage_edges", "glossary_terms",
                "planted_defects"):
        rows.append({"item": key.replace("_", " ").capitalize(), "value": manifest[key]})
    rows.append({"item": "", "value": ""})
    rows.append({"item": "Defect rates", "value": ""})
    for key, value in manifest["parameters"].items():
        rows.append({"item": key.replace("_", " ").capitalize(), "value": value})
    rows.append({"item": "", "value": ""})
    rows.append({"item": "Planted defect classes", "value": ""})
    for defect_class, count in sorted(manifest["defect_summary"].items()):
        rows.append({"item": defect_class, "value": f"{count} instance(s)"})
    rows.append({"item": "", "value": ""})
    rows.append({"item": "Source systems", "value": ", ".join(s.name for s in pack.systems)})
    rows.append({"item": "Domains", "value": ", ".join(pack.domains)})
    rows.append({"item": "Conformed backbone", "value": " -> ".join(pack.backbone)})
    rows.append({"item": "Business units", "value": ", ".join(pack.business_units)})
    return rows


_COLUMNS = {
    "Cognos_Rationalization": COGNOS_RATIONALIZATION_COLUMNS,
    "Cognos_KPI_Lineage": COGNOS_KPI_COLUMNS,
    "PowerBI_Inventory": POWERBI_INVENTORY_COLUMNS,
    "PowerBI_Measure_Lineage": POWERBI_MEASURE_COLUMNS,
    "Collibra_Metadata": COLLIBRA_METADATA_COLUMNS,
    "Collibra_Lineage": COLLIBRA_LINEAGE_COLUMNS,
    "Alation_Metadata": ALATION_METADATA_COLUMNS,
    "Business_Glossary": GLOSSARY_COLUMNS,
    "Planted_Defects": PLANTED_DEFECT_COLUMNS,
    "README": ("item", "value"),
}


def tab_columns(tab: str, rows: list[dict]) -> list[str]:
    declared = _COLUMNS.get(tab)
    if declared:
        return list(declared)
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns


def write_pack_workbook(pack, path: str | Path) -> Path:
    """Write one industry workbook with the section 17.1 tabs."""
    sheets: dict[str, list[list]] = {}
    for tab in TAB_ORDER:
        rows = pack.tabs.get(tab, [])
        columns = tab_columns(tab, rows)
        sheets[tab] = [list(columns)] + [[row.get(c) for c in columns] for row in rows]
    return write_workbook(path, sheets)
