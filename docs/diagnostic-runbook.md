# Day-one diagnostic runbook

*Review finding R-55.*

What to do on the first day the client's extracts arrive, in order, and what
each step tells you. The whole sequence is under an hour on a normal estate.

## Before you run anything

Check the extracts against the contract, one file at a time:

```bash
python -m dpre inspect extracts/cognos_kpi_lineage.csv
```

`inspect` names the schema it detected, its confidence, and any required field
that is missing. A file that detects as `unrecognised` is a mapping problem, not
a data problem — the sheet headers do not match the contract in
`docs/usage.md`. Fix it here rather than in the run.

## The first run

```bash
python -m dpre --db client/engine.db run manual \
    --input extracts/cognos_rationalization.xlsx \
    --input extracts/cognos_kpi_lineage.csv \
    --input extracts/collibra_metadata.xlsx \
    --as-of 2026-09-17 --label "first cut"
```

Read the validation issues before the results. A run that proceeds under
`--force` is a run whose numbers carry an asterisk, and the manifest records
that it was forced.

## What to read, in order

**1. The quality gates.** They print with the run. A failed gate is not a
failed run, but it bounds what you may claim:

| Gate | What a failure means |
| --- | --- |
| Ingest reconciliation | The extracts disagree with each other about row counts |
| Resolution rate | Too much lineage could not be tied to a catalog object |
| Parse rate | Too many expressions were opaque to the parser |
| Coverage | The top candidates do not cover enough usage to be worth a programme |
| Stability | This run's candidates differ too much from the previous run's |

**2. The quarantine.** `dpre` prints the count; the Gaps tab and
`/api/v1/runs/{run}/gaps` list the rows with their reason codes.
`NO_CATALOG_TABLE` in volume means the catalog export is narrower than the
lineage export, which is the single most common day-one problem and is fixed by
asking for the missing schemas rather than by tuning anything.

**3. The conflicts.** Every competing definition is a steward conversation that
was going to happen anyway. Sort by usage at stake and take the top ten to the
first workshop.

**4. The agent log.** Compare the Resolver's resolution rate against the
benchmark bands via `/api/v1/runs/{run}/benchmark`. A rate far below the band
for this industry says something structural about the extracts, not about the
estate.

## Diagnosing the three common failures

**Everything is Exploratory.** Expected on a first run: gate G1 needs a named
consumer, which no machine can supply. Work the backlog with the client and
record confirmations. They carry forward.

**Resolution rate under the floor.** Look at the quarantine reason codes. If
`NO_CATALOG_TABLE` dominates, the catalog is missing schemas. If
`LOW_CONFIDENCE` dominates, naming conventions differ enough that rules ER-4 to
ER-6 are not firing, and a mapping file for the worst offenders is worth more
than a threshold change.

**The stability gate fails on the second run.** Check that both runs are the
same estate. The gate compares candidate sets, and two different clients share
no metrics at all, so it fires correctly and loudly. If it is the same estate,
`dpre status --previous <run>` shows what moved.

## Before anything leaves the building

```bash
python -m dpre --db client/engine.db audit
```

The audit must report an intact chain. It will also report that the weight
vector is pending council approval, which is true and stays true until a council
member approves a version with `dpre weights --approve`. Do not cut a pack that
claims a governed score under an unapproved vector without saying so.

Then cut the pack, and read the executive summary yourself before sending it.
