"""Run quality: gates that can be trusted, measured detection, input DQ, replay.

Each module here answers one review finding and exposes plain functions over
the objects the pipeline already has; the schema each defines is created by its
``ensure_schema``. Nothing in this package decides anything: it measures, and
the numbers go on the manifest for a human to read.
"""
from .gates import (comparable_previous_run, gate_state, run_publishable, stability_between,
                    stability_gate)
from .dq import dq_scorecard, ensure_schema as ensure_dq_schema, save_dq_scorecard
from .detection import (detection_scorecard, ensure_schema as ensure_detection_schema,
                        save_detection_scorecard)
from .replay import config_hash, config_snapshot, file_digests, freshness_gate, input_dates
from .remediation import (ensure_schema as ensure_remediation_schema, remediation_plan,
                          save_remediation_plan, write_remediation_plan_csv)
from .stewardship import (ensure_schema as ensure_stewardship_schema, save_stewardship_requests,
                          stewardship_register)
from .bias import bias_register

__all__ = [
    "comparable_previous_run", "gate_state", "run_publishable", "stability_between",
    "stability_gate", "dq_scorecard", "ensure_dq_schema", "save_dq_scorecard",
    "detection_scorecard", "ensure_detection_schema", "save_detection_scorecard",
    "config_hash", "config_snapshot", "file_digests", "freshness_gate", "input_dates",
    "ensure_remediation_schema", "remediation_plan", "save_remediation_plan",
    "write_remediation_plan_csv", "ensure_stewardship_schema", "save_stewardship_requests",
    "stewardship_register", "bias_register",
]
