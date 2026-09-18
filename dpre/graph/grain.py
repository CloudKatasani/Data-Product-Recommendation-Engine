"""Grain inference (specification section 4.3).

Each table is assigned a grain from catalog primary keys, mapped to the
conformed backbone or to a domain entity when no backbone match exists. A KPI
inherits the grain of its fact table; multi-table KPIs take the finest grain
among their measures. Grain is the primary constraint on candidate generation.
"""
from __future__ import annotations

from ..config import CONFORMED_BACKBONE, GRAIN_FINENESS
from ..models import ColumnNode, TableNode
from ..util.text import name_similarity, tokenize

EVENT_HINTS = ("event", "log", "activity", "clickstream", "telemetry", "sensor", "alert")
TRANSACTION_HINTS = ("transaction", "txn", "payment", "posting", "journal", "line")


def backbone_for(pack_backbone: list[str] | None = None) -> list[str]:
    return list(pack_backbone or CONFORMED_BACKBONE)


def infer_backbone(tables: list[TableNode], columns_by_table: dict[str, list[ColumnNode]],
                   fallback: list[str] | None = None) -> list[str]:
    """Derive the conformed backbone from the catalog rather than assuming one.

    A backbone entity is a dimension with a primary key that several fact tables
    carry a foreign key to. Ordering is by how widely each entity is referenced:
    the entity every subject area joins to is the coarsest, the one only a few
    reach is the finest. Where the catalog shows no such chain the configured
    default stands (specification section 4.3).
    """
    entities: dict[str, str] = {}
    for table in tables:
        columns = columns_by_table.get(table.table_fqn, [])
        if table.measure_count >= 2:
            continue
        for column in columns:
            if not column.pk_flag or not is_entity_key(column.column_name):
                continue
            entity = _entity_from_key(column.column_name)
            if entity and entity.lower() not in ("staging", "batch"):
                entities.setdefault(entity.lower(), entity)

    references: dict[str, set[str]] = {}
    for table in tables:
        if table.measure_count < 2:
            continue
        for column in columns_by_table.get(table.table_fqn, []):
            if not is_entity_key(column.column_name):
                continue
            entity = _entity_from_key(column.column_name)
            if not entity:
                continue
            key = entity.lower()
            if key in entities:
                references.setdefault(key, set()).add(table.table_fqn)

    chain = [(entities[key], len(fqns)) for key, fqns in references.items() if len(fqns) >= 2]
    if not chain:
        return backbone_for(fallback)
    chain.sort(key=lambda pair: (-pair[1], pair[0]))
    return [name for name, _count in chain]


KEY_TOKENS = ("key", "identifier", "surrogate", "id", "sk", "pk")
KEY_SUFFIXES = ("_key", "_id", "_sk")


def _entity_from_key(column_name: str) -> str:
    tokens = [t for t in tokenize(column_name) if t not in KEY_TOKENS]
    if not tokens:
        return ""
    return " ".join(t.capitalize() for t in tokens)


def is_entity_key(column_name: str) -> bool:
    """A column that identifies a business entity, rather than merely coding one.

    ``account_key`` joins to the account dimension; ``status_code`` is an
    attribute of the row it sits on. Only the first kind can set a grain.
    """
    return column_name.lower().endswith(KEY_SUFFIXES)


NUMERIC_TYPES = ("number", "decimal", "numeric", "float", "double", "int", "real", "money")
TABLE_PREFIXES = ("fct", "fact", "dim", "ref", "stg", "v", "vw", "tbl", "agg")


def is_measure_like(column: ColumnNode) -> bool:
    """A numeric column that is neither a key nor a date behaves as a measure."""
    if column.pk_flag or column.fk_ref:
        return False
    name = column.column_name.lower()
    if name.endswith(("_key", "_id", "_code", "_date", "_ts", "_flag")):
        return False
    return any(t in (column.data_type or "").lower() for t in NUMERIC_TYPES)


def _table_entity(table: TableNode) -> str:
    tokens = [t for t in tokenize(table.table_name) if t not in TABLE_PREFIXES]
    return " ".join(t.capitalize() for t in tokens)


def infer_table_grain(table: TableNode, columns: list[ColumnNode],
                      backbone: list[str]) -> tuple[str, str]:
    """Return ``(grain, source)`` for one table."""
    table_tokens = set(tokenize(table.table_name))
    measure_count = sum(1 for c in columns if is_measure_like(c))
    fact_like = measure_count >= 2

    # An event-grain fact carries a timestamp finer than a day.
    has_timestamp = any(
        "timestamp" in (c.data_type or "").lower() or c.column_name.endswith("_ts")
        for c in columns
    )
    if has_timestamp and (table_tokens & set(EVENT_HINTS)):
        return "Event", "timestamp grain finer than day"

    # Primary keys are the strongest signal. On a fact table a primary key named
    # after the table itself is a surrogate row id, not a business entity.
    pks = [c for c in columns if c.pk_flag]
    for column in pks:
        entity = _entity_from_key(column.column_name)
        if not entity:
            continue
        for candidate in backbone:
            if name_similarity(entity, candidate) >= 0.9:
                return candidate, "catalog primary key"
        if not (fact_like and _is_surrogate(entity, table)):
            return entity, "catalog primary key"

    # Otherwise the finest backbone entity the table carries a foreign key to.
    referenced = []
    for column in columns:
        if not is_entity_key(column.column_name):
            continue
        entity = _entity_from_key(column.column_name)
        if not entity:
            continue
        for candidate in backbone:
            if name_similarity(entity, candidate) >= 0.9:
                referenced.append(candidate)
    if referenced:
        finest = max(referenced, key=lambda e: grain_fineness(e, backbone))
        return finest, "foreign key to the conformed backbone"

    if table_tokens & set(TRANSACTION_HINTS):
        return "Transaction", "table name"
    if table_tokens & set(EVENT_HINTS):
        return "Event", "table name"

    # Domain entity from the table name, e.g. DIM_PAYMENT_ARRANGEMENT -> Payment Arrangement.
    stripped = [t for t in tokenize(table.table_name)
                if t not in ("fct", "dim", "ref", "fact", "v", "stg", "tbl")]
    if stripped:
        return " ".join(t.capitalize() for t in stripped), "table name"
    return "unknown", "unknown"


def _is_surrogate(entity: str, table: TableNode) -> bool:
    """A PK named after the table itself describes the row, not a business entity."""
    return name_similarity(entity, _table_entity(table)) >= 0.85


def grain_fineness(grain: str, backbone: list[str]) -> int:
    if grain in GRAIN_FINENESS:
        return GRAIN_FINENESS[grain]
    if grain in backbone:
        return backbone.index(grain) + 1
    return 6            # a domain entity outside the backbone sits below it


def finest_grain(grains: list[str], backbone: list[str]) -> str:
    """A multi-table KPI takes the finest grain among its measures."""
    known = [g for g in grains if g and g != "unknown"]
    if not known:
        return "unknown"
    return max(known, key=lambda g: grain_fineness(g, backbone))


def grain_ambiguity(grains: list[str]) -> float:
    """Share of members that do not sit at the modal grain."""
    known = [g for g in grains if g and g != "unknown"]
    if not known:
        return 1.0
    counts: dict[str, int] = {}
    for grain in known:
        counts[grain] = counts.get(grain, 0) + 1
    modal = max(counts.values())
    unknown_penalty = len(grains) - len(known)
    return round((len(known) - modal + unknown_penalty) / float(len(grains)), 4)
