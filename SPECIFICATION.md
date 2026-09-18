# Data Product Recommendation Engine — build specification

**Version 1.0 · Status: implemented and verified · 40,500 lines of Python, 392 tests**

This document is written so that a competent engineer, or a coding agent such as
Claude or Codex, can build this application from scratch. It states what the
system does, the rules it enforces, the exact constants it runs on, and the
order in which to build it.

It is a *build* specification. The original commissioning charter is
[`docs/specification.md`](docs/specification.md); its section numbers are cited
throughout by references like "charter §8.3" and are load-bearing — the
traceability matrix and several tests key off them. Do not renumber it.

---

## 0. How to read this document, and how to build from it

Build in the order of §16. Each package there is independently testable, and the
dependency arrows run one way. Two rules matter more than the rest:

1. **The guardrails in §2 are structural, not advisory.** They are enforced by
   the storage layer and the type system, not by convention or review. If your
   implementation can violate one of them, it is not this system.
2. **Every constant in this document is a real value from the running code.**
   Where a number appears, use it. Where a formula appears, implement it
   exactly, because the reference bands, quality gates and tests are calibrated
   against those exact values.

**Technology constraint.** Python 3.11+, standard library only, no runtime
dependencies. This is deliberate: the engine runs on a laptop, inside a
locked-down client environment, and in a container with no package index.
`pytest` and `pyyaml` are permitted for tests only. That constraint forces you
to implement an XLSX reader/writer, a YAML emitter, Louvain community
detection, Jaro-Winkler similarity, an L2-regularised logistic regression, an
HTTP server and a multipart parser. All are small; §15 says how small.

---

## 1. What the system is

An engine that converts **report-level KPI lineage** (Cognos, Power BI) and
**catalog metadata** (Collibra, Alation) into a **ranked, evidence-backed
backlog of data product candidates**, each named by the decision it serves, the
reports it would retire and the metric conflicts it resolves.

It is used on consulting engagements to answer one question in weeks rather than
quarters: *given this reporting estate, which data products should we build
first, and what is the evidence?*

**Two entry paths, one pipeline.**

| Path | Input | Use |
| --- | --- | --- |
| **Manual** | The client's own extracts from Cognos, Power BI, Collibra or Alation | Real engagements |
| **Automated** | Nothing but an industry name | Demonstration, testing, calibration |

Automated mode generates a synthetic estate for one of nine industries, carrying
deliberately planted defects (§12). Both paths produce the same `ExtractBundle`,
so nothing downstream knows which was used.

**What it is not.** It is not text-to-SQL. It does not design data products. It
does not write to the catalog. It does not decide anything.

---

## 2. Guardrails — the non-negotiable properties

These define the system. Everything else is implementation.

### G-1 Propose-only

No code path may move a candidate past status `Proposed` except a recorded human
review decision. Enforce at the storage layer: `Store.save_candidates` raises
`ProposeOnlyError` if any candidate arrives with a status past the engine
ceiling. The only writer of a later status is `Store.record_decision`, which
requires a named, authenticated reviewer.

### G-2 Evidence required, per feature

No score may be written without the evidence rows behind it. A `DP_CANDIDATE_SCORE`
row with no corresponding `DP_CANDIDATE_EVIDENCE` rows raises
`EvidenceMissingError` and the run is not published. This binds **per feature**,
not per candidate: every feature that contributes to a dimension cites the graph
rows it was computed from, including the risk features, which cite the
candidate's own sensitivity class, grain ambiguity and open conflicts.

### G-3 AI outputs are marked

Every drafted name, purpose and decision-register entry carries `AI_DRAFT` until
a human accepts it. The catalog payload lists drafted items under
`import_blocked_by` so nothing unaccepted can reach a catalog.

### G-4 No free-form query from the chat surface

The conversational surface selects a *named* query from a fixed list (§11). It
never composes SQL. Write verbs and non-whitelisted objects are refused. The
governance ledgers are explicitly out of reach.

### G-5 Reproducibility

Same inputs plus same configuration produce the same ranking, byte for byte. No
wall-clock, no unseeded randomness, no dictionary-ordering dependence. Every run
stores its extract ids, file digests, the full clustering configuration and its
hash, the weight version, the parser version, the engine version and build.

### G-6 Tamper evidence

Every decision row is hash-chained to the one before it, and the ledger tables
refuse `UPDATE` and `DELETE` via SQLite triggers. `verify_audit_chain`
recomputes the chain and names the first row that does not verify.

### G-7 Identity is authenticated, never asserted

The reviewer is the principal the server resolved, never a string in a request
body. A non-loopback bind without an identity source is refused at startup.

### G-8 The catalog remains the system of record

The engine writes a *proposal payload* for a steward to import. It never writes
to Collibra or Alation.

### G-9 Gates bind at acceptance

A hard gate caps a candidate's status at scoring time *and* is re-checked when a
human accepts. A `Blocked` candidate cannot be Accepted at all. An `Exploratory`
one needs either a recorded consumer confirmation or an `AcceptWithException`
naming the gate, the rationale and a second approver.

### G-10 Decisions outlive the run

A confirmed consumer, an adjudicated conflict, an accepted metric name and a
report marked decision-critical are keyed by **lineage id**, not candidate id,
and re-applied to the next run before it is scored.

---

## 3. Domain model

Implement as frozen-ish dataclasses in one module (`dpre/models.py`). Field names
mirror the storage tables so the in-memory model, the database and the JSON API
all agree.

### 3.1 Input records (the ingestion contract)

`ReportRecord`, `KpiRecord`, `CatalogColumnRecord`, `CatalogLineageRecord`,
`GlossaryTermRecord`, gathered into one `ExtractBundle`:

```python
@dataclass
class ExtractBundle:
    reports: list[ReportRecord]
    kpis: list[KpiRecord]
    columns: list[CatalogColumnRecord]
    lineage: list[CatalogLineageRecord]
    glossary: list[GlossaryTermRecord]
    planted_defects: list[dict]          # synthetic runs only
    mode: str                            # manual | automated
    catalog: str                         # collibra | alation
    industry: str
    as_of_date: datetime.date
    synthetic: bool
    generation_id: str
    manifest: dict
    source_files: list[dict]
```

