"""The Critic: what a reviewer will reject (specification section 10.1).

Checks each candidate against the hard gates and the Data Product Factory
Stage 1 and Stage 2 exit criteria, and lists the findings. The Critic never
changes a score and never changes a status.

Findings come in two kinds (review finding R-53). *Standing* findings are true
of every candidate until a human acts - AI-drafted decisions, AI-drafted names -
and are collapsed to one line with counts so they cannot drown the rest.
*Candidate-specific* findings are what distinguishes one card from another.
From both, a readiness checklist per DPF stage says how many actions, and by
whom, stand between this candidate and Stage 2 exit; the percentage is kept on
the candidate for the effort model and the wave planner.
"""
from __future__ import annotations

from collections import Counter

from ..canonicalize.grouping import CanonicalizationResult
from ..config import GATE_LINEAGE_FLOOR, EngineConfig
from ..models import Candidate, CritiqueFinding, KnowledgeGraph

# The Stage 1 and Stage 2 exit criteria the engine can check without a human.
STAGE_CRITERIA = (
    ("S1-consumer", "Stage 1 names a real consumer, not a report audience"),
    ("S1-decision", "Stage 1 records the blocked decision and its consequence"),
    ("S1-latency", "Stage 1 records a latency tolerance"),
    ("S2-scope", "Stage 2 scope lists the metrics in and the metrics out"),
    ("S2-value", "Stage 2 states a value hypothesis with a measurable claim"),
    ("S2-owner", "Stage 2 names an owner and a steward"),
    ("S2-grain", "Stage 2 declares one evaluation grain"),
    ("S2-definitions", "Stage 2 metrics have definitions a steward can sign"),
)

# Findings that hold for every candidate until a human acts; one line, with counts.
STANDING_CRITERION = "standing"

# Owner roles per specification section 15.1 and the DPF stage owners.
ROLE_CONSUMER = "Named consumer"
ROLE_STEWARD = "Domain steward"
ROLE_COUNCIL = "Data product council"
ROLE_ENGINE = "Engine team"
ROLE_PRIVACY = "Privacy officer"
ROLE_CATALOG = "Catalog admin"


def critique(candidates: list[Candidate], result: CanonicalizationResult,
             graph: KnowledgeGraph, config: EngineConfig | None = None) -> None:
    config = config or EngineConfig()
    for candidate in candidates:
        findings, standing = _critique_one(candidate, result, graph, config)
        candidate.critique = findings + _collapse_standing(candidate, standing)
        checklist = readiness_checklist(candidate, result, graph, config)
        setattr(candidate, "_readiness", checklist["percent"])
        candidate.narrative["readiness"] = checklist


def _collapse_standing(candidate: Candidate, standing: dict[str, int]) -> list[CritiqueFinding]:
    """One informational line for everything that only a human signature clears."""
    if not standing:
        return []
    parts = [f"{count} {label}" for label, count in sorted(standing.items())]
    return [CritiqueFinding(
        candidate.candidate_id, STANDING_CRITERION,
        "Standing until a human acts: " + "; ".join(parts), "info")]


