"""The executive pack: one call, one folder, everything a client is handed (R-12).

``build_pack`` assembles the three deliverables in memory and ``write_pack``
puts them on disk:

1. the executive summary, as Markdown and as one self-contained HTML file;
2. the backlog workbook, twelve tabs, through ``dpre.util.xlsx``;
3. one dossier per candidate, Markdown and HTML, readable by a business owner.

It is a pure function of the objects the pipeline already produces. The Store
is optional and read-only (reviewer decisions and the since-last-run delta) and
the programme enrichment is optional and read defensively, so the pack builds
on a bare run, on a run with a database, and on a fully enriched run - each
time saying which parts it left out rather than inventing them.

Nothing here writes a candidate status, and nothing reads the clock: a pack
re-cut from the same run is byte-identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..util.xlsx import write_workbook
from .blocks import Document
from .dossier import candidate_dossier
from .engagement import Engagement
from .enrichment import EnrichmentView
from .html import render_html
from .markdown import render_markdown
from .summary import executive_summary
from .workbook import TAB_NAMES, build_workbook

SUMMARY_STEM = "executive-summary"
WORKBOOK_NAME = "backlog-workbook.xlsx"
DOSSIER_DIR = "dossiers"


@dataclass
class Dossier:
    """One candidate's page, rendered both ways."""

    candidate_id: str
    name: str
    document: Document
    markdown: str
    html: str

    @property
    def stem(self) -> str:
        return f"dossier-{self.candidate_id}"


@dataclass
class ExecutivePack:
    """Everything the engagement team hands over for one run."""

    run_id: str
    engagement: Engagement
    summary_document: Document
    executive_summary_md: str
    executive_summary_html: str
    workbook: dict[str, list[list[Any]]] = field(default_factory=dict)
    dossiers: list[Dossier] = field(default_factory=list)
    synthetic: bool = False
    omitted: list[str] = field(default_factory=list)

    @property
    def tabs(self) -> list[str]:
        return list(self.workbook)

    def dossier_for(self, candidate_id: str) -> Dossier | None:
        return next((d for d in self.dossiers if d.candidate_id == candidate_id), None)

    def manifest(self) -> dict[str, Any]:
        """What the pack contains, for a README or an API response."""
        return {
            "run_id": self.run_id,
            "synthetic": self.synthetic,
            "engagement": self.engagement.to_dict(),
            "executive_summary": {"markdown_chars": len(self.executive_summary_md),
                                  "html_chars": len(self.executive_summary_html),
                                  "sections": [s.key for s in self.summary_document.sections]},
            "workbook": {"tabs": self.tabs,
                         "rows": {tab: max(0, len(rows) - 1)
                                  for tab, rows in self.workbook.items()}},
            "dossiers": [{"candidate_id": d.candidate_id, "name": d.name}
                         for d in self.dossiers],
            "omitted": list(self.omitted),
        }


def build_pack(result: Any, store: Any = None, engagement: Any = None,
               enrichment: Any = None) -> ExecutivePack:
    """Assemble the pack in memory.

    ``result`` is a ``RunResult``. ``store`` is optional and only read.
    ``enrichment`` is the optional dict from ``dpre.programme.enrich_run``;
    anything missing from it is left out of the pack and named in ``omitted``.
    """
    engagement = Engagement.coerce(engagement)
    view = EnrichmentView(enrichment)
    document = executive_summary(result, store=store, engagement=engagement,
                                 enrichment=enrichment)
    pack = ExecutivePack(
        run_id=result.manifest.run_id,
        engagement=engagement,
        summary_document=document,
        executive_summary_md=render_markdown(document),
        executive_summary_html=render_html(document),
        workbook=build_workbook(result, store=store, enrichment=enrichment),
        dossiers=build_dossiers(result, engagement=engagement, enrichment=enrichment),
        synthetic=bool(getattr(result.manifest, "synthetic", False)),
        omitted=view.missing(),
    )
    return pack


