# Assumptions register

Every constant the ranking, the gates, the clustering, the resolver, the
canonicalizer and the classifier run on, with where it lives in the code, the
specification section that set it, the open decision it implements and the
proposed owner from section 15.2 (review finding R-25).

The register is not a copy of the constants. `dpre/registers/assumptions.py::assumption_register`
reads them live from `dpre/config.py`, `dpre/score/classify.py`,
`dpre/governance/reasons.py`, `dpre/narrate/narrator.py`, `dpre/narrate/critic.py`
and `dpre/chat/agent.py`, so this page and the code cannot disagree; the table
below was rendered from it and `tests/test_registers.py::test_the_assumptions_register_document_lists_every_key`
fails if a key is added to the code and not to this page.

## How to read it

- **Contestable via** says how the value can be changed today. `EngineConfig field`
  means a run can take a different value without a code change (and
  `POST /api/v1/config` exposes it); `code change` means it cannot, and the
  owner column says who should be asked before the change is made.
- **config_version** - `dpre/registers/assumptions.py::config_version` hashes
  the `(key, value)` pairs of this register. Two runs are comparable only when
  their `config_version` matches; `snapshot_assumptions` writes the register
  into `ASSUMPTION_REGISTER` per run with that stamp.
- The value-model rate card (`dpre/value/assumptions.py::ValueAssumptions`) is
  a register of its own, versioned and approvable through `approve_assumptions`.
  It is included here **by reference** as `value.*` rows; the 16 rates are
  listed at the end and are never duplicated in code.

## The register (77 scoring, gate, clustering, resolution, canonicalization, classification and vocabulary assumptions)

### Scoring

| Key | Value | Unit | Spec | Implemented in | Decision | Owner | Contestable via |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `weights.dimensions.demand` | 0.35 | share of composite | 8.2 | `dpre/config.py::DIMENSION_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.dimensions.consolidation` | 0.3 | share of composite | 8.2 | `dpre/config.py::DIMENSION_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.dimensions.feasibility` | 0.25 | share of composite | 8.2 | `dpre/config.py::DIMENSION_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.dimensions.risk` | -0.1 | share of composite | 8.2 | `dpre/config.py::DIMENSION_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.demand.usage_weight` | 0.5 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.demand.consumer_breadth` | 0.3 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.demand.cadence` | 0.2 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.consolidation.reports_retirable` | 0.5 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.consolidation.variants_collapsed` | 0.3 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.consolidation.conflicts_surfaced` | 0.2 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.feasibility.lineage_completeness` | 0.35 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.feasibility.definition_coverage` | 0.25 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.feasibility.source_health` | 0.25 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.feasibility.calculation_determinism` | 0.15 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.risk.sensitivity` | 0.4 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.risk.grain_ambiguity` | 0.3 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `weights.features.risk.conflict_load` | 0.3 | share of dimension | 8.1 | `dpre/config.py::FEATURE_WEIGHTS (version v1.0-initial)` | D-04 | Data product council | ScoreWeights version, approved through Store.approve_weight_version |
| `disposition_weight.retire` | 1.0 | multiplier | 8.1 | `dpre/config.py::DISPOSITION_WEIGHT` |  | Rationalization lead | code change |
| `disposition_weight.merge` | 0.8 | multiplier | 8.1 | `dpre/config.py::DISPOSITION_WEIGHT` |  | Rationalization lead | code change |
| `disposition_weight.keep` | 0.5 | multiplier | 8.1 | `dpre/config.py::DISPOSITION_WEIGHT` | D-03 | Rationalization lead | code change |
| `disposition_weight.migrate` | 0.3 | multiplier | 8.1 | `dpre/config.py::DISPOSITION_WEIGHT` |  | Rationalization lead | code change |
| `keep_counts_toward_consolidation` | True | boolean | 15.2 | `dpre/config.py::EngineConfig.keep_counts_toward_consolidation` | D-03 | Rationalization lead | EngineConfig field; POST /api/v1/config |
| `usage_window_months` | 12 | months | 15.2 | `dpre/config.py::EngineConfig.usage_window_months` | D-01 | Data product council | EngineConfig field (recorded; the 12-month extract columns are what is read) |
| `recency_half_life_months` | 6.0 | months | 8.1 | `dpre/config.py::EngineConfig.recency_half_life_months` | D-01 | Data product council | EngineConfig field |
| `decision_critical_usage_floor` | 0.6 | share of the maximum usage weight | 15.1 | `dpre/config.py::DECISION_CRITICAL_USAGE_FLOOR` |  | Data product council | code change |
| `sensitivity_rank.public` | 0.0 | 0..1 | 8.1 | `dpre/config.py::SENSITIVITY_RANK` | D-06 | Privacy officer | code change |
| `sensitivity_rank.internal` | 0.35 | 0..1 | 8.1 | `dpre/config.py::SENSITIVITY_RANK` | D-06 | Privacy officer | code change |
| `sensitivity_rank.confidential` | 0.7 | 0..1 | 8.1 | `dpre/config.py::SENSITIVITY_RANK` | D-06 | Privacy officer | code change |
| `sensitivity_rank.restricted` | 1.0 | 0..1 | 8.1 | `dpre/config.py::SENSITIVITY_RANK` | D-06 | Privacy officer | code change |
| `sensitivity_review_threshold` | Restricted | sensitivity class | 15.2 | `dpre/config.py::EngineConfig.sensitivity_review_threshold` | D-06 | Privacy officer | EngineConfig field (recorded, not enforced) |

### Gates

| Key | Value | Unit | Spec | Implemented in | Decision | Owner | Contestable via |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gate.G1.min_users_per_bu` | 2 | users | 8.3 | `dpre/config.py::GATE_MIN_USERS_PER_BU` |  | Data product council | code change |
| `gate.G2.lineage_floor` | 0.6 | share | 8.3 | `dpre/config.py::GATE_LINEAGE_FLOOR` |  | Engine team | code change |
| `gate.G3.grain_ambiguity_max` | 0.3 | share | 8.3 | `dpre/config.py::GATE_GRAIN_AMBIGUITY_MAX` |  | Engine team | code change |
| `engine_max_status` | Proposed | status | 13.1 | `dpre/config.py::ENGINE_MAX_STATUS` | D-08 | Programme sponsor | not contestable: the propose-only guardrail |

