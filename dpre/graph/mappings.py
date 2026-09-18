"""Manual column mappings: a reviewer confirms a near-miss the resolver found.

Review finding R-46: the resolver computes the best near-miss for every
LOW_CONFIDENCE row and then buries it in a detail string. A catalog admin who
looks at "best 0.78 against arrears_amount_usd" and agrees should be able to
say so once, and have every later run honour it. The confirmation lives in a
run-independent ledger table so it survives re-runs, and it is consulted
*before* ER-1 (specification section 4.2), because a human confirmation is
stronger evidence than any rule.

The table is defined here and created by ``ensure_schema``; the pipeline loads
the mappings with ``load_manual_mappings`` and passes them to ``build_graph``.
"""
from __future__ import annotations

import sqlite3

from ..util.text import normalize_identifier

SCHEMA = """
CREATE TABLE IF NOT EXISTS MANUAL_COLUMN_MAPPING (
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_reference TEXT NOT NULL,
    column_fqn TEXT NOT NULL,
    confirmed_by TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    run_id TEXT,
    note TEXT,
    active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS IX_MANUAL_MAPPING_REF ON MANUAL_COLUMN_MAPPING (raw_reference);
"""

MANUAL_RULE = "ER-0"          # a human confirmation outranks every automatic rule
MANUAL_CONFIDENCE = 1.0


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def mapping_key(raw_reference: str) -> str:
    """The lookup key: the raw lineage reference after case and quote normalisation."""
    return normalize_identifier(raw_reference)


def record_manual_mapping(connection: sqlite3.Connection, raw_reference: str, column_fqn: str,
                          confirmed_by: str, confirmed_at: str, run_id: str = "",
                          note: str = "") -> dict:
    """Append one confirmation. The reviewer must be named; the engine never confirms."""
    if not confirmed_by:
        raise PermissionError("a manual mapping must name the person who confirmed it")
    if not raw_reference or not column_fqn:
        raise ValueError("a manual mapping needs both the raw reference and the target column")
    ensure_schema(connection)
    cursor = connection.execute(
        "INSERT INTO MANUAL_COLUMN_MAPPING (raw_reference, column_fqn, confirmed_by, "
        "confirmed_at, run_id, note, active) VALUES (?,?,?,?,?,?,1)",
        (mapping_key(raw_reference), column_fqn, confirmed_by, confirmed_at, run_id, note))
    connection.commit()
    return {"mapping_id": cursor.lastrowid, "raw_reference": mapping_key(raw_reference),
            "column_fqn": column_fqn, "confirmed_by": confirmed_by,
            "confirmed_at": confirmed_at, "run_id": run_id, "note": note}


def load_manual_mappings(connection: sqlite3.Connection) -> dict[str, str]:
    """``{normalised raw reference: column_fqn}``; the latest confirmation wins."""
    ensure_schema(connection)
    rows = connection.execute(
        "SELECT raw_reference, column_fqn FROM MANUAL_COLUMN_MAPPING WHERE active = 1 "
        "ORDER BY mapping_id").fetchall()
    out: dict[str, str] = {}
    for row in rows:
        out[row[0]] = row[1]
    return out
