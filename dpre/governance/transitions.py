"""The candidate status state machine (specification sections 8.3, 10.2, 13.1).

A candidate arrives Blocked, Exploratory or Proposed from the gates. Only a
reviewer decision moves it, and only along the transitions listed here; the
store refuses anything else so an auditor finds the same machine in the code,
the data and this table.
"""
from __future__ import annotations

from ..config import STATUS_ORDER

# Statuses a reviewer decision can start from. Terminal statuses only leave via
# Reverse, which needs a reason and a different actor from the original decision.
ENGINE_STATUSES = ("Blocked", "Exploratory", "Proposed")
REVIEWED_STATUSES = ("Accepted", "Rejected", "Merged", "Deferred")

# (from_status, decision) -> to_status. ``None`` means the status is unchanged.
ALLOWED_TRANSITIONS: dict[tuple[str, str], str | None] = {
    # Accept: Blocked is refused by the gate check before this table is consulted;
    # Exploratory needs a consumer confirmation or an exception (R-02).
    ("Proposed", "Accept"): "Accepted",
    ("Exploratory", "Accept"): "Accepted",
    ("Deferred", "Accept"): "Accepted",
    ("Exploratory", "AcceptWithException"): "Accepted",
    ("Deferred", "AcceptWithException"): "Accepted",
    # Reject and Merge are allowed from any engine status and from Deferred.
    ("Proposed", "Reject"): "Rejected",
    ("Exploratory", "Reject"): "Rejected",
    ("Blocked", "Reject"): "Rejected",
    ("Deferred", "Reject"): "Rejected",
    ("Proposed", "Merge"): "Merged",
    ("Exploratory", "Merge"): "Merged",
    ("Blocked", "Merge"): "Merged",
    ("Deferred", "Merge"): "Merged",
    ("Proposed", "Defer"): "Deferred",
    ("Exploratory", "Defer"): "Deferred",
    ("Blocked", "Defer"): "Deferred",
    # Split and Override leave the status where it is.
    ("Proposed", "Split"): None,
    ("Exploratory", "Split"): None,
    ("Blocked", "Split"): None,
    ("Proposed", "Override"): None,
    ("Exploratory", "Override"): None,
    ("Blocked", "Override"): None,
    ("Deferred", "Override"): None,
    ("Accepted", "Override"): None,
    # Reverse undoes a reviewed status. Accepted -> Rejected is the only way an
    # acceptance is withdrawn; the others re-open the candidate.
    ("Accepted", "Reverse"): "Rejected",
    ("Rejected", "Reverse"): "Proposed",
    ("Merged", "Reverse"): "Proposed",
    ("Deferred", "Reverse"): "Proposed",
}

# CarryForward re-applies a prior run's reviewed status to the matched candidate
# in a new run. Accepted can never land on Blocked: a sunset source found since
# the acceptance re-opens the question for a human.
CARRY_FORWARD_ALLOWED: dict[str, tuple[str, ...]] = {
    "Proposed": ("Accepted", "Rejected", "Merged", "Deferred"),
    "Exploratory": ("Accepted", "Rejected", "Merged", "Deferred"),
    "Blocked": ("Rejected", "Merged", "Deferred"),
}


class TransitionError(ValueError):
    """Raised when a decision is not allowed from the candidate's current status."""


def next_status(from_status: str, decision: str, carried: str = "") -> str | None:
    """Return the status a decision leads to, or raise ``TransitionError``.

    ``carried`` is the previous run's status for a CarryForward decision.
    """
    if from_status not in STATUS_ORDER:
        raise TransitionError(f"unknown status '{from_status}'")
    if decision == "CarryForward":
        allowed = CARRY_FORWARD_ALLOWED.get(from_status, ())
        if carried not in allowed:
            raise TransitionError(
                f"cannot carry status '{carried}' forward onto a {from_status} candidate")
        return carried
    key = (from_status, decision)
    if key not in ALLOWED_TRANSITIONS:
        options = sorted(d for (s, d) in ALLOWED_TRANSITIONS if s == from_status)
        raise TransitionError(
            f"{decision} is not allowed from status {from_status}; allowed decisions are "
            + (", ".join(options) if options else "none"))
    return ALLOWED_TRANSITIONS[key]


def transitions_table() -> list[dict]:
    """The state machine as rows, for documentation and the API."""
    rows = [{"from_status": s, "decision": d, "to_status": t or s}
            for (s, d), t in ALLOWED_TRANSITIONS.items()]
    for status, carried in CARRY_FORWARD_ALLOWED.items():
        for target in carried:
            rows.append({"from_status": status, "decision": "CarryForward", "to_status": target})
    return rows
