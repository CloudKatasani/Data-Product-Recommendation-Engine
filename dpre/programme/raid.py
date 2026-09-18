"""RAID log per run: risks, assumptions, issues, dependencies (R-39).

Everything here is already produced somewhere in the run - a G4 gate, a
quarantine row, an AI_DRAFT flag, a 15.1 risk - but scattered over tabs and
seeds with no owner or date. This rolls it into one register with a type, the
candidates it touches, the evidence ids behind it, an owner role from
specification section 15.1, a severity, a due hint where the run knows one,
and a status. Ids are stable hashes so a row can be tracked across runs.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import GATE_GRAIN_AMBIGUITY_MAX, GATE_LINEAGE_FLOOR, QUALITY_GATES
from ..models import Candidate
from ..util.ids import stable_id
from .dependencies import DependencyRow, build_dependencies

ROLE_STEWARD = "Domain steward"
ROLE_ENGINE = "Engine team"
ROLE_COUNCIL = "Data product council"
ROLE_CATALOG = "Catalog admin"
ROLE_PRIVACY = "Privacy officer"
ROLE_SPONSOR = "Programme sponsor"
ROLE_FINANCE = "Finance business partner"

NEAR_FLOOR = 0.10           # a gate within this of its floor is a risk, not yet a failure
TOP_ISSUE_CONFLICTS = 10    # open conflicts listed individually, by usage at stake

SCHEMA = """
CREATE TABLE IF NOT EXISTS RAID (
    run_id TEXT, raid_id TEXT, type TEXT, title TEXT, candidate_ids TEXT, evidence_ids TEXT,
    owner_role TEXT, severity TEXT, due_hint TEXT, status TEXT, detail TEXT, source TEXT,
    PRIMARY KEY (run_id, raid_id)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass
class RaidRow:
    id: str
    type: str                        # Risk | Assumption | Issue | Dependency
    title: str
    candidate_ids: list[str]
    evidence_ids: list[str]
    owner_role: str
    severity: str                    # high | medium | low
    due_hint: str = ""
    status: str = "OPEN"
    detail: str = ""
    source: str = ""                 # where in the run this came from

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_raid(result: Any, dependencies: list[DependencyRow] | None = None,
               value_assumption_version: str = "") -> list[RaidRow]:
    run_id = result.manifest.run_id
    candidates: list[Candidate] = result.candidates
    dependencies = dependencies if dependencies is not None else build_dependencies(result)
    rows: list[RaidRow] = []
    rows += _risks_from_gates(run_id, candidates, result)
    rows += _risks_from_15_1(run_id, result, candidates)
    rows += _assumptions(run_id, candidates, value_assumption_version)
    rows += _issues(run_id, result, candidates)
    rows += _dependencies(run_id, dependencies, candidates)
    order = {"Risk": 0, "Assumption": 1, "Issue": 2, "Dependency": 3}
    sev = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (order[r.type], sev[r.severity], r.title, r.id))
    return rows


# --------------------------------------------------------------------------

def _rid(run_id: str, kind: str, *key: object) -> str:
    return stable_id("RAID", run_id, kind, *key)