Key fields worth naming explicitly:

- `ReportRecord`: `report_id`, `report_name`, `tool`, `semantic_container`,
  `business_unit`, `owner`, `run_count_90d`, `run_count_12m`,
  `distinct_users_12m`, `last_run_date`, `schedule_flag`, `disposition`
  (`Keep|Merge|Retire|Migrate`), `complexity_score`, `decision_critical`.
- `KpiRecord`: `kpi_id`, `kpi_label`, `report_id`, `calculation_expression`,
  `expression_language` (`cognos|dax`), `aggregation_type`, `filter_expression`,
  `source_system`, `database`, `schema`, `table`, `column`, `measure_scope`
  (`model|report`), `tool`. Property `raw_reference` joins the five source parts
  with dots.
- `CatalogColumnRecord`: the five identity parts plus `business_term`,
  `definition`, `data_domain`, `data_owner`, `data_steward`, `classification`,
  `pii_flag`, `system_of_record`, `certification_status`, `quality_score`,
  `lifecycle_status`, `sunset_date`, `successor_system`. Properties
  `column_fqn` and `table_fqn`.

### 3.2 Graph nodes

`ReportNode`, `KpiNode`, `ColumnNode`, `TableNode`, `EdgeKpiColumn`,
`QuarantineRow`, gathered into `KnowledgeGraph` with `reports`, `kpis`,
`columns`, `tables`, `edges_kpi_column`, `kpi_reports`, `quarantine`,
`glossary`, `systems`, `as_of_date`, `stats`.

`KpiNode` carries the parse results: `expression_ast`, `fingerprint`,
`filter_fp`, `parse_status` (`PARSED|PARSE_FAIL`), `operand_columns`,
`filter_columns`, `time_modifier`, `grain`.

`TableNode` carries `inferred_grain`, `grain_source`, `sor_flag`,
`lifecycle_status`, `sunset_date`, `successor_system`.

### 3.3 Output model

`CanonicalMetric`, `MetricVariant`, `MetricConflict`, `Candidate`,
`CandidateScore`, `ScoreFeature`, `EvidenceRow`, `GateResult`, `ReviewDecision`,
`RunManifest`.

`ScoreFeature` must carry the normalisation basis:

```python
@dataclass
class ScoreFeature:
    dimension: str
    feature: str
    value: float
    normalized: float
    weight: float
    contribution: float
    detail: str = ""
    reference: float = 0.0        # the run maximum divided by, or 0.0
    reference_id: str = ""        # the candidate that set that maximum
    reference_basis: str = "absolute"   # absolute | run_relative
```

This matters: five features are normalised against the best candidate in the
same run, so "demand 82" compares within a run and not between two. The
denominator travels with the number.

---

## 4. Ingestion (charter §3, §16, §17.4)

### 4.1 The eight input contracts

Define each as an `InputSchema(key, label, tool, tab, fields, description)` where
each `FieldSpec(name, required, purpose, aliases)` declares why the field exists
and what other names it goes by. The `tab` is the canonical worksheet name.

| Key | Tab | Required fields |
| --- | --- | --- |
| `cognos_rationalization` | `Cognos_Rationalization` | report_id, report_name, folder_path, fm_package, owner, business_unit, run_count_90d, run_count_12m, distinct_users_12m, last_run_date, disposition |
| `cognos_kpi_lineage` | `Cognos_KPI_Lineage` | kpi_id, kpi_label, report_id, fm_package, query_subject, query_item, calculation_expression, aggregation_type, source_system, database, schema, table, column |
| `powerbi_inventory` | `PowerBI_Inventory` | report_id, report_name, workspace, semantic_model, owner, business_unit, view_count_12m, distinct_users_12m, last_viewed_date |
| `powerbi_measure_lineage` | `PowerBI_Measure_Lineage` | measure_id, measure_name, semantic_model, dax_expression, source_system, source_database, source_schema, source_table, source_column |
| `collibra_metadata` | `Collibra_Metadata` | system, database, schema, table, column, data_domain, data_owner, data_steward, classification, pii_flag |
| `catalog_lineage` | `Collibra_Lineage` | src_table, tgt_table |
| `business_glossary` | `Business_Glossary` | term |
| `alation_metadata` | `Alation_Metadata` | ds_name, schema_name, table_name, column_name, custom_field_domain, steward |

**`report_id` is deliberately optional on `powerbi_measure_lineage`.** Charter
§16.2 distinguishes a model-scoped (shared) measure, which belongs to the
semantic model and is used by many reports, from a report-scoped one. A real
Power BI export carries no report for the former. Requiring one rejects valid
exports.

**`db_name`, `sensitivity_label` and `pii` are optional on `alation_metadata`.**
Many Alation deployments carry a data source and a schema with no separate
database level, and expose sensitivity and PII as custom fields whose names vary
by deployment.

### 4.2 Schema detection

`suggest_schema(columns, sheet="") -> (key, confidence)`:

1. If the normalised sheet name is in `NON_INPUT_TABS` (`readme`,
   `planted_defects`, `notes`, `cover`, `contents`, `index`, `instructions`,
   `changelog`, `manifest`, …) return `("", 0.0)`. A README scored as a KPI
   lineage sheet at 6% confidence is noise and a trap.
2. Score each schema by header overlap: a required field hit scores 2, an
   optional field hit scores 1, divided by the total possible.
3. **If the sheet name matches a schema's `tab`, that is decisive**: score
   becomes `max(score, 0.5) + 0.5`, but only when the header score already
   clears 0.25. Collibra and Alation both describe columns, and a Collibra
   export using generic names like `schema_name` scores higher against Alation
   than against the catalog it came from. The tab name resolves what headers
   cannot.
4. Return the best key, capped at 1.0.

`map_columns(schema_key, columns) -> dict` maps each field name to the actual
header, trying the field name first, then each alias.

### 4.3 Manual ingestion

