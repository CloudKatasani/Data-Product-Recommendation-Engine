"""Grain inference (specification section 4.3).

Each table is assigned a grain from catalog primary keys, mapped to the
conformed backbone or to a domain entity when no backbone match exists. A KPI
inherits the grain of its fact table; multi-table KPIs take the finest grain
among their measures. Grain is the primary constraint on candidate generation.

Why the backbone is inferred the way it is (review finding R-47). Grain is the
one thing a reviewer will argue about on the first card, so the chain has to be
defensible on an ordinary warehouse, not only on the utility estate the
specification uses as its example:

* Date, calendar and period entities never join the backbone. Every fact table
  references the calendar, so counting references would put ``Date`` at the
  top of every chain and assign a customer-grain fact the grain ``Date``.
* Entities are ordered by key containment - the coarser entity is the one whose
  key travels with the finer entity's key - and reference count only breaks
  ties. Where the catalog holds no primary-key chain at all, the configured
  default stands and is recorded as ``declared``, never presented as inferred.
* Fineness is read off the inferred chain. The utility-specific fineness table
  in the configuration is consulted only for a declared backbone.
* Staging, batch and load tables are load steps, not business grains. They are
  marked non-business and a KPI never inherits its grain from one.
"""
from __future__ import annotations

from ..config import CONFORMED_BACKBONE, GRAIN_FINENESS
from ..models import ColumnNode, TableNode
from ..util.text import name_similarity, tokenize

EVENT_HINTS = ("event", "log", "activity", "clickstream", "telemetry", "sensor", "alert")
TRANSACTION_HINTS = ("transaction", "txn", "payment", "posting", "journal", "line")
# Entities that describe *when*, not *what*: never a backbone member (R-47).
TIME_ENTITY_TOKENS = {"date", "time", "calendar", "period", "month", "fiscal", "day", "week",
                      "quarter", "year", "timestamp"}
# Load steps rather than business entities: a non-business grain (R-47).
NON_BUSINESS_TOKENS = {"staging", "stg", "batch", "load", "etl", "landing", "raw", "archive",
                       "temp", "tmp", "work"}
NON_BUSINESS_GRAIN = "Staging"
NON_BUSINESS_SOURCE = "non-business load step; excluded from KPI grain inheritance"

# Event and transaction rows are finer than any entity in any backbone; the
# numbers only need to sit above the longest plausible chain.
TRANSACTION_FINENESS = 8
EVENT_FINENESS = 9
OUTSIDE_BACKBONE_FINENESS = 6


def backbone_for(pack_backbone: list[str] | None = None) -> list[str]:
    return list(pack_backbone or CONFORMED_BACKBONE)


def is_time_entity(entity: str) -> bool:
    return bool(set(tokenize(entity)) & TIME_ENTITY_TOKENS)


def is_non_business_entity(entity: str) -> bool:
    return bool(set(tokenize(entity)) & NON_BUSINESS_TOKENS)


def is_non_business_grain(grain: str) -> bool:
    return grain == NON_BUSINESS_GRAIN or is_non_business_entity(grain)


def infer_backbone(tables: list[TableNode], columns_by_table: dict[str, list[ColumnNode]],
                   fallback: list[str] | None = None) -> list[str]:
    """Derive the conformed backbone from the catalog rather than assuming one.

    A backbone entity is a dimension with a primary key that several fact tables
    carry a foreign key to. Time entities and load-step tables are excluded
    before counting. Ordering is by key containment: entity A sits above entity
    B when the fact tables carrying B's key also carry A's key more often than
    the reverse. Reference count breaks ties. Where the catalog shows no such
    chain the configured default stands (see ``backbone_source``).
    """
    chain = infer_backbone_chain(tables, columns_by_table)
    return [name for name, _count in chain] if chain else backbone_for(fallback)