### Quality Gates

| Key | Value | Unit | Spec | Implemented in | Decision | Owner | Contestable via |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `quality_gate.ingest_reconciliation_tolerance` | 0.005 | share | 13.2 | `dpre/config.py::QUALITY_GATES` |  | Engine team | code change |
| `quality_gate.resolution_rate_floor` | 0.8 | share | 13.2 | `dpre/config.py::QUALITY_GATES` |  | Engine team | code change |
| `quality_gate.parse_rate_floor` | 0.7 | share | 13.2 | `dpre/config.py::QUALITY_GATES` |  | Engine team | code change |
| `quality_gate.coverage_top_n` | 20 | count | 13.2 | `dpre/config.py::QUALITY_GATES` |  | Engine team | code change |
| `quality_gate.coverage_floor` | 0.5 | share | 13.2 | `dpre/config.py::QUALITY_GATES` |  | Engine team | code change |
| `quality_gate.stability_floor` | 0.85 | share | 13.2 | `dpre/config.py::QUALITY_GATES` |  | Engine team | code change |
| `quality_gate.stability_jaccard` | 0.6 | share | 13.2 | `dpre/config.py::QUALITY_GATES` |  | Engine team | code change |

### Clustering

| Key | Value | Unit | Spec | Implemented in | Decision | Owner | Contestable via |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cluster.min_similarity` | 0.25 | Jaccard | 6.1 | `dpre/config.py::ClusterConfig.min_similarity` |  | Engine team | EngineConfig.cluster field; POST /api/v1/config |
| `cluster.same_domain_bonus` | 0.2 | added similarity | 6.1 | `dpre/config.py::ClusterConfig.same_domain_bonus` |  | Engine team | EngineConfig.cluster field; POST /api/v1/config |
| `cluster.resolution` | 1.0 | Louvain resolution | 6.1 | `dpre/config.py::ClusterConfig.resolution` |  | Engine team | EngineConfig.cluster field; POST /api/v1/config |
| `cluster.min_metrics` | 3 | metrics | 6.1 | `dpre/config.py::ClusterConfig.min_metrics` | D-02 | Data product council | EngineConfig.cluster field; POST /api/v1/config |
| `cluster.min_business_units` | 2 | business units | 6.1 | `dpre/config.py::ClusterConfig.min_business_units` | D-02 | Data product council | EngineConfig.cluster field; POST /api/v1/config |
| `cluster.entity_master_min_communities` | 3 | communities | 6.1 | `dpre/config.py::ClusterConfig.entity_master_min_communities` |  | Engine team | code change |
| `cluster.composite_min_candidates` | 2 | candidates | 6.1 | `dpre/config.py::ClusterConfig.composite_min_candidates` |  | Engine team | code change |
| `cluster.composite_min_users` | 3 | users | 6.1 | `dpre/config.py::ClusterConfig.composite_min_users` |  | Engine team | code change |
| `cluster.resolution_sweep` | [0.7, 1.0, 1.3] | Louvain resolutions | 6.1 | `dpre/config.py::ClusterConfig.resolution_sweep` |  | Engine team | code change |

### Resolution

| Key | Value | Unit | Spec | Implemented in | Decision | Owner | Contestable via |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `er_confidence.ER-1` | 1.0 | confidence | 4.2 | `dpre/config.py::ER_CONFIDENCE` |  | Engine team | code change |
| `er_confidence.ER-2` | 0.95 | confidence | 4.2 | `dpre/config.py::ER_CONFIDENCE` |  | Engine team | code change |
| `er_confidence.ER-3` | 0.9 | confidence | 4.2 | `dpre/config.py::ER_CONFIDENCE` |  | Engine team | code change |
| `er_confidence.ER-4` | 0.8 | confidence | 4.2 | `dpre/config.py::ER_CONFIDENCE` |  | Engine team | code change |
| `er_confidence.ER-5` | 0.7 | confidence | 4.2 | `dpre/config.py::ER_CONFIDENCE` |  | Engine team | code change |
| `er_confidence.ER-6` | 0.6 | confidence | 4.2 | `dpre/config.py::ER_CONFIDENCE` |  | Engine team | code change |
| `er_probable_threshold` | 0.8 | confidence | 4.2 | `dpre/config.py::ER_PROBABLE_THRESHOLD` |  | Engine team | code change |
| `er4_name_similarity` | 0.92 | Jaro-Winkler | 4.2 | `dpre/config.py::ER4_NAME_SIMILARITY` |  | Engine team | code change |
| `er6_embedding_similarity` | 0.85 | cosine (offline stand-in) | 4.2 | `dpre/config.py::ER6_EMBEDDING_SIMILARITY` |  | Engine team | code change |
| `conformed_backbone_default` | ["Customer", "Account", "Premise", "Service Point", "Meter"] | entities | 4.3 | `dpre/config.py::CONFORMED_BACKBONE` |  | Domain steward | build_graph(backbone=...) or dpre/accelerators/packs.py::backbone_for_industry |

### Canonicalization

| Key | Value | Unit | Spec | Implemented in | Decision | Owner | Contestable via |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `nominal_conflict_label_similarity` | 0.9 | similarity | 5.2 | `dpre/config.py::NOMINAL_CONFLICT_LABEL_SIMILARITY` |  | Domain steward | code change |
| `parser_version` | parser-1.2.0 | version | 13.1 | `dpre/config.py::PARSER_VERSION` |  | Engine team | code change |
| `steward_resolution_order` | ["glossary term steward (1.0)", "table steward (0.85)", "... | ordered list | 5.1 | `dpre/config.py::STEWARD_RESOLUTION_ORDER` |  | Domain steward | code change |
| `glossary_status_credit` | {"approved": 1.0, "certified": 1.0, "published": 1.0, "ac... | credit per status | 5.1 | `dpre/config.py::GLOSSARY_STATUS_CREDIT` |  | Domain steward | code change |

### Classification

| Key | Value | Unit | Spec | Implemented in | Decision | Owner | Contestable via |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `archetype_threshold.Entity Master` | 0.55 | rule score | 7.1 | `dpre/score/classify.py::MATCH_THRESHOLDS` |  | Data product council | code change (feedback loop lists rules overridden > 30% for rewriting) |
| `archetype_threshold.Reference Data` | 0.6 | rule score | 7.1 | `dpre/score/classify.py::MATCH_THRESHOLDS` |  | Data product council | code change (feedback loop lists rules overridden > 30% for rewriting) |
| `archetype_threshold.Event Stream` | 0.6 | rule score | 7.1 | `dpre/score/classify.py::MATCH_THRESHOLDS` |  | Data product council | code change (feedback loop lists rules overridden > 30% for rewriting) |
| `archetype_threshold.Metric / KPI` | 0.55 | rule score | 7.1 | `dpre/score/classify.py::MATCH_THRESHOLDS` |  | Data product council | code change (feedback loop lists rules overridden > 30% for rewriting) |
| `archetype_threshold.Feature Store` | 0.45 | rule score | 7.1 | `dpre/score/classify.py::MATCH_THRESHOLDS` |  | Data product council | code change (feedback loop lists rules overridden > 30% for rewriting) |
| `archetype_threshold.Insight / Recommendation` | 0.45 | rule score | 7.1 | `dpre/score/classify.py::MATCH_THRESHOLDS` |  | Data product council | code change (feedback loop lists rules overridden > 30% for rewriting) |
| `reference_row_ceiling` | 10000 | rows | 7.1 | `dpre/score/classify.py::REFERENCE_ROW_CEILING` |  | Engine team | code change |
| `archetype_confidence_two_choice` | 0.6 | margin | 7.3 | `dpre/score/classify.py::classify (literal)` |  | Engine team | code change |

### Vocabulary

| Key | Value | Unit | Spec | Implemented in | Decision | Owner | Contestable via |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `reason_codes` | {"Accept": ["value_clear", "retires_reports", "resolves_c... | closed vocabulary | 10.2 | `dpre/governance/reasons.py::REASON_CODES` |  | Data product council | code change (tests assert every code is labelled) |
| `overridable_fields` | ["archetype", "tier", "proposed_name", "grain", "owner_ca... | fields | 10.2 | `dpre/governance/reasons.py::OVERRIDABLE_FIELDS` |  | Data product council | code change |
| `persona_map` | {"collection": "Collections manager", "credit": "Credit m... | keyword -> role | 9.2 | `dpre/narrate/narrator.py::ROLE_BY_KEYWORD` |  | Named consumer | code change; dpre/accelerators/packs.py::persona_for per industry |
| `stage_criteria` | ["S1-consumer", "S1-decision", "S1-latency", "S2-scope", ... | criteria | 10.1 | `dpre/narrate/critic.py::STAGE_CRITERIA` |  | Data product council | code change |
| `suggested_questions` | ["Which candidates retire the most Finance reports?", "Sh... | questions | 10.3 | `dpre/chat/agent.py::SUGGESTED_QUESTIONS` |  | Engine team | code change |

### Value (by reference to `dpre/value/assumptions.py`)

| Key | Default value | Owner | Contestable via |
| --- | --- | --- | --- |
| `value.currency` | USD | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.maintenance_hours_per_report` | {"low": 24.0, "medium": 60.0, "high": 120.0} | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.complexity_band_edges` | [0.34, 0.67] | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.loaded_hourly_rate` | 95.0 | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.licence_cost_per_report_per_year` | {"cognos": 1200.0, "powerbi": 420.0} | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.infra_cost_per_package_per_year` | 15000.0 | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.reconciliation_hours_per_conflict_per_period` | 6.0 | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.periods_per_year` | 12 | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.mis_decision_cost_band` | {"regulatory": 50000.0, "financial": 20000.0, "operational": 5000.0} | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.disposition_realisation` | {"retire": 1.0, "merge": 1.0, "migrate": 0.5, "keep": 0.0} | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.build_cost_per_effort_point` | 1800.0 | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.build_cost_by_size` | {"XS": 25000.0, "S": 60000.0, "M": 140000.0, "L": 260000.0, "XL": 4... | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.build_cost_method` | points | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.discount_rate` | 0.08 | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.horizon_years` | 3 | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |
| `value.ramp_year1_share` | 0.5 | Client finance partner | `dpre/value/assumptions.py::approve_assumptions` |

The `basis` of the rate card ("illustrative - to be contested by the client"
until approved) is carried on every value row, so no committee sees a money
figure without the sentence that says where the rates came from.

## What is still only a code constant, and why it matters

- The hard gates (`gate.G1.min_users_per_bu`, `gate.G2.lineage_floor`,
  `gate.G3.grain_ambiguity_max`) are section 8.3 values the specification
  fixed. They are listed so a client can see them; changing them is a
  specification change, not a configuration change.
- `disposition_weight.keep` (0.5) is the half of open decision D-03 that the
  `keep_counts_toward_consolidation` flag does not cover: when Keep reports
  count, this is how much they count.
- `sensitivity_review_threshold` is **recorded, not enforced** (D-06): see
  [decision-register.md](decision-register.md).
- `archetype_threshold.*` are the rule bars in `dpre/score/classify.py`. The
  feedback loop (`dpre/review/feedback.py::archetype_override_rates`) lists a
  rule overridden more than 30% of the time for rewriting; it does not move
  the bar itself.
- `persona_map` is the keyword-to-role map the narrator uses when a client's
  business-unit names are generic. The per-industry accelerators
  (`dpre/accelerators/packs.py::persona_for`) carry a better one.

## Wiring the register into a run

Not done here because `dpre/pipeline.py` and `dpre/store.py` belong to other
owners; the calls are one line each:

- after `store.persist_run(...)` in `dpre/pipeline.py::run_pipeline`:
  `manifest.stats["config_version"] = snapshot_assumptions(store.connection, run_id, config=config)`
  and `snapshot_decisions(store.connection, run_id, config=config, catalog=bundle.catalog)`;
- in `dpre/server/app.py::config_update`, after each change:
  `ws.store.record_config_change(principal.identity, field, before, after)` (the ledger
  exists; the route does not call it yet);
- an `Assumptions` tab in the browser reading `GET /api/v1/registers/assumptions`
  (route to add in `dpre/server/app.py`: `{"rows": [a.to_dict() for a in assumption_register(ws.config)]}`).
