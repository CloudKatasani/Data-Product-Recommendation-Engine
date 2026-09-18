# Specification to implementation map

Every numbered section of [`specification.md`](specification.md), and where it
lives in the code. Use this when checking whether a requirement was met, or when
deciding where a change belongs.

| Spec | Requirement | Implementation |
| --- | --- | --- |
| 2 | Objectives, scope, non-goals | `README.md` "Deliberate limitations"; the non-goals are enforced by the guardrails below |
| 3.1 | Cognos rationalization report contract | `dpre/ingest/schemas.py::COGNOS_RATIONALIZATION`, `dpre/ingest/adapters/cognos.py::adapt_reports` |
| 3.2 | KPI lineage report contract | `schemas.py::COGNOS_KPI_LINEAGE`, `adapters/cognos.py::adapt_kpis` |
| 3.3 | Collibra metadata and lineage contract | `schemas.py::COLLIBRA_METADATA`, `CATALOG_LINEAGE`, `BUSINESS_GLOSSARY`, `adapters/catalog.py` |
| 3 (validation) | Row-count reconciliation, quarantine reason codes, as-of stamping | `dpre/ingest/validator.py` |
| 4 | Canonical knowledge graph, node types | `dpre/models.py`, `dpre/graph/builder.py` |
| 4.2 | ER-1 … ER-6 with confidences, probable-edge rule | `dpre/graph/resolver.py`, thresholds in `dpre/config.py` |
| 4.3 | Grain inference against the conformed backbone | `dpre/graph/grain.py` (inferred from the catalog; an industry accelerator supplies one where the estate is silent, via `dpre/accelerators/packs.py::backbone_for_industry`) |
| 5.1 | Parse, normalize, fingerprint, group, name, assign steward | `dpre/canonicalize/expr.py`, `fingerprint.py`, `grouping.py` |
| 5.2 | Match tiers: identical, variant, nominal conflict, cousin, opaque | `grouping.py::canonicalize`, `_link_cousins`, `_find_conflicts` |
| 5.3 | Conflict register | `dpre/canonicalize/conflicts.py` |
| 5.4 | Conflict patterns → Stage 6 decisions | `conflicts.py::PATTERN_DECISIONS`, surfaced in `dpre/seeds/semantic_model.py` |
| 6.1 | Bipartite build, projection, Louvain, grain split, size floor, naming | `dpre/cluster/bipartite.py`, `louvain.py`, `generator.py` |
| 6.2 | Consumer-aligned composites | `cluster/generator.py::_composite_candidates` |
| 6.3 | Entity master detection | `cluster/bipartite.py::hub_tables`, `generator.py::_entity_master_candidates` |
| 6.4 | What is deliberately not a candidate | `bipartite.py::build_bipartite` (unresolved metrics become data gaps), gate G4 for sunset-only sources |
| 7.1 | Archetype rules, evaluated in order, first match wins | `dpre/score/classify.py` (`ARCHETYPES` order + `MATCH_THRESHOLDS`) |
| 7.2 | Tier rules | `classify.py::_tier` |
| 7.3 | Confidence as a margin; below 0.6 show two | `classify.py::classify`, surfaced on the card |
| 8.1 | Dimensions and features | `dpre/score/scorer.py`, weights in `dpre/config.py::FEATURE_WEIGHTS` |
| 8.2 | Composite and versioned weights | `config.py::ScoreWeights`, `scorer.py` |
| 8.3 | Hard gates G1–G4 | `scorer.py::_apply_gates`, `_status_from_gates` |
| 8.4 | Explainability contract | `scorer.py` writes `EvidenceRow`s; `store.py` refuses a score without them |
| 9.1 | The candidate record | `dpre/models.py::Candidate` and the drawer in `dpre/server/static/app.js` |
| 9.2 | Seeds for DPF stages 1, 2, 3, 5, 6, 12 | `dpre/seeds/` |
| 9.3 | Catalog registration payload | `dpre/seeds/catalog_payload.py` |
| 9.4 | Coverage curve, retirement map, conflict heat map | `dpre/portfolio/views.py` |
| 10 | Chartered agents, one human gate, per-agent output tables, run id | `dpre/pipeline.py`, `dpre/store.py`. Nine agents now: the specification's seven, plus Programme (`dpre/programme/`) and Assessor (`dpre/quality/`) |
| 10.2 | Human review gate: accept, reject, merge, split, defer | `dpre/review/workflow.py`; the status machine is `dpre/governance/transitions.py` and the vocabulary `dpre/governance/reasons.py` |
| 10.3 | Conversational surface over a semantic view | `dpre/chat/semantic_view.py`, `agent.py` |
| 10.4 | Run cadence and runtime | `pipeline.py`; a full-estate run is ~0.4s (see `tests/test_pipeline.py`) |
| 11.1 | RAW / GRAPH / RECO tables, run_id and as_of on everything | `dpre/store.py::SCHEMA` |
| 11.2 | Reference SQL (metric demand, affinity, report coverage) | `dpre/usage.py`, `cluster/bipartite.py::project`, `cluster/generator.py::_retirable_reports` |
| 11 (null rule) | Every feature is a number; missing inputs lower the score | `score/scorer.py` (tested in `test_cluster_score.py`) |
| 12 | Worked example shape | reproduced by `python3 -m dpre run automated --industry utility` |
| 13.1 | Guardrails | `store.py` (propose-only, evidence), `narrate/ai.py` (AI_DRAFT), `chat/semantic_view.py` (grants), `seeds/catalog_payload.py` (proposal only). Tamper evidence is `store.py::verify_audit_chain` over hash-chained `REVIEW_DECISION` rows with append-only triggers, reported by `dpre/governance/audit.py` |
| 13.2 | Run quality gates | `pipeline.py::_quality_gates`, thresholds in `config.py::QUALITY_GATES`; the stability comparison is `dpre/quality/gates.py` and extract freshness `dpre/quality/replay.py::freshness_gate` |
| 13.3 | Feedback loop: weights, archetype thresholds, resolution | `dpre/review/feedback.py`; a council member may not approve weights trained on their own decisions (`dpre/server/security.py::assert_weight_approver_independent`) |
| 14.1 | Acceptance criteria | `tests/test_acceptance.py`, one test per bullet |
| 14.2 | Success measures | `dpre/programme/status.py::TARGETS` with actual, target and RAG per measure; `dpre status` |
| 14.3 | Phase exit criteria and falsifiers | `dpre/registers/traceability.py::traceability_matrix`; `dpre registers traceability` |
| 14 (detection rate) | The claim that detection is measurable | `dpre/quality/detection.py` compares planted against detected per class and names the misses; `dpre assess` |
| 15.1 | Risk mitigations (decision-critical override, hub extraction, opaque tier) | `review/workflow.py::mark_decision_critical`, `cluster/bipartite.py::hub_tables`, `canonicalize/expr.py` |
| 15.2 | Open decisions D-01 … D-08 | `dpre/registers/decisions.py`: each one with the position the engine takes meanwhile, where it is implemented, and a `decide` path that records who settled it. The underlying constants stay in `config.py` |
| 15 (assumptions) | Every contestable figure | `dpre/registers/assumptions.py::assumption_register`, 93 entries each naming its file and the decision that would change it |
| 16.1 | Cognos and Power BI object mapping | `dpre/ingest/adapters/powerbi.py`, `schemas.py` |
| 16.2 | Shared vs local measures, DAX parsing, lineage depth, usage parity | `graph/builder.py` (one node, many report edges), `canonicalize/expr.py`, `dpre/usage.py::tool_percentiles` |
| 16.3 | Alation as the alternative catalog | `adapters/catalog.py` (one adapter, two shapes) |
| 17.1 | One workbook per industry, same tabs | `dpre/synth/workbook.py`, `industries.py` |
| 17.2 | Planted defects and expected detection | `dpre/synth/defects.py`, planted in `synth/generator.py`, detection asserted in `tests/test_synthetic.py` |
| 17.3 | Fixed seed, synthetic flag, industry differences are content only | `synth/generator.py` (`GenerationParams` is shared; only `IndustryPack` changes) |
| 17.4 | The workbook is the ingestion contract | `tests/test_synthetic.py::test_the_workbook_is_the_ingestion_contract` |