def _risks_from_gates(run_id: str, candidates: list[Candidate], result: Any) -> list[RaidRow]:
    rows: list[RaidRow] = []
    for candidate in sorted(candidates, key=lambda c: c.candidate_id):
        if not candidate.score:
            continue
        cid = candidate.candidate_id
        gates = {g.gate: g for g in candidate.score.gates}
        features = {f.feature: f for f in candidate.score.features}
        g4 = gates.get("G4")
        if g4 and not g4.passed:
            sunset = [s for s in candidate.sources
                      if s.lifecycle_status == "sunset" and not s.successor_system]
            dates = sorted({result.graph.tables[s.table_fqn].sunset_date for s in sunset
                            if s.table_fqn in result.graph.tables
                            and result.graph.tables[s.table_fqn].sunset_date})
            rows.append(RaidRow(
                _rid(run_id, "G4", cid), "Risk",
                f"Sunset source without successor: {', '.join(sorted({s.system for s in sunset}))}",
                [cid], [s.table_fqn for s in sunset], ROLE_CATALOG, "high",
                dates[0] if dates else "", "OPEN",
                f"{candidate.proposed_name} is Blocked (G4). {g4.detail}", "gate G4"))
        health = features.get("source_health")
        if health and health.value < 1.0 and not (g4 and not g4.passed):
            rows.append(RaidRow(
                _rid(run_id, "source_health", cid), "Risk",
                f"Source health {health.value:.2f}: {health.detail}", [cid],
                [s.table_fqn for s in candidate.sources if not s.sor_flag][:8], ROLE_ENGINE,
                "medium" if health.value >= 0.5 else "high", "", "OPEN",
                "Lineage may stop at a reporting database rather than the system of record "
                "(spec 15.1); profile the source at Stage 3", "feature source_health"))
        lineage = features.get("lineage_completeness")
        if lineage and GATE_LINEAGE_FLOOR <= lineage.value < GATE_LINEAGE_FLOOR + NEAR_FLOOR:
            rows.append(RaidRow(
                _rid(run_id, "G2-near", cid), "Risk",
                f"Lineage completeness {lineage.value:.2f} is within {NEAR_FLOOR:.2f} of the "
                f"G2 floor {GATE_LINEAGE_FLOOR:.2f}", [cid],
                [q.raw_reference for q in result.graph.quarantine
                 if any(q.kpi_id in result.canonical.metrics[m].kpi_ids
                        for m in candidate.metric_ids if m in result.canonical.metrics)][:8],
                ROLE_ENGINE, "medium", "", "OPEN",
                "One more quarantined row on the next extract drops this candidate to "
                "Exploratory", "gate G2"))
        ambiguity = features.get("grain_ambiguity")
        if ambiguity and GATE_GRAIN_AMBIGUITY_MAX - NEAR_FLOOR < ambiguity.value \
                <= GATE_GRAIN_AMBIGUITY_MAX:
            rows.append(RaidRow(
                _rid(run_id, "G3-near", cid), "Risk",
                f"Grain ambiguity {ambiguity.value:.2f} is within {NEAR_FLOOR:.2f} of the "
                f"G3 ceiling {GATE_GRAIN_AMBIGUITY_MAX:.2f}", [cid],
                [m for m in candidate.metric_ids
                 if m in result.canonical.metrics
                 and result.canonical.metrics[m].grain != candidate.grain][:8],
                ROLE_ENGINE, "medium", "", "OPEN",
                "A split by grain may be needed at Stage 2", "gate G3"))
        g1 = gates.get("G1")
        qualifying = [c for c in candidate.consumers if c.users >= 2]
        if g1 and g1.passed and len(qualifying) == 1:
            rows.append(RaidRow(
                _rid(run_id, "G1-near", cid), "Risk",
                f"Single qualifying consumer: {qualifying[0].business_unit}", [cid],
                [qualifying[0].business_unit], ROLE_COUNCIL, "low", "", "OPEN",
                "G1 rests on one business unit; if it declines the candidate has no consumer",
                "gate G1"))
    return rows


