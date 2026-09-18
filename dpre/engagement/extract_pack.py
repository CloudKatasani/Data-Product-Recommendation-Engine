"""The extract-request pack: what to ask the client's BI and catalog teams for (R-55).

A one-day diagnostic depends on the right extracts arriving before the
workshop. ``GET /api/v1/schemas`` and ``dpre inspect`` describe the contracts
in ``dpre/ingest/schemas.py``, but they say what a field is used for, not how
a Cognos administrator or a Collibra admin obtains it. The workbook README
tab documents the synthetic pack, not the ask.

This module turns the contracts into a request pack: one tab per contract
with the required and optional columns, the aliases the mapper already
recognises (so the client can keep its own headings), an example value, and a
"how to obtain" note per source system, plus a summary tab the engagement
manager sends at T-5. It reads ``SCHEMAS`` live, so a field added to a
contract appears in the pack without a second edit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ingest.schemas import RECOMMENDED_INPUTS, REQUIRED_INPUTS, SCHEMAS, InputSchema

# Where each extract comes from and who owns it on the client side.
SOURCES: dict[str, dict[str, str]] = {
    "cognos_rationalization": {
        "owner": "Cognos administrator / BI platform team",
        "system": "Cognos Analytics audit database (COGIPF_* tables) plus the content store",
        "how": "Report inventory from the content store (search path, name, owner, package); "
               "run counts, distinct users and last run from COGIPF_RUNREPORT and "
               "COGIPF_USERLOGON over the last 12 months; disposition from the "
               "rationalization workbook the BI team maintains.",
        "format": "one row per report; CSV or XLSX",
        "cut": "usage counted up to the data cut date",
    },
    "cognos_kpi_lineage": {
        "owner": "Cognos administrator / Framework Manager modeller",
        "system": "Report specifications (XML) and the Framework Manager model",
        "how": "Parse each report specification for data items and their expressions; resolve "
               "query items through the FM package to the database, schema, table and column. "
               "A Framework Manager lineage export or the Cognos SDK gives the same rows.",
        "format": "one row per KPI per referenced column; CSV or XLSX",
        "cut": "specifications as deployed at the cut date",
    },
    "powerbi_inventory": {
        "owner": "Power BI service administrator",
        "system": "Power BI admin scanner API and the activity log",
        "how": "Workspaces, reports and datasets from the scanner API (GetScanResult); view "
               "counts, distinct users and last viewed from the activity log or the usage "
               "metrics dataset over 12 months; refresh schedule from the dataset settings.",
        "format": "one row per report; CSV or XLSX",
        "cut": "activity counted up to the data cut date",
    },
    "powerbi_measure_lineage": {
        "owner": "Power BI service administrator / semantic model owners",
        "system": "Semantic model definitions (TMDL or XMLA) and the scanner API with lineage",
        "how": "Measures and their DAX from the model definition; report-level measures from "
               "the report definition; source table and column from the M query source step "
               "where it resolves, else the model table as the terminus.",
        "format": "one row per measure per source column; CSV or XLSX",
        "cut": "models as published at the cut date",
    },
    "collibra_metadata": {
        "owner": "Collibra administrator / data governance office",
        "system": "Collibra Data Intelligence Platform",
        "how": "Export the Column asset type with its parent Table, Schema, Database and System, "
               "the Business Term relation, Data Domain, Owner and Steward responsibilities, "
               "classification and PII attributes; the Export API or a saved Data Catalog view.",
        "format": "one row per column; CSV or XLSX",
        "cut": "catalog as at the cut date",
    },
    "catalog_lineage": {
        "owner": "Collibra administrator / data governance office",
        "system": "Collibra technical lineage (or Alation lineage)",
        "how": "Export table-to-table and column-to-column lineage relations for the schemas "
               "in scope; include the transformation text where the harvester captured it.",
        "format": "one row per edge; CSV or XLSX",
        "cut": "lineage as harvested at the cut date",
    },
    "business_glossary": {
        "owner": "Data governance office / domain stewards",
        "system": "Collibra Business Glossary (or Alation articles)",
        "how": "Export Business Term assets with definition, domain, steward responsibility "
               "and status; include Draft terms, which count at half credit.",
        "format": "one row per term; CSV or XLSX",
        "cut": "glossary as at the cut date",
    },
    "alation_metadata": {
        "owner": "Alation catalog administrator",
        "system": "Alation Data Catalog",
        "how": "Export columns with data source, database, schema and table, custom fields for "
               "domain and sub-domain, steward, sensitivity label and PII flag through the "
               "Alation API or a bulk export.",
        "format": "one row per column; CSV or XLSX",
        "cut": "catalog as at the cut date",
    },
}

# Example values per field name, shared across contracts; anything absent gets
# a shape-based default so no cell is blank in the client's copy.
EXAMPLES: dict[str, str] = {
    "report_id": "RPT-000123", "report_name": "Arrears Aging Weekly", "folder_path":
    "/Finance/Collections", "fm_package": "Billing Package", "owner": "j.doe",
    "business_unit": "Credit & Collections", "run_count_90d": "42", "run_count_12m": "180",
    "distinct_users_12m": "9", "last_run_date": "2026-08-29", "disposition": "Retire",
    "schedule_flag": "true", "schedule_frequency": "Weekly", "redundancy_cluster_id": "RC-7",
    "complexity_score": "0.42", "decision_critical": "false", "kpi_id": "KPI-000456",
    "kpi_label": "Arrears 60+", "query_subject": "Account Balance", "query_item":
    "Arrears Amount", "calculation_expression": "total([Arrears Amount] for [Days Past Due] > 60)",
    "aggregation_type": "SUM", "source_system": "CIS", "database": "CISPROD", "schema":
    "CIS_CORE", "table": "FCT_ACCOUNT_BALANCE", "column": "arrears_amount",
    "filter_expression": "[Budget Billing Flag] = 'N'", "column_role": "operand",
    "usage_rank": "3", "cross_report_count": "11", "expression_language": "cognos",
    "measure_scope": "report", "workspace": "Finance Reporting", "semantic_model":
    "Collections Model", "view_count_12m": "310", "last_viewed_date": "2026-09-01",
    "view_count_90d": "77", "app_name": "Collections App", "storage_mode": "Import",
    "refresh_schedule": "Daily", "certified": "true", "measure_id": "M-0031",
    "measure_name": "Arrears 60+", "dax_expression":
    "CALCULATE(SUM(Balance[Arrears]), Balance[DaysPastDue] > 60)", "source_database":
    "CISPROD", "source_schema": "CIS_CORE", "source_table": "FCT_ACCOUNT_BALANCE",
    "source_column": "arrears_amount", "model_table": "Balance", "model_column": "Arrears",
    "system": "CIS", "data_domain": "Billing & Collections", "data_owner": "a.owner",
    "data_steward": "s.steward", "classification": "Confidential", "pii_flag": "false",
    "business_term": "Arrears", "definition": "Balance past its due date at the reporting date",
    "sub_domain": "Receivables", "data_type": "DECIMAL(18,2)", "nullable": "false",
    "primary_key": "false", "foreign_key": "DIM_ACCOUNT.account_key", "system_of_record":
    "true", "certification_status": "Certified", "quality_score": "0.93", "lifecycle_status":
    "active", "sunset_date": "", "successor_system": "", "row_count": "12000000",
    "src_table": "CIS_CORE.FCT_ACCOUNT_BALANCE", "tgt_table": "RPT.V_ARREARS", "src_system":
    "CIS", "src_database": "CISPROD", "src_schema": "CIS_CORE", "src_column": "arrears_amount",
    "tgt_system": "CIS", "tgt_database": "CISPROD", "tgt_schema": "RPT", "tgt_column":
    "arrears_amount", "level": "column", "transformation": "SUM by account",
    "term": "Arrears", "term_id": "BT-0001", "domain": "Billing & Collections", "steward":
    "s.steward", "status": "Approved", "ds_name": "CIS", "db_name": "CISPROD", "schema_name":
    "CIS_CORE", "table_name": "FCT_ACCOUNT_BALANCE", "column_name": "arrears_amount",
    "custom_field_domain": "Billing & Collections", "sensitivity_label": "Confidential", "pii":
    "false", "title": "Arrears", "description": "Balance past its due date",
    "custom_field_sub_domain": "Receivables", "type_name": "DECIMAL", "is_nullable": "false",
    "is_pk": "false", "fk_target": "DIM_ACCOUNT.account_key", "source_of_record": "true",
    "endorsement": "Certified", "data_health_score": "0.93", "lifecycle_stage": "active",
    "row_estimate": "12000000",
}

# Field-level notes where the obvious column is the wrong one.
FIELD_NOTES: dict[tuple[str, str], str] = {
    ("cognos_rationalization", "run_count_12m"): "Count executions, not views of saved output.",
    ("cognos_rationalization", "distinct_users_12m"): "Distinct user ids, not sessions.",
    ("cognos_rationalization", "last_run_date"): "Last successful execution, any user.",
    ("cognos_rationalization", "disposition"): "Keep / Merge / Retire / Migrate exactly; other "
                                               "words are treated as Keep.",
    ("cognos_kpi_lineage", "calculation_expression"): "The raw Cognos expression; do not "
                                                      "pre-simplify it, the parser needs the "
                                                      "thresholds and filters as written.",
    ("cognos_kpi_lineage", "column"): "The physical column the query item maps to, after "
                                      "alias expansion in the FM model.",
    ("powerbi_inventory", "view_count_12m"): "Report views from the activity log; normalised "
                                             "per tool before scoring, so scale differences "
                                             "with Cognos do not matter.",
    ("powerbi_measure_lineage", "source_table"): "From the M query source step; if the model "
                                                 "is Import and the step cannot be read, leave "
                                                 "blank and the row is flagged MODEL_TERMINUS.",
    ("collibra_metadata", "classification"): "Public / Internal / Confidential / Restricted; "
                                             "unknown values are treated as Internal.",
    ("collibra_metadata", "primary_key"): "Needed for grain inference; without it every table "
                                          "is 'unknown' grain and G3 cannot pass.",
    ("collibra_metadata", "lifecycle_status"): "'sunset' with no successor_system triggers "
                                               "gate G4 (Blocked).",
    ("business_glossary", "status"): "Approved terms count fully toward definition coverage; "
                                     "Draft terms at half.",
}


def _example(field_name: str, required: bool) -> str:
    if field_name in EXAMPLES:
        return EXAMPLES[field_name]
    return "(required)" if required else "(optional)"


def contract_rows(schema: InputSchema) -> list[list[Any]]:
    rows: list[list[Any]] = [["Field", "Required", "Used for", "Accepted headings (aliases)",
                              "Example", "Note"]]
    for spec in schema.fields:
        rows.append([
            spec.name, "yes" if spec.required else "no", spec.used_for,
            ", ".join(spec.aliases), _example(spec.name, spec.required),
            FIELD_NOTES.get((schema.key, spec.name), ""),
        ])
    return rows


def extract_request_pack(engagement: Any = None, cut_date: str = "") -> dict[str, list[list]]:
    """The pack as ``{tab: rows}``; ``engagement`` may be an EngagementRecord or a dict."""
    client = getattr(engagement, "client", "") if engagement is not None else ""
    if isinstance(engagement, dict):
        client = engagement.get("client", "")
        cut_date = cut_date or engagement.get("data_cut_date", "")
    else:
        cut_date = cut_date or (getattr(engagement, "data_cut_date", "") if engagement else "")
    readme = [
        ["Field", "Value"],
        ["Purpose", "The extracts the diagnostic needs, one tab per extract, with the "
                    "columns, the headings we already recognise, and how to obtain them."],
        ["Client", client or "(to be completed)"],
        ["Data cut date", cut_date or "(agree with the sponsor; every extract is cut at it)"],
        ["Required", ", ".join(SCHEMAS[k].label for k in REQUIRED_INPUTS)],
        ["Strongly recommended", ", ".join(SCHEMAS[k].label for k in RECOMMENDED_INPUTS
                                           if k not in REQUIRED_INPUTS)],
        ["Formats", ".csv, .tsv, .json, .jsonl or one .xlsx with one tab per extract"],
        ["Headings", "Keep your own column headings where they appear under 'Accepted "
                     "headings'; the mapper resolves them and shows what it guessed."],
        ["Check before sending", "python3 -m dpre inspect <file> lists what was detected, "
                                 "the mapping and any missing required field."],
        ["What we never need", "Customer records, transaction rows or report output. Every "
                               "extract is metadata and usage counts."],
    ]
    summary = [["Extract", "Tab name", "Owner on the client side", "Source system",
                "How to obtain", "Format", "Cut-date rule", "Required"]]
    for key, schema in SCHEMAS.items():
        source = SOURCES.get(key, {})
        summary.append([
            schema.label, schema.tab, source.get("owner", ""), source.get("system", ""),
            source.get("how", ""), source.get("format", ""), source.get("cut", ""),
            "required" if key in REQUIRED_INPUTS else
            ("recommended" if key in RECOMMENDED_INPUTS else "optional"),
        ])
    sheets: dict[str, list[list]] = {"README": readme, "Request_Summary": summary}
    for key, schema in SCHEMAS.items():
        sheets[schema.tab] = contract_rows(schema)
    return sheets


def write_extract_request_pack(path: str | Path, engagement: Any = None,
                               cut_date: str = "") -> Path:
    from ..util.xlsx import write_workbook
    return write_workbook(path, extract_request_pack(engagement, cut_date))


def request_letter(engagement: Any = None, cut_date: str = "") -> str:
    """A plain-text T-5 request an engagement manager can paste into an email."""
    client = getattr(engagement, "client", "") if engagement is not None else ""
    cut_date = cut_date or (getattr(engagement, "data_cut_date", "") if engagement else "")
    lines = [f"Extract request for the data product diagnostic{' - ' + client if client else ''}",
             "", f"Data cut date: {cut_date or 'to be agreed'}", "",
             "We need the following, each cut at the same date. Column headings can stay as "
             "they are in your tools; the attached workbook lists the ones we recognise.", ""]
    for key, schema in SCHEMAS.items():
        source = SOURCES.get(key, {})
        need = ("REQUIRED" if key in REQUIRED_INPUTS else
                "recommended" if key in RECOMMENDED_INPUTS else "optional")
        lines.append(f"- {schema.label} [{need}] - owner: {source.get('owner', '')}")
        lines.append(f"  {source.get('how', '')}")
    lines += ["", "Nothing we ask for contains customer records or report output; every extract "
                  "is metadata and usage counts."]
    return "\n".join(lines)
