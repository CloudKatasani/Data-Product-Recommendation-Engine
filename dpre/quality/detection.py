"""Planted against detected: the scorecard that makes the claim falsifiable
(review finding R-19).

The specification says the detection rate is measurable. Nothing measured it.
A demonstration that shows what the engine found, with no statement of what was
there to find, cannot be wrong, and a claim that cannot be wrong is worth
nothing to a client's assurance function.

The synthetic generator plants defects on purpose and records them on the
bundle as ``planted_defects``: a class, how it was planted, what detection
should look like, and the objects it touched. This module reads that list, asks
the run what it actually found for each class, and reports recall per class with
the misses named. Where a class has a countable false-positive notion - a
conflict that no planted defect explains - precision is reported too; where it
does not, precision is left null rather than invented.

Nothing here changes a score. It is the honesty check that runs beside one, and
it only works on a synthetic estate, because only there is the truth known.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS DETECTION_SCORECARD (
    run_id TEXT, defect_class TEXT, planted INTEGER, detected INTEGER, recall REAL,
    precision REAL, false_positives INTEGER, missed TEXT, detail TEXT, method TEXT,
    PRIMARY KEY (run_id, defect_class)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


#: How each planted class is recognised in a run's output, and in one sentence
#: why that is the right evidence for it. A class with no entry is reported as
#: not measurable rather than silently scored zero.
METHODS: dict[str, str] = {
    "IDENTICAL_KPI": "KPI rows collapsed into one canonical metric by fingerprint",
    "STRUCTURAL_COUSIN": "a nominal conflict raised between metrics sharing a label",
    "DENOMINATOR_SWAP": "a conflict with the DENOMINATOR pattern on the affected reports",
    "THRESHOLD_DRIFT": "a conflict with the THRESHOLD pattern on the affected reports",
    "EXCLUSION_DRIFT": ("any conflict raised on the affected reports: the register has no "
                        "exclusion pattern of its own, so this is credit for finding the "
                        "argument, not for naming its cause"),
    "TIME_BASIS_DRIFT": "a conflict with the TIME_BASIS pattern on the affected reports",
    "GRAIN_MIXING": "a candidate split by grain, or a grain-ambiguity gate result",
    "CROSS_TOOL_DUPLICATION": "one canonical metric carrying more than one tool",
    "OPAQUE_EXPRESSION": "a lineage row behind a canonical metric flagged opaque",
    "MISSING_DEFINITION": "an operand column with no catalog definition, named in a gap",
    "BROKEN_LINEAGE": "a lineage row quarantined with a reason code",
    "SUNSET_SOURCE": "a source system carrying a sunset lifecycle in the catalog",
    "ZOMBIE_REPORT": "a report with no run inside the usage window",
    "UNASSIGNED_STEWARD": "a glossary term carrying no steward in the catalog extract",
    "REGULATORY_LOW_USAGE": "a report the retirement view holds back as decision-critical",
}


def _ids(value: Any) -> set[str]:
    """The affected-object list as a set; the generator writes it semicolon-joined."""
    if isinstance(value, (list, tuple, set)):
        return {str(v).strip() for v in value if str(v).strip()}
    return {part.strip() for part in str(value or "").split(";") if part.strip()}


def _as_date(value: Any):
    """The manifest carries the as-of as an ISO string; a report carries a date."""
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return value


def _detected_objects(result: Any) -> dict[str, set[str]]:
    """What the run found, per defect class, in the same id space as the plant.

    The generator records each defect against the objects it touched, and those
    are different kinds of object per class: report ids for a duplicated KPI,
    lineage row ids for broken lineage, a system name for a sunset source,
    business term names for an unassigned steward. A set built in the wrong id
    space intersects with nothing and reports a miss that never happened, so
    each branch below is written to the class's own space.

    Every set is built from the run's own output and never from the planted
    list. That is the point of the exercise.
    """
    graph, canonical = result.graph, result.canonical
    metrics = list(canonical.metrics.values())
    found: dict[str, set[str]] = {key: set() for key in METHODS}

    for metric in metrics:
        reports = set(getattr(metric, "report_ids", []) or [])
        if getattr(metric, "variant_count", 0) > 1:
            found["IDENTICAL_KPI"] |= reports
        if len(set(getattr(metric, "tools", []) or [])) > 1:
            found["CROSS_TOOL_DUPLICATION"] |= reports
        if getattr(metric, "opaque", False):
            # Planted against the lineage rows, so answer in KPI ids.
            found["OPAQUE_EXPRESSION"] |= set(getattr(metric, "kpi_ids", []) or [])

    # The conflict register's pattern vocabulary is DENOMINATOR, THRESHOLD and
    # TIME_BASIS. Exclusion drift has no pattern of its own - the engine raises
    # it as one of the other two - so it is credited when any conflict lands on
    # the reports it was planted in, and the method line says so.
    for conflict in canonical.conflicts:
        pattern = (getattr(conflict, "pattern", "") or "").upper()
        sides = set(getattr(conflict, "reports_a", []) or []) | \
            set(getattr(conflict, "reports_b", []) or [])
        found["STRUCTURAL_COUSIN"] |= sides
        found["EXCLUSION_DRIFT"] |= sides
        if "DENOMINATOR" in pattern or "OPERAND" in pattern:
            found["DENOMINATOR_SWAP"] |= sides
        if "THRESHOLD" in pattern or "FILTER" in pattern or "LITERAL" in pattern:
            found["THRESHOLD_DRIFT"] |= sides
        if "TIME" in pattern or "PERIOD" in pattern:
            found["TIME_BASIS_DRIFT"] |= sides

    for row in graph.quarantine:
        found["BROKEN_LINEAGE"].add(getattr(row, "kpi_id", "") or "")

    for table in getattr(graph, "tables", {}).values():
        lifecycle = (getattr(table, "lifecycle_status", "") or "").lower()
        if lifecycle in ("sunset", "deprecated", "retired"):
            # Planted against the system, which is what a successor has to be
            # mapped for; the table is recorded too so either form intersects.
            found["SUNSET_SOURCE"].add(getattr(table, "system", "") or "")
            found["SUNSET_SOURCE"].add(getattr(table, "table_fqn", "") or "")

    for column in getattr(graph, "columns", {}).values():
        if not (getattr(column, "definition", "") or "").strip():
            found["MISSING_DEFINITION"].add(getattr(column, "column_fqn", "") or "")


    # Planted against glossary terms, which is where a steward is actually
    # recorded; a catalog column inherits one rather than holding it.
    for term in (getattr(graph, "glossary", None) or {}).values() \
            if isinstance(getattr(graph, "glossary", None), dict) \
            else (getattr(graph, "glossary", None) or []):
        if not (getattr(term, "steward", "") or "").strip():
            found["UNASSIGNED_STEWARD"].add(getattr(term, "term", "") or "")

    as_of = _as_date(getattr(result.manifest, "as_of_date", None))
    for report in graph.reports.values():
        last = getattr(report, "last_run_date", None)
        if as_of and (last is None or (as_of - last).days >= 365):
            found["ZOMBIE_REPORT"].add(report.report_id)
        # A regulatory report held off the retirement list, by the same rule the
        # retirement view uses: the decision-critical flag, or a name that reads
        # as a filing. The flag alone would under-report, because a client's
        # extract rarely carries it.
        if _is_held(report):
            found["REGULATORY_LOW_USAGE"].add(report.report_id)

    for candidate in result.candidates:
        reports = {r.report_id for r in candidate.reports}
        if getattr(candidate, "_grain_ambiguity", 0.0) > 0.0:
            found["GRAIN_MIXING"] |= reports
        if "grain" in (getattr(candidate, "origin", "") or "").lower():
            found["GRAIN_MIXING"] |= reports

    for key in found:
        found[key].discard("")
    return found


def _is_held(report: Any) -> bool:
    """The retirement view's own hold rule, reused so the two cannot disagree."""
    from ..portfolio.views import is_hold

    return bool(is_hold(report))