def _critique_one(candidate: Candidate, result: CanonicalizationResult,
                  graph: KnowledgeGraph,
                  config: EngineConfig) -> tuple[list[CritiqueFinding], dict[str, int]]:
    findings: list[CritiqueFinding] = []
    standing: dict[str, int] = {}
    metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]

    def add(criterion: str, finding: str, severity: str) -> None:
        findings.append(CritiqueFinding(candidate.candidate_id, criterion, finding, severity))

    # ---- hard gates ---------------------------------------------------
    for gate in (candidate.score.gates if candidate.score else []):
        if not gate.passed:
            severity = "blocker" if gate.gate == "G4" else "major"
            add(f"{gate.gate} {gate.name}", f"{gate.detail}. {gate.effect}".strip(), severity)

    # ---- Stage 1 ------------------------------------------------------
    if not candidate.decisions_drafted:
        add("S1-consumer", "No business unit could be attributed, so no decision register "
                           "entry could be drafted", "blocker")
    else:
        unconfirmed = [d for d in candidate.decisions_drafted if d.status == "AI_DRAFT"]
        if unconfirmed:
            standing["decision-register drafts to confirm with the named consumer "
                     "(blocked decision, latency, consequence)"] = len(unconfirmed)
    thin = [c for c in candidate.consumers if c.users < 2]
    if thin:
        add("S1-consumer",
            "business units with a single user are a report audience, not a consumer: "
            + ", ".join(sorted(c.business_unit for c in thin))[:160], "minor")

    # ---- Stage 2 ------------------------------------------------------
    if not candidate.owner_candidate:
        add("S2-owner", "No owner candidate: the catalog has no owner on the dominant "
                        "domain's tables", "major")
    if not candidate.steward_candidate:
        add("S2-owner", "No steward candidate: no business term on the candidate's fact "
                        "tables carries a steward", "major")
    if candidate.grain in ("", "unknown"):
        add("S2-grain", "Grain could not be inferred from catalog primary keys", "blocker")
    undefined = [m for m in metrics if not m.definition or m.opaque]
    if undefined:
        add("S2-definitions",
            f"{len(undefined)} metrics have no definition a steward can sign, including "
            + ", ".join(m.canonical_name for m in undefined[:4]), "major")
    drafted_names = [m for m in metrics if m.name_status == "AI_DRAFT"]
    if drafted_names:
        standing["AI-drafted metric names a steward must accept before catalog import"] = \
            len(drafted_names)
    if candidate.name_status == "AI_DRAFT":
        standing["AI-drafted product name and purpose"] = 1
    if len(metrics) < config.cluster.min_metrics:
        add("S2-scope", f"Only {len(metrics)} canonical metrics: below the floor of "
                        f"{config.cluster.min_metrics}, this is a feature of another product "
                        "rather than a product", "major")
    if not candidate.reports:
        add("S2-value", "No reports are covered, so the value hypothesis has nothing behind it",
            "blocker")

    # ---- evidence and lineage ----------------------------------------
    if candidate.score and not candidate.evidence:
        add("S2-value", "Score row carries no evidence rows and must not be published",
            "blocker")
    lineage = next((f.value for f in (candidate.score.features if candidate.score else [])
                    if f.feature == "lineage_completeness"), 1.0)
    if lineage < GATE_LINEAGE_FLOOR:
        add("S3-sources", f"Lineage completeness {lineage:.2f} is below the {GATE_LINEAGE_FLOOR:.2f} "
                          "floor; Stage 3 source discovery will stall on the gap list", "major")
    open_conflicts = _open_conflicts(candidate, result)
    if open_conflicts:
        stewards = Counter(c.steward_id or "UNASSIGNED" for c in open_conflicts)
        load = ", ".join(f"{s} x{n}" for s, n in stewards.most_common(3))
        add("S2-definitions",
            f"{len(open_conflicts)} conflicting definitions are unresolved; a steward must "
            f"adjudicate before the semantic model can be certified (load: {load})", "major")
    pii = [a for a in candidate.attributes if a.pii_flag]
    if pii:
        add("S9-privacy",
            f"{len(pii)} PII attributes are in scope, so Stage 9 privacy review applies: "
            + ", ".join(sorted(a.name for a in pii))[:160], "minor")
    probable = [a for a in candidate.attributes if 0 < a.confidence < 0.80]
    if probable:
        add("S3-sources",
            f"{len(probable)} attributes rest on probable lineage only and are shown as "
            "unconfirmed", "minor")
    return findings, standing


def _open_conflicts(candidate: Candidate, result: CanonicalizationResult) -> list:
    wanted = set(candidate.conflicts)
    return [c for c in result.conflicts
            if c.conflict_id in wanted and c.resolution_status == "OPEN"]


# --------------------------------------------------------------------------
# Readiness checklist (R-53)
# --------------------------------------------------------------------------