`ingest_manual(sources, as_of=None, catalog_preference=None) -> IngestResult`.
Each `SourceSpec(path, schema_key, sheet, mapping, label)` is loaded, dispatched
to its adapter, and appended to the bundle. Then two post-passes:

**Model-measure attachment.** A model-scoped Power BI measure has no report.
Attach each one to every report sharing its semantic model, cloning the KPI
record per report with id `f"{measure_id}::{report_id}"`. One shared measure
becomes one KPI node per consuming report, which is what makes it look shared to
the clusterer. A *report*-scoped measure naming no report is unattributable:
report it as a gap, never spread it across the model, because that would invent
usage the export does not claim.

**Catalog de-duplication.** Collibra and Alation describe the same physical
estate. If both were supplied, keep one — the stated preference, else the first
bound — and record setting the other aside. Ingesting both counts every column
twice and inflates duplication, definition coverage and the whole backlog.

### 4.4 Validation

`validate(bundle) -> ValidationReport` with issues at `error | warning | info`:
row-count reconciliation against the extract manifest within 0.5%, orphan report
ids, missing references, sparse usage, duplicate rows, invalid dispositions,
negative run counts, a 90-day count above the 12-month count, future last-run
dates, unrecognised aggregations. An `error` stops the run unless forced, and a
forced run records that it was forced.

`_check_coverage` additionally requires the KPI lineage input, warns when no
catalog was supplied, and notes missing recommended inputs.

---

## 5. The knowledge graph (charter §4)

### 5.1 Node and edge construction

`build_graph(bundle, backbone=None, manual_mappings=None) -> KnowledgeGraph`.
Build Report, KPI, Table and Column nodes, then resolve every KPI's source
reference to a catalog column.

### 5.2 Entity resolution ER-1 … ER-6

Each rule carries its own confidence. Apply in order; first match wins.

| Rule | Match | Confidence |
| --- | --- | --- |
| ER-1 | Exact five-part FQN | 1.00 |
| ER-2 | Case- and separator-insensitive FQN | 0.95 |
| ER-3 | Reporting view walked upstream to its source table via catalog lineage | 0.90 |
| ER-4 | Table matches, column name similarity ≥ 0.92 (Jaro-Winkler) | 0.80 |
| ER-5 | Schema matches, table and column inferred from the query subject | 0.70 |
| ER-6 | Embedding similarity ≥ 0.85 over label and column text | 0.60 |

`ER_PROBABLE_THRESHOLD = 0.80`: an edge below this is *probable* only and counts
against feasibility. ER-3 walks upstream to the system of record but **never
into a staging table** — a load step is not upstream of the reporting database.

Anything unresolved is quarantined with a reason code:
`NO_CATALOG_TABLE`, `NO_CATALOG_COLUMN`, `MISSING_REFERENCE`, `LOW_CONFIDENCE`,
`MODEL_TERMINUS` (a Power BI import model breaks lineage at the M query).

Quarantined rows are counted in the same unit as resolved edges — one row of the
lineage extract — so gate G2 is comparable with the run-level resolution rate.

### 5.3 Grain inference (charter §4.3)

Infer the conformed backbone from the catalog rather than assuming one: rank
candidate entities by how many tables carry a primary or foreign key to them.
An industry accelerator (§13.3) supplies a backbone where the estate is silent.

Two exclusions matter:

- `BACKBONE_EXCLUDED_TOKENS` — a date dimension is joined by everything and
  identifies nothing, so `date`, `time`, `calendar`, `period`, `day`, `month`,
  `quarter`, `year`, `week`, `fiscal`, `datetime`, `clock` can never be the
  coarsest business entity.
- `NON_BUSINESS_GRAINS` — `Staging`, `Batch`, `Load`, `Stage`, `Landing`,
  `Work` describe a load step, not a business entity. A KPI never inherits one,
  and a table carrying one is reported as ungrained.

`GRAIN_FINENESS` orders the backbone so a grain split can tell coarse from fine.

### 5.4 Stewardship resolution (charter §5.1 step 6)

Ownership and stewardship are different jobs. Resolve in a configured order,
each step carrying its own confidence:

| Source | Confidence |
| --- | --- |
| glossary term steward | 1.00 |
| table steward | 0.85 |
| domain steward | 0.70 |
| domain owner (escalation) | 0.55 |
| report owner (suggestion, not a steward) | 0.30 |

A report owner is **never promoted to steward**, only recorded as a suggestion
with its own source label. The order is configuration, not a chain of `or`
expressions, so it does not depend on which extract was read first.

---

## 6. Canonicalization (charter §5)

### 6.1 Expression parsing

Parse every Cognos report expression and DAX measure to an AST. Normalise:

- **Formatting functions are stripped** — `round`, `cast`, `to_char`, `format`,
  `currency`, `tonumber`, `convert`, `trim`, `value` change how a number is
  displayed, not what it is.
- **`int()` is NOT stripped.** `int([days]/30)` truncates, so a bucketed figure
  is not the raw ratio and must not fingerprint alike.
- **Cross-tool function names normalise** — `total(...)` and `SUM(...)` reduce
  to one token, so the same calculation fingerprints alike in Cognos and
  Power BI. This is what makes cross-tool duplication detectable.
- **Time intelligence maps to a modifier** — `sameperiodlastyear` → `PY`,
  `datesytd` → `YTD`, `dateadd` → `SHIFT`, and so on.
- **CALCULATE filters go to `filter_fp`; Cognos `for` clauses do not.** A
  `for [fiscal_period]` clause changes the operand set and therefore the metric;
  a CALCULATE filter shapes a variant.

An expression the parser cannot reduce is marked `opaque` with a parse note. It
is not dropped, and it lowers calculation determinism.

### 6.2 Fingerprinting

```
fingerprint = SHA-256(aggregation + sorted operand columns + arithmetic shape)
```

**Literals stay in the shape.** `> 60` and `> 59` must not collapse into one
metric — that is precisely what makes a planted threshold drift detectable as a
conflict rather than invisible as a merge. Commutative operators (`+`, `*`) sort
their operands so `a+b` and `b+a` agree.

