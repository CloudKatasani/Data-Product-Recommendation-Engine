"""Evidence per feature, enforced as structure (review finding R-22).

Section 8.4 promises that a reviewer can open any number and see the rows that
produced it. The store enforced that promise per candidate: one evidence row
anywhere on the card satisfied the check, while the feature a reviewer actually
opens - sensitivity, conflict load, grain ambiguity - often had none. The rules
here make the promise hold at the level it is read:

* Every feature writes at least one row, and a feature with nothing to cite
  writes *negative evidence* ("no PII columns; highest class Internal") - the
  absence is itself the fact behind the number.
* Additive features carry their addends. A row's contribution is written in
  a fixed marker, ``(contributes 12.3456)``, so the sum of the rows can be
  checked against the feature value by a test, not taken on trust.
* The normaliser is recorded: the run maximum and the candidate that set it.

``missing_feature_evidence`` is the constraint check; the scorer raises on it
and the store is expected to call it before writing (see the wiring notes).
"""
from __future__ import annotations

import re

from ..models import Candidate, EvidenceRow

_CONTRIBUTION = re.compile(r"\(contributes (-?[0-9]+(?:\.[0-9]+)?)\)\s*$")

# Features whose value is the sum of their evidence contributions.
ADDITIVE_FEATURES = ("usage_weight", "reports_retirable", "variants_collapsed",
                     "conflicts_surfaced", "consumer_breadth")
NEGATIVE_EVIDENCE = "none"


class FeatureEvidenceError(ValueError):
    """A score feature would be written without an evidence row behind it."""


def contributes(text: str, value: float) -> str:
    """Append the machine-readable contribution marker to a detail string."""
    return f"{text} (contributes {value:.4f})"


def contribution_of(row: EvidenceRow | dict) -> float | None:
    detail = row.detail if isinstance(row, EvidenceRow) else str(row.get("detail", ""))
    match = _CONTRIBUTION.search(detail or "")
    return float(match.group(1)) if match else None


def negative(candidate_id: str, feature: str, text: str) -> EvidenceRow:
    """A row that records what was looked for and not found."""
    return EvidenceRow(candidate_id, feature, NEGATIVE_EVIDENCE, "none", text)


def evidence_sum(candidate: Candidate, feature: str) -> float:
    return round(sum(c for c in (contribution_of(r) for r in candidate.evidence
                                  if r.feature == feature) if c is not None), 4)


def missing_feature_evidence(candidate: Candidate) -> list[str]:
    """Features on the score that have no evidence row at all."""
    if candidate.score is None:
        return []
    covered = {row.feature for row in candidate.evidence}
    return [f.feature for f in candidate.score.features if f.feature not in covered]


def assert_feature_evidence(candidates: list[Candidate]) -> None:
    problems = []
    for candidate in candidates:
        missing = missing_feature_evidence(candidate)
        if missing:
            problems.append(f"{candidate.candidate_id}: {', '.join(missing)}")
    if problems:
        raise FeatureEvidenceError("score features without evidence rows: "
                                   + "; ".join(problems))


def evidence_summary(candidate: Candidate) -> dict[str, dict]:
    """Per feature: how many rows, and the sum of contributions where additive."""
    out: dict[str, dict] = {}
    for row in candidate.evidence:
        entry = out.setdefault(row.feature, {"rows": 0, "negative": 0, "sum": 0.0})
        entry["rows"] += 1
        if row.evidence_type == NEGATIVE_EVIDENCE:
            entry["negative"] += 1
        value = contribution_of(row)
        if value is not None:
            entry["sum"] = round(entry["sum"] + value, 4)
    return out
