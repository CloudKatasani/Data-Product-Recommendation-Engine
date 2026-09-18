# Controls matrix, RACI and framework mapping

What a client's audit function can adopt: one row per control with the
objective, the control, whether it is preventive or detective, the owner, the
frequency, where the evidence sits, the test procedure, the enforcing
function and the proving tests (review finding R-27). The specification's
section 13.1 guardrails and the README's "enforced by structure" table list
mechanisms; this page presents them as controls, and adds the two that
`dpre/engagement` and `dpre/registers` introduce.

Rendered from `dpre/registers/controls.py::CONTROLS`.
`dpre/registers/controls.py::verify_controls` checks statically that every
cited function and test exists in the tree, and
`tests/test_registers.py::test_every_control_cites_code_and_tests_that_exist`
fails the build if one disappears. Live test status is `python -m pytest tests -q`;
the "Proving tests" column says which tests to read.

**Status** - `operating` means the control runs today with no wiring
outstanding; `partial` means the mechanism exists but part of it is a
convention or a call the pipeline does not yet make (each partial control says
what in its test procedure or in the decision register).

## Controls

| Id | Control objective | Control | Kind | Owner | Frequency | Evidence | Enforcing code | Proving tests | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C-01 | No engine path can move a candidate past Proposed | Store.save_candidates raises ProposeOnlyError for any status past ENGINE_MAX_STATUS; only a REVIEW_DECISION row naming a reviewer moves a status | preventive | Engine team / operator | every write | DP_CANDIDATE.status; REVIEW_DECISION | `dpre/store.py::save_candidates`<br>`dpre/store.py::orphan_statuses`<br>`dpre/config.py::ENGINE_MAX_STATUS` | `tests/test_acceptance.py::test_no_code_path_but_a_review_decision_moves_a_candidate_past_proposed`<br>`tests/test_cluster_score.py::test_engine_never_proposes_past_proposed` | operating |
| C-02 | Every review decision is attributable to an authenticated person | Store.record_decision refuses an empty reviewer; over HTTP the reviewer is the resolved principal, never a string in the request body | preventive | Programme sponsor | every decision | REVIEW_DECISION.reviewer, actor_role | `dpre/store.py::record_decision`<br>`dpre/server/security.py::resolve_principal` | `tests/test_server.py::test_review_identity_comes_from_the_principal_not_the_body`<br>`tests/test_security.py::test_identity_is_taken_from_the_header_and_the_body_is_ignored` | operating |
| C-03 | Only an authorised role may accept, steward, waive or approve, within its domains | authorize checks the role-to-action matrix and, for review, steward and download, the principal's domain scope (D-08) | preventive | Programme sponsor | every request | ROLE_ACTIONS; token map; proxy identity | `dpre/server/security.py::authorize`<br>`dpre/server/security.py::ROLE_ACTIONS`<br>`dpre/server/security.py::DOMAIN_SCOPED` | `tests/test_security.py::test_a_role_without_the_action_is_refused`<br>`tests/test_security.py::test_domain_scope_bounds_review_and_download`<br>`tests/test_security.py::test_the_role_matrix_is_the_documented_one` | operating |
| C-04 | No score is published without the evidence rows behind it | Store.save_candidates raises EvidenceMissingError for a score without evidence; Store.publish refuses a run whose scores lack evidence | preventive | Engine team / operator | every run | DP_CANDIDATE_SCORE joined to DP_CANDIDATE_EVIDENCE | `dpre/store.py::save_candidates`<br>`dpre/store.py::publish` | `tests/test_acceptance.py::test_a_score_row_without_evidence_cannot_be_written`<br>`tests/test_acceptance.py::test_published_runs_have_evidence_behind_every_score`<br>`tests/test_cluster_score.py::test_every_score_carries_evidence` | operating |
| C-05 | Hard gates G1-G4 bind at acceptance, not only at scoring | Store._check_accept_gates raises GateError on Accept of a Blocked candidate and on an Exploratory one without a consumer confirmation or an exception | preventive | Data product council | every Accept | REVIEW_DECISION; GATE_WAIVER | `dpre/store.py::_check_accept_gates`<br>`dpre/score/scorer.py::_apply_gates` | `tests/test_acceptance.py::test_a_gated_candidate_cannot_be_accepted_without_an_exception_row`<br>`tests/test_governance.py::test_accept_on_blocked_raises_with_the_gate_detail`<br>`tests/test_cluster_score.py::test_hard_gates_cap_the_status` | operating |
| C-06 | A gate exception needs a second approver (four-eyes) | accept_with_exception refuses the same person as reviewer and second approver and writes a GATE_WAIVER row that stays open until the gate clears | preventive | Data product council | every exception | GATE_WAIVER; REVIEW_DECISION.second_approver | `dpre/store.py::_check_accept_gates`<br>`dpre/review/workflow.py::accept_with_exception` | `tests/test_governance.py::test_exploratory_needs_a_consumer_confirmation_or_a_second_approver`<br>`tests/test_workflow.py::test_an_exploratory_candidate_needs_an_exception_with_a_second_approver` | operating |
| C-07 | Status changes follow one state machine | next_status refuses any (from_status, decision) pair outside ALLOWED_TRANSITIONS | preventive | Engine team / operator | every decision | DP_CANDIDATE_STATUS_HISTORY | `dpre/governance/transitions.py::next_status`<br>`dpre/governance/transitions.py::ALLOWED_TRANSITIONS` | `tests/test_governance.py::test_transitions_are_enforced`<br>`tests/test_governance.py::test_status_history_records_every_change` | operating |
| C-08 | The decision trail is append-only and tamper-evident | Ledgers carry BEFORE UPDATE / BEFORE DELETE triggers that abort; REVIEW_DECISION rows are hash-chained and Store.verify_audit_chain detects a change made around the application | detective | Programme sponsor | on demand and before each pack | REVIEW_DECISION.row_hash, prev_hash | `dpre/governance/ledger.py::append_only_triggers`<br>`dpre/governance/ledger.py::row_hash`<br>`dpre/store.py::verify_audit_chain` | `tests/test_governance.py::test_decisions_are_hash_chained_and_verify`<br>`tests/test_governance.py::test_ledgers_refuse_update_and_delete`<br>`tests/test_governance.py::test_tampering_around_the_triggers_is_detected`<br>`tests/test_acceptance.py::test_the_audit_trail_is_append_only_and_reversals_are_attributed` | operating |
| C-09 | A decision is reversed only by a different actor with a reason | Store._check_reversal_actor refuses a Reverse by the original decision's reviewer | preventive | Data product council | every reversal | REVIEW_DECISION (decision = Reverse) | `dpre/store.py::_check_reversal_actor` | `tests/test_workflow.py::test_reversing_an_acceptance_needs_a_reason_and_another_actor` | operating |
| C-10 | Reason codes and override values come from a closed vocabulary | validate_reason and validate_override refuse codes, fields and values outside the taxonomy; 'other' needs a note | preventive | Data product council | every decision | REVIEW_DECISION.reason_code, field_overridden | `dpre/governance/reasons.py::validate_reason`<br>`dpre/governance/reasons.py::validate_override` | `tests/test_governance.py::test_reason_codes_are_validated_server_side`<br>`tests/test_governance.py::test_override_values_are_validated`<br>`tests/test_workflow.py::test_a_reason_code_outside_the_taxonomy_is_refused` | operating |
| C-11 | Weight versions are immutable, approved separately, by an independent approver | Store.save_weights raises WeightVersionError on a changed existing version; approve_weight_version writes WEIGHT_APPROVAL; assert_weight_approver_independent refuses an approver whose decisions trained the proposal | preventive | Data product council | every weight change | SCORE_WEIGHT; WEIGHT_APPROVAL; RUN.weight_hash | `dpre/store.py::save_weights`<br>`dpre/store.py::approve_weight_version`<br>`dpre/server/security.py::assert_weight_approver_independent` | `tests/test_governance.py::test_initial_weights_ship_unapproved_and_versions_are_immutable`<br>`tests/test_governance.py::test_a_sign_contradiction_is_not_proposed_and_cannot_be_approved`<br>`tests/test_security.py::test_a_weight_approver_may_not_have_trained_the_proposal` | operating |
| C-12 | Configuration changes are ledgered with the person who made them | Store.record_config_change refuses an empty actor and appends to CONFIG_CHANGE | detective | Data product council | every change | CONFIG_CHANGE | `dpre/store.py::record_config_change`<br>`dpre/store.py::config_changes` | `tests/test_governance.py::test_initial_weights_ship_unapproved_and_versions_are_immutable` | operating |
| C-13 | AI-drafted names and definitions are marked until a steward accepts them, and cannot reach the catalog first | Every drafted field carries AI_DRAFT; catalog_payload lists drafts under import_blocked_by; Store.accept_metric_name needs a named steward | preventive | Domain steward | every draft | KPI_CANONICAL.name_status; METRIC_NAME_DECISION | `dpre/seeds/catalog_payload.py::catalog_payload`<br>`dpre/store.py::accept_metric_name` | `tests/test_acceptance.py::test_ai_drafted_names_are_visibly_marked_everywhere`<br>`tests/test_workflow.py::test_accepting_an_ai_drafted_name_needs_a_steward` | operating |
| C-14 | Only allow-listed fields leave the estate to a language model, PII names are redacted, and every call is ledgered | build_prompt drops fields outside PROMPT_FIELD_ALLOWLIST and replaces PII column names; each call lands on the AI ledger and RUN_AI_CALL | preventive | Privacy officer | every model call | RUN_AI_CALL | `dpre/narrate/ai.py::build_prompt`<br>`dpre/narrate/ai.py::PROMPT_FIELD_ALLOWLIST` | `tests/test_export.py::test_prompts_carry_only_allow_listed_fields`<br>`tests/test_export.py::test_pii_column_names_are_redacted_before_prompting`<br>`tests/test_export.py::test_ledger_persists_to_its_own_table` | operating |
| C-15 | The conversational surface reads only the semantic view, read-only | check_query refuses write tokens and any object in FORBIDDEN_OBJECTS; the agent picks a named query with bound parameters and never composes SQL | preventive | Engine team / operator | every question | chat/semantic_view.py::QUERIES | `dpre/chat/semantic_view.py::check_query`<br>`dpre/chat/semantic_view.py::ALLOWED_OBJECTS`<br>`dpre/chat/semantic_view.py::FORBIDDEN_OBJECTS` | `tests/test_acceptance.py::test_the_conversational_agent_cannot_query_outside_its_semantic_view`<br>`tests/test_server.py::test_chat_answers_cite_evidence` | partial |
| C-16 | A run that fails a quality gate is not published | _quality_gates evaluates the five section 13.2 gates; RUN.published is set only by Store.publish and only when every gate passed and candidates exist | preventive | Engine team / operator | every run | RUN.quality_gates, RUN.published | `dpre/pipeline.py::_quality_gates`<br>`dpre/store.py::publish` | `tests/test_acceptance.py::test_run_quality_gates_are_all_evaluated`<br>`tests/test_pipeline.py::test_a_run_with_no_catalog_publishes_only_the_gap_list` | operating |
| C-17 | Synthetic and real extracts are never mixed, and a synthetic run never reaches the catalog | validate raises MIXED_SYNTHETIC when flags differ; every synthetic row carries the flag and a generation id; catalog_payload blocks import of a synthetic run | preventive | Engine team / operator | every ingest | RUN.synthetic, generation_id | `dpre/ingest/validator.py::validate`<br>`dpre/seeds/catalog_payload.py::catalog_payload` | `tests/test_acceptance.py::test_synthetic_rows_are_never_mixed_with_real_ones`<br>`tests/test_export.py::test_catalog_payload_blocks_import_of_a_synthetic_run`<br>`tests/test_synthetic.py::test_every_row_is_flagged_synthetic` | operating |
| C-18 | Every run is reproducible from its stored inputs | The manifest stores extract ids, weight version and hash, parser version and generation id; the same inputs and configuration produce the same ranking | detective | Engine team / operator | every run | RUN (extract_ids, weight_hash, parser_version) | `dpre/pipeline.py::run_pipeline`<br>`dpre/store.py::weight_vector_hash` | `tests/test_acceptance.py::test_a_run_is_reproducible_from_its_stored_inputs`<br>`tests/test_pipeline.py::test_the_manifest_records_what_a_replay_needs` | operating |
| C-19 | A run is persisted whole, published last | Store.persist_run writes graph, canonicalization and candidates in one BEGIN IMMEDIATE transaction and publishes only at the end | preventive | Engine team / operator | every run | RUN.published | `dpre/store.py::persist_run` | `tests/test_governance.py::test_a_run_is_persisted_in_one_transaction_and_published_last`<br>`tests/test_governance.py::test_concurrent_persists_leave_every_run_consistent` | operating |
| C-20 | Sensitivity is carried into the risk score and PII is listed on the card | The risk feature takes the maximum SENSITIVITY_RANK across operand columns; the critic lists PII attributes under S9-privacy | detective | Privacy officer | every run | DP_CANDIDATE_SCORE.features; DP_CANDIDATE_CRITIQUE | `dpre/score/scorer.py::_apply_gates`<br>`dpre/narrate/critic.py::_critique_one`<br>`dpre/config.py::SENSITIVITY_RANK` | `tests/test_cluster_score.py::test_every_feature_resolves_to_a_number`<br>`tests/test_export.py::test_labels_cover_every_critique_criterion_and_severity` | partial |
| C-21 | Conflict adjudication is a ledgered steward act with a closed vocabulary | Store.resolve_conflict validates the status against CONFLICT_STATUSES, needs a named steward, and writes CONFLICT_DECISION keyed by fingerprint pair so it survives re-runs | preventive | Domain steward | every adjudication | CONFLICT_DECISION; REVIEW_DECISION (subject_type = conflict) | `dpre/store.py::resolve_conflict`<br>`dpre/store.py::CONFLICT_STATUSES` | `tests/test_acceptance.py::test_the_conflict_register_is_complete_enough_to_sign_off`<br>`tests/test_governance.py::test_conflict_adjudication_is_validated_and_ledgered` | operating |
| C-22 | Uploads are bounded and allow-listed; no server path reaches a client | validate_upload checks extension and magic bytes; safe_child refuses traversal; the store and uploads live outside anything served | preventive | Engine team / operator | every upload | server logs (request id) | `dpre/server/security.py::validate_upload`<br>`dpre/server/security.py::safe_child` | `tests/test_security.py::test_upload_extensions_are_allow_listed_and_magic_checked`<br>`tests/test_security.py::test_path_traversal_cannot_escape_a_directory`<br>`tests/test_server.py::test_the_database_lives_outside_anything_served` | operating |
| C-23 | Every run is attributed to one engagement and one client | attach_run links a run to an ENGAGEMENT row with the config version it ran on; engagement_for_run answers which client a run was for | detective | Programme sponsor | every run | ENGAGEMENT; RUN_ENGAGEMENT | `dpre/engagement/model.py::attach_run`<br>`dpre/engagement/model.py::engagement_for_run`<br>`dpre/engagement/model.py::unattributed_runs` | `tests/test_engagement.py::test_a_run_is_attributed_to_one_engagement` | partial |
| C-24 | The assumptions and open decisions a run ran on are snapshotted with it | snapshot_assumptions and snapshot_decisions persist the registers per run with a config_version hash; a decision taken is an append-only DECISION_LOG row | detective | Data product council | every run | ASSUMPTION_REGISTER; DECISION_REGISTER; DECISION_LOG | `dpre/registers/assumptions.py::snapshot_assumptions`<br>`dpre/registers/decisions.py::snapshot_decisions`<br>`dpre/registers/decisions.py::decide` | `tests/test_registers.py::test_assumption_register_snapshots_with_a_config_version`<br>`tests/test_registers.py::test_decisions_are_logged_append_only` | partial |