`filter_fp` is a separate fingerprint over the filter expression.

### 6.3 Match tiers (charter §5.2)

| Tier | Condition | Result |
| --- | --- | --- |
| Identical | Same fingerprint, same filter_fp | Merge into one canonical metric |
| Variant | Same fingerprint, different filter_fp | One metric, recorded variants |
| Nominal conflict | Similar label, different fingerprint | **A conflict for a steward** |
| Structural cousin | Same operands, different aggregation | Linked, not merged |
| Opaque | Unparseable | Own metric, flagged |

### 6.4 The conflict register (charter §5.3, §5.4)

Candidate generation for nominal conflicts uses three blocking keys, because a
12-character label prefix misses `DSO` against `Days Sales Outstanding`:

`CONFLICT_BLOCKING_KEYS = ("label-token", "operand-set", "glossary-term")`

Every pair the blocks produce is scored. `NOMINAL_CONFLICT_LABEL_SIMILARITY = 0.90`.
Each conflict row carries `conflict_id`, `label`, both metric ids, both usage
weights, `difference_summary`, `pattern` (`DENOMINATOR | THRESHOLD | TIME_BASIS`),
`resolution_status`, both report lists, both expressions, `steward_id`, and the
Stage 6 decision the pattern implies.

---

## 7. Clustering (charter §6)

1. **Bipartite build** — metrics against the source tables they read.
2. **Projection** — a metric–metric graph weighted by shared source tables, with
   `same_domain_bonus = 0.20` and `min_similarity = 0.25`.
3. **Louvain** at `resolution = 1.0`, swept over `(0.7, 1.0, 1.3)`, seeded at
   `random_seed = 17` so the result is reproducible.
4. **Grain split** — a community mixing grains is split, because a product has
   one grain.
5. **Size floor** — `min_metrics = 3`, `min_business_units = 2`.
6. **Entity master extraction** — a hub table read by at least
   `entity_master_min_communities = 3` communities becomes its own candidate.
7. **Consumer-aligned composites** — `composite_min_candidates = 2`,
   `composite_min_users = 3`.

Deliberately not candidates: unresolved metrics (they become data gaps) and
sunset-only sources (gate G4).

---

## 8. Classification and scoring (charter §7, §8)

### 8.1 Archetypes

Evaluated **in the documented order; first match wins**. The ranking by score
only decides the runner-up, and therefore the confidence.

| Archetype | Match threshold |
| --- | --- |
| Entity Master | 0.55 |
| Reference Data | 0.60 |
| Event Stream | 0.60 |
| Metric / KPI | 0.55 |
| Feature Store | 0.45 |
| Insight / Recommendation | 0.45 |

**Confidence is the bare margin over the runner-up**, clamped to `[0, 1]`. Do
not blend the winner's own score in: a candidate scoring 0.9 on two archetypes
is the least certain case there is, and blending makes it look confident. Below
0.6, show both readings. Expect most candidates to sit below 0.6 — the rules
genuinely overlap, and a reviewer should see that.

A candidate the clusterer built by hub extraction (`origin == "entity_master"`)
is classified by construction at confidence 0.9, and shows no runner-up.

### 8.2 Dimensions and features

```
composite = 0.35·demand + 0.30·consolidation + 0.25·feasibility − 0.10·risk
```

| Dimension | Feature | Weight | Normalisation |
| --- | --- | --- | --- |
| demand | usage_weight | 0.50 | run-relative |
| demand | consumer_breadth | 0.30 | run-relative |
| demand | cadence | 0.20 | absolute |
| consolidation | reports_retirable | 0.50 | run-relative |
| consolidation | variants_collapsed | 0.30 | run-relative |
| consolidation | conflicts_surfaced | 0.20 | run-relative |
| feasibility | lineage_completeness | 0.35 | absolute |
| feasibility | definition_coverage | 0.25 | absolute |
| feasibility | source_health | 0.25 | absolute |
| feasibility | calculation_determinism | 0.15 | absolute |
| risk | sensitivity | 0.40 | absolute |
| risk | grain_ambiguity | 0.30 | absolute |
| risk | conflict_load | 0.30 | absolute |

Run-relative features use a log ratio against the run maximum, and record that
maximum and the candidate that set it on the feature.

**A conflict is a benefit or a cost, never both.** A conflict with both metrics
inside the candidate is settled by building it, and counts toward
`conflicts_surfaced`. One reaching outside is a steward adjudication the product
cannot avoid, and counts toward `conflict_load`. Using one count for both asks a
reviewer to read the same signal two ways.

**Usage weighting.** `USAGE_WINDOW_MONTHS = 12`,
`RECENCY_HALF_LIFE_MONTHS = 6.0`, and usage is normalised to a percentile
*within each tool* before tools are combined, because a Cognos run count and a
Power BI view count do not mean the same thing.
`DECISION_CRITICAL_USAGE_FLOOR = 0.60` floors a report a reviewer marked
decision-critical.

`DISPOSITION_WEIGHT = {retire: 1.0, merge: 0.8, keep: 0.5, migrate: 0.3}`.
`SENSITIVITY_RANK = {public: 0.0, internal: 0.35, confidential: 0.7, restricted: 1.0}`.

**The null rule:** every feature is a number. A missing input lowers the score;
it never raises it and never crashes.

### 8.3 Hard gates (charter §8.3)

No weight can override a gate.

| Gate | Test | Effect |
| --- | --- | --- |
| **G1** | At least 2 business units with ≥ 2 users **and** a recorded consumer confirmation | Exploratory |
| **G2** | Lineage completeness ≥ 0.60 | Exploratory |
| **G3** | Grain ambiguity ≤ 0.30 | Exploratory |
| **G4** | No sunset source without a mapped successor | **Blocked** |

G1 has two halves and only one is computable. The engine can see that two
business units read these metrics; it cannot know which decision the data
blocks, how fresh it must be, or what happens without it. Until a human records
those, the candidate stays `Exploratory`.

### 8.4 Explainability

