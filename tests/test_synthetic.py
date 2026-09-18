"""The synthetic data pack and the defects it plants (specification section 17)."""
from __future__ import annotations

from collections import Counter

import pytest

from dpre.ingest import ingest_automated
from dpre.synth import INDUSTRY_KEYS, generate_pack
from dpre.synth.defects import DEFECT_CLASSES
from dpre.synth.workbook import TAB_ORDER, write_pack_workbook

# The row bands of the specification's section 17.1 table.
TAB_RANGES = {
    "Cognos_Rationalization": (130, 170),
    "Cognos_KPI_Lineage": (520, 670),
    "PowerBI_Inventory": (70, 110),
    "PowerBI_Measure_Lineage": (150, 250),
    "Collibra_Metadata": (600, 1000),
    "Collibra_Lineage": (300, 600),
    "Alation_Metadata": (600, 1000),
    "Business_Glossary": (80, 150),
    "Planted_Defects": (28, 35),
}


@pytest.mark.parametrize("industry", INDUSTRY_KEYS)
def test_every_industry_fills_the_documented_row_bands(industry, as_of):
    pack = generate_pack(industry, as_of=as_of)
    assert list(pack.tabs) == list(TAB_ORDER)
    for tab, (low, high) in TAB_RANGES.items():
        rows = len(pack.tabs[tab])
        assert low <= rows <= high, f"{industry}.{tab} has {rows} rows, band is {low}-{high}"


def test_generation_is_reproducible_from_the_seed(as_of):
    first = generate_pack("banking", as_of=as_of)
    second = generate_pack("banking", as_of=as_of)
    assert first.seed == second.seed
    assert first.bundle.generation_id == second.bundle.generation_id
    assert first.tabs["Cognos_KPI_Lineage"] == second.tabs["Cognos_KPI_Lineage"]


def test_a_different_seed_changes_the_content_not_the_shape(as_of):
    default = generate_pack("banking", as_of=as_of)
    other = generate_pack("banking", seed=999, as_of=as_of)
    assert other.bundle.generation_id != default.bundle.generation_id
    for tab, (low, high) in TAB_RANGES.items():
        assert low <= len(other.tabs[tab]) <= high


def test_every_row_is_flagged_synthetic(pack):
    for tab in ("Cognos_Rationalization", "Cognos_KPI_Lineage", "PowerBI_Inventory",
                "PowerBI_Measure_Lineage", "Collibra_Metadata"):
        for row in pack.tabs[tab]:
            assert row.get("synthetic") is True
    assert all(record.synthetic for record in pack.bundle.kpis)


def test_the_readme_records_the_parameters_and_the_defects(pack):
    items = {row["item"]: row["value"] for row in pack.tabs["README"]}
    assert items["Industry key"] == "utility"
    assert items["Random seed"] == pack.seed
    assert "TRUE" in str(items["Synthetic"])
    for defect_class in pack.bundle.manifest["defect_summary"]:
        assert defect_class in items


def test_all_documented_defect_classes_are_planted(pack):
    planted = {row["defect_class"] for row in pack.tabs["Planted_Defects"]}
    assert planted == set(DEFECT_CLASSES), sorted(set(DEFECT_CLASSES) - planted)
    for row in pack.tabs["Planted_Defects"]:
        assert row["expected_detection"] and row["affected_count"] >= 1


def test_the_workbook_is_the_ingestion_contract(pack, tmp_path, as_of):
    """Section 17.4: the same tabs the reviewer reads are what the engine ingests."""
    from dpre.ingest import ingest_manual, sources_from_workbook
    path = write_pack_workbook(pack, tmp_path / "pack.xlsx")
    manual = ingest_manual(sources_from_workbook(path), as_of=as_of)
    automated = ingest_automated("utility", as_of=as_of)
    assert manual.ok
    assert len(manual.bundle.reports) == len(automated.bundle.reports)
    assert len(manual.bundle.kpis) == len(automated.bundle.kpis)
    assert len(manual.bundle.columns) == len(automated.bundle.columns)