def _risks_from_15_1(run_id: str, result: Any, candidates: list[Candidate]) -> list[RaidRow]:
    """The seven programme risks of section 15.1, instantiated with the run's values."""
    graph, canonical = result.graph, result.canonical
    columns = list(graph.columns.values())
    metrics = list(canonical.metrics.values())
    rows: list[RaidRow] = []
    all_ids = sorted(c.candidate_id for c in candidates)

    defined = sum(1 for c in columns if c.business_term and c.definition)
    coverage = defined / max(1, len(columns))
    rows.append(RaidRow(
        _rid(run_id, "15.1", "definitions"), "Risk",
        f"Catalog definitions sparse: {coverage:.0%} of {len(columns)} columns carry a term "
        "and a definition", all_ids,
        [c.column_fqn for c in columns if not (c.business_term and c.definition)][:10],
        ROLE_STEWARD, "high" if coverage < 0.5 else "medium" if coverage < 0.8 else "low",
        "", "OPEN", "Spec 15.1: publish the gap list; Phase 1 includes a definition-backfill "
        "sprint for the pilot domain", "spec 15.1 row 1"))

    parse_rate = float(graph.stats.get("parse_rate", 0.0))
    opaque = [m for m in metrics if m.opaque]
    rows.append(RaidRow(
        _rid(run_id, "15.1", "parser"), "Risk",
        f"Unparseable expressions: parse rate {parse_rate:.1%}, {len(opaque)} opaque metrics",
        sorted({c.candidate_id for c in candidates
                if any(m in {o.metric_id for o in opaque} for m in c.metric_ids)}),
        [m.metric_id for m in opaque][:10], ROLE_ENGINE,
        "high" if parse_rate < QUALITY_GATES["parse_rate_floor"] else
        "medium" if opaque else "low", "", "OPEN",
        "Spec 15.1: opaque tier with feasibility penalty; manual definition queue; parser "
        "extended by observed failure classes", "spec 15.1 row 2"))

    non_sor = sorted({s.table_fqn for c in candidates for s in c.sources if not s.sor_flag})
    rows.append(RaidRow(
        _rid(run_id, "15.1", "sor"), "Risk",
        f"Lineage may stop short of the system of record: {len(non_sor)} candidate source "
        "tables are not SoR",
        sorted({c.candidate_id for c in candidates if any(not s.sor_flag for s in c.sources)}),
        non_sor[:10], ROLE_ENGINE, "medium" if non_sor else "low", "", "OPEN",
        "Spec 15.1: extend upstream through catalog lineage (ER-3); else score source health "
        "as non-SoR", "spec 15.1 row 3"))

    from ..portfolio.views import is_hold
    regulatory = [r for r in graph.reports.values() if is_hold(r)]
    low_usage = [r for r in regulatory if r.run_count_12m < 12]
    rows.append(RaidRow(
        _rid(run_id, "15.1", "usage"), "Risk",
        f"Usage read as value: {len(regulatory)} regulatory or decision-critical reports, "
        f"{len(low_usage)} of them low-run", all_ids,
        [r.report_id for r in low_usage][:10], ROLE_COUNCIL,
        "high" if low_usage else "low", "", "OPEN",
        "Spec 15.1: G1 named-consumer gate; reviewers mark reports decision-critical to floor "
        "their weight; such reports are held from retirement", "spec 15.1 row 4"))

    drafted = sum(1 for m in metrics if m.name_status == "AI_DRAFT")
    rows.append(RaidRow(
        _rid(run_id, "15.1", "ai_names"), "Risk",
        f"AI-drafted names accepted unread: {drafted} of {len(metrics)} metric names are drafts",
        all_ids, [], ROLE_STEWARD, "medium" if drafted else "low", "", "OPEN",
        "Spec 15.1: AI_DRAFT blocks catalog import; naming accepted separately from "
        "candidate acceptance", "spec 15.1 row 5"))

    sizes = Counter(len(v) for v in result.cluster.communities.values())
    largest = max(sizes, default=0)
    clustered = sum(len(v) for v in result.cluster.communities.values()) or 1
    share = largest / clustered
    rows.append(RaidRow(
        _rid(run_id, "15.1", "giant"), "Risk",
        f"Giant cluster: largest community holds {largest} of {clustered} metrics ({share:.0%})",
        [c.candidate_id for c in candidates if len(c.metric_ids) == largest][:3],
        list(result.cluster.entity_master_tables), ROLE_ENGINE,
        "high" if share > 0.5 else "medium" if share > 0.3 else "low", "", "OPEN",
        "Spec 15.1: resolution sweep, grain split, entity-master extraction removes hubs",
        "spec 15.1 row 6"))

    identities = {t.owner_id for t in graph.tables.values() if t.owner_id} | \
                 {t.steward_id for t in graph.tables.values() if t.steward_id}
    owners = {r.owner for r in graph.reports.values() if r.owner}
    unmapped = sorted(o for o in owners if o not in identities)
    rows.append(RaidRow(
        _rid(run_id, "15.1", "owners"), "Risk",
        f"Report owner strings unmapped to catalog identities: {len(unmapped)} of {len(owners)}",
        [], unmapped[:10], ROLE_CATALOG, "low", "", "OPEN",
        "Spec 15.1: manual mapping table for the top 200 owners by usage; rest flagged",
        "spec 15.1 row 7"))
    return rows