def build_dossiers(result: Any, engagement: Any = None, enrichment: Any = None) -> list[Dossier]:
    """One dossier per candidate, in backlog order."""
    from ..portfolio.views import attribute_reports
    attribution = attribute_reports(result.candidates)
    dossiers: list[Dossier] = []
    for candidate in result.ranked():
        document = candidate_dossier(candidate, result, engagement=engagement,
                                     enrichment=enrichment, attribution=attribution)
        dossiers.append(Dossier(
            candidate_id=candidate.candidate_id,
            name=candidate.proposed_name,
            document=document,
            markdown=render_markdown(document),
            html=render_html(document),
        ))
    return dossiers


def write_pack(result: Any, out_dir: str | Path, store: Any = None, engagement: Any = None,
               enrichment: Any = None, pack: ExecutivePack | None = None) -> list[Path]:
    """Write the pack under ``out_dir/<run_id>`` and return every path written.

    Pass ``pack`` to write one that is already built; otherwise it is built here.
    """
    pack = pack or build_pack(result, store=store, engagement=engagement, enrichment=enrichment)
    base = Path(out_dir) / pack.run_id
    base.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    summary_md = base / f"{SUMMARY_STEM}.md"
    summary_md.write_text(pack.executive_summary_md, encoding="utf-8")
    written.append(summary_md)

    summary_html = base / f"{SUMMARY_STEM}.html"
    summary_html.write_text(pack.executive_summary_html, encoding="utf-8")
    written.append(summary_html)

    workbook_path = write_workbook(base / WORKBOOK_NAME, pack.workbook)
    written.append(Path(workbook_path))

    dossier_dir = base / DOSSIER_DIR
    dossier_dir.mkdir(parents=True, exist_ok=True)
    for dossier in pack.dossiers:
        md_path = dossier_dir / f"{dossier.stem}.md"
        md_path.write_text(dossier.markdown, encoding="utf-8")
        html_path = dossier_dir / f"{dossier.stem}.html"
        html_path.write_text(dossier.html, encoding="utf-8")
        written.extend([md_path, html_path])

    index = base / "README.md"
    index.write_text(_index_markdown(pack), encoding="utf-8")
    written.append(index)
    return written


def _index_markdown(pack: ExecutivePack) -> str:
    """A contents page, so the folder is navigable without opening every file."""
    lines = [f"# {pack.engagement.title}: pack for run {pack.run_id}", ""]
    if pack.synthetic:
        lines += ["> **DEMONSTRATION RUN ON SYNTHETIC DATA - NOT CLIENT DATA.**", ""]
    lines += [
        f"{pack.engagement.confidentiality}.", "",
        "## Contents", "",
        f"- [`{SUMMARY_STEM}.md`]({SUMMARY_STEM}.md) - the executive summary, for reading and "
        "quoting.",
        f"- [`{SUMMARY_STEM}.html`]({SUMMARY_STEM}.html) - the same summary as one "
        "self-contained page, for printing or forwarding.",
        f"- [`{WORKBOOK_NAME}`]({WORKBOOK_NAME}) - the backlog workbook: "
        + ", ".join(TAB_NAMES) + ".",
        f"- [`{DOSSIER_DIR}/`]({DOSSIER_DIR}/) - one page per candidate "
        f"({len(pack.dossiers)} in total), Markdown and printable HTML.",
        "",
        "## Candidates", "",
        "| Candidate | Dossier |", "| --- | --- |",
    ]
    for dossier in pack.dossiers:
        lines.append(f"| {dossier.name} (`{dossier.candidate_id}`) | "
                     f"[{dossier.stem}.md]({DOSSIER_DIR}/{dossier.stem}.md) / "
                     f"[html]({DOSSIER_DIR}/{dossier.stem}.html) |")
    if pack.omitted:
        lines += ["", "## Not included in this pack", "",
                  "The following were not supplied to the pack builder and are omitted rather "
                  "than estimated:", ""]
        lines += [f"- {item}" for item in pack.omitted]
    lines += ["", "---", "The engine proposes; only a named reviewer decides. Every drafted "
              "name, purpose and decision in this pack is marked AI_DRAFT until a human "
              "accepts it."]
    return "\n".join(lines) + "\n"
