"""Markdown renderer for a pack document (review finding R-12).

Markdown is the format that survives: it goes in a repository, an email, a
ticket or a wiki without a viewer. The renderer is deliberately plain - ATX
headings, pipe tables, no HTML fallback - so the same file reads well in a
terminal and in a browser.
"""
from __future__ import annotations

from typing import Any

from .blocks import Document, Section

_TONE_PREFIX = {"warning": "**Warning.** ", "danger": "**Action required.** ",
                "ok": "**On track.** ", "note": ""}


def render_markdown(document: Document) -> str:
    """The whole document as one Markdown string, ending in a newline."""
    lines: list[str] = [f"# {document.title}", ""]
    if document.banner:
        lines += [f"> **{document.banner}**", ""]
    if document.subtitle:
        lines += [f"*{document.subtitle}*", ""]
    for section in document.sections:
        lines += render_section(section)
    return "\n".join(lines).rstrip() + "\n"


def render_section(section: Section) -> list[str]:
    lines = [f"{'#' * max(2, section.level)} {section.title}", ""]
    for block in section.blocks:
        lines += render_block(block)
    return lines


def render_block(block: dict[str, Any]) -> list[str]:
    kind = block.get("type")
    if kind == "paragraph":
        return [block["text"], ""]
    if kind == "bullets":
        lines = [block["lead"], ""] if block.get("lead") else []
        return lines + [f"- {item}" for item in block["items"]] + [""]
    if kind == "table":
        return _table(block)
    if kind == "kpis":
        return _kpis(block)
    if kind == "callout":
        # A title already says what the tone would: never print both.
        if block.get("title"):
            return [f"> **{block['title']}.** {block['text']}", ""]
        return [f"> {_TONE_PREFIX.get(block.get('tone', 'note'), '')}{block['text']}", ""]
    if kind == "definitions":
        return [f"- **{key}:** {value}" for key, value in block["items"]] + [""]
    return []


def _table(block: dict[str, Any]) -> list[str]:
    columns = block["columns"]
    lines = []
    if block.get("caption"):
        lines += [f"*{block['caption']}*", ""]
    lines.append("| " + " | ".join(_escape(c) for c in columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in block["rows"]:
        cells = list(row) + [""] * (len(columns) - len(row))
        lines.append("| " + " | ".join(_escape(c) for c in cells[:len(columns)]) + " |")
    lines.append("")
    return lines


def _kpis(block: dict[str, Any]) -> list[str]:
    lines = []
    for item in block["items"]:
        note = f" - {item['note']}" if item.get("note") else ""
        lines.append(f"- **{item['label']}: {item['value']}**{note}")
    lines.append("")
    return lines


def _escape(value: str) -> str:
    """Pipes and newlines would break a pipe table; nothing else needs escaping."""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()
