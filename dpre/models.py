"""Dataclasses for the canonical knowledge graph and the RECO output model.

Field names mirror the table definitions in specification section 11.1 so the
in-memory model, the SQLite tables and the JSON API all agree.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Normalized input records (the ingestion contract, section 17.4)
# --------------------------------------------------------------------------


@dataclass
class ReportRecord:
    report_id: str
    report_name: str
    folder_path: str = ""
    tool: str = "cognos"                # cognos | powerbi
    semantic_container: str = ""        # FM package | Power BI semantic model
    workspace: str = ""
    owner: str = ""
    business_unit: str = ""
    run_count_90d: int = 0
    run_count_12m: int = 0
    distinct_users_12m: int = 0
    last_run_date: _dt.date | None = None
    schedule_flag: bool = False
    schedule_frequency: str = ""
    disposition: str = "Keep"           # Keep | Merge | Retire | Migrate
    redundancy_cluster_id: str = ""
    complexity_score: float = 0.0
    decision_critical: bool = False     # reviewer override, section 15.1
    synthetic: bool = False
    generation_id: str = ""
    source_file: str = ""


@dataclass
class KpiRecord:
    kpi_id: str
    kpi_label: str
    report_id: str
    semantic_container: str = ""
    query_subject: str = ""
    query_item: str = ""
    calculation_expression: str = ""
    expression_language: str = "cognos"  # cognos | dax
    aggregation_type: str = ""
    filter_expression: str = ""
    source_system: str = ""
    database: str = ""
    schema: str = ""
    table: str = ""
    column: str = ""
    usage_rank: int = 0
    cross_report_count: int = 0
    measure_scope: str = "report"        # model | report (Power BI, section 16.2)
    tool: str = "cognos"
    synthetic: bool = False
    generation_id: str = ""
    source_file: str = ""

    @property
    def raw_reference(self) -> str:
        parts = [self.source_system, self.database, self.schema, self.table, self.column]
        return ".".join(p for p in parts if p)


@dataclass
class CatalogColumnRecord:
    system: str
    database: str
    schema: str
    table: str
    column: str
    business_term: str = ""
    definition: str = ""
    data_domain: str = ""
    sub_domain: str = ""
    data_owner: str = ""
    data_steward: str = ""
    classification: str = "Internal"     # Public | Internal | Confidential | Restricted
    pii_flag: bool = False
    data_type: str = ""
    nullable: bool = True
    primary_key: bool = False
    foreign_key: str = ""
    system_of_record: bool = False
    certification_status: str = ""
    quality_score: float = 0.0
    lifecycle_status: str = "active"     # active | sunset
    sunset_date: str = ""
    successor_system: str = ""
    row_count: int = 0
    catalog: str = "collibra"            # collibra | alation
    synthetic: bool = False
    source_file: str = ""

    @property
    def column_fqn(self) -> str:
        return ".".join([self.system, self.database, self.schema, self.table, self.column])

    @property
    def table_fqn(self) -> str:
        return ".".join([self.system, self.database, self.schema, self.table])


@dataclass
class CatalogLineageRecord:
    src_system: str = ""
    src_database: str = ""
    src_schema: str = ""
    src_table: str = ""
    src_column: str = ""
    tgt_system: str = ""
    tgt_database: str = ""
    tgt_schema: str = ""
    tgt_table: str = ""
    tgt_column: str = ""
    level: str = "column"                # column | table
    transformation: str = ""

    @property
    def src_fqn(self) -> str:
        parts = [self.src_system, self.src_database, self.src_schema, self.src_table]
        if self.src_column:
            parts.append(self.src_column)
        return ".".join(p for p in parts if p)

    @property
    def tgt_fqn(self) -> str:
        parts = [self.tgt_system, self.tgt_database, self.tgt_schema, self.tgt_table]
        if self.tgt_column:
            parts.append(self.tgt_column)
        return ".".join(p for p in parts if p)


@dataclass
class GlossaryTermRecord:
    term_id: str
    term: str
    definition: str = ""
    domain: str = ""
    sub_domain: str = ""
    steward: str = ""
    status: str = ""


@dataclass
class ExtractBundle:
    """Everything one run ingests, already adapted to the canonical shapes."""

    reports: list[ReportRecord] = field(default_factory=list)
    kpis: list[KpiRecord] = field(default_factory=list)
    columns: list[CatalogColumnRecord] = field(default_factory=list)
    lineage: list[CatalogLineageRecord] = field(default_factory=list)
    glossary: list[GlossaryTermRecord] = field(default_factory=list)
    planted_defects: list[dict] = field(default_factory=list)
    mode: str = "manual"                 # manual | automated
    catalog: str = "collibra"
    industry: str = "generic"
    as_of_date: _dt.date = field(default_factory=_dt.date.today)
    synthetic: bool = False
    generation_id: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)
    source_files: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------
# GRAPH schema
# --------------------------------------------------------------------------


@dataclass
class ColumnNode:
    column_fqn: str
    table_fqn: str
    system: str
    column_name: str
    data_type: str = ""
    nullable: bool = True
    pk_flag: bool = False
    fk_ref: str = ""
    sensitivity: str = "Internal"
    pii_flag: bool = False
    business_term: str = ""
    definition: str = ""
    steward_id: str = ""
    owner_id: str = ""
    domain: str = ""
    sub_domain: str = ""
    certification_status: str = ""
    quality_score: float = 0.0


@dataclass
class TableNode:
    table_fqn: str
    system: str
    table_name: str
    sor_flag: bool = False
    lifecycle_status: str = "active"
    sunset_date: str = ""
    successor_system: str = ""
    inferred_grain: str = "unknown"
    grain_source: str = "unknown"
    domain: str = ""
    sub_domain: str = ""
    owner_id: str = ""
    steward_id: str = ""
    row_count: int = 0
    column_count: int = 0
    measure_count: int = 0


@dataclass
class KpiNode:
    kpi_id: str
    report_id: str
    label: str
    tool: str
    aggregation: str
    expression: str
    expression_ast: dict[str, Any]
    fingerprint: str
    filter_fp: str
    parse_status: str                    # PARSED | PARSE_FAIL
    filter_expression: str = ""
    semantic_container: str = ""
    measure_scope: str = "report"
    grain: str = "unknown"
    operand_columns: list[str] = field(default_factory=list)
    filter_columns: list[str] = field(default_factory=list)
    time_modifier: str = ""
    parse_note: str = ""


@dataclass
class EdgeKpiColumn:
    kpi_id: str
    column_fqn: str
    role: str                            # operand | filter
    er_rule: str
    confidence: float
    raw_reference: str = ""


@dataclass
class QuarantineRow:
    kpi_id: str
    raw_reference: str
    reason_code: str
    detail: str = ""
    role: str = "operand"


@dataclass
class KnowledgeGraph:
    reports: dict[str, ReportRecord] = field(default_factory=dict)
    kpis: dict[str, KpiNode] = field(default_factory=dict)
    columns: dict[str, ColumnNode] = field(default_factory=dict)
    tables: dict[str, TableNode] = field(default_factory=dict)
    edges_kpi_column: list[EdgeKpiColumn] = field(default_factory=list)
    # A Power BI measure defined once in a semantic model and used by twenty
    # reports is one KPI node with twenty report edges (section 16.2).
    kpi_reports: dict[str, list[str]] = field(default_factory=dict)
    quarantine: list[QuarantineRow] = field(default_factory=list)
    glossary: dict[str, GlossaryTermRecord] = field(default_factory=dict)
    systems: dict[str, dict] = field(default_factory=dict)
    as_of_date: _dt.date = field(default_factory=_dt.date.today)
    stats: dict[str, Any] = field(default_factory=dict)

    def reports_for_kpi(self, kpi_id: str) -> list[str]:
        return self.kpi_reports.get(kpi_id, [])

    def kpi_columns(self, kpi_id: str, role: str | None = None) -> list[EdgeKpiColumn]:
        return [
            e for e in self.edges_kpi_column
            if e.kpi_id == kpi_id and (role is None or e.role == role)
        ]


# --------------------------------------------------------------------------
# RECO schema
# --------------------------------------------------------------------------


@dataclass
class CanonicalMetric:
    metric_id: str
    canonical_name: str
    definition: str
    fingerprint: str
    grain: str
    aggregation: str
    steward_id: str = ""
    steward_source: str = "unassigned"
    name_status: str = "AI_DRAFT"         # AI_DRAFT | ACCEPTED
    domain: str = ""
    sub_domain: str = ""
    operand_columns: list[str] = field(default_factory=list)
    filter_columns: list[str] = field(default_factory=list)
    source_tables: list[str] = field(default_factory=list)
    kpi_ids: list[str] = field(default_factory=list)
    report_ids: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    variant_count: int = 0
    opaque: bool = False
    cousins: list[str] = field(default_factory=list)
    time_modifiers: list[str] = field(default_factory=list)
    usage_weight: float = 0.0
    consumer_breadth: int = 0
    report_count: int = 0


@dataclass
class MetricVariant:
    kpi_id: str
    metric_id: str
    tier: str                            # IDENTICAL | VARIANT | COUSIN | OPAQUE
    filter_fp: str
    filter_expression: str = ""
    variant_label: str = ""


@dataclass
class MetricConflict:
    conflict_id: str
    label: str
    metric_id_a: str
    metric_id_b: str
    usage_weight_a: float
    usage_weight_b: float
    difference_summary: str
    pattern: str = ""                    # section 5.4 pattern
    resolution_status: str = "OPEN"
    reports_a: list[str] = field(default_factory=list)
    reports_b: list[str] = field(default_factory=list)
    expression_a: str = ""
    expression_b: str = ""
    steward_id: str = ""
    similarity: float = 0.0
    semantic_model_decision: str = ""


@dataclass
class CandidateConsumer:
    business_unit: str
    users: int
    report_count: int
    scheduled_share: float
    top_reports: list[str] = field(default_factory=list)
    cadence: str = ""


@dataclass
class CandidateReport:
    report_id: str
    report_name: str
    coverage: float
    disposition: str
    users: int
    last_run: str
    business_unit: str = ""
    owner: str = ""


@dataclass
class CandidateSource:
    table_fqn: str
    system: str
    share_of_metrics: float
    sor_flag: bool
    lifecycle_status: str
    domain: str = ""
    successor_system: str = ""


@dataclass
class CandidateAttribute:
    column_fqn: str
    name: str
    role: str
    data_type: str
    definition: str
    business_term: str
    sensitivity: str
    pii_flag: bool
    steward_id: str
    nullable: bool = True
    confidence: float = 0.0


@dataclass
class ScoreFeature:
    dimension: str
    feature: str
    value: float
    normalized: float
    weight: float
    contribution: float
    detail: str = ""


@dataclass
class GateResult:
    gate: str
    name: str
    passed: bool
    detail: str
    effect: str = ""


@dataclass
class CandidateScore:
    candidate_id: str
    weight_version: str
    demand: float
    consolidation: float
    feasibility: float
    risk: float
    composite: float
    features: list[ScoreFeature] = field(default_factory=list)
    gates: list[GateResult] = field(default_factory=list)


@dataclass
class EvidenceRow:
    candidate_id: str
    feature: str
    evidence_type: str                   # report | kpi | column | term | table | conflict
    evidence_id: str
    detail: str = ""


@dataclass
class CritiqueFinding:
    candidate_id: str
    criterion: str
    finding: str
    severity: str                        # blocker | major | minor | info


@dataclass
class DecisionDraft:
    business_unit: str
    persona: str
    cadence: str
    questions: list[str]
    inferred_decision: str
    latency_tolerance: str = "TO BE CONFIRMED"
    consequence: str = "TO BE CONFIRMED"
    status: str = "AI_DRAFT"


@dataclass
class Candidate:
    candidate_id: str
    run_id: str
    proposed_name: str
    purpose: str
    archetype: str
    tier: str
    grain: str
    domain: str
    sub_domain: str = ""
    owner_candidate: str = ""
    steward_candidate: str = ""
    status: str = "Exploratory"
    archetype_confidence: float = 0.0
    archetype_runner_up: str = ""
    tier_confidence: float = 0.0
    name_status: str = "AI_DRAFT"
    parent_candidate_id: str = ""
    child_candidate_ids: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    metric_ids: list[str] = field(default_factory=list)
    consumers: list[CandidateConsumer] = field(default_factory=list)
    reports: list[CandidateReport] = field(default_factory=list)
    sources: list[CandidateSource] = field(default_factory=list)
    attributes: list[CandidateAttribute] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    decisions_drafted: list[DecisionDraft] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    score: CandidateScore | None = None
    critique: list[CritiqueFinding] = field(default_factory=list)
    evidence: list[EvidenceRow] = field(default_factory=list)
    narrative: dict[str, Any] = field(default_factory=dict)
    origin: str = "community"            # community | composite | entity_master | grain_child
    resolution: float = 1.0
    tools: list[str] = field(default_factory=list)
    as_of_date: str = ""


@dataclass
class ReviewDecision:
    candidate_id: str
    decision: str                        # Accept | Reject | Merge | Split | Defer
    reason_code: str
    reviewer: str
    decided_at: str
    field_overridden: str = ""
    new_value: str = ""
    target_candidate_id: str = ""
    note: str = ""
    run_id: str = ""


@dataclass
class RunManifest:
    run_id: str
    mode: str
    industry: str
    catalog: str
    as_of_date: str
    started_at: str
    finished_at: str = ""
    weight_version: str = ""
    parser_version: str = ""
    generation_id: str = ""
    synthetic: bool = False
    extract_ids: list[str] = field(default_factory=list)
    quality_gates: list[dict] = field(default_factory=list)
    published: bool = False
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    agent_log: list[dict] = field(default_factory=list)
