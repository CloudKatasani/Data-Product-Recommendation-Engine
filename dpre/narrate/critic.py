"""The Critic: what a reviewer will reject (specification section 10.1).

Checks each candidate against the hard gates and the Data Product Factory
Stage 1 and Stage 2 exit criteria, and lists the findings. The Critic never
changes a score and never changes a status.
"""
from __future__ import annotations

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


def critique(candidates: list[Candidate], result: CanonicalizationResult,
             graph: KnowledgeGraph, config: EngineConfig | None = None) -> None:
    config = config or EngineConfig()
    for candidate in candidates:
        candidate.critique = _critique_one(candidate, result, graph, config)


def _critique_one(candidate: Candidate, result: CanonicalizationResult,
                  graph: KnowledgeGraph, config: EngineConfig) -> list[CritiqueFinding]:
    findings: list[CritiqueFinding] = []
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
            add("S1-decision",
                f"{len(unconfirmed)} decision-register entries are AI drafts: the blocked "
                "decision, latency tolerance and consequence must be confirmed with the "
                "named consumer before Stage 1 can exit", "major")
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
        add("S2-definitions",
            f"{len(drafted_names)} metric names are AI drafts and cannot reach the catalog "
            "until a steward accepts them", "minor")
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
    if candidate.conflicts:
        add("S2-definitions",
            f"{len(candidate.conflicts)} conflicting definitions are unresolved; a steward must "
            "adjudicate before the semantic model can be certified", "major")
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
    return findings
