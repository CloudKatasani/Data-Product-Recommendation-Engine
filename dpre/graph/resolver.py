"""Entity resolution: Cognos query item / DAX column to catalog column.

Rules ER-1 to ER-6 of specification section 4.2 run in order; the first match
wins and its confidence is stored on the edge. Anything that resolves below the
ER-6 floor is quarantined with a reason code and counted against feasibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import (
    ER4_NAME_SIMILARITY, ER6_EMBEDDING_SIMILARITY, ER_CONFIDENCE, QUARANTINE_REASONS,
)
from ..models import CatalogColumnRecord, CatalogLineageRecord, KpiRecord
from ..util.text import embedding_similarity, name_similarity, normalize_identifier, token_key


@dataclass
class Resolution:
    column_fqn: str
    er_rule: str
    confidence: float
    detail: str = ""


@dataclass
class ResolverIndex:
    """Lookups over the catalog extract, built once per run."""

    by_fqn: dict[str, str] = field(default_factory=dict)
    by_schema_table_column: dict[tuple[str, str, str], str] = field(default_factory=dict)
    by_table_column: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    columns_by_table: dict[str, list[str]] = field(default_factory=dict)
    tables_by_name: dict[str, list[str]] = field(default_factory=dict)
    token_by_table: dict[str, dict[str, str]] = field(default_factory=dict)
    term_by_table: dict[str, dict[str, str]] = field(default_factory=dict)
    record_by_fqn: dict[str, CatalogColumnRecord] = field(default_factory=dict)
    upstream: dict[str, list[str]] = field(default_factory=dict)
    table_upstream: dict[str, list[str]] = field(default_factory=dict)
    domain_by_table: dict[str, str] = field(default_factory=dict)
    sor_by_table: dict[str, bool] = field(default_factory=dict)


def build_index(columns: list[CatalogColumnRecord],
                lineage: list[CatalogLineageRecord]) -> ResolverIndex:
    index = ResolverIndex()
    for record in columns:
        fqn = record.column_fqn
        table_fqn = record.table_fqn
        index.record_by_fqn[fqn] = record
        index.by_fqn[normalize_identifier(fqn)] = fqn
        index.by_schema_table_column[(
            normalize_identifier(record.schema), normalize_identifier(record.table),
            normalize_identifier(record.column))] = fqn
        index.by_table_column.setdefault(
            (normalize_identifier(record.table), normalize_identifier(record.column)), []).append(fqn)
        index.columns_by_table.setdefault(table_fqn, []).append(fqn)
        index.tables_by_name.setdefault(normalize_identifier(record.table), []).append(table_fqn)
        index.token_by_table.setdefault(table_fqn, {})[token_key(record.column)] = fqn
        if record.business_term:
            index.term_by_table.setdefault(table_fqn, {})[token_key(record.business_term)] = fqn
        index.domain_by_table[table_fqn] = record.data_domain
        index.sor_by_table[table_fqn] = record.system_of_record
    for table_fqn, fqns in index.tables_by_name.items():
        index.tables_by_name[table_fqn] = sorted(set(fqns))
    for edge in lineage:
        if edge.level == "column" and edge.src_column and edge.tgt_column:
            index.upstream.setdefault(_norm_fqn(edge.tgt_fqn), []).append(edge.src_fqn)
        else:
            index.table_upstream.setdefault(_norm_fqn(edge.tgt_fqn), []).append(edge.src_fqn)
    return index


def _norm_fqn(fqn: str) -> str:
    return normalize_identifier(fqn)


def resolve_reference(kpi: KpiRecord, reference_column: str, index: ResolverIndex,
                      query_item: str = "") -> Resolution | tuple[None, str, str]:
    """Resolve one lineage reference. Returns a Resolution or (None, reason, detail)."""
    table = kpi.table
    column = reference_column or kpi.column
    if not table or not column:
        return None, "MISSING_REFERENCE", QUARANTINE_REASONS["MISSING_REFERENCE"]

    # ER-1: exact match on the full physical name after case and quote normalization.
    fqn = ".".join([kpi.source_system, kpi.database, kpi.schema, table, column])
    hit = index.by_fqn.get(normalize_identifier(fqn))
    if hit:
        return Resolution(hit, "ER-1", ER_CONFIDENCE["ER-1"], "exact physical name")
    hit = index.by_schema_table_column.get(
        (normalize_identifier(kpi.schema), normalize_identifier(table), normalize_identifier(column)))
    if hit:
        return Resolution(hit, "ER-1", ER_CONFIDENCE["ER-1"], "exact schema.table.column")
    candidates = index.by_table_column.get(
        (normalize_identifier(table), normalize_identifier(column)))
    if candidates:
        return Resolution(candidates[0], "ER-1", ER_CONFIDENCE["ER-1"], "exact table.column")

    table_fqns = index.tables_by_name.get(normalize_identifier(table), [])
    if not table_fqns:
        # The Power BI adapter cannot see past an import model's M query.
        if kpi.tool == "powerbi" and getattr(kpi, "_storage_mode", "").lower() == "import":
            return None, "MODEL_TERMINUS", QUARANTINE_REASONS["MODEL_TERMINUS"]
        return None, "NO_CATALOG_TABLE", QUARANTINE_REASONS["NO_CATALOG_TABLE"]

    # ER-2: exact table match plus column match after alias expansion. The query
    # item alias is expanded to the underlying column via the package metadata.
    alias_key = token_key(column)
    item_key = token_key(query_item) if query_item else ""
    for table_fqn in table_fqns:
        tokens = index.token_by_table.get(table_fqn, {})
        for key in (alias_key, item_key):
            if key and key in tokens:
                return Resolution(tokens[key], "ER-2", ER_CONFIDENCE["ER-2"],
                                  "query-item alias expanded to the underlying column")

    # ER-3: catalog lineage says this reporting column derives from one upstream
    # column; inherit that edge.
    for table_fqn in table_fqns:
        target = f"{table_fqn}.{column}"
        sources = index.upstream.get(_norm_fqn(target), [])
        if len(sources) == 1 and sources[0] in index.record_by_fqn:
            return Resolution(sources[0], "ER-3", ER_CONFIDENCE["ER-3"],
                              "inherited from catalog column lineage")
        for upstream_table in index.table_upstream.get(_norm_fqn(table_fqn), []):
            candidate = f"{upstream_table}.{column}"
            if candidate in index.record_by_fqn:
                return Resolution(candidate, "ER-3", ER_CONFIDENCE["ER-3"],
                                  "inherited from catalog table lineage")

    # ER-4: table match plus column-name similarity on tokenized names.
    best: tuple[float, str] = (0.0, "")
    for table_fqn in table_fqns:
        for candidate in index.columns_by_table.get(table_fqn, []):
            candidate_name = candidate.rsplit(".", 1)[-1]
            score = name_similarity(column, candidate_name)
            if score > best[0]:
                best = (score, candidate)
    if best[0] >= ER4_NAME_SIMILARITY:
        return Resolution(best[1], "ER-4", ER_CONFIDENCE["ER-4"],
                          f"column name similarity {best[0]:.2f}")

    # ER-5: business-term match - the query-item label equals a catalog term
    # whose column sits in the same table.
    for table_fqn in table_fqns:
        terms = index.term_by_table.get(table_fqn, {})
        for key in (item_key, alias_key):
            if key and key in terms:
                return Resolution(terms[key], "ER-5", ER_CONFIDENCE["ER-5"],
                                  "business-term match within the table")

    # ER-6: embedding similarity of label and definition text, same domain.
    label_text = " ".join(filter(None, [query_item or column, kpi.kpi_label]))
    best = (0.0, "")
    for table_fqn in table_fqns:
        for candidate in index.columns_by_table.get(table_fqn, []):
            record = index.record_by_fqn[candidate]
            candidate_text = " ".join(filter(None, [
                record.column, record.business_term, record.definition]))
            score = embedding_similarity(label_text, candidate_text)
            if score > best[0]:
                best = (score, candidate)
    if best[0] >= ER6_EMBEDDING_SIMILARITY:
        return Resolution(best[1], "ER-6", ER_CONFIDENCE["ER-6"],
                          f"embedding similarity {best[0]:.2f}")

    if best[1]:
        return None, "LOW_CONFIDENCE", (
            f"{QUARANTINE_REASONS['LOW_CONFIDENCE']} (best {best[0]:.2f} against "
            f"{best[1].rsplit('.', 1)[-1]})")
    return None, "NO_CATALOG_COLUMN", QUARANTINE_REASONS["NO_CATALOG_COLUMN"]


def extend_upstream(resolution: Resolution, index: ResolverIndex) -> Resolution:
    """Walk a reporting-layer column up to its system-of-record source (ER-3).

    The KPI lineage report stops at the reporting database; catalog lineage
    extends it upstream. Where it cannot, the reporting column stands as the
    terminus and source health is scored as non-system-of-record.
    """
    seen: set[str] = set()
    current = resolution
    for _ in range(4):
        table_fqn = current.column_fqn.rsplit(".", 1)[0]
        if index.sor_by_table.get(table_fqn, False):
            return current
        sources = [s for s in index.upstream.get(_norm_fqn(current.column_fqn), [])
                   if s in index.record_by_fqn and s not in seen]
        # Only walk towards the system of record. A staging table upstream of a
        # fact is not "upstream of the reporting database"; it is a load step.
        sources = [s for s in sources if index.sor_by_table.get(s.rsplit(".", 1)[0], False)]
        if len(sources) != 1:
            return current
        seen.add(sources[0])
        current = Resolution(
            sources[0], "ER-3", min(current.confidence, ER_CONFIDENCE["ER-3"]),
            "extended upstream of the reporting database via catalog lineage")
    return current