def _assumptions(run_id: str, candidates: list[Candidate], value_version: str) -> list[RaidRow]:
    rows: list[RaidRow] = []
    for candidate in sorted(candidates, key=lambda c: c.candidate_id):
        cid = candidate.candidate_id
        drafts = [d for d in candidate.decisions_drafted if d.status == "AI_DRAFT"]
        if drafts:
            rows.append(RaidRow(
                _rid(run_id, "A-decisions", cid), "Assumption",
                f"{len(drafts)} AI-drafted decision-register entries: blocked decision, latency "
                "and consequence assumed from usage", [cid],
                [d.business_unit for d in drafts], "Named consumer", "medium", "", "OPEN",
                "; ".join(d.inferred_decision[:80] for d in drafts[:2]), "narrator AI_DRAFT"))
        if candidate.name_status == "AI_DRAFT":
            rows.append(RaidRow(
                _rid(run_id, "A-name", cid), "Assumption",
                f"AI-drafted name and purpose: '{candidate.proposed_name}'", [cid], [],
                ROLE_STEWARD, "low", "", "OPEN", candidate.purpose[:160], "narrator AI_DRAFT"))
    if value_version:
        rows.append(RaidRow(
            _rid(run_id, "A-value", value_version), "Assumption",
            f"Value assumptions {value_version} are illustrative until approved",
            sorted(c.candidate_id for c in candidates), [value_version], ROLE_FINANCE,
            "medium", "", "OPEN",
            "Every benefit figure multiplies engine counts by this rate card; contest the "
            "rates, not the counts", "value model"))
    return rows


def _issues(run_id: str, result: Any, candidates: list[Candidate]) -> list[RaidRow]:
    rows: list[RaidRow] = []
    graph, canonical = result.graph, result.canonical
    metric_to_candidates: dict[str, set[str]] = {}
    for candidate in candidates:
        for metric_id in candidate.metric_ids:
            metric_to_candidates.setdefault(metric_id, set()).add(candidate.candidate_id)
    kpi_to_metric = canonical.metric_by_kpi

    by_reason: dict[str, list] = {}
    for row in graph.quarantine:
        by_reason.setdefault(row.reason_code, []).append(row)
    for code, quarantined in sorted(by_reason.items()):
        touched = sorted({cid for q in quarantined
                          for cid in metric_to_candidates.get(kpi_to_metric.get(q.kpi_id, ""), ())})
        rows.append(RaidRow(
            _rid(run_id, "I-quarantine", code), "Issue",
            f"{len(quarantined)} lineage rows quarantined: {code}", touched,
            [q.raw_reference for q in quarantined][:10], ROLE_CATALOG,
            "high" if len(quarantined) >= 25 else "medium", "", "OPEN",
            "Resolve in the catalog or the lineage extract; counts against G2 and Stage 3",
            "resolver quarantine"))

    stewardless = [m for m in canonical.metrics.values() if not m.steward_id]
    if stewardless:
        rows.append(RaidRow(
            _rid(run_id, "I-stewardless"), "Issue",
            f"{len(stewardless)} canonical metrics have no steward",
            sorted({cid for m in stewardless for cid in metric_to_candidates.get(m.metric_id, ())}),
            [m.metric_id for m in stewardless][:10], ROLE_STEWARD, "high", "", "OPEN",
            "No one can sign the definition or adjudicate a conflict; 14.2 targets 80% "
            "confirmed stewards", "canonicalizer"))

    open_conflicts = sorted((c for c in canonical.conflicts if c.resolution_status == "OPEN"),
                            key=lambda c: (-(c.usage_weight_a + c.usage_weight_b), c.conflict_id))
    for conflict in open_conflicts[:TOP_ISSUE_CONFLICTS]:
        at_stake = conflict.usage_weight_a + conflict.usage_weight_b
        rows.append(RaidRow(
            _rid(run_id, "I-conflict", conflict.conflict_id), "Issue",
            f"Open conflict '{conflict.label}' ({conflict.pattern}), usage at stake {at_stake:,.0f}",
            sorted(metric_to_candidates.get(conflict.metric_id_a, set())
                   | metric_to_candidates.get(conflict.metric_id_b, set())),
            [conflict.conflict_id, conflict.metric_id_a, conflict.metric_id_b], ROLE_STEWARD,
            "high" if at_stake >= 1000 else "medium", "", "OPEN",
            f"{conflict.difference_summary}; steward {conflict.steward_id or 'UNASSIGNED'}; "
            f"Stage 6 decision: {conflict.semantic_model_decision}", "conflict register"))
    if len(open_conflicts) > TOP_ISSUE_CONFLICTS:
        rest = open_conflicts[TOP_ISSUE_CONFLICTS:]
        rows.append(RaidRow(
            _rid(run_id, "I-conflict-rest"), "Issue",
            f"{len(rest)} further open conflicts below the top {TOP_ISSUE_CONFLICTS} by usage",
            sorted({cid for c in rest for cid in
                    metric_to_candidates.get(c.metric_id_a, set())
                    | metric_to_candidates.get(c.metric_id_b, set())}),
            [c.conflict_id for c in rest][:10], ROLE_STEWARD, "medium", "", "OPEN",
            "See the conflict heat map for the full adjudication backlog", "conflict register"))

    pii = sorted({a.column_fqn for c in candidates for a in c.attributes if a.pii_flag})
    if pii:
        rows.append(RaidRow(
            _rid(run_id, "I-pii"), "Issue",
            f"{len(pii)} PII columns in candidate scope need Stage 9 privacy review",
            sorted({c.candidate_id for c in candidates if any(a.pii_flag for a in c.attributes)}),
            pii[:10], ROLE_PRIVACY, "medium", "", "OPEN",
            "Open decision D-06: the sensitivity threshold that forces privacy review",
            "attributes"))
    return rows