Every feature writes `EvidenceRow`s naming the graph objects behind it, with a
`detail` sentence. A feature with no contributing rows writes a *negative*
evidence row explaining why it scored zero. The store refuses a score without
evidence.

---

## 9. Storage (charter §11)

SQLite, 45 tables, created at open. `isolation_level=None` with explicit
`BEGIN IMMEDIATE … COMMIT/ROLLBACK`, WAL, `busy_timeout=30000`,
`foreign_keys=ON`, one writer at a time behind a re-entrant lock, and nested
`transaction()` calls joining the outer transaction.

**Never call a module's `ensure_schema` inside a transaction** — those commit,
which ends the enclosing transaction early. The store creates every module's
tables at open instead.

Table families:

- **RUN** — `RUN`, `SCHEMA_VERSION`, `RUN_DELTA`, `RUN_BENCHMARK`.
- **GRAPH** — `GRAPH_NODE_REPORT`, `GRAPH_NODE_KPI`, `GRAPH_NODE_COLUMN`,
  `GRAPH_NODE_TABLE`, `GRAPH_EDGE_KPI_COLUMN`, `GRAPH_ER_QUARANTINE`.
- **CANONICAL** — `KPI_CANONICAL`, `KPI_VARIANT`, `KPI_CONFLICT`,
  `KPI_CANONICAL_HISTORY`.
- **CANDIDATE** — `DP_CANDIDATE` and its satellites for metrics, sources,
  consumers, reports, score, evidence, narrative, critique, effort, value,
  dependency, rank range, plus `DP_CANDIDATE_STATUS_HISTORY` and
  `DP_CANDIDATE_PAYLOAD_HISTORY`.
- **GOVERNANCE (append-only)** — `REVIEW_DECISION`, `GATE_WAIVER`,
  `CONFLICT_DECISION`, `METRIC_NAME_DECISION`, `REPORT_OVERRIDE`,
  `CANDIDATE_CONSUMER_CONFIRMATION`, `BENEFIT_PLAN`, `BENEFIT_ACTUAL`,
  `SCORE_WEIGHT`, `WEIGHT_APPROVAL`, `CONFIG_CHANGE`, `GOVERNANCE_SEED`.
- **PROGRAMME** — `DP_WAVE`, `DP_WAVE_UNSCHEDULED`, `RAID`, `VALUE_ASSUMPTION`.

Every append-only table carries `TRG_<TABLE>_NO_UPDATE` and
`TRG_<TABLE>_NO_DELETE` triggers that `RAISE(ABORT, …)`.

**`persist_run`** writes weights, the run (published = 0), the graph, the
canonicalization, the candidates, and publishes last, all in one transaction. A
run with no candidates is never published.

**Weight immutability.** `save_weights` is a no-op for an identical vector and
raises `WeightVersionError` for a different vector under an existing version.
Approval is a separate ledger row.

---

## 10. Governance

### 10.1 Decision vocabulary

`DECISIONS = (Accept, AcceptWithException, Reject, Merge, Split, Defer,
Override, Reverse, CarryForward)`.

A reason code is **required** for every decision except `Accept`. Each decision
has a closed list of codes, and the code `other` additionally requires a note.
Examples: `Reject` takes `no_named_consumer`, `duplicate_of_existing`,
`too_small`, `wrong_boundary`, `source_not_viable`, `already_planned`, `other`.
`Accept` takes `value_clear`, `retires_reports`, `resolves_conflicts`,
`strategic`, `accept_with_open_dependencies`, `other`.

### 10.2 Status machine

Statuses: `Proposed`, `Exploratory`, `Blocked`, `Accepted`, `Rejected`,
`Merged`, `Deferred`. Every transition is enumerated; anything else raises
`TransitionError`. Highlights:

- `Proposed | Exploratory | Deferred` + `Accept` → `Accepted`
- `Exploratory | Deferred` + `AcceptWithException` → `Accepted`
- `Blocked` + `Accept` → **refused**
- `Split` and `Override` keep the status they started from
- `Accepted` + `Reverse` → `Rejected`; `Rejected | Merged | Deferred` +
  `Reverse` → `Proposed`. A reversal needs a *different* actor from the one who
  made the decision being reversed.

### 10.3 Acceptance checks

`record_decision` is the only path past `Proposed`. Before applying `Accept` it:
validates the reviewer, the decision and the reason code; checks the gates (G-9);
checks the transition; and, for a composite, refuses when a part the board has
already parked (`Exploratory`, `Blocked`, `Deferred`, `Rejected`) sits beneath
it — unless the reason code is `accept_with_open_dependencies`. A `Proposed`
part is *not* a blocker: it is in the same review pass.

It then appends a hash-chained row, writes status history, writes a
`GATE_WAIVER` on `AcceptWithException`, and writes a `BENEFIT_PLAN` on `Accept`.

### 10.4 The hash chain

Each `REVIEW_DECISION` row hashes a fixed field list plus the previous row's
hash. `verify_audit_chain(run_id=None)` recomputes every hash and link and
returns `{ok, rows, first_break, orphans}`, where orphans are candidates past
`Proposed` with no decision behind them.

### 10.5 Lineage identity and carry-forward

```
lineage_id = SHA-256(sorted metric fingerprints + grain)
```

Run-independent. `seed_from_ledgers(store, run_id)` re-applies prior steward
decisions to a new run before scoring. `carry_forward(store, run_id, previous)`
carries statuses across, writing decision rows with `actor_role='carry_forward'`
so the audit chain has something behind each status — and so throughput
reporting can exclude them, because nobody decided anything this period.

If a lineage's fingerprints have changed since the decision, the seeding
re-opens it as "definition changed" rather than silently carrying a decision
made about different maths.

---

## 11. The conversational surface (charter §10.3)

A semantic view over the engine's own governed tables, plus **14 named queries**:
`candidate_ranking`, `retirement_ranking`, `conflicts_by_label`,
`candidate_detail`, `candidate_blockers`, `candidate_consumers`,
`candidate_metrics`, `candidate_sensitive_attributes`, `candidate_sources`,
`evidence_for_feature`, `metric_search`, `gap_list`, `blocked_candidates`,
`zombie_reports`.

