"""Client-facing exports: the executive pack, the backlog workbook and dossiers.

The engine's outputs used to stop at the browser application, stdout, a JSON
dump and per-candidate seeds (review finding R-12). This package turns one run
into the artifacts an engagement manager hands to a Chief Data Officer and an
audit committee, without a screenshot or a re-typed number.

    from dpre.export import build_pack, write_pack

    pack = build_pack(result, store=store, engagement={"client": "Acme"},
                      enrichment=enrich_run(result, store))
    paths = write_pack(result, "out/packs", pack=pack)

Everything is stdlib-only, deterministic and pure: the same run produces the
same bytes.
"""
from __future__ import annotations

from .blocks import Document, Section
from .dossier import candidate_dossier
from .engagement import Engagement
from .enrichment import EnrichmentView
from .html import render_html
from .markdown import render_markdown
from .pack import (DOSSIER_DIR, SUMMARY_STEM, WORKBOOK_NAME, Dossier, ExecutivePack,
                   build_dossiers, build_pack, write_pack)
from .summary import OPEN_DECISIONS, SYNTHETIC_BANNER, executive_summary
from .workbook import TAB_NAMES, build_workbook, write_backlog_workbook

__all__ = [
    "build_pack", "write_pack", "ExecutivePack", "Dossier", "build_dossiers",
    "executive_summary", "candidate_dossier", "build_workbook", "write_backlog_workbook",
    "render_markdown", "render_html", "Document", "Section", "Engagement", "EnrichmentView",
    "TAB_NAMES", "OPEN_DECISIONS", "SYNTHETIC_BANNER", "SUMMARY_STEM", "WORKBOOK_NAME",
    "DOSSIER_DIR",
]
