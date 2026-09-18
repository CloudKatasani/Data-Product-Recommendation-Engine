"""DPF Stage 5 seed: the attribute register workbook."""
from __future__ import annotations

from pathlib import Path

from ..models import Candidate, KnowledgeGraph
from ..util.xlsx import write_workbook
from .provenance import SYNTHETIC_BANNER, run_provenance

COLUMNS = (
    "attribute_name", "role", "business_term", "definition", "source_column",
    "data_type", "nullable", "sensitivity", "pii", "steward", "lineage_confidence",
    "allowed_values", "derivation_reviewed", "signed_off_by", "signed_off_date",
)


def attribute_register_rows(candidate: Candidate) -> list[dict]:
    rows = []
    for attribute in candidate.attributes:
        rows.append({
            "attribute_name": attribute.name,
            "role": attribute.role,
            "business_term": attribute.business_term or "MISSING - catalog gap",
            "definition": attribute.definition or "MISSING - catalog gap",
            "source_column": attribute.column_fqn,
            "data_type": attribute.data_type,
            "nullable": "Y" if attribute.nullable else "N",
            "sensitivity": attribute.sensitivity,
            "pii": "Y" if attribute.pii_flag else "N",
            "steward": attribute.steward_id or "UNASSIGNED",
            "lineage_confidence": attribute.confidence,
            "allowed_values": "TO BE COMPLETED",
            "derivation_reviewed": "TO BE COMPLETED",
            "signed_off_by": "",
            "signed_off_date": "",
        })
    return rows


def write_attribute_register(candidate: Candidate, path: str | Path,
                             graph: KnowledgeGraph | None = None) -> Path:
    rows = attribute_register_rows(candidate)
    provenance = run_provenance(candidate, graph)
    header = [
        ["Data Product Factory - Stage 5 Attribute Register (seed)"],
        [f"Candidate: {candidate.candidate_id} - {candidate.proposed_name}"],
        ["Allowed values, derivation review and sign-off are for the steward to complete."],
        [f"run_id: {provenance['run_id']}", f"as_of: {provenance['as_of_date']}",
         f"synthetic: {'TRUE' if provenance['synthetic'] else 'FALSE'}",
         f"generation_id: {provenance['generation_id']}"],
    ]
    if provenance["synthetic"]:
        header.insert(0, [SYNTHETIC_BANNER])
    header.append([])
    sheet = header + [list(COLUMNS)] + [[row.get(c) for c in COLUMNS] for row in rows]
    return write_workbook(path, {"Attribute Register": sheet})