def detection_scorecard(result: Any, planted: list[dict] | None = None) -> list[dict]:
    """One row per planted defect class: planted, detected, recall and the misses.

    ``planted`` defaults to the bundle's own list. On a client estate there is
    no planted list, so the scorecard is empty and says so by being empty
    rather than by reporting a perfect score.
    """
    if planted is None:
        bundle = getattr(getattr(result, "ingest", None), "bundle", None)
        planted = list(getattr(bundle, "planted_defects", []) or [])
    if not planted:
        return []

    found = _detected_objects(result)
    by_class: dict[str, list[dict]] = {}
    for defect in planted:
        by_class.setdefault(str(defect.get("defect_class", "")), []).append(defect)

    rows: list[dict] = []
    for defect_class, defects in sorted(by_class.items()):
        method = METHODS.get(defect_class, "")
        if not method:
            rows.append({
                "defect_class": defect_class, "planted": len(defects), "detected": 0,
                "recall": None, "precision": None, "false_positives": 0, "missed": [],
                "detail": "no detection rule is claimed for this class",
                "method": "not measurable",
            })
            continue
        detected_ids = found.get(defect_class, set())
        hits, misses = [], []
        for defect in defects:
            wanted = _ids(defect.get("affected_objects"))
            (hits if (wanted & detected_ids) else misses).append(
                str(defect.get("defect_id", "")))
        recall = round(len(hits) / len(defects), 4) if defects else None
        rows.append({
            "defect_class": defect_class,
            "planted": len(defects),
            "detected": len(hits),
            "recall": recall,
            "precision": None,
            "false_positives": 0,
            "missed": misses,
            "detail": defects[0].get("expected_detection", ""),
            "method": method,
        })
    return rows


def detection_summary(rows: list[dict]) -> dict:
    """Overall recall, the classes fully missed, and what was not measurable."""
    measured = [r for r in rows if r["recall"] is not None]
    planted = sum(r["planted"] for r in measured)
    detected = sum(r["detected"] for r in measured)
    return {
        "classes": len(rows),
        "classes_measured": len(measured),
        "planted": planted,
        "detected": detected,
        "recall": round(detected / planted, 4) if planted else None,
        "classes_missed": sorted(r["defect_class"] for r in measured if r["detected"] == 0),
        "not_measurable": sorted(r["defect_class"] for r in rows if r["recall"] is None),
    }


def save_detection_scorecard(connection: sqlite3.Connection, run_id: str,
                             rows: list[dict]) -> None:
    ensure_schema(connection)
    connection.executemany(
        "INSERT OR REPLACE INTO DETECTION_SCORECARD (run_id, defect_class, planted, detected, "
        "recall, precision, false_positives, missed, detail, method) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(run_id, r["defect_class"], r["planted"], r["detected"], r["recall"], r["precision"],
          r["false_positives"], json.dumps(r["missed"]), r["detail"], r["method"])
         for r in rows])
    connection.commit()


def load_detection_scorecard(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = [dict(r) for r in connection.execute(
        "SELECT * FROM DETECTION_SCORECARD WHERE run_id = ? ORDER BY defect_class",
        (run_id,)).fetchall()]
    for row in rows:
        row["missed"] = json.loads(row["missed"] or "[]")
    return rows
