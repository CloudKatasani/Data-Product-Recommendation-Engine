"""Input contracts for the four supported extracts (specification section 3).

Required fields gate ingestion. Optional fields improve scores but their absence
is recorded as a feasibility gap, not a failure.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    name: str
    required: bool
    used_for: str
    aliases: tuple[str, ...] = ()

    def candidates(self) -> tuple[str, ...]:
        return (self.name,) + self.aliases


@dataclass(frozen=True)
class InputSchema:
    key: str
    label: str
    tool: str
    tab: str
    fields: tuple[FieldSpec, ...]
    description: str = ""

    @property
    def required_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.required]

    @property
    def optional_fields(self) -> list[str]:
        return [f.name for f in self.fields if not f.required]

    def field(self, name: str) -> FieldSpec | None:
        for spec in self.fields:
            if spec.name == name:
                return spec
        return None

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "tool": self.tool, "tab": self.tab,
            "description": self.description,
            "fields": [
                {"name": f.name, "required": f.required, "used_for": f.used_for,
                 "aliases": list(f.aliases)}
                for f in self.fields
            ],
        }


COGNOS_RATIONALIZATION = InputSchema(
    key="cognos_rationalization", label="Cognos rationalization report", tool="cognos",
    tab="Cognos_Rationalization",
    description="Report inventory with usage, ownership and rationalization disposition.",
    fields=(
        FieldSpec("report_id", True, "Report node identity", ("id", "report_key", "search_path")),
        FieldSpec("report_name", True, "Report node identity", ("name", "title")),
        FieldSpec("folder_path", True, "Report node identity", ("folder", "path", "location")),
        FieldSpec("fm_package", True, "Report to Package edge",
                  ("package", "framework_manager_package", "semantic_model", "model")),
        FieldSpec("owner", True, "Consumer attribution, steward candidates", ("report_owner", "contact")),
        FieldSpec("business_unit", True, "Consumer attribution", ("bu", "department", "org_unit")),
        FieldSpec("run_count_90d", True, "Demand weight", ("runs_90d", "executions_90d")),
        FieldSpec("run_count_12m", True, "Demand weight", ("runs_12m", "executions_12m", "run_count")),
        FieldSpec("distinct_users_12m", True, "Consumer breadth", ("users", "distinct_users", "unique_users")),
        FieldSpec("last_run_date", True, "Recency decay", ("last_run", "last_executed", "last_used")),
        FieldSpec("disposition", True, "Retirement impact", ("rationalization_disposition", "action")),
        FieldSpec("schedule_flag", False, "Cadence in the decision-register seed", ("scheduled", "is_scheduled")),
        FieldSpec("schedule_frequency", False, "Cadence", ("frequency", "schedule")),
        FieldSpec("redundancy_cluster_id", False, "Prior evidence of duplication", ("cluster_id",)),
        FieldSpec("complexity_score", False, "Feasibility", ("complexity",)),
        FieldSpec("decision_critical", False, "Reviewer override that floors usage weight",
                  ("critical", "regulatory")),
    ),
)

COGNOS_KPI_LINEAGE = InputSchema(
    key="cognos_kpi_lineage", label="Cognos KPI lineage report", tool="cognos",
    tab="Cognos_KPI_Lineage",
    description="KPI to report to package to source column, with the calculation expression.",
    fields=(
        FieldSpec("kpi_id", True, "KPI node identity", ("id", "measure_id", "kpi_key")),
        FieldSpec("kpi_label", True, "KPI identity and name similarity", ("kpi_name", "label", "measure_name")),
        FieldSpec("report_id", True, "KPI to Report edge", ("report", "report_key")),
        FieldSpec("fm_package", True, "KPI to Package edge", ("package", "semantic_model")),
        FieldSpec("query_subject", True, "Package to query-subject edge", ("subject", "model_table")),
        FieldSpec("query_item", True, "Query-item edge", ("item", "model_column")),
        FieldSpec("calculation_expression", True, "Parsed to an AST; canonicalization fingerprint",
                  ("expression", "calculation", "dax_expression", "formula")),
        FieldSpec("aggregation_type", True, "Fingerprint component", ("aggregation", "agg")),
        FieldSpec("source_system", True, "KPI to Column edge", ("system", "ds_name")),
        FieldSpec("database", True, "KPI to Column edge", ("db", "database_name", "source_database")),
        FieldSpec("schema", True, "KPI to Column edge", ("schema_name", "source_schema")),
        FieldSpec("table", True, "KPI to Column edge", ("table_name", "source_table")),
        FieldSpec("column", True, "KPI to Column edge", ("column_name", "source_column")),
        FieldSpec("filter_expression", False, "Variant detection", ("filter", "slicer", "where_clause")),
        FieldSpec("column_role", False, "Operand or filter role on the edge", ("role",)),
        FieldSpec("usage_rank", False, "Cross-check on the derived reuse count", ("rank",)),
        FieldSpec("cross_report_count", False, "Cross-check on the derived reuse count", ("report_count",)),
        FieldSpec("expression_language", False, "Parser selection (cognos or dax)", ("language", "dialect")),
        FieldSpec("measure_scope", False, "Power BI model or report scope", ("scope",)),
    ),
)

POWERBI_INVENTORY = InputSchema(
    key="powerbi_inventory", label="Power BI inventory", tool="powerbi",
    tab="PowerBI_Inventory",
    description="Reports, workspaces, semantic models and activity-log usage.",
    fields=(
        FieldSpec("report_id", True, "Report node identity", ("id", "report_key")),
        FieldSpec("report_name", True, "Report node identity", ("name", "title")),
        FieldSpec("workspace", True, "Report container", ("workspace_name", "folder_path")),
        FieldSpec("semantic_model", True, "Semantic container", ("dataset", "dataset_name", "fm_package")),
        FieldSpec("owner", True, "Consumer attribution", ("report_owner", "contact")),
        FieldSpec("business_unit", True, "Consumer attribution", ("bu", "department")),
        FieldSpec("view_count_12m", True, "Demand weight", ("views_12m", "run_count_12m", "views")),
        FieldSpec("distinct_users_12m", True, "Consumer breadth", ("users", "distinct_users")),
        FieldSpec("last_viewed_date", True, "Recency decay", ("last_viewed", "last_run_date")),
        FieldSpec("view_count_90d", False, "Demand weight", ("views_90d", "run_count_90d")),
        FieldSpec("app_name", False, "Report container", ("app",)),
        FieldSpec("storage_mode", False, "Lineage depth (import models break at the M query)", ("mode",)),
        FieldSpec("refresh_schedule", False, "Cadence", ("schedule", "schedule_frequency")),
        FieldSpec("disposition", False, "Retirement impact", ("action",)),
        FieldSpec("certified", False, "Trust signal", ("endorsement",)),
    ),
)

POWERBI_MEASURE_LINEAGE = InputSchema(
    key="powerbi_measure_lineage", label="Power BI measure lineage", tool="powerbi",
    tab="PowerBI_Measure_Lineage",
    description="DAX measures (model and report scope) to model table to source column.",
    fields=(
        FieldSpec("measure_id", True, "KPI node identity", ("id", "kpi_id")),
        FieldSpec("measure_name", True, "KPI identity", ("name", "kpi_label")),
        # Not required: section 16.2 distinguishes a model-scoped (shared)
        # measure, which belongs to the semantic model and is used by many
        # reports, from a report-scoped one. A real Power BI export carries no
        # report for the former, and demanding one rejected the whole sheet.
        FieldSpec("report_id", False, "KPI to Report edge, for a report-scoped measure",
                  ("report", "defined_in_report", "report_name")),
        FieldSpec("semantic_model", True, "Semantic container", ("dataset", "fm_package")),
        FieldSpec("model_id", False, "Semantic container identity", ("dataset_id",)),
        FieldSpec("dax_expression", True, "Parsed to an AST", ("expression", "calculation_expression")),
        FieldSpec("source_system", True, "KPI to Column edge", ("system", "ds_name")),
        FieldSpec("source_database", True, "KPI to Column edge", ("database", "db_name")),
        FieldSpec("source_schema", True, "KPI to Column edge", ("schema", "schema_name")),
        FieldSpec("source_table", True, "KPI to Column edge", ("table", "table_name")),
        FieldSpec("source_column", True, "KPI to Column edge", ("column", "column_name")),
        FieldSpec("measure_scope", False, "Shared vs local measure (section 16.2)", ("scope",)),
        FieldSpec("workspace", False, "Report container", ("workspace_name",)),
        FieldSpec("model_table", False, "Query item", ("table",)),
        FieldSpec("model_column", False, "Query item", ("column",)),
        FieldSpec("aggregation_type", False, "Fingerprint component", ("aggregation",)),
        FieldSpec("filter_expression", False, "Variant detection", ("filter",)),
        FieldSpec("column_role", False, "Operand or filter role", ("role",)),
        FieldSpec("storage_mode", False, "Lineage depth", ("mode",)),
    ),
)

COLLIBRA_METADATA = InputSchema(
    key="collibra_metadata", label="Collibra metadata", tool="collibra",
    tab="Collibra_Metadata",
    description="Physical column inventory with business, governance and sensitivity metadata.",
    fields=(
        FieldSpec("system", True, "Physical node identity", ("source_system", "ds_name")),
        FieldSpec("database", True, "Physical node identity", ("db", "db_name", "database_name")),
        FieldSpec("schema", True, "Physical node identity", ("schema_name",)),
        FieldSpec("table", True, "Physical node identity", ("table_name",)),
        FieldSpec("column", True, "Physical node identity", ("column_name",)),
        FieldSpec("data_domain", True, "Domain cohesion; candidate domain assignment",
                  ("domain", "custom_field_domain")),
        FieldSpec("data_owner", True, "Owner candidate", ("owner",)),
        FieldSpec("data_steward", True, "Steward candidate", ("steward",)),
        FieldSpec("classification", True, "Risk score", ("sensitivity", "sensitivity_label")),
        FieldSpec("pii_flag", True, "Risk score", ("pii", "is_pii")),
        FieldSpec("business_term", False, "Attribute definitions; semantic similarity", ("term", "title")),
        FieldSpec("definition", False, "Attribute definitions", ("description", "business_definition")),
        FieldSpec("sub_domain", False, "Domain cohesion", ("custom_field_sub_domain",)),
        FieldSpec("data_type", False, "Attribute register seed", ("type", "type_name")),
        FieldSpec("nullable", False, "Attribute register seed", ("is_nullable",)),
        FieldSpec("primary_key", False, "Grain inference", ("is_pk", "pk_flag")),
        FieldSpec("foreign_key", False, "Grain inference", ("fk_target", "fk")),
        FieldSpec("system_of_record", False, "Feasibility; prefer SoR sources", ("sor", "source_of_record")),
        FieldSpec("certification_status", False, "Trust signal", ("certification", "endorsement")),
        FieldSpec("quality_score", False, "Trust signal", ("dq_score", "data_health_score")),
        FieldSpec("lifecycle_status", False, "Source health", ("lifecycle", "lifecycle_stage", "status")),
        FieldSpec("sunset_date", False, "Gate G4", ("retirement_date",)),
        FieldSpec("successor_system", False, "Gate G4", ("successor",)),
        FieldSpec("row_count", False, "Reference-data archetype", ("row_estimate", "rows")),
    ),
)

CATALOG_LINEAGE = InputSchema(
    key="catalog_lineage", label="Catalog lineage", tool="collibra",
    tab="Collibra_Lineage",
    description="Table-to-table and column-to-column edges that extend lineage upstream (ER-3).",
    fields=(
        FieldSpec("src_table", True, "Upstream node", ("source_table", "from_table")),
        FieldSpec("tgt_table", True, "Downstream node", ("target_table", "to_table")),
        FieldSpec("src_system", False, "Upstream node", ("source_system", "from_system")),
        FieldSpec("src_database", False, "Upstream node", ("source_database",)),
        FieldSpec("src_schema", False, "Upstream node", ("source_schema",)),
        FieldSpec("src_column", False, "Upstream node", ("source_column", "from_column")),
        FieldSpec("tgt_system", False, "Downstream node", ("target_system", "to_system")),
        FieldSpec("tgt_database", False, "Downstream node", ("target_database",)),
        FieldSpec("tgt_schema", False, "Downstream node", ("target_schema",)),
        FieldSpec("tgt_column", False, "Downstream node", ("target_column", "to_column")),
        FieldSpec("level", False, "Edge level (table or column)", ("lineage_level", "grain")),
        FieldSpec("transformation", False, "Edge annotation", ("transformation_logic", "rule")),
    ),
)

BUSINESS_GLOSSARY = InputSchema(
    key="business_glossary", label="Business glossary", tool="collibra",
    tab="Business_Glossary",
    description="Terms, definitions, domains and stewards.",
    fields=(
        FieldSpec("term", True, "Business term node", ("term_name", "name", "title")),
        FieldSpec("definition", False, "Attribute definitions", ("description",)),
        FieldSpec("term_id", False, "Term identity", ("id",)),
        FieldSpec("domain", False, "Domain assignment", ("data_domain",)),
        FieldSpec("sub_domain", False, "Domain assignment", ()),
        FieldSpec("steward", False, "Steward candidate", ("data_steward",)),
        FieldSpec("status", False, "Trust signal", ("term_status",)),
    ),
)

ALATION_METADATA = InputSchema(
    key="alation_metadata", label="Alation metadata", tool="alation",
    tab="Alation_Metadata",
    description="The same physical estate in Alation's export shape (section 16.3).",
    fields=(
        FieldSpec("ds_name", True, "Physical node identity", ("system", "data_source")),
        # Alation models a data source and a schema; many deployments carry no
        # separate database level at all, so requiring one rejected exports
        # that were complete. The adapter falls back to the data source name.
        FieldSpec("db_name", False, "Physical node identity",
                  ("database", "database_name", "catalog")),
        FieldSpec("schema_name", True, "Physical node identity", ("schema",)),
        FieldSpec("table_name", True, "Physical node identity", ("table",)),
        FieldSpec("column_name", True, "Physical node identity", ("column",)),
        FieldSpec("custom_field_domain", True, "Domain assignment", ("domain", "data_domain")),
        FieldSpec("steward", True, "Steward candidate", ("data_steward",)),
        # Alation exposes these as custom fields, and their names vary by
        # deployment; a missing sensitivity is scored as Internal, not dropped.
        FieldSpec("sensitivity_label", False, "Risk score",
                  ("classification", "sensitivity", "custom_field_sensitivity")),
        FieldSpec("pii", False, "Risk score", ("pii_flag", "custom_field_pii")),
        FieldSpec("data_owner", False, "Owner candidate", ("owner",)),
        FieldSpec("title", False, "Business term", ("business_term",)),
        FieldSpec("description", False, "Definition", ("definition",)),
        FieldSpec("custom_field_sub_domain", False, "Domain assignment", ("sub_domain",)),
        FieldSpec("type_name", False, "Attribute register seed", ("data_type",)),
        FieldSpec("is_nullable", False, "Attribute register seed", ("nullable",)),
        FieldSpec("is_pk", False, "Grain inference", ("primary_key",)),
        FieldSpec("fk_target", False, "Grain inference", ("foreign_key",)),
        FieldSpec("source_of_record", False, "Feasibility", ("system_of_record",)),
        FieldSpec("endorsement", False, "Trust signal", ("certification_status",)),
        FieldSpec("data_health_score", False, "Trust signal", ("quality_score",)),
        FieldSpec("lifecycle_stage", False, "Source health", ("lifecycle_status",)),
        FieldSpec("row_estimate", False, "Reference-data archetype", ("row_count",)),
    ),
)

SCHEMAS = {
    s.key: s for s in (
        COGNOS_RATIONALIZATION, COGNOS_KPI_LINEAGE, POWERBI_INVENTORY,
        POWERBI_MEASURE_LINEAGE, COLLIBRA_METADATA, CATALOG_LINEAGE,
        BUSINESS_GLOSSARY, ALATION_METADATA,
    )
}

# What a manual-mode run needs before it can proceed, and what merely improves it.
REQUIRED_INPUTS = ("cognos_kpi_lineage",)
CATALOG_INPUTS = ("collibra_metadata", "alation_metadata")
RECOMMENDED_INPUTS = ("cognos_rationalization",) + CATALOG_INPUTS


#: Tabs that document a workbook rather than feed the engine. Guessing a schema
#: for one produces a 6%-confidence suggestion that is noise in the inspector
#: and a trap for anyone who binds it by hand.
NON_INPUT_TABS = frozenset({
    "readme", "read_me", "planted_defects", "defects", "notes", "cover",
    "contents", "index", "instructions", "changelog", "manifest", "glossary_notes",
})


def _normalize(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def suggest_schema(columns: list[str], sheet: str = "") -> tuple[str, float]:
    """Guess which input a file or tab is, from its headers and its name.

    Header overlap alone cannot separate Collibra from Alation: both describe
    columns, and a client whose Collibra export uses generic names like
    ``schema_name`` scores higher against Alation than against the catalog it
    actually came from. The tab name settles it, because a sheet called
    ``Collibra_Metadata`` is not ambiguous to a human and should not be to us.
    """
    name = _normalize(sheet)
    if name in NON_INPUT_TABS:
        return "", 0.0

    lowered = {_normalize(c) for c in columns if c}
    by_tab = {_normalize(s.tab): key for key, s in SCHEMAS.items() if s.tab}

    best, best_score = "", 0.0
    named = by_tab.get(name) or (name if name in SCHEMAS else "")
    for key, schema in SCHEMAS.items():
        hits = 0
        for spec in schema.fields:
            if any(c in lowered for c in spec.candidates()):
                hits += 2 if spec.required else 1
        total = sum(2 if f.required else 1 for f in schema.fields)
        score = hits / total if total else 0.0
        if key == named:
            # The name is decisive, but only for a sheet whose headers are at
            # least plausible: a tab named after a schema and shaped like
            # nothing is still a mismatch worth reporting honestly.
            score = max(score, 0.5) + 0.5 if score >= 0.25 else score
        if score > best_score:
            best, best_score = key, score
    return best, round(min(1.0, best_score), 3)


def map_columns(schema_key: str, columns: list[str]) -> dict[str, str]:
    """Auto-map a file's headers onto a schema's field names."""
    schema = SCHEMAS[schema_key]
    lowered = {str(c).strip().lower().replace(" ", "_"): c for c in columns if c}
    mapping: dict[str, str] = {}
    for spec in schema.fields:
        for candidate in spec.candidates():
            if candidate in lowered:
                mapping[spec.name] = lowered[candidate]
                break
    return mapping
