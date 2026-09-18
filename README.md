# Data Product Recommendation Engine

Converts report-level KPI lineage and catalog metadata into a ranked,
evidence-backed backlog of data product candidates. Each candidate is named by
the decision it serves, the reports it would retire and the metric conflicts it
resolves. **Humans approve; the engine only proposes.**

It implements the specification in [`docs/specification.md`](docs/specification.md),
and it runs two ways:

| Path | What you supply | What happens |
| --- | --- | --- |
| **Manual** | Your own extracts from Cognos, Power BI, Collibra or Alation | Files are matched to the ingestion contract, columns auto-mapped (you correct anything it guessed wrong), then the pipeline runs |
| **Automated** | Nothing but an industry | A synthetic pack is generated for that industry — carrying the duplication, conflicting definitions, broken lineage and missing stewards the engine is built to find — and the same pipeline runs against it |

Both paths produce the same `ExtractBundle`, so nothing downstream knows which
one was used. Nine industries ship: generic, utility/energy, banking, insurance,
retail/CPG, healthcare, manufacturing, telecom and public sector.

## Quick start

No dependencies beyond the Python standard library (3.11+).

```bash
# 1. See what the engine finds in a synthetic utility estate
python3 -m dpre run automated --industry utility

# 2. Open the application: Manual on the left, Automated on the right
python3 -m dpre serve                 # http://127.0.0.1:8000

# 3. Or run against your own extracts
python3 -m dpre inspect my_cognos_lineage.csv
python3 -m dpre run manual --input my_rationalization.csv:cognos_rationalization \
                           --input my_cognos_lineage.csv:cognos_kpi_lineage \
                           --input my_collibra_export.csv:collibra_metadata
```

A full-estate run — ~240 reports, ~580 lineage rows, ~660 catalog columns —
takes about a third of a second, because clustering operates on canonical
metrics (hundreds), not on reports (tens of thousands).

## What it does, in order

```
extracts ──▶ Ingestor ──▶ Resolver ──▶ Canonicalizer ──▶ Clusterer ──▶ Scorer ──▶ Narrator ──▶ Critic
                                                                                            │
                                    human gate ◀── Assessor ◀── Programme ◀──────────────────┘
```

1. **Ingestor** lands the extracts, validates required fields, reconciles row
   counts and refuses to mix synthetic rows with real ones.
2. **Resolver** builds the knowledge graph (KPI → Report → Package → Table →
   Column → Business Term) and runs entity resolution rules ER-1 to ER-6, each
   with its own confidence. Anything that will not resolve is quarantined with a
   reason code and counted against feasibility.
3. **Canonicalizer** parses every Cognos expression and DAX measure to an AST,
   normalizes it, and fingerprints it as
   `SHA-256(aggregation + sorted operand columns + arithmetic shape)`. Identical
   fingerprints merge; the same fingerprint with a different filter is a
   variant; a similar label with a different fingerprint is a **conflict** for a
   steward to adjudicate. Cognos and DAX fingerprint alike, so a KPI that exists
   in both tools becomes one metric spanning both.
4. **Clusterer** projects metrics onto a metric–metric graph weighted by shared
   source tables, runs Louvain under a grain constraint, splits communities that
   mix grains, extracts entity masters and seeds consumer-aligned composites.
5. **Scorer** computes demand, consolidation, feasibility and risk from named
   features, applies hard gates G1–G4, and records the graph rows behind every
   number. A score without evidence cannot be written.
6. **Narrator** drafts the purpose and one decision-register entry per business
   unit — marked `AI_DRAFT`, with the blocked decision left for a human.
7. **Critic** checks each candidate against the gates and the DPF Stage 1–2 exit
   criteria and lists what a reviewer will reject.
8. **Programme** prices and sequences what the others found: build effort with
   its drivers, benefit attributed once across the estate rather than claimed
   twice, payback and three-year net present value, delivery waves that respect
   dependencies, and a RAID log. A board cannot sequence work it cannot price.
9. **Assessor** measures the run rather than the estate. Data quality of the
   inputs by dimension, how much of what was planted the engine actually found,
   the remaining gaps as units with an owner, the metrics awaiting a steward,
   and what the ranking is blind to.

