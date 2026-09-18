"""Stewardship as a register of requests, not a guess presented as a fact
(review finding R-44).

Ownership and stewardship are different jobs. A report owner is accountable for
a report; a data steward is responsible for a definition. The engine used to
promote the first to the second when nothing better was available, which put a
name in the steward field that the named person had never agreed to and could
not have agreed to, because nobody asked them.

Two changes follow. Resolution is ordered and each step carries its own
confidence, so a steward taken from a glossary term is not the same claim as one
inferred from a report owner - and a report owner is recorded as a *suggestion*
with its own source label, never as a steward. The resolution order is
configuration (``STEWARD_RESOLUTION_ORDER``), not a chain of ``or`` expressions,
so the order does not depend on which extract happened to be read first.

This module turns what is left into a register: one row per metric that needs a
steward, with the best suggestion, where the suggestion came from, how confident
that source is, and the sentence to send the domain owner. It is a request list
for a workshop, and every row is a question rather than an assignment.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from ..config import STEWARD_RESOLUTION_ORDER, STEWARD_SUGGESTION_SOURCE

SCHEMA = """
CREATE TABLE IF NOT EXISTS STEWARDSHIP_REQUEST (
    run_id TEXT, metric_id TEXT, canonical_name TEXT, domain TEXT, grain TEXT,
    state TEXT, suggested_steward TEXT, suggestion_source TEXT, confidence REAL,
    usage_weight REAL, reports TEXT, ask TEXT,
    PRIMARY KEY (run_id, metric_id)
);
"""

#: Confidence per resolution source. The configured order is the intended
#: vocabulary; the canonicalizer's own labels are mapped onto it so a source it
#: emits is never scored zero merely because the two spellings differ.
SOURCE_CONFIDENCE: dict[str, float] = {
    **{name: confidence for name, confidence in STEWARD_RESOLUTION_ORDER},
    "business term steward": 1.00,
    "column steward": 0.85,
    "most frequent report owner": 0.30,
}

#: Sources that mean somebody accepted the role in the catalog. Kept identical
#: to the benchmark's list, so the register and the estate KPI cannot disagree
#: about what "confirmed" means.
CONFIRMED_SOURCES = frozenset({
    "business term steward", "column steward", "glossary term steward", "table steward",
})

STATES = ("confirmed", "suggested", "unassigned")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def steward_state(metric: Any) -> str:
    """Confirmed, merely suggested, or nothing at all.

    A steward is confirmed only when the source is a catalog business term or a
    column steward - somebody who accepted the role in the catalog. Anything
    inferred is a suggestion, whatever the field contains.
    """
    steward = (getattr(metric, "steward_id", "") or "").strip()
    source = (getattr(metric, "steward_source", "") or "").strip()
    if not steward:
        return "unassigned"
    if source == STEWARD_SUGGESTION_SOURCE or "suggestion" in source.lower():
        return "suggested"
    if source in CONFIRMED_SOURCES:
        return "confirmed"
    return "suggested"


def stewardship_register(result: Any) -> list[dict]:
    """One row per metric whose steward is not confirmed, worst first by usage."""
    graph, canonical = result.graph, result.canonical
    rows: list[dict] = []
    for metric in canonical.metrics.values():
        state = steward_state(metric)
        if state == "confirmed":
            continue
        report_ids = list(getattr(metric, "report_ids", []) or [])
        usage = sum(float(getattr(graph.reports.get(r), "run_count_12m", 0) or 0)
                    for r in report_ids if graph.reports.get(r))
        suggestion = (getattr(metric, "steward_id", "") or "").strip()
        source = (getattr(metric, "steward_source", "") or "").strip()
        rows.append({
            "metric_id": metric.metric_id,
            "canonical_name": metric.canonical_name,
            "domain": getattr(metric, "domain", "") or "",
            "grain": getattr(metric, "grain", "") or "",
            "state": state,
            "suggested_steward": suggestion,
            "suggestion_source": source,
            "confidence": SOURCE_CONFIDENCE.get(source, 0.0),
            "usage_weight": round(usage, 2),
            "reports": sorted(report_ids),
            "ask": _ask(metric, state, suggestion),
        })
    rows.sort(key=lambda r: (-r["usage_weight"], r["canonical_name"]))
    return rows


def _ask(metric: Any, state: str, suggestion: str) -> str:
    """The sentence to put in front of a domain owner, in their language."""
    name = metric.canonical_name
    domain = getattr(metric, "domain", "") or "this domain"
    if state == "unassigned":
        return (f"Who owns the definition of '{name}' in {domain}? "
                "Nothing in the catalog names a steward for it.")
    return (f"'{name}' has no steward in the catalog. {suggestion} owns a report that uses "
            "it, which is not the same thing. Should they be the steward, or someone else?")


def stewardship_summary(rows: list[dict], result: Any) -> dict:
    metrics = len(result.canonical.metrics)
    by_state: dict[str, int] = defaultdict(int)
    for row in rows:
        by_state[row["state"]] += 1
    confirmed = metrics - len(rows)
    return {
        "metrics": metrics,
        "confirmed": confirmed,
        "confirmed_share": round(confirmed / metrics, 4) if metrics else 0.0,
        "requests": len(rows),
        "by_state": dict(by_state),
        "usage_at_stake": round(sum(r["usage_weight"] for r in rows), 2),
    }


def save_stewardship_requests(connection: sqlite3.Connection, run_id: str,
                              rows: list[dict]) -> None:
    ensure_schema(connection)
    connection.executemany(
        "INSERT OR REPLACE INTO STEWARDSHIP_REQUEST (run_id, metric_id, canonical_name, "
        "domain, grain, state, suggested_steward, suggestion_source, confidence, usage_weight, "
        "reports, ask) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(run_id, r["metric_id"], r["canonical_name"], r["domain"], r["grain"], r["state"],
          r["suggested_steward"], r["suggestion_source"], r["confidence"], r["usage_weight"],
          json.dumps(r["reports"]), r["ask"]) for r in rows])
    connection.commit()


def load_stewardship_requests(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = [dict(r) for r in connection.execute(
        "SELECT * FROM STEWARDSHIP_REQUEST WHERE run_id = ? ORDER BY usage_weight DESC",
        (run_id,)).fetchall()]
    for row in rows:
        row["reports"] = json.loads(row["reports"] or "[]")
    return rows
