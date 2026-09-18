"""Deprecated: the glossary of record moved to :mod:`dpre.glossary.terms`.

Term resolution briefly lived here while the graph package was being taught to
read the business glossary (review finding R-34). It now belongs to a package of
its own, because the glossary is also the source of the glossary delta the
engine proposes back to the governance office, which is not a graph concern.

This module stays only so an import written against the earlier layout keeps
working; it adds nothing. New code imports from :mod:`dpre.glossary`.
"""
from __future__ import annotations

from ..glossary.terms import (  # noqa: F401  (re-exported for compatibility)
    DEFINITION_CREDIT, TermLink, definition_credit, glossary_summary, link_terms,
    status_class, term_link_for, term_links,
)

__all__ = [
    "DEFINITION_CREDIT", "TermLink", "definition_credit", "glossary_summary",
    "link_terms", "status_class", "term_link_for", "term_links",
]