## RACI over the roles the specification names

R responsible, A accountable (one per activity), C consulted, I informed.
Roles: Programme sponsor, Data product council, Domain steward, Reviewer (domain data product owner), Privacy officer, Catalog admin, Rationalization lead, Engine team / operator, Named consumer.

| Activity | Programme sponsor | Data product council | Domain steward | Reviewer | Privacy officer | Catalog admin | Rationalization lead | Engine team / operator | Named consumer | Mechanism |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Run the engine on an extract | A | I | I |  |  |  |  | R |  | `dpre/pipeline.py::run_pipeline; role 'operator'` |
| Sign off the conflict register (Phase 1 exit) |  | A | R |  |  |  |  | C |  | `Store.resolve_conflict; CONFLICT_DECISION` |
| Accept an AI-drafted metric name or definition |  | A | R |  |  |  |  |  | C | `Store.accept_metric_name; METRIC_NAME_DECISION` |
| Accept, reject, merge, split or defer a candidate |  | A | C | R |  |  |  | I | C | `dpre/review/workflow.py::review; REVIEW_DECISION` |
| Confirm the blocked decision, latency and consequence (Stage 1) |  |  |  | A |  |  |  |  | R | `dpre/review/workflow.py::confirm_consumer; CANDIDATE_CONSUMER_CONFIRMATION` |
| Waive a hard gate at acceptance |  | A |  | R | C |  |  |  |  | `accept_with_exception; GATE_WAIVER (second approver)` |
| Approve a score weight version (D-04) |  | A |  | I |  |  |  | R |  | `Store.approve_weight_version; WEIGHT_APPROVAL; assert_weight_approver_independent` |
| Change engine configuration (size floor, resolution, Keep handling) |  | A |  |  |  |  | C | R |  | `POST /api/v1/config; Store.record_config_change; CONFIG_CHANGE` |
| Decide the usage window and seasonal treatment (D-01) |  | A |  |  |  |  | C | R |  | `dpre/registers/decisions.py::decide; DECISION_LOG` |
| Decide whether Keep reports count (D-03) |  | C |  |  |  |  | A | R |  | `decide('D-03', ...); EngineConfig.keep_counts_toward_consolidation` |
| Set the sensitivity threshold for privacy review (D-06) |  | C |  |  | A |  |  | R |  | `decide('D-06', ...); enforcement is wiring_needed` |
| Agree the catalog asset type and import the payload (D-07) |  |  | R |  |  | A |  | C |  | `dpre/seeds/catalog_payload.py; import by a steward, never pushed` |
| Define which role may Accept per domain (D-08) | A | C |  |  |  |  |  | R |  | `ENGAGEMENT_REVIEWER roster; dpre/server/security.py::ROLE_ACTIONS` |
| Mark a low-usage report decision-critical |  | A |  | R |  |  | C |  |  | `mark_decision_critical; REPORT_OVERRIDE` |
| Approve the value rate card | A |  |  |  |  |  | C | R |  | `dpre/value/assumptions.py::approve_assumptions; VALUE_ASSUMPTION` |
| Verify the audit chain before a committee pack | A | I |  |  |  |  |  | R |  | `Store.verify_audit_chain; dpre/governance/audit.py::audit_report` |

