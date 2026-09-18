"""The small document vocabulary both renderers walk (review finding R-12).

A pack is built once as structured blocks and rendered twice - Markdown for a
repository or an email, self-contained HTML for printing and forwarding - so
the two can never drift. Six block kinds carry everything the executive summary
and the dossiers need:

``paragraph``   one body paragraph.
``bullets``     a list, optionally with a lead-in line.
``table``       columns, rows and an optional caption; every cell is a string.
``kpis``        a row of headline figures, each with a label and a note.
``callout``     a boxed note with a tone (``warning``, ``note``, ``ok``).
``definitions`` label/value pairs, for cover and provenance blocks.

Sections group blocks under a heading. Nothing here formats for one medium, so
a third renderer (a DOCX writer, say) needs no change to the pack builders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

TONES = ("note", "warning", "ok", "danger")


@dataclass
class Section:
    """A heading and its blocks. ``level`` 2 is a summary section, 3 a sub-section."""

    key: str
    title: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    level: int = 2

    def add(self, block: dict[str, Any] | None) -> "Section":
        """Append a block, ignoring ``None`` so builders can be written inline."""
        if block is not None:
            self.blocks.append(block)
        return self

    def extend(self, blocks: Iterable[dict[str, Any] | None]) -> "Section":
        for block in blocks:
            self.add(block)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "level": self.level,
                "blocks": list(self.blocks)}


@dataclass
class Document:
    """A rendered-once pack document: title, subtitle, banner and sections."""

    title: str
    subtitle: str = ""
    banner: str = ""                      # the synthetic warning, when there is one
    sections: list[Section] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def add(self, section: Section | None) -> "Document":
        if section is not None and section.blocks:
            self.sections.append(section)
        return self

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "subtitle": self.subtitle, "banner": self.banner,
                "meta": dict(self.meta), "sections": [s.to_dict() for s in self.sections]}


# --------------------------------------------------------------------------
# block constructors
# --------------------------------------------------------------------------

def paragraph(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    return {"type": "paragraph", "text": text} if text else None


def bullets(items: Sequence[str], lead: str = "") -> dict[str, Any] | None:
    kept = [str(i).strip() for i in items if str(i).strip()]
    if not kept:
        return None
    return {"type": "bullets", "lead": lead, "items": kept}


def table(columns: Sequence[str], rows: Sequence[Sequence[Any]], caption: str = "",
          empty: str = "") -> dict[str, Any] | None:
    """A table, or a paragraph saying why it is empty when ``empty`` is given."""
    body = [[cell_text(c) for c in row] for row in rows]
    if not body:
        return paragraph(empty) if empty else None
    return {"type": "table", "columns": [str(c) for c in columns], "rows": body,
            "caption": caption}


def kpis(items: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """``[{"label", "value", "note"}]`` rendered as a headline figure row."""
    kept = [{"label": str(i.get("label", "")), "value": cell_text(i.get("value", "")),
             "note": str(i.get("note", ""))} for i in items if i]
    return {"type": "kpis", "items": kept} if kept else None


def callout(text: str, tone: str = "note", title: str = "") -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    return {"type": "callout", "tone": tone if tone in TONES else "note",
            "title": title, "text": text}


def definitions(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any] | None:
    kept = [(str(k), cell_text(v)) for k, v in pairs if cell_text(v)]
    return {"type": "definitions", "items": kept} if kept else None


# --------------------------------------------------------------------------
# cell formatting, shared so Markdown and HTML agree digit for digit
# --------------------------------------------------------------------------

def cell_text(value: Any) -> str:
    """One display string for any cell value; floats keep one decimal, ints group."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.1f}" if abs(value) >= 10 else f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple)):
        return ", ".join(cell_text(v) for v in value)
    return str(value)


def money(amount: Any, currency: str = "") -> str:
    """A benefit figure a council reads at a glance: no cents, thousands grouped."""
    try:
        number = float(amount)
    except (TypeError, ValueError):
        return ""
    prefix = f"{currency} " if currency else ""
    return f"{prefix}{number:,.0f}"


def percent(share: Any, digits: int = 0) -> str:
    try:
        return f"{float(share):.{digits}%}"
    except (TypeError, ValueError):
        return ""
