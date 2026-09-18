# Using the engine

## Manual mode: bringing your own extracts

The engine needs one thing it cannot run without: a **KPI lineage extract**, at
the grain of one row per KPI per referenced column. Everything else improves the
result and its absence is recorded as a gap rather than treated as a failure.

| Input | Required | Without it |
| --- | --- | --- |
| Cognos KPI lineage, or Power BI measure lineage | yes | the engine cannot build the graph |
| Cognos rationalization report, or Power BI inventory | strongly recommended | demand and retirement impact are scored from lineage alone and are weak |
| Collibra or Alation metadata | strongly recommended | every lineage row quarantines, so gate G2 caps every candidate at Exploratory |
| Catalog lineage | optional | lineage stops at the reporting layer; those sources score as non-system-of-record |
| Business glossary | optional | definition coverage falls and the gap list grows |

Accepted formats: `.csv`, `.tsv`, `.json`, `.jsonl`, `.xlsx`. A single workbook
with several tabs works — each tab is bound separately.

### The mapping step

You do not have to rename your columns. On upload, each table is matched to an
input contract and its columns auto-mapped by name, alias and shape; the wizard
shows what it guessed and its confidence, and every field is a dropdown you can
correct. Required fields are marked, and the run button stays disabled until the
lineage extract is bound.

From the command line:

```bash
python3 -m dpre inspect extract.xlsx          # what it detected and how it mapped
python3 -m dpre run manual --input extract.xlsx
python3 -m dpre run manual --input kpis.csv:cognos_kpi_lineage \
                           --input catalog.csv:collibra_metadata
```

### Reading the ingestion report

A failed run tells you which contract was unsatisfied and why. Common cases:

- `MISSING_REQUIRED_INPUT` — no KPI lineage was bound.
- `ORPHAN_KPI_REPORTS` — lineage references reports the inventory does not hold;
  usually two extracts taken at different times.
- `MISSING_COLUMN_REFERENCE` — lineage rows with no table or column; these
  quarantine as `MISSING_REFERENCE`.
- `MIXED_SYNTHETIC` — synthetic and real rows in one run, which is refused.

## Automated mode: the synthetic pack

Pick an industry and run. The pack carries the defects the engine exists to
find, and the `Planted_Defects` tab records every one, so you can check
detection rather than take it on trust:

```bash
python3 -m dpre generate utility --out data/synthetic
python3 -m dpre run automated --industry utility
```

Use it to evaluate the engine before your extracts exist, to demonstrate the
review workflow, or as a fixture. Reviewer feedback should change the generator
parameters, not the workbook, so the pack stays reproducible.

## Reading a candidate card

- **Composite** is `0.35·demand + 0.30·consolidation + 0.25·feasibility −
  0.10·risk`, on the weight version named on the card.
- **Gates** cap status; they never adjust the number. A failed G4 means Blocked;
  any other failure means Exploratory.
- **Every feature opens.** The Score tab lists each feature's raw value, its
  normalization, its weight and its contribution, and the evidence rows are the
  actual report, KPI, column and conflict ids behind it.
- **AI_DRAFT means unread.** Names, purposes and decision-register entries are
  drafts until a human accepts them, and they cannot reach the catalog first.

## Reviewing

A decision must name a reviewer — propose-only means acceptance is attributable.

| Decision | Effect |
| --- | --- |
| Accept | status Accepted; the seeds and catalog payload become loadable |
| Reject | status Rejected, with a reason code that trains the weights |
| Merge | metrics move into the target candidate; the source is Merged |
| Split | splits by grain or by consumer; children keep the parent link |
| Defer | status Deferred with a reason |
| Override | changes archetype, tier, name, grain, owner or steward, and logs the field |

Conflicts are adjudicated through the API or the store rather than the browser:
`POST /api/runs/{run_id}/conflicts/{conflict_id}/resolve` with a status and a
named steward, or `Store.resolve_conflict`. The register itself is unchanged —
it is written on every run, carried into the Stage 6 seed as parameters and open
decisions, and ranked by usage at stake on the Portfolio tab.

Two further actions matter:

- **Accept a metric name** — an AI-drafted name cannot reach the catalog until a
  steward accepts it, separately from accepting the candidate.
- **Mark a report decision-critical** — floors the usage weight of a low-run,
  high-consequence report on the next run. This is the answer to the engine's own
  blind spot: usage is a demand proxy, not value.

## The feedback loop

Every decision is a training signal. Monthly, three things are re-estimated:

1. **Score weights** — logistic regression of Accept against Reject on the four
   dimensions. Below 50 decisions the engine says so and proposes nothing.
   A proposal is not a version: a council member must approve it, and it applies
   to the *next* run — accepted candidates are never re-scored.
2. **Archetype thresholds** — a rule overridden more than 30% of the time is
   listed for rewriting, not re-weighting.
3. **Clustering resolution** — more splits than merges in a domain means the
   clusters are too coarse, and the proposed resolution rises.

## Asking questions

The conversational surface reads the engine's own output tables through a fixed
set of named queries. It cannot compose SQL, cannot write, and cannot reach the
raw extracts. Questions it answers today:

- which candidates retire the most \<domain or business unit\> reports
- show the competing definitions of \<metric\>
- what would block \<candidate\> at Stage 9
- who consumes \<candidate\>
- explain the demand score of \<candidate\>
- where is the lineage broken
- which reports have not run in a year
- what is blocked and why

Every answer cites the candidate, metric, conflict, report or column ids behind
it, and names the query it used.
