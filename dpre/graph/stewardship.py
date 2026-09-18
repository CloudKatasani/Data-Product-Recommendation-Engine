"""Stewardship resolution (specification sections 5.1 step 6 and 9.1).

Ownership and stewardship are different roles. An *owner* is accountable for a
domain; a *steward* is responsible for a definition. Review finding R-44 showed
the engine conflating them: when no column carried a steward it promoted the
most frequent report owner, which put 17 of 114 banking metrics in the hands of
someone who had never agreed to the job, and it ignored the glossary steward
entirely.

The order below is fixed, so two runs over the same estate name the same person,
and each step carries the confidence a reviewer should read it with:

1. the steward of the glossary term of record        (1.00)
2. the steward of the fact table, by majority of its columns (0.85)
3. the steward of the data domain                    (0.70)
4. the owner of the data domain, as an escalation    (0.55)
5. the most frequent report owner, as a *suggestion* flagged "not a steward" (0.30)

Nothing here confirms a steward. Confirmation is a human act recorded in the
review ledger; the engine only proposes (section 13.1).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..config import STEWARD_SUGGESTION_SOURCE
from ..glossary.terms import term_link_for
from ..models import KnowledgeGraph
from .domains import domain_nodes

_ORDER_CONFIDENCE = {
    "glossary term steward": 1.00,
    "table steward": 0.85,
    "domain steward": 0.70,
    "domain owner (escalation)": 0.55,
    STEWARD_SUGGESTION_SOURCE: 0.30,
    "unassigned": 0.0,
}


@dataclass
class StewardAssignment:
    """Who the engine proposes, on what basis, and how sure it is."""

    steward_id: str = ""
    source: str = "unassigned"
    confidence: float = 0.0
    is_suggestion: bool = False          # a report owner, not a steward
    is_escalation: bool = False          # nobody is responsible; an owner is asked
    confirmed: bool = False              # only a reviewer decision sets this
    disagreement: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "steward_id": self.steward_id, "source": self.source,
            "confidence": self.confidence, "is_suggestion": self.is_suggestion,
            "is_escalation": self.is_escalation, "confirmed": self.confirmed,
            "disagreement": self.disagreement, "detail": self.detail,
        }


def table_steward(graph: KnowledgeGraph, table_fqn: str) -> tuple[str, str]:
    """Majority steward of a table's columns, with a disagreement note.

    ``table_meta.setdefault`` used to take whichever column record the extract
    happened to list first, so the answer depended on row order (R-44).
    """
    # The catalog's own steward on the column, before any glossary override, is
    # the table-level signal: the glossary speaks per term, not per table.
    stewards = Counter(
        _catalog_steward(column) for column in graph.columns.values()
        if column.table_fqn == table_fqn and _catalog_steward(column)
    )
    if not stewards:
        return "", ""
    ranked = stewards.most_common()
    winner, votes = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == votes:
        # A tie is resolved alphabetically so the run is reproducible, and said
        # out loud so a steward can settle it.
        tied = sorted(name for name, count in ranked if count == votes)
        winner = tied[0]
        return winner, (f"{len(tied)} stewards tie on {table_fqn.rsplit('.', 1)[-1]}: "
                        + ", ".join(tied))
    if len(ranked) > 1:
        others = ", ".join(f"{name} ({count})" for name, count in ranked[1:3])
        return winner, (f"{winner} holds {votes} of "
                        f"{sum(stewards.values())} columns; also named: {others}")
    return winner, ""


def _catalog_steward(column) -> str:
    recorded = getattr(column, "_column_steward", None)
    return column.steward_id if recorded is None else recorded


def domain_roles(graph: KnowledgeGraph, domain: str) -> tuple[str, str]:
    """``(steward, owner)`` of a data domain, by majority across its columns."""
    if not domain:
        return "", ""
    node = domain_nodes(graph).get(domain)
    return (node.steward, node.owner) if node is not None else ("", "")


def assign_steward(operand_columns: list[str], report_ids: list[str],
                   graph: KnowledgeGraph, domain: str = "") -> StewardAssignment:
    """Resolve the steward of one canonical metric, in the documented order."""
    columns = [graph.columns[fqn] for fqn in operand_columns if fqn in graph.columns]

    # 1. The glossary term of record.
    for column in columns:
        link = term_link_for(graph, column.column_fqn)
        if link is not None and link.term_id and link.glossary_steward:
            disagreement = ""
            if link.steward_disagreement:
                disagreement = (f"the glossary names {link.glossary_steward}, the column "
                                f"extract names {link.column_steward}")
            return StewardAssignment(
                steward_id=link.glossary_steward, source="glossary term steward",
                confidence=_ORDER_CONFIDENCE["glossary term steward"],
                disagreement=disagreement,
                detail=f"steward of glossary term '{link.business_term}' ({link.term_id})",
            )

    # 2. The fact table, by majority of its columns.
    for table_fqn in _tables_by_weight(columns):
        steward, note = table_steward(graph, table_fqn)
        if steward:
            return StewardAssignment(
                steward_id=steward, source="table steward",
                confidence=_ORDER_CONFIDENCE["table steward"], disagreement=note,
                detail=f"majority steward of {table_fqn.rsplit('.', 1)[-1]}",
            )

    # 3 and 4. The domain: its steward, then its owner as an escalation.
    domain = domain or _dominant_domain(columns)
    steward, owner = domain_roles(graph, domain)
    if steward:
        return StewardAssignment(
            steward_id=steward, source="domain steward",
            confidence=_ORDER_CONFIDENCE["domain steward"],
            detail=f"steward of the {domain} domain; no table-level steward is recorded",
        )
    if owner:
        return StewardAssignment(
            steward_id=owner, source="domain owner (escalation)",
            confidence=_ORDER_CONFIDENCE["domain owner (escalation)"], is_escalation=True,
            detail=(f"nobody is recorded as responsible for these columns; the {domain} "
                    "domain owner is asked to nominate a steward"),
        )

    # 5. A report owner is a suggestion, never an assignment.
    owners = Counter(graph.reports[r].owner for r in report_ids
                     if r in graph.reports and graph.reports[r].owner)
    if owners:
        suggested = owners.most_common(1)[0][0]
        return StewardAssignment(
            steward_id=suggested, source=STEWARD_SUGGESTION_SOURCE,
            confidence=_ORDER_CONFIDENCE[STEWARD_SUGGESTION_SOURCE], is_suggestion=True,
            is_escalation=True,
            detail=(f"{suggested} owns the reports that use this metric and is suggested "
                    "as a starting point; owning a report does not make them its steward"),
        )
    return StewardAssignment(detail="no steward, domain owner or report owner is recorded")


def _tables_by_weight(columns) -> list[str]:
    """Operand tables, the one contributing most columns first, then by name."""
    counts = Counter(column.table_fqn for column in columns)
    return [fqn for fqn, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _dominant_domain(columns) -> str:
    domains = Counter(column.domain for column in columns if column.domain)
    return domains.most_common(1)[0][0] if domains else ""
