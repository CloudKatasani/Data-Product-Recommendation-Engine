"""Candidate dependencies as tracked items, not hidden fields (R-11).

Four kinds. ``candidate``: a composite or grain child needs its parts first.
``entity_master``: any candidate whose sources include a hub table needs that
Entity Master, otherwise every product re-derives the same account or customer
key. ``successor_system``: a sunset source needs its successor mapped (owner:
catalog admin) or migrated. ``steward_adjudication``: open conflicts need a
named steward's decision. Each row names an owner role from specification
section 15.1 and a due hint where the run knows one (a sunset date).
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from ..models import Candidate

ROLE_COUNCIL = "Data product council"
ROLE_STEWARD = "Domain steward"
ROLE_CATALOG = "Catalog admin"
ROLE_ENGINE = "Engine team"

SCHEMA = """
CREATE TABLE IF NOT EXISTS DP_CANDIDATE_DEPENDENCY (
    run_id TEXT, candidate_id TEXT, type TEXT, target TEXT, owner_role TEXT,
    due_hint TEXT, status TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS IX_DEPENDENCY ON DP_CANDIDATE_DEPENDENCY (run_id, candidate_id);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass
class DependencyRow:
    candidate_id: str
    type: str                  # candidate | entity_master | successor_system | steward_adjudication
    target: str
    owner_role: str
    due_hint: str = ""
    status: str = "OPEN"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_dependencies(result: Any) -> list[DependencyRow]:
    """Every dependency the run can see, in a stable order."""
    candidates: list[Candidate] = result.candidates
    graph = result.graph
    by_id = {c.candidate_id: c for c in candidates}
    masters = _entity_masters(candidates)
    open_conflicts = {c.conflict_id: c for c in result.canonical.conflicts
                      if c.resolution_status == "OPEN"}
    rows: list[DependencyRow] = []

    for candidate in sorted(candidates, key=lambda c: c.candidate_id):
        cid = candidate.candidate_id
        # -- candidate edges: parent first, then declared depends_on --------
        declared: list[str] = []
        if candidate.parent_candidate_id:
            declared.append(candidate.parent_candidate_id)
        declared += [d for d in candidate.depends_on if d not in declared]
        for target in declared:
            if target == cid:
                continue
            other = by_id.get(target)
            rows.append(DependencyRow(
                cid, "candidate", target, ROLE_COUNCIL, "",
                other.status if other else "MISSING",
                (f"{'parent' if target == candidate.parent_candidate_id else 'component'}: "
                 f"{other.proposed_name}" if other else "target not in this run")))
        # -- entity-master edges -------------------------------------------
        if candidate.origin != "entity_master":
            sources = {s.table_fqn for s in candidate.sources}
            for table_fqn, master in sorted(masters.items()):
                if table_fqn in sources and master.candidate_id != cid \
                        and master.candidate_id not in declared:
                    rows.append(DependencyRow(
                        cid, "entity_master", master.candidate_id, ROLE_STEWARD, "",
                        master.status, f"joins on hub table {table_fqn}; the "
                        f"{master.grain} master must publish its key first"))
        # -- successor-system edges ----------------------------------------
        seen_systems: set[str] = set()
        for source in candidate.sources:
            if source.lifecycle_status != "sunset" or source.system in seen_systems:
                continue
            seen_systems.add(source.system)
            table = graph.tables.get(source.table_fqn)
            sunset_date = table.sunset_date if table else ""
            if source.successor_system:
                rows.append(DependencyRow(
                    cid, "successor_system", source.successor_system, ROLE_ENGINE, sunset_date,
                    "OPEN", f"{source.system} sunsets; lineage must be re-pointed to "
                            f"{source.successor_system} before cut-over"))
            else:
                rows.append(DependencyRow(
                    cid, "successor_system", source.system, ROLE_CATALOG, sunset_date,
                    "BLOCKING", f"{source.system} sunsets with no successor mapped in the "
                                "catalog; candidate is Blocked (G4) until one is"))
        # -- steward adjudication edges, one per steward -------------------
        stewards: Counter = Counter()
        for conflict_id in candidate.conflicts:
            conflict = open_conflicts.get(conflict_id)
            if conflict is not None:
                stewards[conflict.steward_id or "UNASSIGNED"] += 1
        for steward, count in sorted(stewards.items()):
            rows.append(DependencyRow(
                cid, "steward_adjudication", steward, ROLE_STEWARD, "", "OPEN",
                f"{count} open conflict(s) to adjudicate"
                + (" - no steward assigned" if steward == "UNASSIGNED" else "")))
    return rows


def _entity_masters(candidates: list[Candidate]) -> dict[str, Candidate]:
    """Hub table -> the Entity Master candidate built on it."""
    out: dict[str, Candidate] = {}
    for candidate in candidates:
        if candidate.origin == "entity_master":
            for source in candidate.sources:
                out.setdefault(source.table_fqn, candidate)
    return out


def dependency_graph(rows: list[DependencyRow]) -> dict[str, list[str]]:
    """candidate_id -> candidate ids it must follow (candidate and entity_master edges)."""
    edges: dict[str, list[str]] = {}
    for row in rows:
        if row.type in ("candidate", "entity_master") and row.status != "MISSING":
            edges.setdefault(row.candidate_id, [])
            if row.target not in edges[row.candidate_id]:
                edges[row.candidate_id].append(row.target)
    return edges


def dependents_of(rows: list[DependencyRow]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in rows:
        if row.type in ("candidate", "entity_master"):
            out.setdefault(row.target, [])
            if row.candidate_id not in out[row.target]:
                out[row.target].append(row.candidate_id)
    return out


def save_dependencies(connection: sqlite3.Connection, run_id: str,
                      rows: list[DependencyRow]) -> None:
    ensure_schema(connection)
    connection.execute("DELETE FROM DP_CANDIDATE_DEPENDENCY WHERE run_id = ?", (run_id,))
    connection.executemany(
        "INSERT INTO DP_CANDIDATE_DEPENDENCY VALUES (?,?,?,?,?,?,?,?)",
        [(run_id, r.candidate_id, r.type, r.target, r.owner_role, r.due_hint, r.status,
          r.detail) for r in rows])
    connection.commit()


def load_dependencies(connection: sqlite3.Connection, run_id: str,
                      candidate_id: str | None = None) -> list[dict]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    sql = "SELECT * FROM DP_CANDIDATE_DEPENDENCY WHERE run_id = ?"
    params: tuple = (run_id,)
    if candidate_id:
        sql += " AND (candidate_id = ? OR target = ?)"
        params = (run_id, candidate_id, candidate_id)
    return [dict(r) for r in connection.execute(sql + " ORDER BY candidate_id, type, target",
                                                params).fetchall()]


def to_json(rows: list[DependencyRow]) -> str:
    return json.dumps([r.to_dict() for r in rows])
