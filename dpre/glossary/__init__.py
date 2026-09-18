"""The business glossary as the term of record (review finding R-34)."""
from .terms import TermLink, link_terms, term_link_for, term_links, DEFINITION_CREDIT
from .delta import glossary_delta, write_glossary_delta

__all__ = ["TermLink", "link_terms", "term_link_for", "term_links", "DEFINITION_CREDIT",
           "glossary_delta", "write_glossary_delta"]