def readiness_checklist(candidate: Candidate, result: CanonicalizationResult,
                        graph: KnowledgeGraph, config: EngineConfig | None = None) -> dict:
    """DPF readiness per stage: items outstanding, who must act, adjudication load.

    Each item is one human action; the percentage is items done over items in
    scope. Steward adjudications are counted per conflict because that is the
    unit of steward work, so a candidate with 32 open conflicts is honestly
    further from Stage 2 than one with none.
    """
    config = config or EngineConfig()
    metrics = [result.metrics[m] for m in candidate.metric_ids if m in result.metrics]
    gates = {g.gate: g for g in (candidate.score.gates if candidate.score else [])}
    open_conflicts = _open_conflicts(candidate, result)
    stages: list[dict] = []

    def stage(number: int, name: str, items: list[tuple[str, bool, str, int]]) -> None:
        rows = [{"item": item, "done": done, "who_must_act": who, "load": load}
                for item, done, who, load in items]
        stages.append({
            "stage": number, "name": name, "items": rows,
            "outstanding": sum(r["load"] for r in rows if not r["done"]),
            "in_scope": sum(r["load"] for r in rows),
        })

    consumers_ok = bool(gates.get("G1") and gates["G1"].passed)
    drafts = [d for d in candidate.decisions_drafted if d.status == "AI_DRAFT"]
    stage(1, "Consumption Discovery", [
        ("named consumer confirmed (G1)", consumers_ok, ROLE_COUNCIL, 1),
        ("blocked decision, latency and consequence confirmed per business unit",
         bool(candidate.decisions_drafted) and not drafts, ROLE_CONSUMER,
         max(1, len(candidate.decisions_drafted))),
    ])
    undefined = [m for m in metrics if not m.definition or m.opaque]
    drafted_names = [m for m in metrics if m.name_status == "AI_DRAFT"]
    stage(2, "Charter", [
        ("product name and purpose accepted", candidate.name_status != "AI_DRAFT",
         ROLE_STEWARD, 1),
        ("owner named", bool(candidate.owner_candidate), ROLE_COUNCIL, 1),
        ("steward named", bool(candidate.steward_candidate), ROLE_STEWARD, 1),
        ("one evaluation grain (G3)", bool(gates.get("G3") and gates["G3"].passed),
         ROLE_ENGINE, 1),
        ("metric names accepted", not drafted_names, ROLE_STEWARD, max(1, len(drafted_names))),
        ("metric definitions a steward can sign", not undefined, ROLE_STEWARD,
         max(1, len(undefined))),
        ("conflicting definitions adjudicated", not open_conflicts, ROLE_STEWARD,
         max(1, len(open_conflicts))),
        ("value hypothesis with a baseline and target", bool(candidate.reports), ROLE_COUNCIL, 1),
    ])
    quarantined = [q for q in graph.quarantine if any(q.kpi_id in m.kpi_ids for m in metrics)]
    probable = [a for a in candidate.attributes if 0 < a.confidence < 0.80]
    sunset_ok = bool(gates.get("G4") and gates["G4"].passed)
    stage(3, "Source Discovery", [
        ("lineage above the floor (G2)", bool(gates.get("G2") and gates["G2"].passed),
         ROLE_ENGINE, 1),
        ("quarantined lineage rows resolved", not quarantined, ROLE_CATALOG,
         max(1, len(quarantined))),
        ("probable-lineage attributes confirmed", not probable, ROLE_CATALOG,
         max(1, len(probable))),
        ("no sunset source without a successor (G4)", sunset_ok, ROLE_CATALOG, 1),
    ])
    missing_definitions = [a for a in candidate.attributes
                           if a.role == "operand" and not a.definition]
    stage(5, "Attribute Register", [
        ("operand columns carry a catalog definition", not missing_definitions, ROLE_STEWARD,
         max(1, len(missing_definitions))),
    ])
    pii = [a for a in candidate.attributes if a.pii_flag]
    stage(9, "Privacy", [
        ("privacy review where PII is in scope", not pii, ROLE_PRIVACY, 1 if pii else 1),
    ])
    if not pii:
        stages[-1]["items"][0]["done"] = True
        stages[-1]["outstanding"] = 0

    in_scope = sum(s["in_scope"] for s in stages)
    outstanding = sum(s["outstanding"] for s in stages)
    percent = round(100.0 * (in_scope - outstanding) / in_scope, 1) if in_scope else 0.0
    by_role: Counter = Counter()
    for s in stages:
        for row in s["items"]:
            if not row["done"]:
                by_role[row["who_must_act"]] += row["load"]
    return {
        "percent": percent,
        "outstanding_actions": outstanding,
        "actions_in_scope": in_scope,
        "adjudication_load": len(open_conflicts),
        "actions_by_role": dict(sorted(by_role.items())),
        "stages": stages,
    }


def split_findings(candidate: Candidate) -> dict:
    """Standing line and candidate-specific blockers, for a card or a status pack."""
    standing = [f for f in candidate.critique if f.criterion == STANDING_CRITERION]
    specific = [f for f in candidate.critique if f.criterion != STANDING_CRITERION]
    return {
        "standing": standing[0].finding if standing else "",
        "blockers": [f for f in specific if f.severity == "blocker"],
        "specific": specific,
        "counts": dict(Counter(f.severity for f in specific)),
    }
