"""Catalog adapters: Collibra and Alation (specification sections 3.3 and 16.3).

Whichever catalog is present becomes the system of record; nothing downstream
knows which one it was.
"""
from __future__ import annotations

from ...models import CatalogColumnRecord, CatalogLineageRecord, GlossaryTermRecord
from ...util import tabular
from .base import FieldReader

SENSITIVITY_SYNONYMS = {
    "public": "Public", "open": "Public", "unclassified": "Public",
    "internal": "Internal", "internal use only": "Internal", "company confidential": "Confidential",
    "confidential": "Confidential", "sensitive": "Confidential", "pii": "Confidential",
    "restricted": "Restricted", "highly confidential": "Restricted", "secret": "Restricted",
}


def normalize_sensitivity(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return "Internal"
    return SENSITIVITY_SYNONYMS.get(text, value.strip().title())


def normalize_lifecycle(value: str) -> str:
    text = (value or "").strip().lower()
    if text in ("sunset", "deprecated", "retiring", "end of life", "eol", "decommissioned"):
        return "sunset"
    return "active"


def adapt_columns(records: list[dict], catalog: str = "collibra",
                  mapping: dict[str, str] | None = None,
                  source_file: str = "") -> tuple[list[CatalogColumnRecord], FieldReader]:
    schema_key = "alation_metadata" if catalog == "alation" else "collibra_metadata"
    columns = list(records[0].keys()) if records else []
    reader = FieldReader(schema_key, columns, mapping)
    alation = catalog == "alation"

    def field(record, collibra_name, alation_name, default=""):
        return reader.text(record, alation_name if alation else collibra_name, default)

    out: list[CatalogColumnRecord] = []
    for record in records:
        system = field(record, "system", "ds_name")
        table = field(record, "table", "table_name")
        column = field(record, "column", "column_name")
        if not table or not column:
            continue
        out.append(CatalogColumnRecord(
            system=system,
            database=field(record, "database", "db_name"),
            schema=field(record, "schema", "schema_name"),
            table=table,
            column=column,
            business_term=field(record, "business_term", "title"),
            definition=field(record, "definition", "description"),
            data_domain=field(record, "data_domain", "custom_field_domain"),
            sub_domain=field(record, "sub_domain", "custom_field_sub_domain"),
            data_owner=field(record, "data_owner", "data_owner"),
            data_steward=field(record, "data_steward", "steward"),
            classification=normalize_sensitivity(
                field(record, "classification", "sensitivity_label", "Internal")),
            pii_flag=reader.boolean(record, "pii" if alation else "pii_flag"),
            data_type=field(record, "data_type", "type_name"),
            nullable=reader.boolean(record, "is_nullable" if alation else "nullable", True),
            primary_key=reader.boolean(record, "is_pk" if alation else "primary_key"),
            foreign_key=field(record, "foreign_key", "fk_target"),
            system_of_record=reader.boolean(
                record, "source_of_record" if alation else "system_of_record"),
            certification_status=field(record, "certification_status", "endorsement"),
            quality_score=reader.number(record, "data_health_score" if alation else "quality_score"),
            lifecycle_status=normalize_lifecycle(
                field(record, "lifecycle_status", "lifecycle_stage", "active")),
            sunset_date=reader.text(record, "sunset_date"),
            successor_system=reader.text(record, "successor_system"),
            row_count=reader.integer(record, "row_estimate" if alation else "row_count"),
            catalog=catalog,
            synthetic=tabular.as_bool(record.get("synthetic"), False),
            source_file=source_file,
        ))
    return out, reader


def adapt_lineage(records: list[dict], mapping: dict[str, str] | None = None,
                  source_file: str = "") -> tuple[list[CatalogLineageRecord], FieldReader]:
    columns = list(records[0].keys()) if records else []
    reader = FieldReader("catalog_lineage", columns, mapping)
    out: list[CatalogLineageRecord] = []
    for record in records:
        src_table = reader.text(record, "src_table")
        tgt_table = reader.text(record, "tgt_table")
        if not src_table or not tgt_table:
            continue
        src_column = reader.text(record, "src_column")
        tgt_column = reader.text(record, "tgt_column")
        level = reader.text(record, "level", "column" if src_column and tgt_column else "table")
        out.append(CatalogLineageRecord(
            src_system=reader.text(record, "src_system"),
            src_database=reader.text(record, "src_database"),
            src_schema=reader.text(record, "src_schema"),
            src_table=src_table,
            src_column=src_column,
            tgt_system=reader.text(record, "tgt_system"),
            tgt_database=reader.text(record, "tgt_database"),
            tgt_schema=reader.text(record, "tgt_schema"),
            tgt_table=tgt_table,
            tgt_column=tgt_column,
            level="column" if level.lower().startswith("col") else "table",
            transformation=reader.text(record, "transformation"),
        ))
    return out, reader


def adapt_glossary(records: list[dict], mapping: dict[str, str] | None = None,
                   source_file: str = "") -> tuple[list[GlossaryTermRecord], FieldReader]:
    columns = list(records[0].keys()) if records else []
    reader = FieldReader("business_glossary", columns, mapping)
    out: list[GlossaryTermRecord] = []
    for i, record in enumerate(records, start=1):
        term = reader.text(record, "term")
        if not term:
            continue
        out.append(GlossaryTermRecord(
            term_id=reader.text(record, "term_id", f"BT-{i:04d}"),
            term=term,
            definition=reader.text(record, "definition"),
            domain=reader.text(record, "domain"),
            sub_domain=reader.text(record, "sub_domain"),
            steward=reader.text(record, "steward"),
            status=reader.text(record, "status"),
        ))
    return out, reader