The agent classifies intent, picks one named query, binds parameters, and
answers with citations. It never composes SQL.

`FORBIDDEN_OBJECTS` names everything out of reach: `RAW_*`, every governance
ledger, every history table, and the engagement and register tables. A test
asserts the list, and that each forbidden object is genuinely refused.

---

## 12. Synthetic data (charter §17)

One parameterised generator, one `IndustryPack` per industry. Nine industries:
`generic`, `utility`, `banking`, `insurance`, `retail`, `healthcare`,
`manufacturing`, `telecom`, `public_sector`. Only the content differs; the
generation parameters are shared.

Output is one workbook per industry with the tabs of §4.1 plus `README` and
`Planted_Defects`. **The workbook is the ingestion contract** — a test asserts
that the generated tabs bind to the schemas with nothing missing.

### 12.1 The fifteen planted defect classes

`IDENTICAL_KPI`, `THRESHOLD_DRIFT`, `EXCLUSION_DRIFT`, `DENOMINATOR_SWAP`,
`TIME_BASIS_DRIFT`, `CROSS_TOOL_DUPLICATION`, `GRAIN_MIXING`, `BROKEN_LINEAGE`,
`MISSING_DEFINITION`, `UNASSIGNED_STEWARD`, `SUNSET_SOURCE`,
`OPAQUE_EXPRESSION`, `ZOMBIE_REPORT`, `REGULATORY_LOW_USAGE`,
`STRUCTURAL_COUSIN`.

Each planted defect records its class, how it was planted, the expected
detection, and the objects it touched. This is what makes §14.2 possible.

Fixed seed, `synthetic` flag on every row, and a `generation_id` stamped
throughout, so a synthetic run can never be mistaken for a client finding.

---

## 13. Beyond the charter

The charter describes an engine. These exist because a backlog is not a
deliverable and a ranking nobody can audit is not advice.

### 13.1 The value model

Benefit in money, from named components: maintenance hours saved per retired
report (banded by complexity at `loaded_hourly_rate = 95.0`), licence cost per
report per year (`cognos: 1200`, `powerbi: 420`), infrastructure per package
(`15000`), reconciliation hours per conflict per period (`6.0` × `12`), and
mis-decision cost banded `{regulatory: 50000, financial: 20000, operational: 5000}`.

Build cost from effort points at `1800.0` per point. `discount_rate = 0.08`,
`horizon_years = 3`, `ramp_year1_share = 0.5`.

**Attribution is the point.** A report retired by two candidates is a saving
once. The portfolio figure attributes each report and each conflict once across
the estate, and the gross claim is shown beside it. Every figure names its
assumption version (`va-1.0-illustrative`), and the register is versioned and
approvable.

### 13.2 The programme layer

**Effort.** Eleven named drivers with rates: `metric_count` 1.0,
`source_systems` 4.0, `open_conflicts` 1.5, `pii_attributes` 1.0,
`attributes_without_definition` 0.5, `quarantined_lineage_rows` 0.5,
`opaque_metrics` 2.0, `cross_tool_spread` 6.0, `report_complexity` 10.0,
`grain_ambiguity` 12.0, `readiness_gap` 8.0. Points band into
`XS ≤ 12 < S ≤ 24 < M ≤ 45 < L ≤ 75 < XL`, and each size maps to weeks across
the six DPF stages.

**Dependencies** are tracked rows, not hidden fields: candidate, entity master,
successor system, steward adjudication.

**Waves** — `products_per_wave = 3`, `wave_length_weeks = 12`,
`max_points_per_wave = 120.0`, `max_waves = 12`. An entity master must land in a
strictly earlier wave than anything reading its hub table.

**RAID** — risks, assumptions, issues and dependencies per run, each with an
owner role, a severity, a due hint and the evidence behind it.

**Status report** — the charter's §14.2 measures with actual, target and RAG,
decision throughput excluding carry-forwards, the Blocked and Deferred queues
with reasons, acceptance rate by domain and reviewer, time to first decision,
backlog ageing, and phase exit criteria.

### 13.3 Assessment

**Input data quality** — one row per (input, dimension, rule) across
uniqueness, validity, consistency, completeness, timeliness and accuracy, with
rows checked, rows failed, sample ids, threshold and result.

**Measured detection** — compare planted against detected per defect class, with
the misses named. Each class is matched **in its own id space**: report ids for a
duplicated KPI, lineage row ids for broken lineage, a system name for a sunset
source, glossary terms for an unassigned steward. A set built in the wrong space
intersects with nothing and reports a miss that never happened. A class with no
detection rule is reported as *not measurable*, never scored zero. Expect 82–96%
recall across the nine packs.

**Remediation** — gaps grouped into units one person fixes in one action, ranked
by the report runs behind them, with an owner role, an action and a run-over-run
status. Undefined columns group **by table**, not by column: one ticket per
column is five hundred tickets nobody opens.

**Stewardship** — a register of questions, not assignments. Each row carries the
suggestion, its source, its confidence and the sentence to put to a domain
owner.

**Bias** — seven entries, each naming the mechanism, the direction, who it
disadvantages, and the mitigation *that exists in the code*, with an explicit
admission where none does. Cadence blindness, incumbency in demand, catalog
coverage as a feasibility proxy, parseability as a determinism proxy, partial
tool parity, consumer breadth favouring large units, and offline similarity
understating renamed labels.

**Replay** — configuration snapshot and hash, file digests, per-input extract
dates, and a three-state freshness gate (warn > 30 days, fail > 90 days) where
the oldest input decides and the spread between inputs is reported.

### 13.4 Engagement, registers, accelerators

**Engagement** — `ENGAGEMENT`, `ENGAGEMENT_REVIEWER`, `RUN_ENGAGEMENT`. A run
attaches to exactly one engagement, once, and is never moved. Scope drift
between the agreed domains and cut date and what actually arrived is reported as
issues in the validator's shape. A domain is only called absent when *nothing*
in the extract names it — not the columns, the glossary, or a report's business
unit — because regulatory reporting owns no tables in most estates.

