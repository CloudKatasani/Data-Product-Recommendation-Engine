"""Minimal .xlsx reader/writer built on zipfile + ElementTree.

The synthetic data pack (specification section 17) ships as one workbook per
industry and manual-mode users upload workbooks, so the engine needs both
directions without pulling in a third-party dependency.

Reading is *bounded* (R-30). A workbook is a zip archive of XML, so a 500 KB
upload can inflate to hundreds of megabytes and take the review server down
with it. Every member is checked against a decompressed-size ceiling and a
compression-ratio ceiling before it is read, the read itself is capped, the
whole archive has a budget, and a sheet stops at a row limit. The ceilings live
in :class:`Limits` so an operator can raise them for a genuinely large estate
without editing code.
"""
from __future__ import annotations

import datetime as _dt
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_RE = re.compile(r"([A-Z]+)(\d+)")
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class WorkbookTooLarge(ValueError):
    """A workbook exceeds the bounds this engine will spend memory on."""


@dataclass(frozen=True)
class Limits:
    """Ceilings applied to one workbook while it is read.

    ``max_ratio`` is the guard that a size header cannot defeat: a member
    compressing better than this is a zip bomb, not an extract. ``max_member_bytes``
    and ``max_total_bytes`` bound one part and the archive; ``max_rows_per_sheet``
    bounds what reaches the ingestion layer.
    """

    max_member_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_ratio: int = 200
    max_rows_per_sheet: int = 1_000_000
    max_members: int = 512


DEFAULT_LIMITS = Limits()


def _col_to_index(ref: str) -> int:
    match = _CELL_RE.match(ref)
    letters = match.group(1) if match else ref
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _index_to_col(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _escape(value: str) -> str:
    value = _ILLEGAL.sub("", value)
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def _cell_xml(row_idx: int, col_idx: int, value, shared: dict[str, int], style: int) -> str:
    ref = f"{_index_to_col(col_idx)}{row_idx}"
    style_attr = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, bool):
        return f'<c r="{ref}"{style_attr} t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = str(value)
    if isinstance(value, (_dt.date, _dt.datetime)):
        text = value.isoformat()
    key = text
    if key not in shared:
        shared[key] = len(shared)
    return f'<c r="{ref}"{style_attr} t="s"><v>{shared[key]}</v></c>'


