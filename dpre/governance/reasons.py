"""Decision vocabulary and reason-code taxonomy (specification section 10.2).

Every reviewer decision carries a reason code, and the codes are the training
signal for the feedback loop (section 13.3), so they are a closed vocabulary
validated server-side rather than free text. This module has no store
dependency so the store can import it without a cycle.
"""
from __future__ import annotations

# Decisions a reviewer can make. ``CarryForward`` is written by the engine when
# it re-applies a prior reviewer decision to the same candidate in a later run
# (R-09); it always names the original reviewer and decision.
DECISIONS = (
    "Accept", "AcceptWithException", "Reject", "Merge", "Split", "Defer", "Override",
    "Reverse", "CarryForward",
)

# Decisions on which a reason code is mandatory. Accept may omit it (the value
# hypothesis is the reason), everything else must say why.
REASON_REQUIRED = frozenset({
    "Reject", "Defer", "Merge", "Split", "Override", "AcceptWithException", "Reverse",
    "CarryForward",
})

OTHER = "other"

REASON_CODES: dict[str, tuple[str, ...]] = {
    "Accept": ("value_clear", "retires_reports", "resolves_conflicts", "strategic",
               "accept_with_open_dependencies", OTHER),
    "AcceptWithException": ("gate_waived", OTHER),
    "Reject": ("no_named_consumer", "duplicate_of_existing", "too_small", "wrong_boundary",
               "source_not_viable", "already_planned", OTHER),
    "Merge": ("same_decision", "same_grain_and_sources", "duplicate_candidate", OTHER),
    "Split": ("mixed_grain", "mixed_consumers", "scope_too_broad", OTHER),
    "Defer": ("awaiting_source_migration", "awaiting_steward", "capacity", "awaiting_privacy",
              OTHER),
    "Override": ("wrong_archetype", "wrong_tier", "wrong_name", "wrong_owner", "wrong_grain",
                 "consumer_confirmed", "decision_critical", "conflict_resolved", "name_accepted",
                 OTHER),
    "Reverse": ("decided_in_error", "new_evidence", "consumer_withdrawn", "source_changed",
                OTHER),
    "CarryForward": ("carried_from_previous_run",),
}

# Fields a reviewer may override, and the closed values where one exists.
# Free-text fields are length-capped so a 5,000-character "value" is refused.
OVERRIDABLE_FIELDS = ("archetype", "tier", "proposed_name", "grain", "owner_candidate",
                      "steward_candidate", "name_status", "domain")
OVERRIDE_VALUE_ENUMS: dict[str, tuple[str, ...]] = {
    "archetype": ("Entity Master", "Reference Data", "Event Stream", "Metric / KPI",
                  "Feature Store", "Insight / Recommendation"),
    "tier": ("Source-aligned", "Aggregate", "Consumer-aligned"),
    "name_status": ("AI_DRAFT", "ACCEPTED"),
}
OVERRIDE_VALUE_MAX_LENGTH = 200


class ReasonCodeError(ValueError):
    """Raised when a decision carries a reason code outside the taxonomy."""


class OverrideValueError(ValueError):
    """Raised when an Override names a field or value outside the allowed set."""


def validate_reason(decision: str, reason_code: str, note: str = "") -> str:
    """Return the validated reason code or raise ``ReasonCodeError``.

    Rules (R-43): the code must belong to the decision's list; it is mandatory
    for Reject, Defer, Merge, Split, Override, AcceptWithException, Reverse and
    CarryForward; ``other`` requires a note explaining what the taxonomy lacks.
    """
    if decision not in REASON_CODES:
        raise ReasonCodeError(f"unknown decision '{decision}'; expected one of "
                              + ", ".join(DECISIONS))
    code = (reason_code or "").strip()
    if not code:
        if decision in REASON_REQUIRED:
            raise ReasonCodeError(
                f"{decision} requires a reason code; one of "
                + ", ".join(REASON_CODES[decision]))
        return ""
    if code not in REASON_CODES[decision]:
        raise ReasonCodeError(
            f"'{code}' is not a reason code for {decision}; expected one of "
            + ", ".join(REASON_CODES[decision]))
    if code == OTHER and not (note or "").strip():
        raise ReasonCodeError("reason code 'other' requires a note saying what the taxonomy lacks")
    return code


def validate_override(field: str, value: str) -> tuple[str, str]:
    """Check an Override's field and value against the allowed set (R-15)."""
    if field not in OVERRIDABLE_FIELDS:
        raise OverrideValueError(
            f"'{field}' cannot be overridden; allowed fields are "
            + ", ".join(OVERRIDABLE_FIELDS))
    value = (value or "").strip()
    if not value:
        raise OverrideValueError(f"an override of {field} needs a new value")
    if len(value) > OVERRIDE_VALUE_MAX_LENGTH:
        raise OverrideValueError(
            f"override value for {field} exceeds {OVERRIDE_VALUE_MAX_LENGTH} characters")
    allowed = OVERRIDE_VALUE_ENUMS.get(field)
    if allowed and value not in allowed:
        raise OverrideValueError(
            f"'{value}' is not a valid {field}; expected one of " + ", ".join(allowed))
    return field, value
