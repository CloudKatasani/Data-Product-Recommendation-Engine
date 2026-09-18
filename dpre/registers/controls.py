"""The controls matrix, RACI and framework mapping an audit function can adopt (R-27).

Specification section 13.1 and the README list guardrails "enforced by
structure". An auditor does not read guardrails; an auditor reads a controls
matrix: control objective, the control, whether it is preventive or
detective, who owns it, how often it operates, where the evidence sits, how
to test it, and the code and test that prove it exists. Most of those
controls already exist in this codebase. They were never presented as
controls, so a client's audit function had to reverse-engineer them from
``dpre/store.py`` and the test names.

Each control below cites the enforcing function as ``path::name`` and the
proving tests as ``tests/file.py::test_name``. ``verify_controls`` checks,
statically, that every cited function and test still exists in the source
tree, so a refactor that quietly drops a control fails a test here rather than
surfacing in an audit. It does not run the tests; ``python -m pytest tests``
does, and the matrix says which ones.

Frameworks are cited by name and practice, not by page: DAMA-DMBOK knowledge
areas, DCAM capability components, and COBIT 2019 management practices
(APO14 Managed Data, DSS06 Managed Business Process Controls, and the few
others that fit). The mapping is a starting point for the client's own
framework crosswalk, which is theirs to finish.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

PREVENTIVE = "preventive"
DETECTIVE = "detective"

# The roles the specification names (sections 10.2, 13.3, 15.1, 15.2) plus the
# two the operating model needs to run the thing at all.
RACI_ROLES = (
    "Programme sponsor", "Data product council", "Domain steward", "Reviewer (domain data "
    "product owner)", "Privacy officer", "Catalog admin", "Rationalization lead",
    "Engine team / operator", "Named consumer",
)


@dataclass(frozen=True)
class Control:
    control_id: str
    objective: str
    control: str
    kind: str                       # preventive | detective
    owner: str
    frequency: str
    evidence: str                   # table, ledger or artefact where evidence sits
    test_procedure: str
    enforcing: tuple[str, ...]      # path::name
    proving_tests: tuple[str, ...]  # tests/file.py::test_name
    frameworks: dict[str, str] = field(default_factory=dict)
    spec_section: str = ""
    status: str = "operating"       # operating | partial | proposed

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["enforcing"] = list(self.enforcing)
        payload["proving_tests"] = list(self.proving_tests)
        return payload


_DG = "Data Governance"
_DQ = "Data Quality"
_MD = "Metadata"
_SEC = "Data Security"

CONTROLS: tuple[Control, ...] = (
    Control(
        "C-01", "No engine path can move a candidate past Proposed",
        "Store.save_candidates raises ProposeOnlyError for any status past ENGINE_MAX_STATUS; "
        "only a REVIEW_DECISION row naming a reviewer moves a status",
        PREVENTIVE, "Engine team / operator", "every write", "DP_CANDIDATE.status; REVIEW_DECISION",
        "Attempt to persist a candidate with status Accepted through the store API; expect "
        "ProposeOnlyError. Query DP_CANDIDATE for reviewed statuses with no decision row "
        "(Store.orphan_statuses) and expect none.",
        ("dpre/store.py::save_candidates", "dpre/store.py::orphan_statuses",
         "dpre/config.py::ENGINE_MAX_STATUS"),
        ("tests/test_acceptance.py::test_no_code_path_but_a_review_decision_moves_a_candidate_past_proposed",
         "tests/test_cluster_score.py::test_engine_never_proposes_past_proposed"),
        {"DMBOK": _DG, "DCAM": "Data Governance - decision rights",
         "COBIT": "DSS06.03 Manage roles, responsibilities, access privileges and levels of authority"},
        "13.1, 14.1"),
    Control(
        "C-02", "Every review decision is attributable to an authenticated person",
        "Store.record_decision refuses an empty reviewer; over HTTP the reviewer is the "
        "resolved principal, never a string in the request body",
        PREVENTIVE, "Programme sponsor", "every decision", "REVIEW_DECISION.reviewer, actor_role",
        "POST a review with a different reviewer in the body than in the identity header; "
        "expect the stored row to carry the principal.",
        ("dpre/store.py::record_decision", "dpre/server/security.py::resolve_principal"),
        ("tests/test_server.py::test_review_identity_comes_from_the_principal_not_the_body",
         "tests/test_security.py::test_identity_is_taken_from_the_header_and_the_body_is_ignored"),
        {"DMBOK": _DG, "DCAM": "Data Governance - accountability",
         "COBIT": "DSS06.05 Ensure traceability and accountability for information events"},
        "10.2"),
    Control(
        "C-03", "Only an authorised role may accept, steward, waive or approve, within its domains",
        "authorize checks the role-to-action matrix and, for review, steward and download, "
        "the principal's domain scope (D-08)",
        PREVENTIVE, "Programme sponsor", "every request", "ROLE_ACTIONS; token map; proxy identity",
        "Call a council-only route as a reviewer and expect 403; call review on a domain "
        "outside the principal's scope and expect 403.",
        ("dpre/server/security.py::authorize", "dpre/server/security.py::ROLE_ACTIONS",
         "dpre/server/security.py::DOMAIN_SCOPED"),
        ("tests/test_security.py::test_a_role_without_the_action_is_refused",
         "tests/test_security.py::test_domain_scope_bounds_review_and_download",
         "tests/test_security.py::test_the_role_matrix_is_the_documented_one"),
        {"DMBOK": _SEC, "DCAM": "Data Control Environment - access",
         "COBIT": "DSS05.04 Manage user identity and logical access"},
        "15.2 D-08"),
    Control(
        "C-04", "No score is published without the evidence rows behind it",
        "Store.save_candidates raises EvidenceMissingError for a score without evidence; "
        "Store.publish refuses a run whose scores lack evidence",
        PREVENTIVE, "Engine team / operator", "every run",
        "DP_CANDIDATE_SCORE joined to DP_CANDIDATE_EVIDENCE",
        "Strip the evidence from a candidate and persist it; expect EvidenceMissingError. "
        "Query scores with no evidence rows on a published run; expect none.",
        ("dpre/store.py::save_candidates", "dpre/store.py::publish"),
        ("tests/test_acceptance.py::test_a_score_row_without_evidence_cannot_be_written",
         "tests/test_acceptance.py::test_published_runs_have_evidence_behind_every_score",
         "tests/test_cluster_score.py::test_every_score_carries_evidence"),
        {"DMBOK": _DQ, "DCAM": "Data Quality Management - evidence",
         "COBIT": "APO14.06 Ensure a data quality assessment approach"},
        "8.4, 13.1, 14.1"),
    Control(
        "C-05", "Hard gates G1-G4 bind at acceptance, not only at scoring",
        "Store._check_accept_gates raises GateError on Accept of a Blocked candidate and on "
        "an Exploratory one without a consumer confirmation or an exception",
        PREVENTIVE, "Data product council", "every Accept", "REVIEW_DECISION; GATE_WAIVER",
        "Accept a Blocked candidate; expect GateError naming G4. Accept an Exploratory one "
        "with no confirmation; expect GateError.",
        ("dpre/store.py::_check_accept_gates", "dpre/score/scorer.py::_apply_gates"),
        ("tests/test_acceptance.py::test_a_gated_candidate_cannot_be_accepted_without_an_exception_row",
         "tests/test_governance.py::test_accept_on_blocked_raises_with_the_gate_detail",
         "tests/test_cluster_score.py::test_hard_gates_cap_the_status"),
        {"DMBOK": _DG, "DCAM": "Data Governance - control gates",
         "COBIT": "DSS06.02 Control the processing of information"},
        "8.3"),
    Control(
        "C-06", "A gate exception needs a second approver (four-eyes)",
        "accept_with_exception refuses the same person as reviewer and second approver and "
        "writes a GATE_WAIVER row that stays open until the gate clears",
        PREVENTIVE, "Data product council", "every exception", "GATE_WAIVER; REVIEW_DECISION.second_approver",
        "Grant an exception with reviewer == second approver; expect refusal. List open "
        "waivers (Store.open_waivers) and reconcile to decisions.",
        ("dpre/store.py::_check_accept_gates", "dpre/review/workflow.py::accept_with_exception"),
        ("tests/test_governance.py::test_exploratory_needs_a_consumer_confirmation_or_a_second_approver",
         "tests/test_workflow.py::test_an_exploratory_candidate_needs_an_exception_with_a_second_approver"),
        {"DMBOK": _DG, "DCAM": "Data Governance - segregation of duties",
         "COBIT": "DSS06.03 Manage roles, responsibilities, access privileges and levels of authority"},
        "8.3, 10.2"),
    Control(
        "C-07", "Status changes follow one state machine",
        "next_status refuses any (from_status, decision) pair outside ALLOWED_TRANSITIONS",
        PREVENTIVE, "Engine team / operator", "every decision", "DP_CANDIDATE_STATUS_HISTORY",
        "Reject an Accepted candidate directly; expect TransitionError. Compare the "
        "transitions table with the documented one.",
        ("dpre/governance/transitions.py::next_status",
         "dpre/governance/transitions.py::ALLOWED_TRANSITIONS"),
        ("tests/test_governance.py::test_transitions_are_enforced",
         "tests/test_governance.py::test_status_history_records_every_change"),
        {"DMBOK": _DG, "DCAM": "Data Governance - process control",
         "COBIT": "DSS06.02 Control the processing of information"},
        "10.2, 13.1"),
    Control(
        "C-08", "The decision trail is append-only and tamper-evident",
        "Ledgers carry BEFORE UPDATE / BEFORE DELETE triggers that abort; REVIEW_DECISION "
        "rows are hash-chained and Store.verify_audit_chain detects a change made around "
        "the application",
        DETECTIVE, "Programme sponsor", "on demand and before each pack", "REVIEW_DECISION.row_hash, prev_hash",
        "Attempt a DELETE on REVIEW_DECISION; expect IntegrityError. Edit a row with the "
        "triggers dropped; expect verify_audit_chain to report the first break.",
        ("dpre/governance/ledger.py::append_only_triggers", "dpre/governance/ledger.py::row_hash",
         "dpre/store.py::verify_audit_chain"),
        ("tests/test_governance.py::test_decisions_are_hash_chained_and_verify",
         "tests/test_governance.py::test_ledgers_refuse_update_and_delete",
         "tests/test_governance.py::test_tampering_around_the_triggers_is_detected",
         "tests/test_acceptance.py::test_the_audit_trail_is_append_only_and_reversals_are_attributed"),
        {"DMBOK": _DG, "DCAM": "Data Control Environment - audit trail",
         "COBIT": "DSS06.05 Ensure traceability and accountability for information events"},
        "13.1"),
    Control(
        "C-09", "A decision is reversed only by a different actor with a reason",
        "Store._check_reversal_actor refuses a Reverse by the original decision's reviewer",
        PREVENTIVE, "Data product council", "every reversal", "REVIEW_DECISION (decision = Reverse)",
        "Reverse an acceptance as the same reviewer; expect TransitionError 'different actor'.",
        ("dpre/store.py::_check_reversal_actor",),
        ("tests/test_workflow.py::test_reversing_an_acceptance_needs_a_reason_and_another_actor",),
        {"DMBOK": _DG, "DCAM": "Data Governance - segregation of duties",
         "COBIT": "DSS06.03 Manage roles, responsibilities, access privileges and levels of authority"},
        "10.2"),
    Control(
        "C-10", "Reason codes and override values come from a closed vocabulary",
        "validate_reason and validate_override refuse codes, fields and values outside the "
        "taxonomy; 'other' needs a note",
        PREVENTIVE, "Data product council", "every decision", "REVIEW_DECISION.reason_code, field_overridden",
        "Post a decision with a reason outside REASON_CODES; expect ReasonCodeError. Override "
        "archetype to a value outside the enum; expect OverrideValueError.",
        ("dpre/governance/reasons.py::validate_reason", "dpre/governance/reasons.py::validate_override"),
        ("tests/test_governance.py::test_reason_codes_are_validated_server_side",
         "tests/test_governance.py::test_override_values_are_validated",
         "tests/test_workflow.py::test_a_reason_code_outside_the_taxonomy_is_refused"),
        {"DMBOK": _MD, "DCAM": "Data Governance - standards",
         "COBIT": "APO14.02 Define and maintain a consistent business glossary"},
        "10.2, 13.3"),
    Control(
        "C-11", "Weight versions are immutable, approved separately, by an independent approver",
        "Store.save_weights raises WeightVersionError on a changed existing version; "
        "approve_weight_version writes WEIGHT_APPROVAL; assert_weight_approver_independent "
        "refuses an approver whose decisions trained the proposal",
        PREVENTIVE, "Data product council", "every weight change", "SCORE_WEIGHT; WEIGHT_APPROVAL; RUN.weight_hash",
        "Re-save a version with different numbers; expect WeightVersionError. Approve a "
        "proposal as one of its training reviewers; expect refusal.",
        ("dpre/store.py::save_weights", "dpre/store.py::approve_weight_version",
         "dpre/server/security.py::assert_weight_approver_independent"),
        ("tests/test_governance.py::test_initial_weights_ship_unapproved_and_versions_are_immutable",
         "tests/test_governance.py::test_a_sign_contradiction_is_not_proposed_and_cannot_be_approved",
         "tests/test_security.py::test_a_weight_approver_may_not_have_trained_the_proposal"),
        {"DMBOK": _DG, "DCAM": "Data Governance - change control",
         "COBIT": "BAI10.03 Maintain and control configuration items"},
        "8.2, 13.3, 15.2 D-04"),
    Control(
        "C-12", "Configuration changes are ledgered with the person who made them",
        "Store.record_config_change refuses an empty actor and appends to CONFIG_CHANGE",
        DETECTIVE, "Data product council", "every change", "CONFIG_CHANGE",
        "Record a change with no actor; expect PermissionError. List CONFIG_CHANGE and "
        "reconcile to RUN.weight_version and the assumption register snapshot.",
        ("dpre/store.py::record_config_change", "dpre/store.py::config_changes"),
        ("tests/test_governance.py::test_initial_weights_ship_unapproved_and_versions_are_immutable",),
        {"DMBOK": _DG, "DCAM": "Data Governance - change control",
         "COBIT": "BAI10.03 Maintain and control configuration items"},
        "13.3"),
    Control(
        "C-13", "AI-drafted names and definitions are marked until a steward accepts them, "
                "and cannot reach the catalog first",
        "Every drafted field carries AI_DRAFT; catalog_payload lists drafts under "
        "import_blocked_by; Store.accept_metric_name needs a named steward",
        PREVENTIVE, "Domain steward", "every draft", "KPI_CANONICAL.name_status; METRIC_NAME_DECISION",
        "Build the seeds for a fresh run; expect AI_DRAFT on the card, the charter and the "
        "payload, and a non-empty import_blocked_by.",
        ("dpre/seeds/catalog_payload.py::catalog_payload", "dpre/store.py::accept_metric_name"),
        ("tests/test_acceptance.py::test_ai_drafted_names_are_visibly_marked_everywhere",
         "tests/test_workflow.py::test_accepting_an_ai_drafted_name_needs_a_steward"),
        {"DMBOK": _MD, "DCAM": "Data Governance - approval of definitions",
         "COBIT": "APO14.02 Define and maintain a consistent business glossary"},
        "13.1, 14.1"),
    Control(
        "C-14", "Only allow-listed fields leave the estate to a language model, PII names "
                "are redacted, and every call is ledgered",
        "build_prompt drops fields outside PROMPT_FIELD_ALLOWLIST and replaces PII column "
        "names; each call lands on the AI ledger and RUN_AI_CALL",
        PREVENTIVE, "Privacy officer", "every model call", "RUN_AI_CALL",
        "Build a prompt with an extra field and a PII column; expect the field absent and the "
        "column replaced by the placeholder. Count RUN_AI_CALL rows against narrated candidates.",
        ("dpre/narrate/ai.py::build_prompt", "dpre/narrate/ai.py::PROMPT_FIELD_ALLOWLIST"),
        ("tests/test_export.py::test_prompts_carry_only_allow_listed_fields",
         "tests/test_export.py::test_pii_column_names_are_redacted_before_prompting",
         "tests/test_export.py::test_ledger_persists_to_its_own_table"),
        {"DMBOK": _SEC, "DCAM": "Data Control Environment - privacy",
         "COBIT": "DSS05.03 Manage endpoint security; APO14.08 Manage the life cycle of data assets"},
        "10.1, 15.3"),
    Control(
        "C-15", "The conversational surface reads only the semantic view, read-only",
        "check_query refuses write tokens and any object in FORBIDDEN_OBJECTS; the agent "
        "picks a named query with bound parameters and never composes SQL",
        PREVENTIVE, "Engine team / operator", "every question", "chat/semantic_view.py::QUERIES",
        "Run check_query on every named query and on a DROP; expect the former to pass and "
        "the latter to raise. In Snowflake this becomes a GRANT audit on the view.",
        ("dpre/chat/semantic_view.py::check_query", "dpre/chat/semantic_view.py::ALLOWED_OBJECTS",
         "dpre/chat/semantic_view.py::FORBIDDEN_OBJECTS"),
        ("tests/test_acceptance.py::test_the_conversational_agent_cannot_query_outside_its_semantic_view",
         "tests/test_server.py::test_chat_answers_cite_evidence"),
        {"DMBOK": _SEC, "DCAM": "Data Control Environment - access",
         "COBIT": "DSS05.04 Manage user identity and logical access"},
        "10.3, 13.1, 14.1",
        status="partial"),
    Control(
        "C-16", "A run that fails a quality gate is not published",
        "_quality_gates evaluates the five section 13.2 gates; RUN.published is set only by "
        "Store.publish and only when every gate passed and candidates exist",
        PREVENTIVE, "Engine team / operator", "every run", "RUN.quality_gates, RUN.published",
        "Run with no catalog; expect published = 0 and only the gap list. Inspect RUN for "
        "published runs with a failed gate; expect none.",
        ("dpre/pipeline.py::_quality_gates", "dpre/store.py::publish"),
        ("tests/test_acceptance.py::test_run_quality_gates_are_all_evaluated",
         "tests/test_pipeline.py::test_a_run_with_no_catalog_publishes_only_the_gap_list"),
        {"DMBOK": _DQ, "DCAM": "Data Quality Management - gating",
         "COBIT": "APO14.06 Ensure a data quality assessment approach"},
        "13.2"),
    Control(
        "C-17", "Synthetic and real extracts are never mixed, and a synthetic run never "
                "reaches the catalog",
        "validate raises MIXED_SYNTHETIC when flags differ; every synthetic row carries the "
        "flag and a generation id; catalog_payload blocks import of a synthetic run",
        PREVENTIVE, "Engine team / operator", "every ingest", "RUN.synthetic, generation_id",
        "Flip one row's synthetic flag and validate; expect MIXED_SYNTHETIC. Build a payload "
        "on a synthetic run; expect import_blocked_by to say so.",
        ("dpre/ingest/validator.py::validate", "dpre/seeds/catalog_payload.py::catalog_payload"),
        ("tests/test_acceptance.py::test_synthetic_rows_are_never_mixed_with_real_ones",
         "tests/test_export.py::test_catalog_payload_blocks_import_of_a_synthetic_run",
         "tests/test_synthetic.py::test_every_row_is_flagged_synthetic"),
        {"DMBOK": _DQ, "DCAM": "Data Control Environment - test data segregation",
         "COBIT": "BAI07.04 Establish a test environment"},
        "17.3"),
    Control(
        "C-18", "Every run is reproducible from its stored inputs",
        "The manifest stores extract ids, weight version and hash, parser version and "
        "generation id; the same inputs and configuration produce the same ranking",
        DETECTIVE, "Engine team / operator", "every run", "RUN (extract_ids, weight_hash, parser_version)",
        "Re-run the same pack and compare ranked names and composites; expect equality. "
        "Compare RUN.weight_hash to the SCORE_WEIGHT rows of that version.",
        ("dpre/pipeline.py::run_pipeline", "dpre/store.py::weight_vector_hash"),
        ("tests/test_acceptance.py::test_a_run_is_reproducible_from_its_stored_inputs",
         "tests/test_pipeline.py::test_the_manifest_records_what_a_replay_needs"),
        {"DMBOK": "Data Warehousing and BI", "DCAM": "Data Quality Management - lineage",
         "COBIT": "BAI10.02 Establish and maintain a configuration repository and baseline"},
        "13.1, 14.1"),
    Control(
        "C-19", "A run is persisted whole, published last",
        "Store.persist_run writes graph, canonicalization and candidates in one "
        "BEGIN IMMEDIATE transaction and publishes only at the end",
        PREVENTIVE, "Engine team / operator", "every run", "RUN.published",
        "Kill a persist mid-way (simulated); expect no RUN row or an unpublished one, never "
        "a half-written published run.",
        ("dpre/store.py::persist_run",),
        ("tests/test_governance.py::test_a_run_is_persisted_in_one_transaction_and_published_last",
         "tests/test_governance.py::test_concurrent_persists_leave_every_run_consistent"),
        {"DMBOK": "Data Storage and Operations", "DCAM": "Technology Architecture - integrity",
         "COBIT": "DSS06.02 Control the processing of information"},
        "13.1"),
    Control(
        "C-20", "Sensitivity is carried into the risk score and PII is listed on the card",
        "The risk feature takes the maximum SENSITIVITY_RANK across operand columns; the "
        "critic lists PII attributes under S9-privacy",
        DETECTIVE, "Privacy officer", "every run", "DP_CANDIDATE_SCORE.features; DP_CANDIDATE_CRITIQUE",
        "Pick a candidate with a PII attribute; expect the sensitivity feature > 0 and an "
        "S9-privacy finding naming the column. D-06 (a threshold that caps status) is not "
        "enforced; see the decision register.",
        ("dpre/score/scorer.py::_apply_gates", "dpre/narrate/critic.py::_critique_one",
         "dpre/config.py::SENSITIVITY_RANK"),
        ("tests/test_cluster_score.py::test_every_feature_resolves_to_a_number",
         "tests/test_export.py::test_labels_cover_every_critique_criterion_and_severity"),
        {"DMBOK": _SEC, "DCAM": "Data Control Environment - classification",
         "COBIT": "APO14.08 Manage the life cycle of data assets; DSS05.02 Manage network and connectivity security"},
        "13.1, 15.2 D-06",
        status="partial"),
    Control(
        "C-21", "Conflict adjudication is a ledgered steward act with a closed vocabulary",
        "Store.resolve_conflict validates the status against CONFLICT_STATUSES, needs a named "
        "steward, and writes CONFLICT_DECISION keyed by fingerprint pair so it survives re-runs",
        PREVENTIVE, "Domain steward", "every adjudication", "CONFLICT_DECISION; REVIEW_DECISION (subject_type = conflict)",
        "Resolve a conflict with status 'RESOLVED'; expect ValueError. Resolve with RESOLVED_A "
        "and a steward; expect a CONFLICT_DECISION row and a decision row.",
        ("dpre/store.py::resolve_conflict", "dpre/store.py::CONFLICT_STATUSES"),
        ("tests/test_acceptance.py::test_the_conflict_register_is_complete_enough_to_sign_off",
         "tests/test_governance.py::test_conflict_adjudication_is_validated_and_ledgered"),
        {"DMBOK": _MD, "DCAM": "Data Governance - stewardship",
         "COBIT": "APO14.02 Define and maintain a consistent business glossary"},
        "5.3, 14.1"),
    Control(
        "C-22", "Uploads are bounded and allow-listed; no server path reaches a client",
        "validate_upload checks extension and magic bytes; safe_child refuses traversal; "
        "the store and uploads live outside anything served",
        PREVENTIVE, "Engine team / operator", "every upload", "server logs (request id)",
        "Upload a .exe renamed .xlsx; expect refusal. Request a seed with a traversing name; "
        "expect refusal. Confirm engine.db is not under the static root.",
        ("dpre/server/security.py::validate_upload", "dpre/server/security.py::safe_child"),
        ("tests/test_security.py::test_upload_extensions_are_allow_listed_and_magic_checked",
         "tests/test_security.py::test_path_traversal_cannot_escape_a_directory",
         "tests/test_server.py::test_the_database_lives_outside_anything_served"),
        {"DMBOK": _SEC, "DCAM": "Data Control Environment - security",
         "COBIT": "DSS05.03 Manage endpoint security"},
        "R-29, R-30"),
    Control(
        "C-23", "Every run is attributed to one engagement and one client",
        "attach_run links a run to an ENGAGEMENT row with the config version it ran on; "
        "engagement_for_run answers which client a run was for",
        DETECTIVE, "Programme sponsor", "every run", "ENGAGEMENT; RUN_ENGAGEMENT",
        "List runs with no RUN_ENGAGEMENT row (unattributed_runs); expect none on a live "
        "workspace. Confirm the engagement's data cut date is not before the run's as-of.",
        ("dpre/engagement/model.py::attach_run", "dpre/engagement/model.py::engagement_for_run",
         "dpre/engagement/model.py::unattributed_runs"),
        ("tests/test_engagement.py::test_a_run_is_attributed_to_one_engagement",),
        {"DMBOK": _DG, "DCAM": "Data Management Program - scope",
         "COBIT": "APO14.01 Define and communicate the organization's data management strategy and roles"},
        "R-24",
        status="partial"),
    Control(
        "C-24", "The assumptions and open decisions a run ran on are snapshotted with it",
        "snapshot_assumptions and snapshot_decisions persist the registers per run with a "
        "config_version hash; a decision taken is an append-only DECISION_LOG row",
        DETECTIVE, "Data product council", "every run", "ASSUMPTION_REGISTER; DECISION_REGISTER; DECISION_LOG",
        "Compare ASSUMPTION_REGISTER.config_version between two runs whose rankings are "
        "compared; expect equality. Update a DECISION_LOG row; expect the trigger to abort.",
        ("dpre/registers/assumptions.py::snapshot_assumptions",
         "dpre/registers/decisions.py::snapshot_decisions", "dpre/registers/decisions.py::decide"),
        ("tests/test_registers.py::test_assumption_register_snapshots_with_a_config_version",
         "tests/test_registers.py::test_decisions_are_logged_append_only"),
        {"DMBOK": _DG, "DCAM": "Data Governance - decision rights",
         "COBIT": "BAI10.02 Establish and maintain a configuration repository and baseline"},
        "15.2",
        status="partial"),
)

# RACI over the roles the specification names. Letters: R responsible,
# A accountable, C consulted, I informed. One A per activity.
RACI: tuple[tuple[str, dict[str, str], str], ...] = (
    ("Run the engine on an extract",
     {"Engine team / operator": "R", "Programme sponsor": "A", "Domain steward": "I",
      "Data product council": "I"}, "dpre/pipeline.py::run_pipeline; role 'operator'"),
    ("Sign off the conflict register (Phase 1 exit)",
     {"Domain steward": "R", "Data product council": "A", "Engine team / operator": "C"},
     "Store.resolve_conflict; CONFLICT_DECISION"),
    ("Accept an AI-drafted metric name or definition",
     {"Domain steward": "R", "Data product council": "A", "Named consumer": "C"},
     "Store.accept_metric_name; METRIC_NAME_DECISION"),
    ("Accept, reject, merge, split or defer a candidate",
     {"Reviewer (domain data product owner)": "R", "Data product council": "A",
      "Domain steward": "C", "Named consumer": "C", "Engine team / operator": "I"},
     "dpre/review/workflow.py::review; REVIEW_DECISION"),
    ("Confirm the blocked decision, latency and consequence (Stage 1)",
     {"Named consumer": "R", "Reviewer (domain data product owner)": "A"},
     "dpre/review/workflow.py::confirm_consumer; CANDIDATE_CONSUMER_CONFIRMATION"),
    ("Waive a hard gate at acceptance",
     {"Reviewer (domain data product owner)": "R", "Data product council": "A",
      "Privacy officer": "C"}, "accept_with_exception; GATE_WAIVER (second approver)"),
    ("Approve a score weight version (D-04)",
     {"Data product council": "A", "Engine team / operator": "R",
      "Reviewer (domain data product owner)": "I"},
     "Store.approve_weight_version; WEIGHT_APPROVAL; assert_weight_approver_independent"),
    ("Change engine configuration (size floor, resolution, Keep handling)",
     {"Data product council": "A", "Engine team / operator": "R", "Rationalization lead": "C"},
     "POST /api/v1/config; Store.record_config_change; CONFIG_CHANGE"),
    ("Decide the usage window and seasonal treatment (D-01)",
     {"Data product council": "A", "Rationalization lead": "C", "Engine team / operator": "R"},
     "dpre/registers/decisions.py::decide; DECISION_LOG"),
    ("Decide whether Keep reports count (D-03)",
     {"Rationalization lead": "A", "Data product council": "C", "Engine team / operator": "R"},
     "decide('D-03', ...); EngineConfig.keep_counts_toward_consolidation"),
    ("Set the sensitivity threshold for privacy review (D-06)",
     {"Privacy officer": "A", "Data product council": "C", "Engine team / operator": "R"},
     "decide('D-06', ...); enforcement is wiring_needed"),
    ("Agree the catalog asset type and import the payload (D-07)",
     {"Catalog admin": "A", "Domain steward": "R", "Engine team / operator": "C"},
     "dpre/seeds/catalog_payload.py; import by a steward, never pushed"),
    ("Define which role may Accept per domain (D-08)",
     {"Programme sponsor": "A", "Data product council": "C", "Engine team / operator": "R"},
     "ENGAGEMENT_REVIEWER roster; dpre/server/security.py::ROLE_ACTIONS"),
    ("Mark a low-usage report decision-critical",
     {"Reviewer (domain data product owner)": "R", "Data product council": "A",
      "Rationalization lead": "C"}, "mark_decision_critical; REPORT_OVERRIDE"),
    ("Approve the value rate card",
     {"Programme sponsor": "A", "Rationalization lead": "C", "Engine team / operator": "R"},
     "dpre/value/assumptions.py::approve_assumptions; VALUE_ASSUMPTION"),
    ("Verify the audit chain before a committee pack",
     {"Engine team / operator": "R", "Programme sponsor": "A", "Data product council": "I"},
     "Store.verify_audit_chain; dpre/governance/audit.py::audit_report"),
)


def controls_matrix() -> list[dict]:
    return [c.to_dict() for c in CONTROLS]


def raci() -> list[dict]:
    rows = []
    for activity, assignment, mechanism in RACI:
        accountable = [r for r, letter in assignment.items() if letter == "A"]
        rows.append({"activity": activity, "assignment": dict(assignment),
                     "accountable": accountable[0] if accountable else "",
                     "mechanism": mechanism})
    return rows


def framework_mapping() -> dict[str, dict[str, list[str]]]:
    """framework -> practice/knowledge area -> control ids."""
    out: dict[str, dict[str, list[str]]] = {}
    for control in CONTROLS:
        for framework, reference in control.frameworks.items():
            for part in reference.split(";"):
                part = part.strip()
                if part:
                    out.setdefault(framework, {}).setdefault(part, []).append(control.control_id)
    return out


_REF = re.compile(r"^(?P<path>[\w./-]+\.py)::(?P<name>\w+)$")


def _symbol_present(source: str, name: str) -> bool:
    return re.search(rf"^\s*(?:def|class)\s+{re.escape(name)}\b", source, re.M) is not None \
        or re.search(rf"^\s*{re.escape(name)}\s*[:=]", source, re.M) is not None


def verify_controls(root: str | Path | None = None) -> list[dict]:
    """Static evidence: does every cited function and test still exist in the tree?

    Returns one row per control with ``present`` true when every reference
    resolves, plus the list of references that did not. It reads files; it
    never imports or executes them.
    """
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    cache: dict[str, str] = {}

    def source(path: str) -> str | None:
        if path not in cache:
            target = root / path
            cache[path] = target.read_text(encoding="utf-8") if target.is_file() else None
        return cache[path]

    rows = []
    for control in CONTROLS:
        missing: list[str] = []
        for ref in control.enforcing + control.proving_tests:
            match = _REF.match(ref)
            if not match:
                missing.append(ref)
                continue
            text = source(match["path"])
            if text is None or not _symbol_present(text, match["name"]):
                missing.append(ref)
        rows.append({"control_id": control.control_id, "objective": control.objective,
                     "status": control.status, "present": not missing, "missing": missing,
                     "enforcing": list(control.enforcing),
                     "proving_tests": list(control.proving_tests)})
    return rows


def render_markdown() -> str:
    lines = ["| Id | Control objective | Control | Kind | Owner | Frequency | Evidence | "
             "Enforcing code | Proving tests | Status |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for c in CONTROLS:
        enforcing = "<br>".join(f"`{e}`" for e in c.enforcing)
        tests = "<br>".join(f"`{t}`" for t in c.proving_tests)
        lines.append(f"| {c.control_id} | {c.objective} | {c.control} | {c.kind} | {c.owner} | "
                     f"{c.frequency} | {c.evidence} | {enforcing} | {tests} | {c.status} |")
    return "\n".join(lines)


def render_raci_markdown() -> str:
    short = {role: role.split(" (")[0] for role in RACI_ROLES}
    lines = ["| Activity | " + " | ".join(short[r] for r in RACI_ROLES) + " | Mechanism |",
             "| --- | " + " | ".join("---" for _ in RACI_ROLES) + " | --- |"]
    for activity, assignment, mechanism in RACI:
        cells = [assignment.get(role, "") for role in RACI_ROLES]
        lines.append(f"| {activity} | " + " | ".join(cells) + f" | `{mechanism}` |")
    return "\n".join(lines)


def render_framework_markdown() -> str:
    lines = ["| Framework | Practice or knowledge area | Controls |",
             "| --- | --- | --- |"]
    mapping = framework_mapping()
    for framework in ("DMBOK", "DCAM", "COBIT"):
        for reference, ids in sorted(mapping.get(framework, {}).items()):
            lines.append(f"| {framework} | {reference} | {', '.join(ids)} |")
    return "\n".join(lines)
