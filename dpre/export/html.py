"""Self-contained, print-friendly HTML renderer for a pack document (R-12).

The review found that the only way to get a page out of the engine was to print
the browser application, whose drawer is a fixed overlay, so printing yielded
one viewport. These documents are the opposite: one file, no external asset, no
script, a serif-free stack that exists on every machine, and an ``@media print``
rule set that produces A4 pages with the section headings kept with their
tables. A council member can open it offline, print it, or forward it.
"""
from __future__ import annotations

import html as _html
from typing import Any

from .blocks import Document, Section

# Inline so the file works from a mail attachment or a USB stick. Print rules
# are part of the deliverable, not an afterthought: white background, no shadows,
# tables that do not break a row across pages.
STYLESHEET = """
:root {
  --ink: #16202b; --muted: #5a6b7c; --rule: #d7dee6; --panel: #f4f7fa;
  --warn-bg: #fff6e5; --warn-ink: #7a4d00; --danger-bg: #fdecec; --danger-ink: #8d1f1f;
  --ok-bg: #edf7ef; --ok-ink: #1f5c2e; --accent: #1f4e79;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 40px 64px; background: #fff; color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 14px; line-height: 1.55; -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
main { max-width: 1000px; margin: 0 auto; }
h1 { font-size: 27px; line-height: 1.25; margin: 0 0 4px; color: var(--accent); }
h2 { font-size: 18px; margin: 34px 0 10px; padding-bottom: 6px; border-bottom: 2px solid var(--rule); }
h3 { font-size: 15px; margin: 22px 0 8px; color: var(--accent); }
p { margin: 0 0 10px; }
ul { margin: 0 0 12px; padding-left: 20px; }
li { margin: 0 0 4px; }
.subtitle { color: var(--muted); margin: 0 0 18px; font-size: 13px; }
.banner {
  background: var(--danger-bg); color: var(--danger-ink); border: 2px solid var(--danger-ink);
  border-radius: 4px; padding: 10px 14px; font-weight: 700; margin: 0 0 18px;
  letter-spacing: .02em; text-transform: uppercase; font-size: 12px;
}
table { border-collapse: collapse; width: 100%; margin: 0 0 14px; font-size: 12.5px; }
caption { caption-side: top; text-align: left; color: var(--muted); font-size: 12px; padding-bottom: 6px; }
th, td { border: 1px solid var(--rule); padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: var(--panel); font-weight: 600; }
tbody tr:nth-child(even) td { background: #fafcfe; }
.kpis { display: flex; flex-wrap: wrap; gap: 10px; margin: 0 0 14px; }
.kpi { flex: 1 1 170px; border: 1px solid var(--rule); border-left: 4px solid var(--accent);
       border-radius: 3px; padding: 8px 12px; background: var(--panel); }
.kpi .label { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.kpi .value { font-size: 19px; font-weight: 700; line-height: 1.25; }
.kpi .note { font-size: 11.5px; color: var(--muted); }
.callout { border-left: 4px solid var(--muted); background: var(--panel); padding: 9px 13px;
           margin: 0 0 13px; border-radius: 0 3px 3px 0; }
.callout.warning { background: var(--warn-bg); border-left-color: var(--warn-ink); color: var(--warn-ink); }
.callout.danger { background: var(--danger-bg); border-left-color: var(--danger-ink); color: var(--danger-ink); }
.callout.ok { background: var(--ok-bg); border-left-color: var(--ok-ink); color: var(--ok-ink); }
.callout .callout-title { font-weight: 700; display: block; }
dl { margin: 0 0 14px; display: grid; grid-template-columns: max-content 1fr; gap: 3px 14px; }
dt { color: var(--muted); }
dd { margin: 0; }
footer { margin-top: 36px; padding-top: 10px; border-top: 1px solid var(--rule);
         color: var(--muted); font-size: 11.5px; }
@media print {
  @page { size: A4; margin: 14mm; }
  body { padding: 0; font-size: 10.5pt; }
  h2 { page-break-after: avoid; }
  h3 { page-break-after: avoid; }
  table, ul, .kpis, .callout { page-break-inside: avoid; }
  tr, .kpi { page-break-inside: avoid; }
  a { text-decoration: none; color: inherit; }
}
@media (max-width: 720px) { body { padding: 18px 16px 40px; } .kpi { flex-basis: 100%; } }
"""


def render_html(document: Document) -> str:
    """One complete HTML file: no external stylesheet, font, image or script."""
    parts = [
        "<!DOCTYPE html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{esc(document.title)}</title>",
        f"<style>{STYLESHEET}</style>", "</head>", "<body>", "<main>",
        f"<h1>{esc(document.title)}</h1>",
    ]
    if document.banner:
        parts.append(f'<div class="banner">{esc(document.banner)}</div>')
    if document.subtitle:
        parts.append(f'<p class="subtitle">{esc(document.subtitle)}</p>')
    for section in document.sections:
        parts.extend(render_section(section))
    footer = document.meta.get("footer", "")
    if footer:
        parts.append(f"<footer>{esc(footer)}</footer>")
    parts += ["</main>", "</body>", "</html>"]
    return "\n".join(parts) + "\n"


def render_section(section: Section) -> list[str]:
    tag = f"h{min(4, max(2, section.level))}"
    out = [f'<section id="{esc(section.key)}">', f"<{tag}>{esc(section.title)}</{tag}>"]
    for block in section.blocks:
        out.extend(render_block(block))
    out.append("</section>")
    return out


def render_block(block: dict[str, Any]) -> list[str]:
    kind = block.get("type")
    if kind == "paragraph":
        return [f"<p>{esc(block['text'])}</p>"]
    if kind == "bullets":
        lead = [f"<p>{esc(block['lead'])}</p>"] if block.get("lead") else []
        items = "".join(f"<li>{esc(i)}</li>" for i in block["items"])
        return lead + [f"<ul>{items}</ul>"]
    if kind == "table":
        return [_table(block)]
    if kind == "kpis":
        cards = "".join(
            f'<div class="kpi"><div class="label">{esc(i["label"])}</div>'
            f'<div class="value">{esc(i["value"])}</div>'
            + (f'<div class="note">{esc(i["note"])}</div>' if i.get("note") else "")
            + "</div>" for i in block["items"])
        return [f'<div class="kpis">{cards}</div>']
    if kind == "callout":
        title = (f'<span class="callout-title">{esc(block["title"])}</span>'
                 if block.get("title") else "")
        return [f'<div class="callout {esc(block.get("tone", "note"))}">{title}'
                f'{esc(block["text"])}</div>']
    if kind == "definitions":
        items = "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in block["items"])
        return [f"<dl>{items}</dl>"]
    return []


def _table(block: dict[str, Any]) -> str:
    columns = block["columns"]
    caption = f"<caption>{esc(block['caption'])}</caption>" if block.get("caption") else ""
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    body = []
    for row in block["rows"]:
        cells = list(row) + [""] * (len(columns) - len(row))
        body.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells[:len(columns)]) + "</tr>")
    return f"<table>{caption}<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def esc(value: Any) -> str:
    return _html.escape(str(value if value is not None else ""), quote=True)
