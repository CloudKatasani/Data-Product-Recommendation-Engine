"""Governance core: audit integrity, decision workflow rules and the ledgers
that outlive a run.

* ``reasons``      - decision vocabulary and reason-code taxonomy (R-43)
* ``transitions``  - the candidate status state machine (R-15)
* ``ledger``       - run-independent tables, append-only triggers, hashing
* ``identity``     - lineage ids and run-over-run carry-forward (R-09)
* ``benefits``     - planned versus realised benefits after Accept (R-09)
* ``seeding``      - re-apply steward decisions to a new run (R-14, R-57, R-07)
* ``audit``        - the report an auditor reads

Nothing here imports ``dpre.pipeline``; the pipeline calls in.
"""
from .audit import audit_report
from .benefits import EVENT_TYPES, benefits_summary, realisation_view, record_benefit_event
from .identity import carry_forward, jaccard, lineage_id, lineage_id_for
from .ledger import SCHEMA, ensure_schema, utc_now
from .reasons import (
    DECISIONS, OVERRIDABLE_FIELDS, REASON_CODES, REASON_REQUIRED, OverrideValueError,
    ReasonCodeError, validate_override, validate_reason,
)
from .seeding import seed_from_ledgers, seed_log
from .transitions import (
    ALLOWED_TRANSITIONS, REVIEWED_STATUSES, TransitionError, next_status, transitions_table,
)

__all__ = [
    "audit_report", "EVENT_TYPES", "benefits_summary", "realisation_view",
    "record_benefit_event", "carry_forward", "jaccard", "lineage_id", "lineage_id_for",
    "SCHEMA", "ensure_schema", "utc_now", "DECISIONS", "OVERRIDABLE_FIELDS", "REASON_CODES",
    "REASON_REQUIRED", "OverrideValueError", "ReasonCodeError", "validate_override",
    "validate_reason", "seed_from_ledgers", "seed_log", "ALLOWED_TRANSITIONS",
    "REVIEWED_STATUSES", "TransitionError", "next_status", "transitions_table",
]
