"""Entity resolution, grain inference and the knowledge graph."""
from __future__ import annotations

from collections import Counter

from dpre.config import ER_CONFIDENCE, QUALITY_GATES
from dpre.graph.grain import finest_grain, grain_ambiguity, infer_table_grain, is_entity_key
from dpre.graph.resolver import build_index, resolve_reference
from dpre.models import CatalogColumnRecord, ColumnNode, KpiRecord, TableNode


def _column(table, column, **kwargs):
    return CatalogColumnRecord(system="CIS", database="CISDB", schema="CORE", table=table,
                               column=column, **kwargs)


def _kpi(table, column, **kwargs):
    return KpiRecord(kpi_id="K1", kpi_label=kwargs.pop("label", "Arrears"), report_id="R1",
                     source_system="CIS", database="CISDB", schema="CORE", table=table,
                     column=column, **kwargs)


def test_er1_exact_match():
    index = build_index([_column("FCT_BAL", "arrears_amount")], [])
    resolution = resolve_reference(_kpi("FCT_BAL", "arrears_amount"), "arrears_amount", index)
    assert resolution.er_rule == "ER-1" and resolution.confidence == ER_CONFIDENCE["ER-1"]


def test_er2_alias_expansion():
    index = build_index([_column("FCT_BAL", "customer_account_number")], [])
    resolution = resolve_reference(_kpi("FCT_BAL", "cust_acct_no"), "cust_acct_no", index)
    assert resolution.er_rule == "ER-2"
    assert resolution.column_fqn.endswith("customer_account_number")


def _reporting_view_estate():
    """A reporting view over a system-of-record fact table, with column lineage."""
    from dpre.models import CatalogLineageRecord
    columns = [
        _column("FCT_BAL", "arrears_amount", system_of_record=True),
        CatalogColumnRecord(system="CIS", database="CISDB", schema="RPT", table="V_BAL",
                            column="arrears_amount", system_of_record=False),
    ]
    lineage = [CatalogLineageRecord(
        src_system="CIS", src_database="CISDB", src_schema="CORE", src_table="FCT_BAL",
        src_column="arrears_amount", tgt_system="CIS", tgt_database="CISDB",
        tgt_schema="RPT", tgt_table="V_BAL", tgt_column="arrears_amount", level="column")]
    return columns, lineage


def test_er3_extends_the_reporting_layer_up_to_the_system_of_record():
    """The lineage report stops at the reporting database; the catalog extends it."""
    from dpre.graph.resolver import extend_upstream
    columns, lineage = _reporting_view_estate()
    index = build_index(columns, lineage)
    kpi = _kpi("V_BAL", "arrears_amount")
    kpi.schema = "RPT"
    direct = resolve_reference(kpi, "arrears_amount", index)
    assert direct.er_rule == "ER-1"
    extended = extend_upstream(direct, index)
    assert extended.er_rule == "ER-3"
    assert extended.column_fqn == "CIS.CISDB.CORE.FCT_BAL.arrears_amount"


def test_er3_inherits_a_column_the_view_does_not_declare():
    from dpre.models import CatalogLineageRecord
    columns = [
        _column("FCT_BAL", "arrears_amount", system_of_record=True),
        CatalogColumnRecord(system="CIS", database="CISDB", schema="RPT", table="V_BAL",
                            column="unrelated_column"),
    ]
    lineage = [CatalogLineageRecord(
        src_system="CIS", src_database="CISDB", src_schema="CORE", src_table="FCT_BAL",
        src_column="arrears_amount", tgt_system="CIS", tgt_database="CISDB",
        tgt_schema="RPT", tgt_table="V_BAL", tgt_column="arrears_amount", level="column")]
    index = build_index(columns, lineage)
    kpi = _kpi("V_BAL", "arrears_amount")
    kpi.schema = "RPT"
    resolution = resolve_reference(kpi, "arrears_amount", index)
    assert resolution.er_rule == "ER-3"


def test_upstream_extension_stops_at_a_staging_table():
    """A staging table upstream of a fact is a load step, not the source of record."""
    from dpre.graph.resolver import extend_upstream
    from dpre.models import CatalogLineageRecord
    columns = [
        _column("FCT_BAL", "arrears_amount", system_of_record=False),
        CatalogColumnRecord(system="CIS", database="CISDB", schema="STG", table="STG_BAL",
                            column="src_arrears_amount", system_of_record=False),
    ]
    lineage = [CatalogLineageRecord(
        src_system="CIS", src_database="CISDB", src_schema="STG", src_table="STG_BAL",
        src_column="src_arrears_amount", tgt_system="CIS", tgt_database="CISDB",
        tgt_schema="CORE", tgt_table="FCT_BAL", tgt_column="arrears_amount", level="column")]
    index = build_index(columns, lineage)
    resolution = resolve_reference(_kpi("FCT_BAL", "arrears_amount"), "arrears_amount", index)
    assert extend_upstream(resolution, index).column_fqn.endswith("FCT_BAL.arrears_amount")


def test_er4_name_similarity():
    index = build_index([_column("FCT_BAL", "arrears_amount")], [])
    resolution = resolve_reference(_kpi("FCT_BAL", "arrears_amount_usd"),
                                   "arrears_amount_usd", index)
    assert resolution.er_rule == "ER-4"
    assert resolution.confidence == ER_CONFIDENCE["ER-4"]


