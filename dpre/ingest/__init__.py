"""Ingestion: the two entry paths into the engine.

Manual - a user supplies extracts from Cognos, Power BI, Collibra or Alation as
files, with an optional column mapping. Automated - the engine generates a
synthetic pack for an industry and ingests that. Both produce the same
:class:`~dpre.models.ExtractBundle`, so nothing downstream knows which was used.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, replace
from pathlib import Path
from ..models import ExtractBundle, KpiRecord
from ..util import tabular
from ..util.xlsx import read_workbook, rows_to_records
from .adapters import catalog as catalog_adapter
from .adapters import cognos as cognos_adapter
from .adapters import powerbi as powerbi_adapter
from .schemas import (
    CATALOG_INPUTS, RECOMMENDED_INPUTS, REQUIRED_INPUTS, SCHEMAS, map_columns, suggest_schema,
)
from .validator import ValidationReport, stamp_as_of, validate

__all__ = [
    "SourceSpec", "IngestResult", "ingest_manual", "ingest_automated", "inspect_file",
    "SCHEMAS", "suggest_schema", "map_columns", "REQUIRED_INPUTS", "RECOMMENDED_INPUTS",
    "CATALOG_INPUTS", "validate", "ValidationReport",
]


@dataclass
class SourceSpec:
    """One user-provided file (manual mode), bound to an input schema."""

    path: str
    schema_key: str
    sheet: str | None = None
    mapping: dict[str, str] = field(default_factory=dict)
    label: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "schema_key": self.schema_key, "sheet": self.sheet,
                "mapping": self.mapping, "label": self.label or Path(self.path).name}


@dataclass
class IngestResult:
    bundle: ExtractBundle
    validation: ValidationReport
    sources: list[dict] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.validation.ok

    def to_dict(self) -> dict:
        return {
            "mode": self.bundle.mode,
            "industry": self.bundle.industry,
            "catalog": self.bundle.catalog,
            "as_of_date": self.bundle.as_of_date.isoformat(),
            "synthetic": self.bundle.synthetic,
            "generation_id": self.bundle.generation_id,
            "sources": self.sources,
            "validation": self.validation.to_dict(),
            "log": self.log,
        }


# --------------------------------------------------------------------------
# Inspection: what the manual-mode wizard shows before a run
# --------------------------------------------------------------------------

def inspect_file(path: str | Path, sample_rows: int = 3) -> dict:
    """Describe a user-provided file: its tables, likely schema and auto-mapping."""
    path = Path(path)
    tables: list[dict] = []
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        for sheet, rows in read_workbook(path).items():
            records = rows_to_records(rows)
            tables.append(_describe(path, records, sheet, sample_rows))
    else:
        records = tabular.load_records(path)
        tables.append(_describe(path, records, None, sample_rows))
    return {"file": path.name, "path": str(path), "tables": tables}


def _describe(path: Path, records: list[dict], sheet: str | None, sample_rows: int) -> dict:
    columns = list(records[0].keys()) if records else []
    # The tab name is evidence too, and for a catalog export it is usually the
    # only evidence that separates Collibra from Alation.
    schema_key, confidence = suggest_schema(columns, sheet or path.stem)
    mapping = map_columns(schema_key, columns) if schema_key else {}
    schema = SCHEMAS.get(schema_key)
    missing = [f for f in (schema.required_fields if schema else []) if f not in mapping]
    return {
        "sheet": sheet,
        "rows": len(records),
        "columns": columns,
        "suggested_schema": schema_key,
        "suggested_label": schema.label if schema else "",
        "confidence": confidence,
        "mapping": mapping,
        "missing_required": missing,
        "sample": records[:sample_rows],
    }


# --------------------------------------------------------------------------
# Manual mode
# --------------------------------------------------------------------------

def ingest_manual(sources: list[SourceSpec], as_of: _dt.date | None = None,
                  catalog_preference: str | None = None) -> IngestResult:
    """Ingest user-provided extracts into one bundle."""
    bundle = ExtractBundle(mode="manual", as_of_date=as_of or _dt.date.today())
    log: list[dict] = []
    described: list[dict] = []
    catalogs_seen: list[str] = []

    for source in sources:
        schema_key = source.schema_key
        if schema_key not in SCHEMAS:
            log.append({"source": source.path, "status": "skipped",
                        "detail": f"unknown input type '{schema_key}'"})
            continue
        records = _load(source)
        entry = {"source": Path(source.path).name, "schema": schema_key,
                 "sheet": source.sheet, "rows_in": len(records)}
        if not records:
            entry.update(status="empty", rows_out=0)
            log.append(entry)
            described.append({**source.to_dict(), "rows": 0})
            continue

        label = Path(source.path).name
        if schema_key == "cognos_rationalization":
            reports, reader = cognos_adapter.adapt_reports(records, source.mapping, label)
            bundle.reports.extend(reports)
            entry.update(rows_out=len(reports))
        elif schema_key == "cognos_kpi_lineage":
            kpis, reader = cognos_adapter.adapt_kpis(records, source.mapping, label)
            bundle.kpis.extend(kpis)
            entry.update(rows_out=len(kpis))
        elif schema_key == "powerbi_inventory":
            reports, reader = powerbi_adapter.adapt_reports(records, source.mapping, label)
            bundle.reports.extend(reports)
            entry.update(rows_out=len(reports))
        elif schema_key == "powerbi_measure_lineage":
            measures, reader = powerbi_adapter.adapt_measures(records, source.mapping, label)
            bundle.kpis.extend(measures)
            entry.update(rows_out=len(measures))
        elif schema_key in ("collibra_metadata", "alation_metadata"):
            catalog_name = "alation" if schema_key == "alation_metadata" else "collibra"
            catalogs_seen.append(catalog_name)
            columns, reader = catalog_adapter.adapt_columns(
                records, catalog_name, source.mapping, label)
            bundle.columns.extend(columns)
            entry.update(rows_out=len(columns), catalog=catalog_name)
        elif schema_key == "catalog_lineage":
            edges, reader = catalog_adapter.adapt_lineage(records, source.mapping, label)
            bundle.lineage.extend(edges)
            entry.update(rows_out=len(edges))
        elif schema_key == "business_glossary":
            terms, reader = catalog_adapter.adapt_glossary(records, source.mapping, label)
            bundle.glossary.extend(terms)
            entry.update(rows_out=len(terms))
        else:
            log.append({**entry, "status": "skipped", "detail": "no adapter"})
            continue

        entry["status"] = "ingested"
        entry["missing_required"] = reader.missing_required()
        entry["missing_optional"] = reader.missing_optional()
        log.append(entry)
        described.append({**source.to_dict(), "rows": entry.get("rows_out", 0)})

    attached, orphaned = _attach_model_measures(bundle)
    if attached:
        log.append({"source": "powerbi_measure_lineage", "status": "resolved",
                    "detail": f"{attached} model-scoped measure row(s) attached to the "
                              "reports that use their semantic model",
                    "rows_out": attached})
    if orphaned:
        log.append({"source": "powerbi_measure_lineage", "status": "dropped",
                    "detail": f"{orphaned} measure(s) could not be placed: a report-scoped "
                              "measure naming no report, or a semantic model no report in "
                              "the inventory uses. Supply the Power BI inventory for those "
                              "workspaces, or the report each local measure belongs to",
                    "rows_out": 0})

    # A user who supplied both catalogs keeps whichever they asked for; otherwise
    # the first one wins. Downstream never learns which catalog it was.
    if catalog_preference in ("collibra", "alation"):
        bundle.catalog = catalog_preference
    elif catalogs_seen:
        bundle.catalog = catalogs_seen[0]
    if len(set(catalogs_seen)) > 1:
        # Collibra and Alation describe the same physical estate. Keeping both
        # counts every column twice, which inflates duplication, definition
        # coverage and ultimately the backlog. Without a stated preference the
        # first one bound wins, and the run says which it used.
        kept = bundle.catalog
        dropped = sum(1 for c in bundle.columns if c.catalog != kept)
        bundle.columns = [c for c in bundle.columns if c.catalog == kept]
        log.append({"source": "catalog", "status": "deduplicated",
                    "detail": f"both catalogs were supplied for one estate; kept {kept} "
                              f"and set aside {dropped} column(s) from the other, which "
                              "would otherwise have been counted twice",
                    "rows_out": len(bundle.columns)})

    bundle.source_files = described
    bundle.synthetic = bool(bundle.kpis) and all(k.synthetic for k in bundle.kpis)
    if bundle.synthetic:
        bundle.generation_id = next((k.generation_id for k in bundle.kpis if k.generation_id), "")
    bundle.manifest = {
        "mode": "manual",
        "sources": described,
        "as_of_date": bundle.as_of_date.isoformat(),
    }
    stamp_as_of(bundle, as_of)
    report = validate(bundle)
    _check_coverage(sources, report)
    return IngestResult(bundle=bundle, validation=report, sources=described, log=log)


def _check_coverage(sources: list[SourceSpec], report: ValidationReport) -> None:
    provided = {s.schema_key for s in sources}
    for key in REQUIRED_INPUTS:
        if key not in provided:
            report.add("error", "MISSING_REQUIRED_INPUT",
                       f"{SCHEMAS[key].label} was not provided",
                       "This input is required; the engine cannot build the graph without it.")
    if not (provided & set(CATALOG_INPUTS)):
        report.add("warning", "NO_CATALOG_INPUT",
                   "No catalog extract was provided",
                   "Supply Collibra or Alation metadata so lineage can resolve past the "
                   "reporting layer.")
    for key in RECOMMENDED_INPUTS:
        if key not in provided and key not in CATALOG_INPUTS:
            report.add("info", "MISSING_RECOMMENDED_INPUT",
                       f"{SCHEMAS[key].label} was not provided",
                       "Scores will be computed without it and the gap is recorded.")


def _attach_model_measures(bundle: ExtractBundle) -> tuple[int, int]:
    """Give every report-less measure the reports that actually use it.

    A shared Power BI measure is defined once on a semantic model and consumed
    by every report built on that model. The lineage export therefore names the
    model, not a report. The engine's graph is KPI-to-Report, so one such row
    becomes one KPI node per consuming report - which is also what makes a
    shared measure look shared, and therefore worth consolidating.

    Returns the number of rows attached and the number that could not be
    placed - a report-scoped measure naming no report, or a model no report in
    the bundle uses. Both are gaps worth reporting rather than rows worth
    dropping quietly.
    """
    # Only a model-scoped measure fans out. A report-scoped one that names no
    # report is unattributable, and spreading it across every report on the
    # model would invent usage that the export does not claim.
    pending = [k for k in bundle.kpis
               if k.tool == "powerbi" and not k.report_id and k.measure_scope == "model"]
    unattributable = [k for k in bundle.kpis
                      if k.tool == "powerbi" and not k.report_id
                      and k.measure_scope != "model"]
    if not pending and not unattributable:
        return 0, 0

    by_model: dict[str, list[str]] = {}
    for report in bundle.reports:
        container = (report.semantic_container or "").strip().lower()
        if container:
            by_model.setdefault(container, []).append(report.report_id)

    attached = 0
    orphaned = len(unattributable)
    expanded: list[KpiRecord] = []
    for measure in pending:
        reports = by_model.get((measure.semantic_container or "").strip().lower(), [])
        if not reports:
            orphaned += 1
            continue
        measure.report_id = reports[0]
        attached += 1
        for extra in reports[1:]:
            clone = replace(measure, report_id=extra,
                            kpi_id=f"{measure.kpi_id}::{extra}")
            expanded.append(clone)
    bundle.kpis.extend(expanded)
    if orphaned:
        bundle.kpis = [k for k in bundle.kpis
                       if not (k.tool == "powerbi" and not k.report_id)]
    return attached + len(expanded), orphaned


def _load(source: SourceSpec) -> list[dict]:
    path = Path(source.path)
    if path.suffix.lower() in (".xlsx", ".xlsm") and source.sheet:
        rows = read_workbook(path).get(source.sheet, [])
        return rows_to_records(rows)
    return tabular.load_records(path, source.sheet)


def sources_from_workbook(path: str | Path, catalog: str = "collibra") -> list[SourceSpec]:
    """Bind every recognisable tab of a workbook to its input schema."""
    info = inspect_file(path, sample_rows=0)
    out: list[SourceSpec] = []
    for table in info["tables"]:
        key = table["suggested_schema"]
        if not key or table["confidence"] < 0.4 or not table["rows"]:
            continue
        if key == "alation_metadata" and catalog != "alation":
            continue
        if key == "collibra_metadata" and catalog != "collibra":
            continue
        out.append(SourceSpec(path=str(path), schema_key=key, sheet=table["sheet"],
                              mapping=table["mapping"]))
    return out


# --------------------------------------------------------------------------
# Automated mode
# --------------------------------------------------------------------------

def ingest_automated(industry: str = "generic", seed: int | None = None,
                     as_of: _dt.date | None = None, catalog: str = "collibra",
                     workbook_path: str | Path | None = None) -> IngestResult:
    """Generate a synthetic pack for an industry and ingest it."""
    from ..synth import generate_pack
    from ..synth.workbook import write_pack_workbook

    pack = generate_pack(industry, seed=seed, as_of=as_of)
    bundle = pack.bundle
    bundle.catalog = catalog
    if catalog == "alation":
        for column in bundle.columns:
            column.catalog = "alation"
    stamp_as_of(bundle, as_of)

    written = None
    if workbook_path:
        written = write_pack_workbook(pack, workbook_path)
        bundle.source_files = [{"path": str(written), "label": Path(written).name,
                                "schema_key": "synthetic_workbook",
                                "rows": sum(len(r) for r in pack.tabs.values())}]

    expected = {
        "reports": len(bundle.reports),
        "kpi_rows": len(bundle.kpis),
        "catalog_columns": len(bundle.columns),
    }
    report = validate(bundle, expected_counts=expected)
    log = [
        {"source": f"synthetic:{pack.industry}", "schema": tab,
         "rows_in": len(rows), "rows_out": len(rows), "status": "generated"}
        for tab, rows in pack.tabs.items() if tab != "README"
    ]
    if written:
        log.append({"source": str(written), "schema": "workbook", "status": "written",
                    "rows_in": 0, "rows_out": 0})
    return IngestResult(bundle=bundle, validation=report,
                        sources=bundle.source_files, log=log)
