# Traceability to specification section 14

One row per phase exit criterion, falsifier and 14.1 acceptance bullet:
the feature that implements or captures it, the tests that prove the
feature, the table the evidence lands in, and whether the criterion is
provable in code, partly in code and completed in the field, or only in the
field (review finding R-40). `docs/implementation-map.md` row 14.1 covers the
bullets; this page covers the phases.

Rendered from `dpre/registers/traceability.py::CRITERIA`;
`tests/test_registers.py::test_every_criterion_and_falsifier_has_a_feature_a_test_and_evidence`
checks that every cited function and test exists.

| Ref | Phase | Kind | Criterion | Threshold | Feature | Tests | Evidence | Provable |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P1-E1 | 1 | exit | >= 80% lineage resolution | >= 0.80 | `dpre/pipeline.py::_quality_gates (resolution_rate)` | `tests/test_graph.py::test_graph_meets_the_run_quality_gates`<br>`tests/test_acceptance.py::test_run_quality_gates_are_all_evaluated` | RUN.quality_gates | provable in code |
| P1-E2 | 1 | exit | >= 70% parse rate | >= 0.70 | `dpre/pipeline.py::_quality_gates (parse_rate)` | `tests/test_graph.py::test_graph_meets_the_run_quality_gates`<br>`tests/test_acceptance.py::test_run_quality_gates_are_all_evaluated` | RUN.quality_gates | provable in code |
| P1-E3 | 1 | exit | Domain steward signs off the conflict register as correct for a 30-metric sample | 30 metrics adjudicated | `dpre/store.py::resolve_conflict; dpre/registers/traceability.py::record_grouping_verdict` | `tests/test_acceptance.py::test_the_conflict_register_is_complete_enough_to_sign_off`<br>`tests/test_governance.py::test_conflict_adjudication_is_validated_and_ledgered`<br>`tests/test_registers.py::test_grouping_verdicts_compute_the_phase_1_falsifier` | CONFLICT_DECISION; GROUPING_VERDICT | partly in code, completed in the field |
| P1-F1 | 1 | falsifier | Stewards reject > 20% of the canonical groupings in the sample | <= 0.20 | `dpre/registers/traceability.py::grouping_rejection_rate` | `tests/test_registers.py::test_grouping_verdicts_compute_the_phase_1_falsifier` | GROUPING_VERDICT | partly in code, completed in the field |
| P2-E1 | 2 | exit | Top 20 candidates cover >= 50% of usage-weighted consumption | >= 0.50 | `dpre/pipeline.py::_usage_coverage; _quality_gates (coverage_sanity)` | `tests/test_acceptance.py::test_top_twenty_candidates_cover_half_of_usage` | RUN.stats.usage_coverage_top_n | provable in code |
| P2-E2 | 2 | exit | 2 candidates Accepted and opened in the DPF with seeds | >= 2 Accepted | `dpre/review/workflow.py::review; dpre/seeds/__init__.py::write_seeds` | `tests/test_workflow.py::test_seed_artifacts_cover_the_documented_stages`<br>`tests/test_programme.py::test_status_report_computes_the_14_2_measures_with_rag` | REVIEW_DECISION; seeds folder | partly in code, completed in the field |
| P2-E3 | 2 | exit | Reviewers rate >= 70% of cards usable without rework | >= 0.70 | `dpre/review/feedback.py::usable_without_rework_rate (REVIEW_DECISION.usable_without_rework, rework_needed)` | `tests/test_governance.py::test_reason_codes_are_validated_server_side`<br>`tests/test_workflow.py::test_the_feedback_report_covers_all_three_re_estimations` | REVIEW_DECISION.usable_without_rework | partly in code, completed in the field |
| P2-F1 | 2 | falsifier | Accepted candidates need their metric set changed by > 40% at DPF Stage 2 | <= 0.40 | `dpre/registers/traceability.py::stage2_metric_drift (DP_CANDIDATE_PAYLOAD_HISTORY written by Merge and Split)` | `tests/test_registers.py::test_stage_2_metric_drift_is_measured_from_payload_history` | DP_CANDIDATE_PAYLOAD_HISTORY | partly in code, completed in the field |
| P3-E1 | 3 | exit | Full-estate run in < 30 minutes | < 1800 s | `dpre/pipeline.py::run_pipeline (agent_log seconds)` | `tests/test_pipeline.py::test_a_full_estate_run_is_quick` | RUN.agent_log | partly in code, completed in the field |
| P3-E2 | 3 | exit | Conversational answers cite evidence on 20 test questions | 20 of 20 cited | `dpre/registers/traceability.py::QUESTION_BANK; citation_rate` | `tests/test_acceptance.py::test_the_agent_answers_the_specifications_own_questions`<br>`tests/test_registers.py::test_the_question_bank_is_answered_with_citations` | chat answers (citations) | partly in code, completed in the field |
| P3-E3 | 3 | exit | First weight re-estimation approved by the council | 1 approved version after v1.0-initial | `dpre/review/feedback.py::reestimate_weights; approve_weights` | `tests/test_governance.py::test_approval_needs_the_floor_and_a_hold_out_win`<br>`tests/test_workflow.py::test_weights_are_re_estimated_once_there_are_enough_decisions` | WEIGHT_APPROVAL; SCORE_WEIGHT | partly in code, completed in the field |
| P3-F1 | 3 | falsifier | Reviewer decisions too few (< 50) to re-estimate weights | >= 50 decisions | `dpre/review/feedback.py::MIN_DECISIONS` | `tests/test_workflow.py::test_weights_are_not_re_estimated_from_too_few_decisions` | REVIEW_DECISION | provable in code |
| A-01 | 14.1 | acceptance | Every recommendation is reproducible from stored extract IDs, weight version and parser version | equal rankings | `dpre/pipeline.py::run_pipeline` | `tests/test_acceptance.py::test_a_run_is_reproducible_from_its_stored_inputs` | RUN | provable in code |
| A-02 | 14.1 | acceptance | No code path other than REVIEW_DECISION moves a candidate past Proposed | ProposeOnlyError | `dpre/store.py::save_candidates` | `tests/test_acceptance.py::test_no_code_path_but_a_review_decision_moves_a_candidate_past_proposed` | DP_CANDIDATE; REVIEW_DECISION | provable in code |
| A-03 | 14.1 | acceptance | Every score row has evidence rows; the inverse cannot be written | EvidenceMissingError | `dpre/store.py::save_candidates` | `tests/test_acceptance.py::test_a_score_row_without_evidence_cannot_be_written`<br>`tests/test_acceptance.py::test_published_runs_have_evidence_behind_every_score` | DP_CANDIDATE_EVIDENCE | provable in code |
| A-04 | 14.1 | acceptance | The conversational agent cannot query outside its semantic view | grant audit | `dpre/chat/semantic_view.py::check_query` | `tests/test_acceptance.py::test_the_conversational_agent_cannot_query_outside_its_semantic_view` | semantic_view.QUERIES | partly in code, completed in the field |
| A-05 | 14.1 | acceptance | AI-drafted names and definitions are visibly marked until accepted, in the card, the seeds and the catalog payload | AI_DRAFT | `dpre/seeds/catalog_payload.py::catalog_payload` | `tests/test_acceptance.py::test_ai_drafted_names_are_visibly_marked_everywhere` | KPI_CANONICAL.name_status | provable in code |
| A-06 | 14.1 | acceptance | A seeded Stage 1 decision register passes the DPF exit criteria once the blocked decision is added | three TO BE CONFIRMED fields | `dpre/seeds/decision_register.py` | `tests/test_acceptance.py::test_a_seeded_decision_register_needs_only_the_blocked_decision` | stage1-decision-register.yaml | partly in code, completed in the field |
| A-07 | 14.1 | acceptance | The conflict register for the pilot domain is signed off by the steward | every conflict adjudicated | `dpre/store.py::resolve_conflict` | `tests/test_acceptance.py::test_the_conflict_register_is_complete_enough_to_sign_off` | CONFLICT_DECISION | partly in code, completed in the field |