def write_workbook(path: str | Path, sheets: dict[str, list[list]]) -> Path:
    """Write ``{sheet_name: rows}`` to an xlsx file. Row 1 is styled as a header."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    shared: dict[str, int] = {}
    sheet_xml: list[str] = []
    safe_names: list[str] = []
    seen: set[str] = set()
    for name, rows in sheets.items():
        safe = re.sub(r"[\\/*?:\[\]]", "_", str(name))[:31] or "Sheet"
        base, n = safe, 1
        while safe.lower() in seen:
            n += 1
            safe = f"{base[:28]}_{n}"
        seen.add(safe.lower())
        safe_names.append(safe)
        body: list[str] = []
        for r_i, row in enumerate(rows, start=1):
            style = 1 if r_i == 1 else 0
            cells = "".join(_cell_xml(r_i, c_i, val, shared, style) for c_i, val in enumerate(row))
            body.append(f'<row r="{r_i}">{cells}</row>')
        widths = ""
        if rows:
            n_cols = max(len(r) for r in rows)
            widths = "<cols>" + "".join(
                f'<col min="{i+1}" max="{i+1}" width="24" customWidth="1"/>' for i in range(n_cols)
            ) + "</cols>"
        freeze = (
            '<sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            "</sheetView></sheetViews>"
        )
        sheet_xml.append(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{NS_MAIN}">{freeze}{widths}<sheetData>'
            + "".join(body)
            + "</sheetData></worksheet>"
        )

    strings = "".join(f"<si><t xml:space=\"preserve\">{_escape(s)}</t></si>" for s in shared)
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="{NS_MAIN}" count="{len(shared)}" uniqueCount="{len(shared)}">{strings}</sst>'
    )
    sheets_decl = "".join(
        f'<sheet name="{_escape(n)}" sheetId="{i+1}" r:id="rId{i+1}"/>'
        for i, n in enumerate(safe_names)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}"><sheets>{sheets_decl}</sheets></workbook>'
    )
    rels = "".join(
        f'<Relationship Id="rId{i+1}" Type="{NS_REL}/worksheet" Target="worksheets/sheet{i+1}.xml"/>'
        for i in range(len(safe_names))
    )
    rels += (
        f'<Relationship Id="rId{len(safe_names)+1}" Type="{NS_REL}/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        f'<Relationship Id="rId{len(safe_names)+2}" Type="{NS_REL}/styles" Target="styles.xml"/>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{NS_PKG_REL}">{rels}</Relationships>'
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<styleSheet xmlns="{NS_MAIN}">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
        '<fills count="3"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/>'
        '<bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
        '<cellXfs count="2"><xf xfId="0"/>'
        '<xf fontId="1" fillId="2" applyFont="1" applyFill="1" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>'
        + "".join(
            f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(len(safe_names))
        )
        + '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sharedStrings+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{NS_PKG_REL}">'
        f'<Relationship Id="rId1" Type="{NS_REL}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/sharedStrings.xml", shared_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        for i, xml in enumerate(sheet_xml):
            zf.writestr(f"xl/worksheets/sheet{i+1}.xml", xml)
    return path


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

class _Budget:
    """How many decompressed bytes this archive may still spend."""

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.remaining = limits.max_total_bytes

    def spend(self, size: int, member: str) -> None:
        self.remaining -= size
        if self.remaining < 0:
            raise WorkbookTooLarge(
                f"the workbook expands beyond {self.limits.max_total_bytes} bytes "
                f"(reading '{member}')")


def _read_member(zf: zipfile.ZipFile, member: str, limits: Limits,
                 budget: "_Budget") -> bytes:
    """Read one archive member within the size and ratio ceilings.

    The declared size is checked first because it is free, then the read itself
    is capped one byte beyond the ceiling, because a crafted archive can lie in
    its directory but cannot lie about the bytes it produces.
    """
    info = zf.getinfo(member)
    if info.file_size > limits.max_member_bytes:
        raise WorkbookTooLarge(
            f"'{member}' declares {info.file_size} bytes, over the "
            f"{limits.max_member_bytes} byte limit for one part")
    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > limits.max_ratio:
            raise WorkbookTooLarge(
                f"'{member}' expands {ratio:.0f}x, over the {limits.max_ratio}x limit; "
                "this is not a spreadsheet")
    with zf.open(member) as handle:
        data = handle.read(limits.max_member_bytes + 1)
    if len(data) > limits.max_member_bytes:
        raise WorkbookTooLarge(
            f"'{member}' expands past the {limits.max_member_bytes} byte limit for one part")
    budget.spend(len(data), member)
    return data


def _open_workbook(path: str | Path, limits: Limits) -> tuple[zipfile.ZipFile, "_Budget"]:
    """Open the archive and refuse one with an implausible number of members."""
    zf = zipfile.ZipFile(path)
    try:
        names = zf.namelist()
        if len(names) > limits.max_members:
            raise WorkbookTooLarge(
                f"the workbook holds {len(names)} parts, over the {limits.max_members} limit")
    except Exception:
        zf.close()
        raise
    return zf, _Budget(limits)


def _read_shared_strings(zf: zipfile.ZipFile, limits: Limits, budget: "_Budget") -> list[str]:
    try:
        raw = _read_member(zf, "xl/sharedStrings.xml", limits, budget)
    except KeyError:
        return []
    root = ET.fromstring(raw)
    out = []
    for si in root.findall(f"{{{NS_MAIN}}}si"):
        out.append("".join(t.text or "" for t in si.iter(f"{{{NS_MAIN}}}t")))
    return out


def sheet_names(path: str | Path, limits: Limits | None = None) -> list[str]:
    limits = limits or DEFAULT_LIMITS
    zf, budget = _open_workbook(path, limits)
    with zf:
        root = ET.fromstring(_read_member(zf, "xl/workbook.xml", limits, budget))
        return [s.get("name", "") for s in root.iter(f"{{{NS_MAIN}}}sheet")]


def read_workbook(path: str | Path, limits: Limits | None = None) -> dict[str, list[list]]:
    """Read every sheet into ``{sheet_name: rows}`` of Python scalars.

    Raises :class:`WorkbookTooLarge` rather than spending unbounded memory on a
    hostile or merely enormous upload (R-30).
    """
    limits = limits or DEFAULT_LIMITS
    out: dict[str, list[list]] = {}
    zf, budget = _open_workbook(path, limits)
    with zf:
        strings = _read_shared_strings(zf, limits, budget)
        wb = ET.fromstring(_read_member(zf, "xl/workbook.xml", limits, budget))
        rel_root = ET.fromstring(_read_member(zf, "xl/_rels/workbook.xml.rels", limits, budget))
        targets = {
            r.get("Id"): r.get("Target", "")
            for r in rel_root.findall(f"{{{NS_PKG_REL}}}Relationship")
        }
        for order, sheet in enumerate(wb.iter(f"{{{NS_MAIN}}}sheet"), start=1):
            name = sheet.get("name", f"Sheet{order}")
            rid = sheet.get(f"{{{NS_REL}}}id")
            target = targets.get(rid, f"worksheets/sheet{order}.xml").lstrip("/")
            member = target if target.startswith("xl/") else f"xl/{target}"
            try:
                data = _read_member(zf, member, limits, budget)
            except KeyError:
                continue
            out[name] = _parse_sheet(data, strings, limits)
    return out


def _parse_sheet(data: bytes, strings: list[str], limits: Limits | None = None) -> list[list]:
    limits = limits or DEFAULT_LIMITS
    root = ET.fromstring(data)
    rows: list[list] = []
    for row in root.iter(f"{{{NS_MAIN}}}row"):
        if len(rows) >= limits.max_rows_per_sheet:
            raise WorkbookTooLarge(
                f"a sheet carries more than {limits.max_rows_per_sheet} rows; "
                "split the extract")
        values: list = []
        for cell in row.findall(f"{{{NS_MAIN}}}c"):
            idx = _col_to_index(cell.get("r", "")) if cell.get("r") else len(values)
            while len(values) < idx:
                values.append(None)
            ctype = cell.get("t")
            if ctype == "inlineStr":
                text = "".join(t.text or "" for t in cell.iter(f"{{{NS_MAIN}}}t"))
                values.append(text)
                continue
            v = cell.find(f"{{{NS_MAIN}}}v")
            if v is None or v.text is None:
                values.append(None)
                continue
            raw = v.text
            if ctype == "s":
                pos = int(raw)
                values.append(strings[pos] if 0 <= pos < len(strings) else "")
            elif ctype == "b":
                values.append(raw in ("1", "true", "TRUE"))
            elif ctype == "str":
                values.append(raw)
            else:
                try:
                    num = float(raw)
                    values.append(int(num) if num.is_integer() else num)
                except ValueError:
                    values.append(raw)
        rows.append(values)
    return rows


def read_sheet_records(path: str | Path, sheet: str,
                       limits: Limits | None = None) -> list[dict]:
    """Read one sheet as dict records keyed by the header row."""
    rows = read_workbook(path, limits).get(sheet, [])
    return rows_to_records(rows)


def rows_to_records(rows: list[list]) -> list[dict]:
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    records = []
    for row in rows[1:]:
        if all(c is None or c == "" for c in row):
            continue
        rec = {}
        for i, key in enumerate(header):
            if not key:
                continue
            rec[key] = row[i] if i < len(row) else None
        records.append(rec)
    return records
