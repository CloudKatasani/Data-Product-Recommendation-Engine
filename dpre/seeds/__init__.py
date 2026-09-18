"""Seed artifacts for the Data Product Factory (specification section 9.2).

Each seed is pre-filled from the candidate record and names exactly what a human
must still add. Nothing marked AI_DRAFT may reach the catalog or the DPF without
a reviewer accepting it.
"""
from __future__ import annotations

from pathlib import Path

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, KnowledgeGraph
from .attribute_register import attribute_register_rows, write_attribute_register
from .catalog_payload import catalog_payload, write_catalog_payload
from .charter import charter_draft, write_charter
from .decision_register import decision_register, write_decision_register
from .retirement import retirement_list, write_retirement_list
from .semantic_model import semantic_model, write_semantic_model
from .source_inventory import source_inventory, write_source_inventory

__all__ = [
    "decision_register", "charter_draft", "source_inventory", "attribute_register_rows",
    "semantic_model", "retirement_list", "catalog_payload", "build_seeds", "write_seeds",
    "SEED_INDEX",
]

SEED_INDEX = (
    {"stage": 1, "name": "Consumption Discovery", "artifact": "decision-register.yaml",
     "prefilled": "Consumer persona, cadence, questions from report titles and KPI labels",
     "human_adds": "The blocked decision, latency tolerance, consequence of not deciding"},
    {"stage": 2, "name": "Charter", "artifact": "charter.yaml",
     "prefilled": "Archetype, tier, scope in and out, value hypothesis",
     "human_adds": "Success measures, sign-off"},
    {"stage": 3, "name": "Source Discovery", "artifact": "source-inventory.yaml",
     "prefilled": "Tables, systems, SoR designation, gap log from unresolved lineage",
     "human_adds": "Profiling statistics"},
    {"stage": 5, "name": "Attribute Register", "artifact": "attribute-register.xlsx",
     "prefilled": "Attribute name, definition, lineage, type, nullability, sensitivity, steward",
     "human_adds": "Allowed values, derivation review, sign-off"},
    {"stage": 6, "name": "Semantic Model", "artifact": "semantic-model.yaml",
     "prefilled": "Entities, dimensions from filter variants, metrics with expressions and "
                  "Stage 1 question links",
     "human_adds": "Join validation, metric certification"},
    {"stage": 12, "name": "Operate", "artifact": "retirement-list.csv",
     "prefilled": "Reports to retire on publication, with owners to notify",
     "human_adds": "Notification and cut-over dates"},
)


def build_seeds(candidate: Candidate, result: CanonicalizationResult, graph: KnowledgeGraph,
                catalog: str = "collibra") -> dict:
    """Every seed for one candidate, as in-memory structures."""
    return {
        "decision_register": decision_register(candidate),
        "charter": charter_draft(candidate, result, graph),
        "source_inventory": source_inventory(candidate, result, graph),
        "attribute_register": attribute_register_rows(candidate),
        "semantic_model": semantic_model(candidate, result, graph),
        "retirement_list": retirement_list(candidate),
        "catalog_payload": catalog_payload(candidate, result, graph, catalog),
    }


def write_seeds(candidate: Candidate, result: CanonicalizationResult, graph: KnowledgeGraph,
                out_dir: str | Path, catalog: str = "collibra") -> list[Path]:
    """Write every seed for one candidate into ``out_dir``."""
    out_dir = Path(out_dir) / candidate.candidate_id
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [
        write_decision_register(candidate, out_dir / "stage1-decision-register.yaml"),
        write_charter(candidate, result, graph, out_dir / "stage2-charter.yaml"),
        write_source_inventory(candidate, result, graph, out_dir / "stage3-source-inventory.yaml"),
        write_attribute_register(candidate, out_dir / "stage5-attribute-register.xlsx"),
        write_semantic_model(candidate, result, graph, out_dir / "stage6-semantic-model.yaml"),
        write_retirement_list(candidate, out_dir / "stage12-retirement-list.csv"),
        write_catalog_payload(candidate, result, graph, out_dir / f"{catalog}-payload.json", catalog),
    ]
    return written