def test_the_alation_tab_describes_the_same_estate(pack, tmp_path, as_of):
    """Section 16.3: nothing downstream knows which catalog it was."""
    from dpre.ingest import SourceSpec, ingest_manual
    path = write_pack_workbook(pack, tmp_path / "pack.xlsx")
    collibra = ingest_manual([
        SourceSpec(str(path), "cognos_kpi_lineage", "Cognos_KPI_Lineage"),
        SourceSpec(str(path), "collibra_metadata", "Collibra_Metadata"),
    ], as_of=as_of)
    alation = ingest_manual([
        SourceSpec(str(path), "cognos_kpi_lineage", "Cognos_KPI_Lineage"),
        SourceSpec(str(path), "alation_metadata", "Alation_Metadata"),
    ], as_of=as_of)
    assert len(collibra.bundle.columns) == len(alation.bundle.columns)
    assert ({c.column_fqn for c in collibra.bundle.columns}
            == {c.column_fqn for c in alation.bundle.columns})
    assert ({c.data_steward for c in collibra.bundle.columns}
            == {c.data_steward for c in alation.bundle.columns})
    assert alation.bundle.catalog == "alation"


def test_usage_follows_a_long_tail(pack):
    runs = sorted((row["run_count_12m"] for row in pack.tabs["Cognos_Rationalization"]),
                  reverse=True)
    top_decile = sum(runs[:max(1, len(runs) // 10)])
    assert top_decile > 0.5 * sum(runs), "a small share of reports must carry most runs"


# ---- detection of the planted defects -------------------------------------

def test_identical_kpis_collapse_to_one_canonical_metric(canonical, graph):
    assert canonical.stats["rows_collapsed"] > 200
    biggest = max(canonical.metrics.values(), key=lambda m: len(m.kpi_ids))
    assert len(biggest.kpi_ids) >= 5


def test_threshold_drift_surfaces_as_a_nominal_conflict(canonical):
    patterns = Counter(conflict.pattern for conflict in canonical.conflicts)
    assert patterns["THRESHOLD"] > 0
    threshold = next(c for c in canonical.conflicts if c.pattern == "THRESHOLD")
    assert "threshold" in threshold.difference_summary.lower()
    assert threshold.semantic_model_decision.startswith("Parameterized")


def test_exclusion_drift_surfaces_as_a_variant(canonical):
    assert canonical.stats["metrics_with_variants"] > 0
    variants = [v for v in canonical.variants if v.tier == "VARIANT"]
    assert variants and any(v.filter_expression for v in variants)


def test_cross_tool_duplication_produces_one_metric_spanning_both_tools(canonical):
    spanning = [m for m in canonical.metrics.values() if len(m.tools) > 1]
    assert spanning, "the same calculation in Cognos and DAX must be one canonical metric"
    assert {"cognos", "powerbi"} == set(spanning[0].tools)


def test_broken_lineage_is_quarantined_with_a_reason_code(graph):
    reasons = Counter(row.reason_code for row in graph.quarantine)
    assert reasons
    assert set(reasons) <= {"NO_CATALOG_TABLE", "NO_CATALOG_COLUMN", "MISSING_REFERENCE",
                            "LOW_CONFIDENCE", "MODEL_TERMINUS"}


def test_opaque_expressions_are_kept_but_penalized(graph, canonical):
    assert graph.stats["opaque_kpis"] > 0
    opaque = [m for m in canonical.metrics.values() if m.opaque]
    assert opaque
    assert all("parser could not read" in m.definition for m in opaque)


def test_missing_definitions_and_stewards_appear_as_gaps(graph, canonical, clustered):
    undefined = [c for c in graph.columns.values() if not c.business_term]
    assert undefined
    gaps = [gap for candidate in clustered.candidates for gap in candidate.gaps]
    assert any("no catalog definition" in gap for gap in gaps)


def test_zombie_reports_lose_their_demand(graph, as_of):
    from dpre.usage import report_usage_weight
    stale = [report for report in graph.reports.values()
             if report.last_run_date and (as_of - report.last_run_date).days > 400]
    assert stale, "the pack must plant a zombie report"
    for report in stale:
        fresh = report_usage_weight(report, as_of)
        report_copy = type(report)(**{**report.__dict__, "last_run_date": as_of})
        assert fresh < 0.2 * report_usage_weight(report_copy, as_of)


def test_regulatory_low_usage_reports_can_be_floored_by_a_reviewer(graph, as_of):
    from dpre.usage import report_usage_weight
    low = min(graph.reports.values(), key=lambda r: r.run_count_12m)
    before = report_usage_weight(low, as_of)
    low.decision_critical = True
    after = report_usage_weight(low, as_of)
    low.decision_critical = False
    assert after >= before
