"""Seed artifacts for the Data Product Factory (specification section 9.2).

Each seed is pre-filled from the candidate record and names exactly what a human
must still add. Nothing marked AI_DRAFT may reach the catalog or the DPF without
a reviewer accepting it. Every seed carries the run's provenance - run id,
as-of date, whether the run was synthetic and its generation id - in its header
and its body (review finding R-36).

Beside the per-candidate seeds, ``write_run_seeds`` writes the run-level
companions: the communications a report owner, a steward and a consumer can act
on (review finding R-37), under ``<out_dir>/<run_id>/communications/``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..canonicalize.grouping import CanonicalizationResult
from ..models import Candidate, KnowledgeGraph
from .attribute_register import attribute_register_rows, write_attribute_register
from .catalog_payload import catalog_payload, write_catalog_payload
from .charter import charter_draft, write_charter
from .communications import build_communications, write_communications
from .decision_register import decision_register, write_decision_register
from .provenance import SYNTHETIC_BANNER, SYNTHETIC_IMPORT_BLOCK, run_provenance
from .retirement import retirement_list, write_retirement_list
from .semantic_model import semantic_model, write_semantic_model
from .source_inventory import source_inventory, write_source_inventory

__all__ = [
    "decision_register", "charter_draft", "source_inventory", "attribute_register_rows",
    "semantic_model", "retirement_list", "catalog_payload", "build_seeds", "write_seeds",
    "write_run_seeds", "build_communications", "write_communications", "run_provenance",
    "SEED_INDEX", "SYNTHETIC_BANNER", "SYNTHETIC_IMPORT_BLOCK",
]

SEED_INDEX = (
    {"stage": 1, "name": "Consumption Discovery", "artifact": "decision-register.yaml",
     "prefilled": "Consumer persona with confidence, cadence, questions phrased from KPI "
                  "labels and distinct report titles",
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
     "prefilled": "Covered reports with a disposition-aware action (retire, merge, rebuild, "
                  "re-point or hold) and the owner to notify",
     "human_adds": "Notification and cut-over dates"},
)

# Run-level companions, written once per run rather than once per candidate.
RUN_SEED_INDEX = (
    {"name": "Owner notification", "artifact": "communications/owner-notification-<owner>.md",
     "recipient": "report owner",
     "prefilled": "Their reports, coverage, replacing candidate, users, action, how to contest"},
    {"name": "Steward adjudication", "artifact": "communications/steward-adjudication-<steward>.md",
     "recipient": "domain steward",
     "prefilled": "Conflicts ranked by usage at stake, both expressions, the difference, the "
                  "Stage 6 decision, names awaiting acceptance"},
    {"name": "Consumer confirmation",
     "artifact": "communications/consumer-confirmation-<business_unit>.md",
     "recipient": "business unit consumer",
     "prefilled": "Persona, inferred decision, the three questions Stage 1 reserves for them"},
)


def build_seeds(candidate: Candidate, result: CanonicalizationResult, graph: KnowledgeGraph,
                catalog: str = "collibra") -> dict:
    """Every seed for one candidate, as in-memory structures."""
    provenance = run_provenance(candidate, graph)
    return {
        "provenance": provenance,
        "decision_register": decision_register(candidate, graph, provenance),
        "charter": charter_draft(candidate, result, graph, provenance),
        "source_inventory": source_inventory(candidate, result, graph, provenance),
        "attribute_register": attribute_register_rows(candidate),
        "semantic_model": semantic_model(candidate, result, graph, provenance),
        "retirement_list": retirement_list(candidate, graph, provenance),
        "catalog_payload": catalog_payload(candidate, result, graph, catalog, provenance),
    }


def write_seeds(candidate: Candidate, result: CanonicalizationResult, graph: KnowledgeGraph,
                out_dir: str | Path, catalog: str = "collibra") -> list[Path]:
    """Write every seed for one candidate into ``out_dir/<candidate_id>``."""
    out_dir = Path(out_dir) / candidate.candidate_id
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [
        write_decision_register(candidate, out_dir / "stage1-decision-register.yaml", graph),
        write_charter(candidate, result, graph, out_dir / "stage2-charter.yaml"),
        write_source_inventory(candidate, result, graph, out_dir / "stage3-source-inventory.yaml"),
        write_attribute_register(candidate, out_dir / "stage5-attribute-register.xlsx", graph),
        write_semantic_model(candidate, result, graph, out_dir / "stage6-semantic-model.yaml"),
        write_retirement_list(candidate, out_dir / "stage12-retirement-list.csv", graph),
        write_catalog_payload(candidate, result, graph, out_dir / f"{catalog}-payload.json", catalog),
    ]
    return written


def write_run_seeds(result: Any, out_dir: str | Path, catalog: str | None = None,
                    include_candidates: bool = True) -> list[Path]:
    """The run-level companion of ``write_seeds``.

    Writes the per-candidate seeds (unless ``include_candidates`` is False,
    when a caller has already written them) and the per-recipient
    communications under ``<out_dir>/<run_id>/communications/``. ``result`` is a
    RunResult: candidates, canonical, graph, manifest.
    """
    base = Path(out_dir) / result.run_id
    catalog = catalog or getattr(result.manifest, "catalog", "collibra") or "collibra"
    written: list[Path] = []
    if include_candidates:
        for candidate in result.candidates:
            written.extend(write_seeds(candidate, result.canonical, result.graph, base, catalog))
    written.extend(write_communications(result, base / "communications"))
    return written