## Where the implementation makes a decision the specification left open

These are judgement calls, made explicitly rather than silently:

1. **The conformed backbone is inferred, not assumed.** Section 4.3 names a
   utility backbone (Customer → Account → Premise → Service Point → Meter). The
   engine derives the chain from the catalog — dimensions with a primary key
   that several fact tables reference, ordered by how widely each is referenced
   — so healthcare resolves Patient → Encounter → Claim → Provider without
   configuration. An explicit backbone can still be passed to `build_graph`.
2. **Literals stay in the arithmetic shape.** Section 5.1 fingerprints
   `aggregation + sorted operand columns + arithmetic shape`. Thresholds are
   kept inside the shape, so `> 60` and `> 59` do not silently collapse into one
   metric — which is what makes the planted threshold drift detectable as a
   conflict rather than invisible as a merge.
3. **CALCULATE filters go to `filter_fp`, Cognos `for` clauses do not.**
   Section 16.2 puts DAX filter arguments in the filter fingerprint; section 5.4
   treats a differing time basis as a conflict. Both hold: a `for [fiscal_period]`
   clause changes the operand set and therefore the metric, while a CALCULATE
   filter shapes a variant.
4. **Cross-tool function names are normalized.** `total(...)` and `SUM(...)`
   reduce to one token so the same calculation fingerprints alike in Cognos and
   Power BI, which is what section 16.2 requires of cross-tool duplication.
