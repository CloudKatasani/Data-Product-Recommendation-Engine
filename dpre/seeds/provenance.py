"""Run provenance stamped on every seed (review finding R-36).

A steward opening a charter YAML or a catalog import payload must be able to
tell, from the file alone, whether it came from the client's estate or from a
synthetic demonstration pack. Every seed header and every seed body therefore
carries ``synthetic`` and ``generation_id``, and the catalog payload lists
``run is synthetic`` under ``import_blocked_by`` so a demo payload can never be
imported by accident (specification sections 9.3 and 17.3).

The flag is read from the knowledge graph's report records, which the Ingestor
stamped from the extracts, so a seed cannot claim to be real when its inputs
were not.
"""
from __future__ import annotations

from typing import Any

from ..models import Candidate, KnowledgeGraph

SYNTHETIC_BANNER = "SYNTHETIC DEMONSTRATION DATA - not client data; do not import or act on it"
SYNTHETIC_IMPORT_BLOCK = "run is synthetic"


def run_provenance(candidate: Candidate, graph: KnowledgeGraph | None = None,
                   manifest: Any = None) -> dict[str, Any]:
    """``{run_id, as_of_date, synthetic, generation_id}`` for one candidate's seeds.

    ``manifest`` (a RunManifest or a dict) wins when given; otherwise the graph's
    report records decide, so the seeds work with or without the pipeline.
    """
    synthetic = False
    generation_id = ""
    if manifest is not None:
        synthetic = bool(_get(manifest, "synthetic", False))
        generation_id = str(_get(manifest, "generation_id", "") or "")
    elif graph is not None:
        for report in graph.reports.values():
            if report.synthetic:
                synthetic = True
            if report.generation_id and not generation_id:
                generation_id = report.generation_id
            if synthetic and generation_id:
                break
    return {
        "run_id": candidate.run_id,
        "as_of_date": candidate.as_of_date,
        "synthetic": synthetic,
        "generation_id": generation_id,
    }


def _get(obj: Any, key: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def seed_header(base: str, provenance: dict[str, Any]) -> str:
    """The comment block at the top of a YAML seed, with the synthetic banner first
    when the run is synthetic so it cannot be missed."""
    lines = []
    if provenance.get("synthetic"):
        lines.append(SYNTHETIC_BANNER)
        lines.append(f"generation_id: {provenance.get('generation_id') or 'unknown'}")
    lines.append(base)
    lines.append(f"run_id: {provenance.get('run_id', '')}  as_of: {provenance.get('as_of_date', '')}"
                 f"  synthetic: {'true' if provenance.get('synthetic') else 'false'}")
    return "\n".join(lines)


def provenance_block(provenance: dict[str, Any]) -> dict[str, Any]:
    """The ``provenance`` mapping written into every seed body."""
    block = {
        "run_id": provenance.get("run_id", ""),
        "as_of_date": provenance.get("as_of_date", ""),
        "synthetic": bool(provenance.get("synthetic")),
        "generation_id": provenance.get("generation_id", "") or None,
        "written_by": "Data Product Recommendation Engine",
    }
    if block["synthetic"]:
        block["banner"] = SYNTHETIC_BANNER
    return block
