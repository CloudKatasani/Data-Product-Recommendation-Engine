"""Shared adapter helpers: field access through an explicit or inferred mapping."""
from __future__ import annotations

from typing import Any

from ...util import tabular
from ..schemas import SCHEMAS, map_columns


class FieldReader:
    """Reads schema field names off raw records through a column mapping."""

    def __init__(self, schema_key: str, columns: list[str], mapping: dict[str, str] | None = None):
        self.schema = SCHEMAS[schema_key]
        auto = map_columns(schema_key, columns)
        self.mapping = {**auto, **{k: v for k, v in (mapping or {}).items() if v}}
        self.columns = columns

    def missing_required(self) -> list[str]:
        return [name for name in self.schema.required_fields if name not in self.mapping]

    def missing_optional(self) -> list[str]:
        return [name for name in self.schema.optional_fields if name not in self.mapping]

    def raw(self, record: dict, field: str, default: Any = None) -> Any:
        source = self.mapping.get(field)
        if source is not None and source in record:
            value = record[source]
            if value not in (None, ""):
                return value
        spec = self.schema.field(field)
        names = spec.candidates() if spec else (field,)
        return tabular.pick(record, *names, default=default)

    def text(self, record: dict, field: str, default: str = "") -> str:
        return tabular.as_str(self.raw(record, field), default)

    def integer(self, record: dict, field: str, default: int = 0) -> int:
        return tabular.as_int(self.raw(record, field), default)

    def number(self, record: dict, field: str, default: float = 0.0) -> float:
        return tabular.as_float(self.raw(record, field), default)

    def boolean(self, record: dict, field: str, default: bool = False) -> bool:
        return tabular.as_bool(self.raw(record, field), default)

    def date(self, record: dict, field: str, default=None):
        return tabular.as_date(self.raw(record, field), default)


def normalize_disposition(value: str) -> str:
    text = (value or "").strip().lower()
    for known in ("retire", "merge", "keep", "migrate"):
        if text.startswith(known):
            return known.capitalize()
    if text in ("decommission", "sunset", "remove", "delete"):
        return "Retire"
    if text in ("consolidate", "combine"):
        return "Merge"
    if text in ("convert", "rebuild", "move"):
        return "Migrate"
    return "Keep"