Then a human accepts, rejects, merges, splits or defers — and that decision is
the only thing that can move a candidate past `Proposed`.

## Guardrails, enforced by structure

| Guardrail | How it is enforced |
| --- | --- |
| Propose-only | `Store.save_candidates` raises `ProposeOnlyError` for any status past `Proposed`; only a `REVIEW_DECISION` row naming an authenticated reviewer moves it |
| Evidence required | A score row without an evidence row raises `EvidenceMissingError` and the run is not published |
| AI outputs marked | Every drafted name, purpose and decision carries `AI_DRAFT`; the catalog payload lists them under `import_blocked_by` until a steward accepts |
| No raw access from chat | The conversational surface picks a *named* query from a whitelist; it never composes SQL, and write verbs and non-whitelisted objects are refused |
| Catalog remains the record | The engine writes a proposal payload for a steward to import; it never writes to Collibra or Alation |
| Sensitivity carried, never dropped | Column sensitivity propagates into the risk score and onto the card; PII columns are listed |
| Replayability | Every run stores extract ids, file digests, the full clustering configuration and its hash, the weight version, the parser version and the generation id; the same inputs produce the same ranking |
| Gates bind at acceptance | A `Blocked` candidate cannot be Accepted at all; an `Exploratory` one needs a recorded consumer confirmation or an `AcceptWithException` naming the gate, the rationale and a second approver |
| Tamper-evident trail | Every decision row is hash-chained to the one before it and the ledgers refuse `UPDATE` and `DELETE`; `dpre audit` recomputes the chain and names the first row that does not verify |
| Identity is authenticated, never asserted | The reviewer is the principal the server resolved through single sign-on, a bearer token or a loopback development name — never a string in a request body |
| Decisions outlive the run | A confirmed consumer, an adjudicated conflict, an accepted metric name and a report marked decision-critical are keyed by lineage and re-applied to the next run |
| A benefit claimed once | A report retired by two candidates is a saving once; the portfolio figure is attributed across the estate, and the gross claim is shown beside it |

Run quality gates (extract freshness, ingest reconciliation, resolution rate
≥ 0.80, parse rate ≥ 0.70, top-20 coverage ≥ 50%, stability ≥ 85%) decide
whether a run publishes at all. A run below the resolution floor publishes only
the gap list.

## Using it

### The web application

`python3 -m dpre serve` then open `http://127.0.0.1:8000`.

- **Start** — choose Manual (drop files, correct the mapping) or Automated
  (pick an industry). Both end in a run summary with the quality gates and the
  agent log.
- **Backlog** — the ranked candidates, filterable; click one for the full card:
  score with every feature and its evidence rows, metrics, consumers, retirable
  reports, attributes with sensitivity, sources, the critic's findings, the
  drafted decision register, the downloadable seeds, and the review actions.
- **Portfolio** — the coverage curve, the retirement map and the conflict heat
  map, which is where conflicts are ranked by the usage at stake behind them.
- **Gaps** — unresolved lineage by reason code, columns with no business term,
  metrics with no steward.
- **Ask** — questions answered from the governed tables, with citations.
- **Runs** — whether this run can be trusted, first: the decision chain
  recomputed, the gate waivers in force, whether the weight vector has council
  approval, the quality of what the engine was fed, what it missed against what
  was planted, and what to fix before the next run. Then every run, its gates
  and the feedback loop.

Sign in before deciding anything. On a loopback instance a name is enough; a
deployed instance takes the identity from single sign-on and refuses to start
on a public address without one.

### The command line

```bash
python3 -m dpre industries                       # the nine industry packs
python3 -m dpre generate all --out data/synthetic  # one workbook per industry
python3 -m dpre run automated --industry banking --seeds out/seeds
python3 -m dpre run manual --input extract.xlsx  # binds every recognisable tab
python3 -m dpre inspect extract.xlsx             # what it detected, and the mapping
python3 -m dpre candidates --status Proposed
python3 -m dpre show CAND-XXXXXXXX
python3 -m dpre review CAND-XXXXXXXX Accept --reviewer "priya.silva" --reason retires_reports
python3 -m dpre ask "which candidates retire the most Finance reports"
python3 -m dpre feedback --approve "data product council"
```

