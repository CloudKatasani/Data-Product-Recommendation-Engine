"""Estate benchmark: is 0.49 conflicts per metric bad? (review finding R-48)

Seven estate KPIs per run, stored in ``RUN_BENCHMARK``, compared with reference
bands derived from the nine synthetic industry packs and with the section 14.2
targets. The bands are hard-coded below; ``derive_reference_bands`` is the
helper that produced them and can be re-run when the generator changes.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any

BENCHMARK_VERSION = "benchmark-1.0"
ZOMBIE_MONTHS = 12
CONFIRMED_STEWARD = ("business term steward", "column steward")

KPI_DEFINITIONS = {
    "duplication_ratio": "KPI rows collapsed into canonical metrics / KPI rows",
    "conflict_density": "nominal conflicts / canonical metrics",
    "lineage_completeness": "lineage rows resolved at 0.80 confidence or better / lineage rows",
    "parse_rate": "expressions parsed / expressions",
    "definition_coverage": "catalog columns with a business term and a definition / columns",
    "steward_coverage": "canonical metrics with any steward (catalog or inferred) / metrics",
    "confirmed_steward_coverage": "canonical metrics whose steward comes from a catalog "
                                  "business term or column, not a report owner / metrics",
    "term_steward_coverage": "catalog columns with a steward / columns",
    "opaque_share": "opaque canonical metrics / canonical metrics",
    "zombie_share": "reports not run in 12 months / reports",
    "top20_coverage": "usage-weighted consumption covered by the top 20 candidates",
}
# Whether a higher value is better, for the outlier direction.
HIGHER_IS_BETTER = {
    "duplication_ratio": False, "conflict_density": False, "lineage_completeness": True,
    "parse_rate": True, "definition_coverage": True, "steward_coverage": True,
    "confirmed_steward_coverage": True,
    "term_steward_coverage": True, "opaque_share": False, "zombie_share": False,
    "top20_coverage": True,
}

# Produced once by derive_reference_bands(as_of=2026-09-17) over the nine
# synthetic packs (generic, utility, banking, insurance, retail, healthcare,
# manufacturing, telecom, public_sector) with the default EngineConfig and the
# generator's fixed seed; low/high are the min and max across packs, median the
# middle value. Re-derive when the generator or the parser version changes.
REFERENCE_BANDS: dict[str, dict[str, float]] = {
    "duplication_ratio": {"low": 0.7414, "median": 0.759, "high": 0.7719},
    "conflict_density": {"low": 0.4132, "median": 0.4775, "high": 0.5339},
    "lineage_completeness": {"low": 0.8747, "median": 0.8901, "high": 0.8999},
    "parse_rate": {"low": 0.9371, "median": 0.9418, "high": 0.945},
    "definition_coverage": {"low": 0.2364, "median": 0.2459, "high": 0.258},
    "steward_coverage": {"low": 1.0, "median": 1.0, "high": 1.0},
    "confirmed_steward_coverage": {"low": 0.7851, "median": 0.8462, "high": 0.8559},
    "term_steward_coverage": {"low": 1.0, "median": 1.0, "high": 1.0},
    "opaque_share": {"low": 0.1776, "median": 0.2018, "high": 0.2124},
    "zombie_share": {"low": 0.0189, "median": 0.0304, "high": 0.0442},
    "top20_coverage": {"low": 0.9187, "median": 0.9511, "high": 0.9756},
}

# Specification section 14.2 targets that can be read as estate KPIs.
TARGETS_14_2 = {
    "lineage_completeness": 0.80,     # phase 1 exit: >= 80% lineage resolution
    "parse_rate": 0.70,               # phase 1 exit: >= 70% parse rate
    "top20_coverage": 0.50,           # phase 2 exit: top 20 cover >= 50%
    "confirmed_steward_coverage": 0.80,  # 14.2: >= 80% of metrics with a confirmed steward
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS RUN_BENCHMARK (
    run_id TEXT, benchmark_version TEXT, kpis TEXT, comparison TEXT, outliers TEXT,
    PRIMARY KEY (run_id)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def estate_kpis(result: Any) -> dict[str, float]:
    """The seven KPIs plus parse rate and top-20 coverage, from a RunResult."""
    graph = result.graph
    canonical = result.canonical
    gstats = graph.stats
    cstats = canonical.stats
    columns = list(graph.columns.values())
    metrics = list(canonical.metrics.values())
    reports = list(graph.reports.values())
    as_of = _as_of(result)
    zombies = sum(1 for r in reports if _months_since(r.last_run_date, as_of) >= ZOMBIE_MONTHS)
    kpi_rows = max(1, cstats.get("kpi_nodes", len(graph.kpis)))
    return {
        "duplication_ratio": round(cstats.get("rows_collapsed", 0) / kpi_rows, 4),
        "conflict_density": round(len(canonical.conflicts) / max(1, len(metrics)), 4),
        "lineage_completeness": round(float(gstats.get("resolution_rate", 0.0)), 4),
        "parse_rate": round(float(gstats.get("parse_rate", 0.0)), 4),
        "definition_coverage": round(
            sum(1 for c in columns if c.business_term and c.definition) / max(1, len(columns)), 4),
        "steward_coverage": round(
            sum(1 for m in metrics if m.steward_id) / max(1, len(metrics)), 4),
        "confirmed_steward_coverage": round(
            sum(1 for m in metrics if m.steward_id and m.steward_source in CONFIRMED_STEWARD)
            / max(1, len(metrics)), 4),
        "term_steward_coverage": round(
            sum(1 for c in columns if c.steward_id) / max(1, len(columns)), 4),
        "opaque_share": round(sum(1 for m in metrics if m.opaque) / max(1, len(metrics)), 4),
        "zombie_share": round(zombies / max(1, len(reports)), 4),
        "top20_coverage": round(float(result.manifest.stats.get("usage_coverage_top_n", 0.0)), 4),
    }


def compare_to_bands(kpis: dict[str, float],
                     bands: dict[str, dict[str, float]] | None = None,
                     tolerance: float = 0.25) -> list[dict]:
    """This run against the band: within, or outside by more than ``tolerance``
    of the band's width (a floor of 0.02 keeps a narrow band from flagging noise)."""
    bands = bands or REFERENCE_BANDS
    rows = []
    for kpi, value in kpis.items():
        band = bands.get(kpi)
        if band is None:
            continue
        width = max(0.02, band["high"] - band["low"])
        margin = tolerance * width
        if value < band["low"] - margin:
            position = "below"
        elif value > band["high"] + margin:
            position = "above"
        elif value < band["low"] or value > band["high"]:
            position = "edge"
        else:
            position = "within"
        better = HIGHER_IS_BETTER[kpi]
        outlier = position in ("below", "above")
        direction = ""
        if outlier:
            direction = "better" if (position == "above") == better else "worse"
        target = TARGETS_14_2.get(kpi)
        rows.append({
            "kpi": kpi,
            "definition": KPI_DEFINITIONS[kpi],
            "value": value,
            "band_low": band["low"],
            "band_median": band["median"],
            "band_high": band["high"],
            "position": position,
            "outlier": outlier,
            "direction": direction,
            "target": target,
            "meets_target": (None if target is None else
                             (value >= target if better else value <= target)),
        })
    return rows