5. **Archetype rules are first-match-in-order, with a score threshold.** Section
   7.1 says "evaluated in order; first match wins", so the ranking by score only
   decides the runner-up and therefore the confidence margin.
6. **Lineage completeness is counted in rows.** Resolved edges and quarantined
   rows are counted in the same unit — one row of the lineage extract — so gate
   G2 is comparable with the run-level resolution rate.
7. **Upstream extension stops at the system of record.** ER-3 walks a reporting
   view up to its source table, but never into a staging table: a load step is
   not "upstream of the reporting database".

## Beyond the specification

The specification describes an engine. These were added because a backlog is
not a deliverable and a ranking nobody can audit is not advice. Each names the
review finding that asked for it.

| Capability | Why | Implementation |
| --- | --- | --- |
| Governance ledgers | A decision a human already took should not be re-litigated every run (R-07, R-09, R-14) | `dpre/governance/`: lineage identity, ledgers that outlive a run, seeding, carry-forward |
| Gates that bind at acceptance | Section 8.3 caps a status; nothing stopped a reviewer accepting past the cap (R-02) | `store.py::_check_accept_gates`, `AcceptWithException` with a gate, a rationale and a second approver |
| Value model | "Retires 32 reports" is not a business case (R-08) | `dpre/value/`: benefit attributed once across the estate, build cost, payback, NPV, all against a versioned assumption register |
| Programme layer | A board cannot sequence work it cannot price (R-10, R-11, R-13, R-39) | `dpre/programme/`: effort with drivers, dependencies, delivery waves, RAID, status report |
| Measured detection | A claim that cannot be wrong is worth nothing (R-19) | `dpre/quality/detection.py` |
| Remediation plan | A gap list is not work (R-46) | `dpre/quality/remediation.py`: units with an owner, a priority and a run-over-run status |
| Stewardship register | A report owner is not a steward (R-44) | `dpre/quality/stewardship.py`, resolution order in `config.py::STEWARD_RESOLUTION_ORDER` |
| Bias assessment | An assurance function will ask what the ranking is blind to (R-49) | `dpre/quality/bias.py`, seven entries with the mitigation in code |
| Input data quality | The quality of what the engine was fed bounds what may be claimed (R-35) | `dpre/quality/dq.py` |
| Identity and hardening | The reviewer was a string in a request body (R-01, R-05, R-29, R-30, R-31) | `dpre/server/security.py`, `settings.py`, `errors.py`, `openapi.py` |
| Client-facing exports | The output stopped at a browser screen (R-12, R-36) | `dpre/export/`: executive summary, backlog workbook, per-candidate dossiers, synthetic banner on every page |
| Engagement model | A run that belongs to nobody cannot be governed (R-24) | `dpre/engagement/`: the record, the roster, scope drift, branding, the extract request pack |
| Controls matrix | An audit function needs something it can test (R-27) | `dpre/registers/controls.py`, 24 controls each verified against the code by `verify_controls` |
| Industry accelerators | Synthetic fixtures are not accelerators (R-38) | `dpre/accelerators/packs.py`: curated backbone, KPI dictionary, personas, steward roles, regulatory patterns, starter glossary |
| Replayability | The manifest omitted the clustering configuration (R-32) | `dpre/quality/replay.py`: configuration snapshot and hash, file digests, freshness gate |
| Platform path | The route to Snowflake and Cortex was undocumented (R-33) | [`snowflake-cortex-migration.md`](snowflake-cortex-migration.md) |