**Registers** — 93 assumptions each naming the file that sets it and the open
decision that would change it; the eight open decisions D-01…D-08 with the
position the engine takes meanwhile; a 24-control matrix where every control is
verified against the code; a RACI; DMBOK, DCAM and COBIT mappings; and a
traceability matrix covering exit criteria *and* falsifiers.

**Accelerators** — per industry: a conformed backbone, a KPI dictionary with the
conflicts each KPI typically produces, a starter glossary, personas, steward
roles and regulatory report-name patterns. Starter terms merge only where the
client's glossary is silent, carry status `Starter`, and carry **no steward**,
because naming one would be a decision nobody took. Unit matching ignores
punctuation and organisational filler, so "credit and collections team" finds
the persona filed under "Credit & Collections", while requiring one name's
meaningful words to contain the other's so "Credit Risk" does not.

### 13.5 Exports

One run produces: an executive summary in Markdown and HTML, a twelve-tab
backlog workbook, and a printable dossier per candidate. Cut in the same process
as the run so the cover and the store cannot disagree. A synthetic run is
banner-marked on **every page**.

---

## 14. Surfaces

### 14.1 The pipeline — nine agents and one human gate

```
extracts ─▶ Ingestor ─▶ Resolver ─▶ Canonicalizer ─▶ Clusterer ─▶ Scorer ─▶ Narrator ─▶ Critic
                                                                                          │
                                       human gate ◀── Assessor ◀── Programme ◀────────────┘
```

Each agent writes only its own output tables, stamps the run id, and logs what it
wrote and how long it took. No agent can move a status past `Proposed`.

The **Narrator** drafts the purpose and one decision-register entry per business
unit, marked `AI_DRAFT`, leaving the blocked decision for a human. The **Critic**
lists what a reviewer will reject. **Programme** prices and sequences.
**Assessor** measures the run.

Order matters: Programme runs **before** persistence so effort, value, wave and
rank range reach the candidate payload; the Assessor runs after, and its findings
become run warnings.

### 14.2 Run quality gates (charter §13.2)

Six gates decide whether a run publishes: `freshness`,
`ingest_reconciliation` (0.5% tolerance), `resolution_rate` (≥ 0.80),
`parse_rate` (≥ 0.70), `coverage_sanity` (top 20 cover ≥ 0.50), `stability`
(≥ 0.85 with Jaccard ≥ 0.60 against a **comparable** previous run).

"Comparable" is essential: comparing across two different estates is meaningless
and will fail spuriously. Match on mode and industry, and report "not assessed"
rather than failing when there is nothing to compare against.

### 14.3 HTTP API

59 routes under `/api/v1`, with `/api` kept as an alias for one release. An
OpenAPI 3.1 document is generated from the router table.

**Identity** resolves in one order: a trusted proxy header (honoured only when
the peer address is a configured trusted proxy), a bearer token (looked up by
digest so a wrong token costs the same as a right one), then — only on a
loopback bind — an `X-DPRE-Identity` development header. `startup_check` refuses
a non-loopback bind with no identity source.

**Roles map to actions:**

| Role | Actions |
| --- | --- |
| reviewer | read, review, download |
| steward | read, steward, download |
| council | read, approve_weights, waive, configure |
| catalog_admin | read, download |
| operator | read, run, administer |

`review`, `steward` and `download` are additionally domain-scoped. A council
member may not approve a weight version trained on their own decisions.

**Hardening:** Host validation, a required `X-DPRE-Request` header on every
state-changing request, content-type and content-length checks, upload size and
magic-byte validation, decompression bounds, pagination on list routes, and
security headers including `default-src 'self'` with no `unsafe-inline`.

**Nothing is served by caller-supplied path.** The database and uploads live
under `workspace/private` (0700). Uploads are referenced by opaque id, and
downloads are keyed by run, candidate and seed name. Uploads are deleted after
ingestion by default.

**Errors** are RFC 9457 problem documents with stable codes
(`DPRE-AUTH-001`, `DPRE-HTTP-002`, `DPRE-RUN-001`, …). A refusal on a loopback
instance says to sign in; it does not name a proxy the user does not have. A 5xx
carries only a correlation id.

### 14.4 Browser application

Vanilla JavaScript, no build step. Six views: Start, Backlog, Portfolio, Gaps,
Ask, Runs.

Non-obvious requirements, each learned the hard way:

- The Content-Security-Policy forbids inline styles, so **no `style` attribute
  may appear in markup**. Use classes, and write genuinely dynamic values
  through the CSSOM.
- `input.files` is a **live** FileList. Copy it to an array *before* clearing
  the input, or the copy is empty. Clear the input afterwards, or picking the
  same file twice fires no second change event.
- Send the **opaque upload id**, never a path — the server no longer serves
  paths.
- Do not let a readiness helper rewrite the status line in a `finally` block; it
  erases the outcome the run just wrote.
- A refusal belongs next to the control that caused it, with the button that
  fixes it, not in a banner at the top of the page.
- Bind **one** catalog, not both.

### 14.5 Command line

23 commands: `industries`, `generate`, `run`, `inspect`, `candidates`, `show`,
`review`, `reasons`, `feedback`, `ask`, `status`, `waves`, `raid`, `value`,
`assess`, `audit`, `weights`, `confirm`, `benefits`, `engagement`, `scope`,
`registers`, `serve`.

`run` takes `--pack` to cut the executive pack in the same process. Refusals the
charter makes on purpose print as a sentence and exit 1, never as a traceback.
Runs and the feedback loop score under the weight version the council approved,
not the code default.

---

## 15. The stdlib implementations

| Component | Approx. lines | Notes |
| --- | --- | --- |
| XLSX reader/writer | 390 | `zipfile` + `ElementTree`; shared strings, inline strings, date serials, a decompression bound |
| Expression/DAX parser | 790 | Tokeniser, recursive descent, normalisation, fingerprint shape |
| Louvain | 164 | Modularity gain, weighted, seeded |
| Text similarity | 176 | Jaro-Winkler, token keys, a deterministic hash-blend stand-in for embeddings |
| YAML emitter | 75 | Seeds only; no parser needed |
| HTTP server | — | `ThreadingHTTPServer` plus a route table |
| Multipart parser | 92 | Boundary splitting, headers, files |
| Logistic regression | in feedback | L2-regularised, Newton steps, leave-one-out below 200 rows |

