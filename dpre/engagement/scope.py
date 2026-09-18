"""Scope and cut-date checks between an engagement and the extract it runs on (R-24).

The engagement record says what the client agreed: which domains are in
scope and the date the data was cut. The extract says what actually arrived.
The two drift in every engagement - a catalog export that includes domains
outside the statement of work, usage rows dated after the cut - and the
drift should be on the run summary before it is in a committee pack.

Nothing here changes the bundle or the score. ``check_scope`` returns issues
in the same shape as ``dpre/ingest/validator.py::ValidationIssue`` so the
pipeline can append them to the manifest warnings without translation.
"""
from __future__ import annotations

import re
import datetime as _dt
from typing import Any

from ..models import ExtractBundle
from .model import EngagementRecord


def _issue(severity: str, code: str, message: str, detail: str = "") -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message, "detail": detail}


#: Filler that distinguishes no two parts of an organisation from each other.
_AREA_STOPWORDS = frozenset({
    "and", "the", "of", "for", "affairs", "team", "teams", "dept", "department",
    "group", "unit", "division", "function", "office", "operations", "services",
    "service", "management", "reporting",
})


def _area_tokens(value: str) -> frozenset[str]:
    cleaned = (value or "").replace("&", " and ").casefold()
    return frozenset(w for w in re.split(r"[^a-z0-9]+", cleaned)
                     if w and w not in _AREA_STOPWORDS)


def _names_the_same_area(domain: str, candidates: set[str]) -> bool:
    """Whether anything in ``candidates`` names the same part of the business.

    'Regulatory' and 'Regulatory Affairs' are one area; 'Finance' and
    'Network Operations' are not. One name's meaningful words have to contain
    the other's, so a partial overlap does not count.
    """
    wanted = _area_tokens(domain)
    if not wanted:
        return False
    return any(tokens and (tokens <= wanted or wanted <= tokens)
               for tokens in (_area_tokens(c) for c in candidates))


def check_scope(record: EngagementRecord, bundle: ExtractBundle) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    # ---- cut date ------------------------------------------------------
    if record.data_cut_date:
        cut = _dt.date.fromisoformat(record.data_cut_date)
        as_of = bundle.as_of_date
        if as_of and as_of > cut:
            issues.append(_issue(
                "warning", "AS_OF_AFTER_CUT_DATE",
                f"extract as-of {as_of.isoformat()} is after the engagement cut date {cut.isoformat()}",
                "The pack cover will carry the cut date; the numbers will carry the as-of. "
                "Either re-cut the extract or record why the later date is acceptable."))
        late = [r.report_id for r in bundle.reports
                if r.last_run_date is not None and r.last_run_date > cut]
        if late:
            issues.append(_issue(
                "info", "USAGE_AFTER_CUT_DATE",
                f"{len(late)} reports carry a last-run date after the cut date",
                ", ".join(sorted(late)[:10])))
    else:
        issues.append(_issue(
            "info", "NO_CUT_DATE", "the engagement records no data cut date",
            "Every number in the pack will be dated by the extract's as-of alone."))

    # ---- domains -------------------------------------------------------
    if record.domains:
        agreed = {d.strip().casefold() for d in record.domains}
        seen: dict[str, int] = {}
        for column in bundle.columns:
            domain = (column.data_domain or "").strip()
            if domain:
                seen[domain] = seen.get(domain, 0) + 1
        outside = {d: n for d, n in seen.items() if d.casefold() not in agreed}
        if outside:
            listed = ", ".join(f"{d} ({n} columns)" for d, n in sorted(outside.items()))
            issues.append(_issue(
                "info", "DOMAINS_OUT_OF_SCOPE",
                f"{len(outside)} catalog domains are outside the engagement scope", listed))
        # A domain can be real in the estate and own no catalog column of its
        # own: Regulatory reporting reads billing and metering tables and
        # registers its presence as the business unit on a report, or as a
        # glossary domain. Declaring it absent on the column evidence alone
        # sends the engagement team looking for an extract that was never
        # missing, so every carrier is checked before a domain is called absent.
        elsewhere = {(term.domain or "").strip() for term in (bundle.glossary or [])}
        elsewhere |= {(report.business_unit or "").strip() for report in bundle.reports}
        elsewhere = {value for value in elsewhere if value}
        missing = [d for d in record.domains
                   if d.strip().casefold() not in {s.casefold() for s in seen}
                   and not _names_the_same_area(d, elsewhere)]
        if missing and bundle.columns:
            issues.append(_issue(
                "warning", "SCOPED_DOMAIN_ABSENT",
                f"{len(missing)} in-scope domains appear nowhere in the extract",
                ", ".join(sorted(missing))))
    else:
        issues.append(_issue(
            "info", "NO_SCOPED_DOMAINS", "the engagement records no in-scope domains",
            "Every domain in the extract is treated as in scope."))

    # ---- engagement window --------------------------------------------
    if record.start_date and record.end_date:
        start, end = (_dt.date.fromisoformat(record.start_date),
                      _dt.date.fromisoformat(record.end_date))
        if end < start:
            issues.append(_issue("warning", "ENGAGEMENT_WINDOW_INVERTED",
                                 "the engagement end date is before its start date"))
    return issues


def scope_summary(record: EngagementRecord, bundle: ExtractBundle) -> dict[str, Any]:
    """Cover-page facts about the extract against the scope, for the run summary."""
    issues = check_scope(record, bundle)
    domains_seen = sorted({(c.data_domain or "").strip() for c in bundle.columns
                           if (c.data_domain or "").strip()})
    return {
        "engagement_id": record.engagement_id,
        "client": record.client,
        "data_cut_date": record.data_cut_date,
        "extract_as_of": bundle.as_of_date.isoformat() if bundle.as_of_date else "",
        "scoped_domains": list(record.domains),
        "domains_in_extract": domains_seen,
        "reports": len(bundle.reports),
        "kpi_rows": len(bundle.kpis),
        "catalog_columns": len(bundle.columns),
        "issues": issues,
        "ok": not any(i["severity"] == "warning" for i in issues),
    }