def _dependencies(run_id: str, dependencies: list[DependencyRow],
                  candidates: list[Candidate]) -> list[RaidRow]:
    rows: list[RaidRow] = []
    names = {c.candidate_id: c.proposed_name for c in candidates}
    external = [d for d in dependencies if d.type in ("successor_system", "steward_adjudication")]
    for dep in external:
        if dep.type == "successor_system":
            rows.append(RaidRow(
                _rid(run_id, "D", dep.candidate_id, dep.type, dep.target), "Dependency",
                f"Successor system for {dep.target}", [dep.candidate_id], [dep.target],
                dep.owner_role, "high" if dep.status == "BLOCKING" else "medium", dep.due_hint,
                dep.status, dep.detail, "dependencies"))
        else:
            rows.append(RaidRow(
                _rid(run_id, "D", dep.candidate_id, dep.type, dep.target), "Dependency",
                f"Steward adjudication by {dep.target} for {names.get(dep.candidate_id, '')}",
                [dep.candidate_id], [dep.target], dep.owner_role,
                "high" if dep.target == "UNASSIGNED" else "medium", "", dep.status, dep.detail,
                "dependencies"))
    internal = [d for d in dependencies if d.type in ("candidate", "entity_master")]
    by_target: dict[str, list[DependencyRow]] = {}
    for dep in internal:
        by_target.setdefault(dep.target, []).append(dep)
    for target, deps in sorted(by_target.items()):
        dependents = sorted({d.candidate_id for d in deps})
        rows.append(RaidRow(
            _rid(run_id, "D-internal", target), "Dependency",
            f"{names.get(target, target)} must be built before {len(dependents)} dependent(s)",
            dependents + [target], [target], ROLE_COUNCIL,
            "high" if deps[0].status in ("Blocked",) else "medium", "",
            deps[0].status, "; ".join(sorted({d.detail for d in deps}))[:200], "dependencies"))
    return rows


def raid_for_candidate(rows: list[RaidRow], candidate_id: str) -> list[RaidRow]:
    return [r for r in rows if candidate_id in r.candidate_ids]


def save_raid(connection: sqlite3.Connection, run_id: str, rows: list[RaidRow]) -> None:
    ensure_schema(connection)
    connection.execute("DELETE FROM RAID WHERE run_id = ?", (run_id,))
    connection.executemany(
        "INSERT INTO RAID VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(run_id, r.id, r.type, r.title, json.dumps(r.candidate_ids), json.dumps(r.evidence_ids),
          r.owner_role, r.severity, r.due_hint, r.status, r.detail, r.source) for r in rows])
    connection.commit()


def load_raid(connection: sqlite3.Connection, run_id: str,
              raid_type: str | None = None) -> list[dict]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    sql = "SELECT * FROM RAID WHERE run_id = ?"
    params: tuple = (run_id,)
    if raid_type:
        sql += " AND type = ?"
        params = (run_id, raid_type)
    out = []
    for row in connection.execute(sql + " ORDER BY type, severity, title", params).fetchall():
        item = dict(row)
        item["candidate_ids"] = json.loads(item["candidate_ids"] or "[]")
        item["evidence_ids"] = json.loads(item["evidence_ids"] or "[]")
        out.append(item)
    return out


def raid_csv_rows(rows: list[RaidRow]) -> list[dict]:
    return [{
        "id": r.id, "type": r.type, "title": r.title, "severity": r.severity,
        "owner_role": r.owner_role, "due_hint": r.due_hint, "status": r.status,
        "candidate_ids": ";".join(r.candidate_ids), "evidence_ids": ";".join(r.evidence_ids),
        "detail": r.detail, "source": r.source,
    } for r in rows]