The engine proposes a backlog; a programme is what a client buys. These answer
the questions asked between review sessions:

```bash
python3 -m dpre status                  # against the section 14.2 measures, with a RAG
python3 -m dpre waves --why             # what ships when, and why it sits there
python3 -m dpre value                   # benefit, build cost, payback, NPV
python3 -m dpre raid --severity high    # the risk log, or --csv for the steering pack
python3 -m dpre assess --bias           # inputs, detection, gaps, and the blind spots
python3 -m dpre audit                   # the decision chain and the open exceptions
python3 -m dpre benefits                # planned against realised, after acceptance
```

And these are the governance surfaces a client's audit function asks for:

```bash
python3 -m dpre reasons                             # the decision and reason-code vocabulary
python3 -m dpre weights                             # versions, and who approved which
python3 -m dpre weights --approve v1.1 --approver "cdo.office"
python3 -m dpre confirm CAND-XXXXXXXX --business-unit "Retail Credit Risk" \
    --blocked-decision "weekly provisioning sign-off" --latency "next business day" \
    --consequence "the provision is set on last week's exposures" --confirmed-by "maria.chen"
python3 -m dpre registers controls --detail          # 24 controls, each verified in code
python3 -m dpre registers decisions                  # D-01..D-08, and what the engine assumes
python3 -m dpre registers assumptions                # every contestable figure, and its file
python3 -m dpre registers traceability               # exit criteria and falsifiers
python3 -m dpre engagement create --client "Acme Utilities" --cut-date 2026-09-17 ...
python3 -m dpre scope --engagement ENG-XXXXXXXX --industry utility
```

A run can also produce the pack itself:

```bash
python3 -m dpre run automated --industry banking \
    --pack out/packs --client "Northwind Bank" --partner "A. Partner"
```

That writes an executive summary in Markdown and HTML, a backlog workbook and a
printable dossier per candidate, cut in the same process as the run so the cover
and the store cannot disagree. A synthetic run is banner-marked on every page.

### As a library

```python
from dpre.ingest import ingest_automated, ingest_manual, SourceSpec
from dpre.pipeline import run_pipeline
from dpre.store import Store

store = Store("data/engine.db")
result = run_pipeline(ingest_automated("healthcare"), store=store)

for candidate in result.ranked()[:5]:
    print(candidate.score.composite, candidate.proposed_name, candidate.status)
```

## Seeds for the Data Product Factory

Every candidate produces the artifacts that open a data product, pre-filled from
the evidence and explicit about what a human must still add:

| Stage | Artifact | Human adds |
| --- | --- | --- |
| 1 Consumption Discovery | `stage1-decision-register.yaml` | the blocked decision, latency tolerance, consequence |
| 2 Charter | `stage2-charter.yaml` | success measures, sign-off |
| 3 Source Discovery | `stage3-source-inventory.yaml` | profiling statistics |
| 5 Attribute Register | `stage5-attribute-register.xlsx` | allowed values, derivation review, sign-off |
| 6 Semantic Model | `stage6-semantic-model.yaml` | join validation, metric certification |
| 12 Operate | `stage12-retirement-list.csv` | notification and cut-over dates |
| — | `collibra-payload.json` / `alation-payload.json` | a steward imports it |

Conflicts become explicit modelling decisions in the Stage 6 seed: a drifting
threshold becomes a **parameter**, an exclusion becomes a **filter dimension**, a
time basis becomes a **declared time grain**.

## The synthetic pack

`python3 -m dpre generate all` writes one workbook per industry with the tabs
that double as the ingestion contract: `README`, `Cognos_Rationalization`,
`Cognos_KPI_Lineage`, `PowerBI_Inventory`, `PowerBI_Measure_Lineage`,
`Collibra_Metadata`, `Collibra_Lineage`, `Alation_Metadata`,
`Business_Glossary`, `Planted_Defects`.

Structure, volumes and defect rates are identical across industries — only the
source systems, domains, entity names, KPI names and glossary terms change — so
the engine carries no industry-specific logic. Every row is flagged
`synthetic = TRUE` and carries a generation id, and the `Planted_Defects` tab
records all fifteen defect classes so detection can be *measured*:

