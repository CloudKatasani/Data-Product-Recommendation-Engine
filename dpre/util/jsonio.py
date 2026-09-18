"""JSON helpers that know how to serialize the engine's dataclasses."""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
from pathlib import Path
from typing import Any


class EngineEncoder(json.JSONEncoder):
    def default(self, o: Any):  # noqa: D102
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, (_dt.date, _dt.datetime)):
            return o.isoformat()
        if isinstance(o, set):
            return sorted(o)
        if hasattr(o, "to_dict"):
            return o.to_dict()
        return super().default(o)


def dumps(payload: Any, indent: int | None = 2) -> str:
    return json.dumps(payload, cls=EngineEncoder, indent=indent, sort_keys=False)


def write_json(path: str | Path, payload: Any, indent: int | None = 2) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(payload, indent=indent), encoding="utf-8")
    return path


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