def backbone_source(tables: list[TableNode], columns_by_table: dict[str, list[ColumnNode]],
                    declared: bool) -> str:
    """How the backbone was arrived at, for the manifest (R-47)."""
    if declared:
        return "declared"
    return "inferred" if infer_backbone_chain(tables, columns_by_table) else \
        "default (no primary-key chain in the catalog)"


def infer_backbone_chain(tables: list[TableNode],
                         columns_by_table: dict[str, list[ColumnNode]]) -> list[tuple[str, int]]:
    """``[(entity, referencing fact tables)]`` in backbone order, or ``[]``."""
    entities: dict[str, str] = {}
    for table in tables:
        if table.measure_count >= 2 or _is_non_business_table(table):
            continue
        for column in columns_by_table.get(table.table_fqn, []):
            if not column.pk_flag or not is_entity_key(column.column_name):
                continue
            entity = _entity_from_key(column.column_name)
            if not entity or is_time_entity(entity) or is_non_business_entity(entity):
                continue
            entities.setdefault(entity.lower(), entity)

    references: dict[str, set[str]] = {}
    for table in tables:
        if table.measure_count < 2 or _is_non_business_table(table):
            continue
        for column in columns_by_table.get(table.table_fqn, []):
            if not is_entity_key(column.column_name):
                continue
            entity = _entity_from_key(column.column_name)
            key = entity.lower() if entity else ""
            if key in entities:
                references.setdefault(key, set()).add(table.table_fqn)

    members = [key for key, fqns in references.items() if len(fqns) >= 2]
    if not members:
        return []

    def containment_wins(key: str) -> int:
        # A is coarser than B when B's tables carry A's key more often than A's
        # tables carry B's key: A's key travels with B's, not the other way.
        wins = 0
        for other in members:
            if other == key:
                continue
            shared = references[key] & references[other]
            if not shared:
                continue
            share_of_other = len(shared) / len(references[other])
            share_of_self = len(shared) / len(references[key])
            if share_of_other > share_of_self:
                wins += 1
        return wins

    ordered = sorted(members, key=lambda k: (-containment_wins(k), -len(references[k]), k))
    return [(entities[key], len(references[key])) for key in ordered]


def _is_non_business_table(table: TableNode) -> bool:
    return bool(set(tokenize(table.table_name)) & NON_BUSINESS_TOKENS)


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

    # A load step has no business grain, whatever its primary key says (R-47).
    pks = [c for c in columns if c.pk_flag]
    if _is_non_business_table(table) or any(
            is_non_business_entity(_entity_from_key(c.column_name)) for c in pks):
        return NON_BUSINESS_GRAIN, NON_BUSINESS_SOURCE

    # An event-grain fact carries a timestamp finer than a day.
    has_timestamp = any(
        "timestamp" in (c.data_type or "").lower() or c.column_name.endswith("_ts")
        for c in columns
    )
    if has_timestamp and (table_tokens & set(EVENT_HINTS)):
        return "Event", "timestamp grain finer than day"

    # Primary keys are the strongest signal. On a fact table a primary key named
    # after the table itself is a surrogate row id, not a business entity.
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


def grain_fineness(grain: str, backbone: list[str], declared: bool = False) -> int:
    """Position in the backbone chain; the configured table only for a declared one."""
    if grain in backbone:
        return backbone.index(grain) + 1
    if grain == "Event":
        return EVENT_FINENESS
    if grain == "Transaction":
        return TRANSACTION_FINENESS
    if declared and grain in GRAIN_FINENESS:
        return GRAIN_FINENESS[grain]
    if grain == "unknown":
        return 0
    return OUTSIDE_BACKBONE_FINENESS   # a domain entity outside the backbone sits below it


def finest_grain(grains: list[str], backbone: list[str]) -> str:
    """A multi-table KPI takes the finest grain among its measures.

    Non-business grains never win: a KPI that reads a staging copy alongside a
    fact table evaluates at the fact table's grain.
    """
    known = [g for g in grains if g and g != "unknown" and not is_non_business_grain(g)]
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