def benchmark_run(result: Any, bands: dict[str, dict[str, float]] | None = None) -> dict:
    kpis = estate_kpis(result)
    comparison = compare_to_bands(kpis, bands)
    return {
        "version": BENCHMARK_VERSION,
        "kpis": kpis,
        "comparison": comparison,
        "outliers": [row["kpi"] for row in comparison if row["outlier"]],
        "worse_than_reference": [row["kpi"] for row in comparison if row["direction"] == "worse"],
        "reference": "nine synthetic industry packs, as of 2026-09-17 (see REFERENCE_BANDS)",
    }


def derive_reference_bands(as_of: _dt.date | None = None,
                           industries: tuple[str, ...] | None = None) -> dict[str, dict[str, float]]:
    """Run every synthetic pack and take min/median/max per KPI. Offline helper;
    imports the pipeline lazily so this module stays import-light."""
    from ..ingest import ingest_automated
    from ..pipeline import run_pipeline
    from ..synth import INDUSTRY_KEYS
    as_of = as_of or _dt.date(2026, 9, 17)
    values: dict[str, list[float]] = {}
    for industry in industries or INDUSTRY_KEYS:
        kpis = estate_kpis(run_pipeline(ingest_automated(industry, as_of=as_of)))
        for kpi, value in kpis.items():
            values.setdefault(kpi, []).append(value)
    bands = {}
    for kpi, series in values.items():
        ordered = sorted(series)
        bands[kpi] = {"low": ordered[0], "median": ordered[len(ordered) // 2],
                      "high": ordered[-1]}
    return bands


def save_benchmark(connection: sqlite3.Connection, run_id: str, benchmark: dict) -> None:
    ensure_schema(connection)
    connection.execute(
        "INSERT OR REPLACE INTO RUN_BENCHMARK VALUES (?,?,?,?,?)",
        (run_id, benchmark["version"], json.dumps(benchmark["kpis"]),
         json.dumps(benchmark["comparison"]), json.dumps(benchmark["outliers"])))
    connection.commit()


def load_benchmark(connection: sqlite3.Connection, run_id: str) -> dict | None:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM RUN_BENCHMARK WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    for key in ("kpis", "comparison", "outliers"):
        item[key] = json.loads(item[key] or "null")
    return item


def benchmark_history(connection: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """KPIs across stored runs, oldest first, so a trend can be drawn."""
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT b.run_id, r.started_at, r.industry, b.kpis FROM RUN_BENCHMARK b "
        "LEFT JOIN RUN r ON r.run_id = b.run_id ORDER BY r.started_at LIMIT ?", (limit,)).fetchall()
    return [{"run_id": r["run_id"], "started_at": r["started_at"], "industry": r["industry"],
             "kpis": json.loads(r["kpis"] or "{}")} for r in rows]


def _as_of(result: Any) -> _dt.date:
    raw = result.manifest.as_of_date
    return _dt.date.fromisoformat(raw) if isinstance(raw, str) else raw


def _months_since(last_run: _dt.date | None, as_of: _dt.date) -> float:
    if last_run is None:
        return 24.0
    return max(0.0, (as_of - last_run).days / 30.44)