## The two criteria that had no capture mechanism

**Phase 1 falsifier - stewards reject > 20% of canonical groupings in a
30-metric sample.** Before this change a steward could resolve a conflict
(`Store.resolve_conflict`) and accept a name (`Store.accept_metric_name`) but
could not say "this grouping is wrong". `dpre/registers/traceability.py::record_grouping_verdict`
writes an append-only `GROUPING_VERDICT` row (run, metric, accept or reject,
steward, reason, sample id); `grouping_rejection_rate` takes the latest
verdict per metric and reports the sample size against 30 and the rate
against 0.20. A rejection needs a reason, because the reason is what tells
the engine team which fingerprint rule is wrong. The HTTP route
`POST /api/v1/runs/{run_id}/metrics/{metric_id}/grouping-verdict` is
wiring for `dpre/server/app.py` (see wiring below).

**Phase 2 falsifier - metric set changed by > 40% at Stage 2.** Merge and
Split already snapshot the candidate payload into `DP_CANDIDATE_PAYLOAD_HISTORY`
before touching it. `stage2_metric_drift` compares the first snapshot's
`metric_ids` with the live `DP_CANDIDATE_METRIC` rows for every Accepted
candidate and reports the share over the 0.40 drift threshold. What changes
inside the DPF after hand-over is not visible to the engine; that part stays
a field measure.

## Computable criteria per run

`dpre/registers/traceability.py::phase_metrics(store, run_id)` returns every
criterion the store can measure, with value, threshold and verdict:
`resolution_rate`, `parse_rate`, `grouping_sample_size`,
`grouping_rejection_rate`, `usage_coverage_top_20`, `accepted_candidates`,
`usable_without_rework_rate`, `stage2_metric_drift`, `run_seconds`,
`approved_weight_versions`, `final_decisions` and `open_conflicts`; with
`ask_questions=True` it also runs the twenty-question bank
(`QUESTION_BANK`) through `dpre/chat/agent.py::ConversationalAgent` and
reports the share answered with citations. On the synthetic utility pack the
bank is answered 20 of 20 with citations; a client replaces the bank with its
own twenty and the rate is computed the same way.

`dpre/programme/status.py::_phase_exit` already reports four of these on the
status report; `phase_metrics` is the superset and is the call the status
report should make once the two modules are wired together.

## Wiring

- `dpre/server/app.py`: add `POST /api/v1/runs/{run_id}/metrics/{metric_id}/grouping-verdict`
  calling `record_grouping_verdict(ws.store.connection, run_id, metric_id,
  body["verdict"], principal.identity, body.get("reason", ""), sample_id=body.get("sample_id", ""))`
  with action `steward`; and `GET /api/v1/runs/{run_id}/phase-metrics` calling `phase_metrics`.
- `dpre/programme/status.py::status_report`: replace `_phase_exit` with
  `phase_metrics(store, run_id)["metrics"]` or merge the two.
- `dpre/store.py`: call `dpre.registers.traceability.ensure_schema(self.connection)`
  in `Store.__init__` so the ledger exists before the first verdict (every
  function here also calls it, so this is belt and braces).
