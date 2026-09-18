"""A small YAML emitter for the DPF seed files (stdlib only)."""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any

_PLAIN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ ./@+-]*$")
_RESERVED = {"yes", "no", "true", "false", "null", "on", "off", "~"}


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    text = str(value)
    if text == "":
        return '""'
    if "\n" in text:
        indent_body = "\n".join("  " + line for line in text.splitlines())
        return "|-\n" + indent_body
    if _PLAIN.match(text) and text.lower() not in _RESERVED and not text.endswith(" "):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dumps(data: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(data, dict):
        if not data:
            return pad + "{}\n"
        out = []
        for key, value in data.items():
            if isinstance(value, (dict, list)) and value:
                out.append(f"{pad}{key}:\n{dumps(value, indent + 1)}")
            elif isinstance(value, (dict, list)):
                out.append(f"{pad}{key}: " + ("{}" if isinstance(value, dict) else "[]") + "\n")
            else:
                rendered = _scalar(value)
                if rendered.startswith("|-"):
                    head, _, body = rendered.partition("\n")
                    body = "\n".join("  " * (indent + 1) + line.strip() for line in body.splitlines())
                    out.append(f"{pad}{key}: {head}\n{body}\n")
                else:
                    out.append(f"{pad}{key}: {rendered}\n")
        return "".join(out)
    if isinstance(data, list):
        if not data:
            return pad + "[]\n"
        out = []
        for item in data:
            if isinstance(item, (dict, list)) and item:
                body = dumps(item, indent + 1)
                first, _, rest = body.partition("\n")
                out.append(f"{pad}- {first.strip()}\n{rest}")
            else:
                out.append(f"{pad}- {_scalar(item)}\n")
        return "".join(out)
    return pad + _scalar(data) + "\n"


def write_yaml(path: str | Path, data: Any, header: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps(data)
    if header:
        text = "".join(f"# {line}\n" for line in header.splitlines()) + text
    path.write_text(text, encoding="utf-8")
    return path
