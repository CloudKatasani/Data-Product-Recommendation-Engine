"""Format-agnostic record loading: csv, tsv, json, jsonl, xlsx.

Loading is bounded and quiet about the filesystem (R-29, R-30). An error names
the file, never the absolute path it was read from, because the message reaches
an HTTP client; and a text extract is size-checked before it is read into
memory, the way :mod:`dpre.util.xlsx` bounds a workbook.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
from pathlib import Path
from typing import Any, Iterable

from . import xlsx

TRUE_VALUES = {"true", "t", "yes", "y", "1", "x"}
FALSE_VALUES = {"false", "f", "no", "n", "0", ""}

#: Ceiling for a delimited or JSON extract read whole into memory. A workbook
#: has its own, richer bounds in :class:`dpre.util.xlsx.Limits`.
MAX_TEXT_BYTES = 256 * 1024 * 1024


class LoadError(ValueError):
    """Raised when a file cannot be parsed into records."""


def _read_text(path: Path, encoding: str = "utf-8-sig") -> str:
    size = path.stat().st_size
    if size > MAX_TEXT_BYTES:
        raise LoadError(f"{path.name} is {size} bytes, over the {MAX_TEXT_BYTES} byte limit")
    try:
        return path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise LoadError(f"{path.name} is not readable as text") from exc


def load_records(path: str | Path, sheet: str | None = None,
                 limits: "xlsx.Limits | None" = None) -> list[dict]:
    """Load one table of records from a file, guessing the format by suffix."""
    path = Path(path)
    if not path.exists():
        raise LoadError(f"file not found: {path.name}")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        return load_delimited(_read_text(path), ",")
    if suffix in (".tsv", ".tab"):
        return load_delimited(_read_text(path), "\t")
    if suffix == ".jsonl" or suffix == ".ndjson":
        return [json.loads(line) for line in _read_text(path, "utf-8").splitlines()
                if line.strip()]
    if suffix == ".json":
        return _from_json(json.loads(_read_text(path, "utf-8")), sheet)
    if suffix in (".xlsx", ".xlsm"):
        book = xlsx.read_workbook(path, limits)
        if sheet is not None:
            if sheet not in book:
                raise LoadError(f"sheet '{sheet}' not in {path.name}; found {list(book)}")
            return xlsx.rows_to_records(book[sheet])
        first = next(iter(book.values()), [])
        return xlsx.rows_to_records(first)
    raise LoadError(f"unsupported file type: {path.suffix}")


def load_delimited(text: str, delimiter: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [{(k or "").strip(): v for k, v in row.items()} for row in reader]


def _from_json(payload: Any, key: str | None) -> list[dict]:
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        if key and key in payload:
            return _from_json(payload[key], None)
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    raise LoadError("json payload contains no array of objects")


def workbook_tabs(path: str | Path,
                  limits: "xlsx.Limits | None" = None) -> dict[str, list[dict]]:
    """Read every sheet of a workbook as records (used by the synthetic pack)."""
    return {name: xlsx.rows_to_records(rows)
            for name, rows in xlsx.read_workbook(path, limits).items()}


# --------------------------------------------------------------------------
# Coercion
# --------------------------------------------------------------------------

def as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text if text else default


def as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return default


def as_date(value: Any, default: _dt.date | None = None) -> _dt.date | None:
    if value is None or value == "":
        return default
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, (int, float)):
        # Excel serial date (1900 date system).
        try:
            return _dt.date(1899, 12, 30) + _dt.timedelta(days=int(value))
        except (OverflowError, ValueError):
            return default
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(text[:len(fmt) + 8], fmt).date()
        except ValueError:
            continue
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return default


def pick(record: dict, *names: str, default: Any = None) -> Any:
    """First present value among ``names``, matched case/underscore-insensitively."""
    lowered = {str(k).strip().lower().replace(" ", "_"): v for k, v in record.items()}
    for name in names:
        key = name.strip().lower().replace(" ", "_")
        if key in lowered and lowered[key] not in (None, ""):
            return lowered[key]
    return default


def write_csv(path: str | Path, rows: Iterable[dict], columns: list[str] | None = None) -> Path:
    rows = list(rows)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in columns})
    return path