Two segregation-of-duties rules sit under the RACI and are enforced in code:
a gate exception needs a second approver who is not the reviewer (C-06), and
a weight version cannot be approved by a council member whose own decisions
trained the proposal (C-11, `dpre/server/security.py::assert_weight_approver_independent`).

## Framework mapping

A starting crosswalk to DAMA-DMBOK knowledge areas, DCAM capability
components and COBIT 2019 management practices. The client's own control
framework owner should finish it; the control ids are stable and the mapping
lives in `dpre/registers/controls.py::Control.frameworks`.

| Framework | Practice or knowledge area | Controls |
| --- | --- | --- |
| DMBOK | Data Governance | C-01, C-02, C-05, C-06, C-07, C-08, C-09, C-11, C-12, C-23, C-24 |
| DMBOK | Data Quality | C-04, C-16, C-17 |
| DMBOK | Data Security | C-03, C-14, C-15, C-20, C-22 |
| DMBOK | Data Storage and Operations | C-19 |
| DMBOK | Data Warehousing and BI | C-18 |
| DMBOK | Metadata | C-10, C-13, C-21 |
| DCAM | Data Control Environment - access | C-03, C-15 |
| DCAM | Data Control Environment - audit trail | C-08 |
| DCAM | Data Control Environment - classification | C-20 |
| DCAM | Data Control Environment - privacy | C-14 |
| DCAM | Data Control Environment - security | C-22 |
| DCAM | Data Control Environment - test data segregation | C-17 |
| DCAM | Data Governance - accountability | C-02 |
| DCAM | Data Governance - approval of definitions | C-13 |
| DCAM | Data Governance - change control | C-11, C-12 |
| DCAM | Data Governance - control gates | C-05 |
| DCAM | Data Governance - decision rights | C-01, C-24 |
| DCAM | Data Governance - process control | C-07 |
| DCAM | Data Governance - segregation of duties | C-06, C-09 |
| DCAM | Data Governance - standards | C-10 |
| DCAM | Data Governance - stewardship | C-21 |
| DCAM | Data Management Program - scope | C-23 |
| DCAM | Data Quality Management - evidence | C-04 |
| DCAM | Data Quality Management - gating | C-16 |
| DCAM | Data Quality Management - lineage | C-18 |
| DCAM | Technology Architecture - integrity | C-19 |
| COBIT | APO14.01 Define and communicate the organization's data management strategy and roles | C-23 |
| COBIT | APO14.02 Define and maintain a consistent business glossary | C-10, C-13, C-21 |
| COBIT | APO14.06 Ensure a data quality assessment approach | C-04, C-16 |
| COBIT | APO14.08 Manage the life cycle of data assets | C-14, C-20 |
| COBIT | BAI07.04 Establish a test environment | C-17 |
| COBIT | BAI10.02 Establish and maintain a configuration repository and baseline | C-18, C-24 |
| COBIT | BAI10.03 Maintain and control configuration items | C-11, C-12 |
| COBIT | DSS05.02 Manage network and connectivity security | C-20 |
| COBIT | DSS05.03 Manage endpoint security | C-14, C-22 |
| COBIT | DSS05.04 Manage user identity and logical access | C-03, C-15 |
| COBIT | DSS06.02 Control the processing of information | C-05, C-07, C-19 |
| COBIT | DSS06.03 Manage roles, responsibilities, access privileges and levels of authority | C-01, C-06, C-09 |
| COBIT | DSS06.05 Ensure traceability and accountability for information events | C-02, C-08 |

## Evidence in the product

`GET /api/v1/controls` is not yet a route (`dpre/server/app.py` is owned
elsewhere). The call it should make is
`{"controls": controls_matrix(), "verification": verify_controls(), "raci": raci()}`
from `dpre.registers`, and the Runs tab can render it with the `present`
flag per control as live evidence that the enforcing code and the proving
tests are in the deployed build.