---

## 16. Build order

Each package is independently testable. Do not start a package before its
dependencies pass.

| # | Package | Depends on | Done when |
| --- | --- | --- | --- |
| 1 | Utilities: text, xlsx, tabular, ids, json, yaml | — | A workbook round-trips; Jaro-Winkler matches known pairs |
| 2 | Domain model and configuration | 1 | Dataclasses and every constant in this document |
| 3 | Synthetic generator, nine industries | 1, 2 | The workbook binds to the contracts with nothing missing; defects are recorded |
| 4 | Ingestion: contracts, detection, adapters, validation | 1–3 | Both entry paths produce identical bundle shapes |
| 5 | Graph: nodes, ER-1…ER-6, grain, stewardship | 4 | Resolution rate ≥ 0.80 on every pack; quarantine carries reason codes |
| 6 | Canonicalization: parse, fingerprint, group, conflicts | 5 | Cognos and DAX fingerprint alike; `> 60` and `> 59` do not merge |
| 7 | Clustering: bipartite, Louvain, grain split, composites | 6 | Candidates are reproducible under a fixed seed |
| 8 | Classification and scoring, gates, evidence | 7 | No score without evidence; gates cap status |
| 9 | Narrator, Critic, DPF seeds | 8 | Everything drafted is marked `AI_DRAFT` |
| 10 | Store, review workflow, feedback loop, portfolio views | 8 | Propose-only and evidence guards raise; the chain verifies |
| 11 | Governance: ledgers, transitions, identity, seeding, audit | 10 | A tampered row is detected; decisions survive a re-run |
| 12 | Value and programme: effort, waves, RAID, status | 10, 11 | Benefit is attributed once; waves respect dependencies |
| 13 | Quality: DQ, detection, remediation, stewardship, bias, replay | 12 | Detection recall ≥ 0.80 with misses named |
| 14 | Chat over the semantic view | 10 | Every forbidden object is refused |
| 15 | HTTP API, identity, hardening, OpenAPI | 10–14 | A non-loopback bind with no identity source refuses to start |
| 16 | Browser application and CLI | 15 | Both entry paths work end to end in a real browser |
| 17 | Exports, engagement, registers, accelerators | 12–16 | Every control in the matrix is verified against the code |

---

## 17. Acceptance criteria

The build is complete when all of these hold. Each should be a test.

1. A run is reproducible from its stored inputs and configuration.
2. No path but a recorded review decision moves a candidate past `Proposed`.
3. No score can be written without evidence rows, per feature.
4. The conversational agent cannot reach outside its semantic view.
5. AI-drafted names are marked everywhere they appear.
6. A seeded decision register needs only the blocked decision from a human.
7. Every planted defect class is detected, and detection recall is reported per
   class with the misses named.
8. A `Blocked` candidate is refused at acceptance, naming the gate.
9. An altered decision row breaks the hash chain and the audit names it.
10. A reviewer's confirmation survives into the next run on its lineage id.
11. Every control in the matrix is present in the code.
12. Both entry paths complete in a real browser, with no console output.
13. A client-shaped workbook — correct, but not shaped like the generator's —
    ingests and produces candidates.
14. The nine industry packs all run with every quality gate passing.

---

## 18. Deliberate limitations

State these plainly; they are design decisions, not gaps.

- **Usage is a demand proxy, not value.** A candidate stays `Exploratory` until
  a human confirms a named consumer.
- **The engine does not design the product.** It proposes grain, attributes and
  metrics; the DPF stages own the design.
- **Conflicting definitions are surfaced, never auto-resolved.**
- **Not text-to-SQL.**
- **Language models only name and describe.** Equivalence is decided
  deterministically. With no model configured the engine uses templates, so runs
  stay reproducible offline. The seam is one function.
- **Every money figure is illustrative** until the client's own rates replace
  the shipped assumptions.
- **The ranking has known blind spots**, and they are written down.
- **This is not the target runtime.** The charter describes Snowflake with
  Cortex; the path is four named seams — storage dialect, XLSX library,
  `EMBED_TEXT_768` behind the similarity function, and `AI_COMPLETE` behind the
  completion seam — plus grants that re-establish the append-only trail.

---

## 19. Reference

| Document | What it holds |
| --- | --- |
| [`docs/specification.md`](docs/specification.md) | The original commissioning charter. Section numbers are cited throughout and by tests. |
| [`docs/implementation-map.md`](docs/implementation-map.md) | Charter section → code, plus the judgement calls made where the charter was silent |
| [`docs/api.md`](docs/api.md) | Route reference and error codes |
| [`docs/security.md`](docs/security.md) | Threat model and hardening posture |
| [`docs/deployment.md`](docs/deployment.md) | Settings, logging, retention |
| [`docs/assumptions-register.md`](docs/assumptions-register.md) | Every contestable figure |
| [`docs/decision-register.md`](docs/decision-register.md) | D-01…D-08 |
| [`docs/controls-matrix.md`](docs/controls-matrix.md) | 24 controls with framework mappings |
| [`docs/traceability.md`](docs/traceability.md) | Exit criteria and falsifiers |
| [`docs/engagement-model.md`](docs/engagement-model.md) | Engagement, roster, scope drift |
| [`docs/accelerators.md`](docs/accelerators.md) | Per-industry curated content |
| [`docs/extract-request-pack.md`](docs/extract-request-pack.md) | What to ask the client's teams for |
| [`docs/demo-script.md`](docs/demo-script.md) | Forty minutes, no slides |
| [`docs/diagnostic-runbook.md`](docs/diagnostic-runbook.md) | Day one with real extracts |
| [`docs/snowflake-cortex-migration.md`](docs/snowflake-cortex-migration.md) | Each seam and what it costs to cross |
