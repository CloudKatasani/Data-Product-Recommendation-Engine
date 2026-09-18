"""Registers: what the engine assumes, what the client has decided, what an
auditor can test, and how section 14 is traced (R-25, R-27, R-40).

* ``assumptions``   - the scoring and classification constants, read live,
                      with owner and decision reference; the value rate card
                      by reference; a config_version hash per run
* ``decisions``     - D-01..D-08 with the engine position, the honest
                      enforcement status and an append-only decision log
* ``controls``      - the controls matrix, RACI and framework mapping, plus a
                      static check that every cited function and test exists
* ``traceability``  - section 14 exit criteria and falsifiers, the grouping
                      verdict ledger, and the computable phase metrics

Nothing here imports ``dpre.pipeline`` or ``dpre.store``; the pipeline calls
in with a connection or a store.
"""
from .assumptions import (
    Assumption, assumption_register, assumptions_for_run, config_version, diff_registers,
    register_hash, snapshot_assumptions, value_assumption_rows,
)
from .controls import (
    CONTROLS, RACI, RACI_ROLES, Control, controls_matrix, framework_mapping, raci,
    verify_controls,
)
from .decisions import (
    DECISIONS, DecisionError, DecisionSpec, decide, decision_log, decision_register,
    decisions_for_run, decisions_taken, open_decisions, snapshot_decisions,
)
from .traceability import (
    CRITERIA, QUESTION_BANK, Criterion, VerdictError, citation_rate, grouping_rejection_rate,
    grouping_verdicts, phase_metrics, record_grouping_verdict, stage2_metric_drift,
    traceability_matrix,
)

__all__ = [
    "Assumption", "assumption_register", "assumptions_for_run", "config_version",
    "diff_registers", "register_hash", "snapshot_assumptions", "value_assumption_rows",
    "CONTROLS", "RACI", "RACI_ROLES", "Control", "controls_matrix", "framework_mapping", "raci",
    "verify_controls", "DECISIONS", "DecisionError", "DecisionSpec", "decide", "decision_log",
    "decision_register", "decisions_for_run", "decisions_taken", "open_decisions",
    "snapshot_decisions", "CRITERIA", "QUESTION_BANK", "Criterion", "VerdictError",
    "citation_rate", "grouping_rejection_rate", "grouping_verdicts", "phase_metrics",
    "record_grouping_verdict", "stage2_metric_drift", "traceability_matrix",
]
