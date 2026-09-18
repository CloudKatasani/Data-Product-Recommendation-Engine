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
| 4.3 | Grain inference against the conformed backbone | `dpre/graph/grain.py` (the backbone is *inferred from the catalog*, not assumed) |
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
| 10 | Seven agents, one human gate, per-agent output tables, run id | `dpre/pipeline.py`, `dpre/store.py` |
| 10.2 | Human review gate: accept, reject, merge, split, defer | `dpre/review/workflow.py` |
| 10.3 | Conversational surface over a semantic view | `dpre/chat/semantic_view.py`, `agent.py` |
| 10.4 | Run cadence and runtime | `pipeline.py`; a full-estate run is ~0.4s (see `tests/test_pipeline.py`) |
| 11.1 | RAW / GRAPH / RECO tables, run_id and as_of on everything | `dpre/store.py::SCHEMA` |
| 11.2 | Reference SQL (metric demand, affinity, report coverage) | `dpre/usage.py`, `cluster/bipartite.py::project`, `cluster/generator.py::_retirable_reports` |
| 11 (null rule) | Every feature is a number; missing inputs lower the score | `score/scorer.py` (tested in `test_cluster_score.py`) |
| 12 | Worked example shape | reproduced by `python3 -m dpre run automated --industry utility` |
| 13.1 | Guardrails | `store.py` (propose-only, evidence), `narrate/ai.py` (AI_DRAFT), `chat/semantic_view.py` (grants), `seeds/catalog_payload.py` (proposal only) |
| 13.2 | Run quality gates | `pipeline.py::_quality_gates`, thresholds in `config.py::QUALITY_GATES` |
| 13.3 | Feedback loop: weights, archetype thresholds, resolution | `dpre/review/feedback.py` |
| 14.1 | Acceptance criteria | `tests/test_acceptance.py`, one test per bullet |
| 15.1 | Risk mitigations (decision-critical override, hub extraction, opaque tier) | `review/workflow.py::mark_decision_critical`, `cluster/bipartite.py::hub_tables`, `canonicalize/expr.py` |
| 15.2 | Open decisions D-01 … D-08 | exposed as configuration in `config.py` (usage window, size floor, Keep handling, weights, sensitivity threshold) |
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
