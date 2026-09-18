"""Link catalog columns to glossary terms and let the glossary speak (R-34).

DMBOK and DCAM both expect the business glossary to be the term of record: the
place where a definition, its steward and its lifecycle status are governed.
The engine ingested the glossary and then read every definition and steward
from the column extract instead, so a Draft term counted fully towards
definition coverage and forty-odd columns carried a steward the glossary
disagreed with.

This module resolves each column's ``business_term`` to a glossary term at
graph build time and records, per column, where the definition and the steward
came from. When the glossary has a term: its definition fills a missing column
definition, its steward takes precedence over the column's (the column's own
steward is kept for the disagreement flag), and its status decides how much
the term is worth in definition coverage (specification section 8.1):

    Approved / Certified / Published  1.0
    Draft / Proposed / Candidate      0.5, flagged
    Deprecated / Retired / Obsolete   0.0, and a gap

Everything is a plain attribute on the column node rather than a schema change,
because the graph model belongs to another package; the accessor functions
below are the contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..models import ColumnNode, KnowledgeGraph
from ..util.text import token_key

APPROVED_STATUSES = {"approved", "certified", "published", "accepted", "active"}
DRAFT_STATUSES = {"draft", "proposed", "candidate", "in review", "review", "pending"}
RETIRED_STATUSES = {"deprecated", "retired", "obsolete", "rejected", "archived"}

# How much a term counts towards definition coverage, by lifecycle status.
DEFINITION_CREDIT = {"approved": 1.0, "draft": 0.5, "retired": 0.0, "none": 0.0,
                     "column-only": 1.0}


@dataclass
class TermLink:
    column_fqn: str
    business_term: str
    term_id: str = ""
    status: str = ""                  # the glossary's own status text
    status_class: str = "none"        # approved | draft | retired | none | column-only
    definition_source: str = "none"   # glossary | column extract | none
    steward_source: str = "none"      # glossary | column extract | none
    glossary_steward: str = ""
    column_steward: str = ""
    steward_disagreement: bool = False

    @property
    def credit(self) -> float:
        return DEFINITION_CREDIT.get(self.status_class, 0.0)

    def to_dict(self) -> dict:
        return {**asdict(self), "credit": self.credit}


def status_class(status: str) -> str:
    text = (status or "").strip().lower()
    if not text:
        return "none"
    if text in APPROVED_STATUSES:
        return "approved"
    if text in DRAFT_STATUSES:
        return "draft"
    if text in RETIRED_STATUSES:
        return "retired"
    return "draft"        # an unknown status is not yet approved


def link_terms(graph: KnowledgeGraph) -> dict[str, TermLink]:
    """Resolve every column's business term against the glossary and apply it.

    Mutates the column nodes: ``definition`` is filled from the glossary when the
    column has none, ``steward_id`` becomes the glossary steward when the term
    names one. The column's own values are kept on the link (and on the node as
    ``_column_steward``) so a disagreement is visible, not overwritten.
    """
    links: dict[str, TermLink] = {}
    for fqn in sorted(graph.columns):
        column = graph.columns[fqn]
        setattr(column, "_column_steward", column.steward_id)
        if not column.business_term:
            continue
        term = graph.glossary.get(token_key(column.business_term))
        if term is None:
            link = TermLink(
                column_fqn=fqn, business_term=column.business_term, status_class="column-only",
                definition_source="column extract" if column.definition else "none",
                steward_source="column extract" if column.steward_id else "none",
                column_steward=column.steward_id,
            )
        else:
            link = TermLink(
                column_fqn=fqn, business_term=column.business_term, term_id=term.term_id,
                status=term.status, status_class=status_class(term.status),
                glossary_steward=term.steward, column_steward=column.steward_id,
            )
            if term.definition:
                column.definition = term.definition
                link.definition_source = "glossary"
            elif column.definition:
                link.definition_source = "column extract"
            if term.steward:
                link.steward_disagreement = bool(column.steward_id
                                                 and column.steward_id != term.steward)
                column.steward_id = term.steward
                link.steward_source = "glossary"
            elif column.steward_id:
                link.steward_source = "column extract"
        links[fqn] = link
        setattr(column, "_term_link", link)
    setattr(graph, "_term_links", links)
    return links


def term_links(graph: KnowledgeGraph) -> dict[str, TermLink]:
    links = getattr(graph, "_term_links", None)
    return links if links is not None else link_terms(graph)


def term_link_for(graph: KnowledgeGraph, column_fqn: str) -> TermLink | None:
    return term_links(graph).get(column_fqn)


def definition_credit(graph: KnowledgeGraph, column: ColumnNode) -> float:
    """How much this column's definition counts (section 8.1, adjusted by status)."""
    if not (column.business_term and column.definition):
        return 0.0
    link = term_link_for(graph, column.column_fqn)
    return link.credit if link else 1.0


def glossary_summary(graph: KnowledgeGraph) -> dict:
    """Headline numbers for the manifest and the Gaps tab."""
    links = term_links(graph)
    classes: dict[str, int] = {}
    for link in links.values():
        classes[link.status_class] = classes.get(link.status_class, 0) + 1
    return {
        "glossary_terms": len(graph.glossary),
        "columns_with_term": len(links),
        "columns_linked_to_glossary": sum(1 for l in links.values() if l.term_id),
        "columns_by_term_status": classes,
        "steward_disagreements": sum(1 for l in links.values() if l.steward_disagreement),
        "definitions_from_glossary": sum(1 for l in links.values()
                                         if l.definition_source == "glossary"),
    }