def test_er5_business_term_match():
    index = build_index([_column("FCT_BAL", "arrears_amount",
                                 business_term="Total Overdue Position")], [])
    resolution = resolve_reference(_kpi("FCT_BAL", "Total Overdue Position"),
                                   "Total Overdue Position", index,
                                   query_item="Total Overdue Position")
    assert resolution.er_rule == "ER-5"


def test_unresolvable_reference_is_quarantined_with_a_reason():
    index = build_index([_column("FCT_BAL", "arrears_amount")], [])
    outcome = resolve_reference(_kpi("FCT_ABSENT", "whatever"), "whatever", index)
    assert isinstance(outcome, tuple)
    assert outcome[1] == "NO_CATALOG_TABLE"


def test_missing_reference_is_quarantined():
    index = build_index([_column("FCT_BAL", "arrears_amount")], [])
    outcome = resolve_reference(_kpi("", ""), "", index)
    assert outcome[1] == "MISSING_REFERENCE"


def test_is_entity_key_distinguishes_keys_from_codes():
    assert is_entity_key("account_key") and is_entity_key("customer_id")
    assert not is_entity_key("status_code") and not is_entity_key("arrears_amount")


def test_fact_grain_comes_from_the_backbone_foreign_keys():
    backbone = ["Customer", "Account", "Premise"]
    table = TableNode(table_fqn="x", system="CIS", table_name="FCT_DISCONNECT_NOTICE",
                      measure_count=3)
    columns = [
        ColumnNode("x.disconnect_notice_row_key", "x", "CIS", "disconnect_notice_row_key",
                   data_type="NUMBER(18,0)", pk_flag=True),
        ColumnNode("x.customer_key", "x", "CIS", "customer_key", data_type="NUMBER(18,0)"),
        ColumnNode("x.account_key", "x", "CIS", "account_key", data_type="NUMBER(18,0)"),
        ColumnNode("x.premise_key", "x", "CIS", "premise_key", data_type="NUMBER(18,0)"),
        ColumnNode("x.notice_count", "x", "CIS", "notice_count", data_type="NUMBER(18,0)"),
        ColumnNode("x.field_visit_cost", "x", "CIS", "field_visit_cost", data_type="NUMBER(18,2)"),
    ]
    grain, source = infer_table_grain(table, columns, backbone)
    assert grain == "Premise" and "foreign key" in source


def test_dimension_grain_comes_from_its_primary_key():
    table = TableNode(table_fqn="x", system="CIS", table_name="DIM_PAYMENT_ARRANGEMENT")
    columns = [ColumnNode("x.payment_arrangement_key", "x", "CIS", "payment_arrangement_key",
                          data_type="NUMBER(18,0)", pk_flag=True)]
    grain, _ = infer_table_grain(table, columns, ["Customer", "Account"])
    assert grain == "Payment Arrangement"


def test_event_grain_needs_a_timestamp():
    table = TableNode(table_fqn="x", system="CIS", table_name="FCT_OUTAGE_EVENT",
                      measure_count=2)
    columns = [ColumnNode("x.event_start_ts", "x", "CIS", "event_start_ts",
                          data_type="TIMESTAMP_NTZ"),
               ColumnNode("x.outage_minutes", "x", "CIS", "outage_minutes",
                          data_type="NUMBER(12,2)")]
    grain, source = infer_table_grain(table, columns, ["Customer"])
    assert grain == "Event" and "timestamp" in source


def test_multi_table_kpi_takes_the_finest_grain():
    backbone = ["Customer", "Account", "Premise", "Service Point", "Meter"]
    assert finest_grain(["Account", "Premise"], backbone) == "Premise"
    assert finest_grain([], backbone) == "unknown"


def test_grain_ambiguity_counts_the_minority():
    assert grain_ambiguity(["Account", "Account", "Account"]) == 0.0
    assert grain_ambiguity(["Account", "Account", "Premise"]) > 0.3


def test_graph_meets_the_run_quality_gates(graph):
    assert graph.stats["resolution_rate"] >= QUALITY_GATES["resolution_rate_floor"]
    assert graph.stats["parse_rate"] >= QUALITY_GATES["parse_rate_floor"]
    assert graph.stats["quarantined_rows"] > 0, "the planted broken lineage must be caught"


def test_all_six_resolution_rules_are_reachable(graph):
    rules = Counter(edge.er_rule for edge in graph.edges_kpi_column)
    assert rules["ER-1"] > 0
    assert sum(rules[rule] for rule in ("ER-2", "ER-3", "ER-4", "ER-5", "ER-6")) > 0
    assert all(0.0 < edge.confidence <= 1.0 for edge in graph.edges_kpi_column)


def test_power_bi_model_measures_are_one_node_with_many_report_edges(graph):
    shared = [kpi_id for kpi_id, reports in graph.kpi_reports.items() if len(reports) > 1]
    assert shared, "a model-scope measure used by several reports must be one KPI node"
    for kpi_id in shared[:5]:
        assert graph.kpis[kpi_id].tool == "powerbi"
        assert graph.kpis[kpi_id].measure_scope == "model"