identical KPIs · threshold drift · exclusion drift · denominator swap ·
time-basis drift · cross-tool duplication · grain mixing · broken lineage ·
missing definitions · unassigned stewards · sunset source · opaque expressions ·
zombie reports · regulatory low-usage reports · structural cousins

## Layout

```
dpre/
  ingest/      adapters (cognos, powerbi, collibra, alation), contracts, validation
  graph/       node and edge construction, ER-1..ER-6, grain inference
  canonicalize/ expression and DAX parsing, fingerprints, grouping, conflicts
  cluster/     bipartite projection, Louvain, candidate generation
  score/       archetype and tier rules, features, gates, evidence
  narrate/     purpose and decision drafts, the critic, the LLM seam
  seeds/       DPF stage artifacts and the catalog payload
  review/      the human gate and the feedback loop
  portfolio/   coverage curve, retirement map, conflict heat map
  chat/        semantic view and the conversational agent
  synth/       the parameterized generator and the nine industry packs
  accelerators/ curated backbone, KPI dictionary, personas and starter glossary per industry
  governance/  the ledgers, the status machine, the hash chain, the audit report
  programme/   effort, dependencies, delivery waves, the RAID log, the status report
  value/       the benefit model and the assumption register behind every figure
  quality/     input data quality, measured detection, remediation, stewardship, bias
  engagement/  whose estate this is, the agreed scope, branding, the extract request pack
  registers/   assumptions, open decisions D-01..D-08, controls, RACI, traceability
  export/      the executive pack, the backlog workbook, the per-candidate dossiers
  server/      HTTP API, identity and request hardening, the browser application
  pipeline.py  the nine agents and the run quality gates
  store.py     the governed output tables
tests/         388 tests, including the section 14.1 acceptance criteria
```

## Tests

```bash
python3 -m pytest tests -q
```

The suite covers the acceptance criteria the specification sets for the engine:
a run is reproducible from its stored inputs; no path but a review decision
moves a candidate past `Proposed`; no score can be written without evidence; the
conversational agent cannot reach outside its semantic view; AI-drafted names
are marked everywhere they appear; a seeded decision register needs only the
blocked decision; and each planted defect class is actually detected.

It also covers the things that make the output defensible: a gated candidate is
refused at acceptance rather than at scoring; an altered decision row breaks the
hash chain and the audit names it; a reviewer's confirmation survives into the
next run on its lineage id; every one of the twenty-four controls in the matrix
is present in the code; and the detection scorecard reports recall per defect
class with the misses named, so a regression in a detection rule fails a test
rather than quietly shrinking a claim.

## Deliberate limitations

- **Usage is a demand proxy, not value.** A candidate stays Exploratory until a
  human confirms a named consumer, and a reviewer can mark a low-run,
  high-consequence report *decision-critical* to floor its weight.
- **The engine does not design the product.** It proposes grain, attributes and
  metrics; the DPF stages and their gates own the design.
- **Cognos and DAX expressions are not treated as truth.** Conflicting variants
  are surfaced for a steward, never auto-resolved.
- **Not text-to-SQL.** The conversational surface reads the engine's own output
  tables through a fixed set of named queries.
- **Language models only name and describe.** Equivalence is decided
  deterministically. With no model configured the engine uses templates, so runs
  stay reproducible offline; `dpre.narrate.ai.register_provider` swaps in Cortex
  `AI_COMPLETE` or any other completion function.
- **Every money figure is illustrative until the client's own rates replace
  them.** The value model ships mid-market assumptions, versioned, and the RAID
  log carries an entry saying so. Read
  [`docs/assumptions-register.md`](docs/assumptions-register.md) before quoting
  a number.
- **The ranking has known blind spots, and they are written down.**
  `dpre assess --bias` lists seven, with the mechanism, who it disadvantages and
  the mitigation in the code — including the one that has none.
- **This is not the target runtime.** It is standard-library Python so it runs
  anywhere; [`docs/snowflake-cortex-migration.md`](docs/snowflake-cortex-migration.md)
  names each seam, what it costs to cross, and the grants that re-establish the
  append-only trail on the platform.
