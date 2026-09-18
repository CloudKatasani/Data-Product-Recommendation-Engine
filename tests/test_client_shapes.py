"""Ingesting a workbook that is correct but not ours.

The fixture is a trimmed copy of a client-shaped extract that the engine
rejected. It is well formed and complete; it simply names its columns and its
Power BI measures the way the source tools actually do, rather than the way the
synthetic generator does. Every assertion here failed before the fix.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from dpre.ingest import ingest_manual, inspect_file, sources_from_workbook
from dpre.ingest.schemas import suggest_schema
from dpre.pipeline import run_pipeline

AS_OF = _dt.date(2026, 9, 17)
FIXTURE = Path(__file__).parent / "fixtures" / "client_shaped_workbook.xlsx"


@pytest.fixture(scope="module")
def tables():
    return {t["sheet"]: t for t in inspect_file(FIXTURE, sample_rows=0)["tables"]}


def test_every_input_tab_is_recognised_with_nothing_missing(tables):
    expected = {
        "Cognos_Rationalization": "cognos_rationalization",
        "Cognos_KPI_Lineage": "cognos_kpi_lineage",
        "PowerBI_Inventory": "powerbi_inventory",
        "PowerBI_Measure_Lineage": "powerbi_measure_lineage",
        "Collibra_Metadata": "collibra_metadata",
        "Collibra_Lineage": "catalog_lineage",
        "Alation_Metadata": "alation_metadata",
        "Business_Glossary": "business_glossary",
    }
    for sheet, key in expected.items():
        table = tables[sheet]
        assert table["suggested_schema"] == key, sheet
        assert not table["missing_required"], f"{sheet}: {table['missing_required']}"
        assert table["confidence"] >= 0.4, sheet


def test_a_tab_name_settles_what_the_headers_cannot(tables):
    """Collibra and Alation both describe columns, and a Collibra export using
    generic names scores higher against Alation than against the catalog it
    came from. The tab name is not ambiguous to a human."""
    collibra = tables["Collibra_Metadata"]
    alation = tables["Alation_Metadata"]
    assert collibra["suggested_schema"] == "collibra_metadata"
    assert alation["suggested_schema"] == "alation_metadata"
    # Without the name, the headers alone are genuinely ambiguous; that is the
    # condition the name exists to resolve, so it is asserted rather than assumed.
    headers_only, _ = suggest_schema(collibra["columns"])
    named, _ = suggest_schema(collibra["columns"], "Collibra_Metadata")
    assert named == "collibra_metadata"
    assert headers_only != named


def test_documentation_tabs_are_not_guessed_at(tables):
    """A README scored as a KPI lineage sheet at six per cent confidence is
    noise in the inspector and a trap for anyone binding tabs by hand."""
    for sheet in ("README", "Planted_Defects"):
        assert tables[sheet]["suggested_schema"] == ""
        assert tables[sheet]["confidence"] == 0.0


def test_a_model_scoped_measure_needs_no_report_of_its_own():
    """Section 16.2: a shared measure belongs to the semantic model and is used
    by every report on it. Requiring a report rejected the whole sheet, and
    dropping the rows discarded exactly the measures worth consolidating."""
    ingest = ingest_manual(sources_from_workbook(FIXTURE), as_of=AS_OF)
    measures = [k for k in ingest.bundle.kpis if k.tool == "powerbi"]
    assert measures, "every Power BI measure was dropped"
    assert all(k.report_id for k in measures), "a KPI node with no report is not in the graph"

    model_scoped = [k for k in measures if k.measure_scope == "model"]
    assert model_scoped
    # One shared measure becomes one KPI node per consuming report, which is
    # what makes it look shared to the clusterer.
    fanned = [k for k in model_scoped if "::" in k.kpi_id]
    assert fanned, "a shared measure reached only one report"
    for kpi in model_scoped:
        assert kpi.semantic_container
        reports = {r.report_id for r in ingest.bundle.reports
                   if r.semantic_container == kpi.semantic_container}
        assert kpi.report_id in reports


def test_a_report_scoped_measure_is_never_sprayed_across_the_model():
    """A local measure naming no report is unattributable. Spreading it over
    every report on the model would invent usage the export does not claim."""
    ingest = ingest_manual(sources_from_workbook(FIXTURE), as_of=AS_OF)
    local = [k for k in ingest.bundle.kpis
             if k.tool == "powerbi" and k.measure_scope == "report"]
    by_source: dict[str, set[str]] = {}
    for kpi in local:
        by_source.setdefault(kpi.kpi_id.split("::")[0], set()).add(kpi.report_id)
    assert all(len(reports) == 1 for reports in by_source.values())
    # What could not be placed is reported, not dropped in silence.
    dropped = [e for e in ingest.log if e.get("status") == "dropped"]
    assert not dropped or "could not be placed" in dropped[0]["detail"]


def test_the_whole_workbook_produces_data_products():
    """The end the user cares about: supply the file, get a backlog."""
    ingest = ingest_manual(sources_from_workbook(FIXTURE), as_of=AS_OF)
    assert ingest.bundle.reports and ingest.bundle.kpis and ingest.bundle.columns
    errors = [i for i in ingest.validation.issues if i.severity == "error"]
    assert not errors, [i.message for i in errors]

    result = run_pipeline(ingest)
    assert result.candidates, "the run produced no data product candidates"
    for candidate in result.candidates:
        assert candidate.score is not None
        assert candidate.proposed_name and candidate.grain
