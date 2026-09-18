# From the stdlib implementation to Snowflake and Cortex

*Review finding R-33.*

This engine is pure Python 3.11 with no runtime dependencies. That is a
deliberate property — it runs on a laptop, in a locked-down client environment,
and in a container with no package index — and it is not the target runtime. The
specification describes Snowflake with Cortex. This document says exactly where
the two differ, what each seam costs to cross, and what does not move at all.

## What does not move

The guardrails are structural, not incidental, and they survive the migration
unchanged: the engine proposes and never decides; no score exists without
evidence rows behind it; drafted text is marked `AI_DRAFT` until a human accepts
it; a run is reproducible from its inputs and its recorded configuration; and no
free-form SQL reaches the conversational surface.

The decision ledger's append-only guarantee is enforced in SQLite by triggers
that refuse `UPDATE` and `DELETE`. In Snowflake the same guarantee is a grant
problem, covered under *Grants* below.

## The seams

There are four places where the offline implementation stands in for a platform
service. Each one is a named function, so crossing the seam is a substitution
rather than a rewrite.

### 1. Language model calls

`dpre/narrate/ai.py` is the whole seam. Offline it uses deterministic templates,
so a run needs no external service and produces the same bytes twice.

```python
from dpre.narrate.ai import register_provider

def cortex(system_prompt: str, user_prompt: str) -> str:
    ...  # SNOWFLAKE.CORTEX.AI_COMPLETE(model, prompt)

register_provider(cortex, model="claude-sonnet-4-5")
```

`register_provider(provider, model)` installs a completion function. Everything
else — the prompt versions, the `AI_DRAFT` marking, and the call ledger that
records purpose, model, prompt version, prompt and response digests, and the
reason a configured provider fell back to the template — already works and does
not change. The ledger is drained after the Narrator step and persisted to
`RUN_AI_CALL`, which is what lets a governance board answer what text left the
building and which model wrote it.

A model is never allowed to decide equivalence. It names and describes what the
deterministic steps already grouped. That restriction is the reason a Cortex
outage degrades the output's prose and not its findings.

### 2. Embeddings

`dpre/util/text.py::embedding_similarity` is a deterministic hash blend, not a
learned embedding. It stands in for `SNOWFLAKE.CORTEX.EMBED_TEXT_768` and its
cosine distance. Two callers use it: rule ER-6 in `dpre/graph/resolver.py`, the
last and weakest resolution rule, and nominal-conflict scoring in
`dpre/canonicalize/grouping.py`.

Both are thresholded by configuration — `ER6_EMBEDDING_SIMILARITY` and
`NOMINAL_CONFLICT_LABEL_SIMILARITY` — and both are recorded in the assumption
register as offline stand-ins. Replacing the function with a real
`EMBED_TEXT_768` call improves recall on renamed and abbreviated labels; it does
not change the shape of anything downstream, because the rule already carries a
per-rule confidence and every edge it produces is quarantined below the floor.

Expect the thresholds to need re-deriving against real embeddings. They were
calibrated against the hash blend and are wrong for a different similarity
function.

### 3. Storage

`dpre/store.py` is SQLite with explicit `BEGIN IMMEDIATE` transactions, WAL, and
one writer at a time behind a lock. The table shapes are already the
specification's `RECO.*` shapes, so the migration is a dialect exercise:

| SQLite | Snowflake |
| --- | --- |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `NUMBER AUTOINCREMENT` |
| `TEXT` holding JSON | `VARIANT`, or `TEXT` with `PARSE_JSON` on read |
| Append-only triggers | Grants plus Time Travel (below) |
| `BEGIN IMMEDIATE` | Default transaction semantics; drop the lock |

The hash chain over `REVIEW_DECISION` is computed in Python and verified by
recomputation, so it migrates unchanged and remains the primary tamper
detection. The triggers are a second line, not the first.

### 4. The XLSX reader and writer

`dpre/util/xlsx.py` reads and writes workbooks with `zipfile` and
`ElementTree` because a dependency was not available. In a platform deployment
this is the first thing to replace with a maintained library. Nothing else
depends on how those bytes are produced.

## Grants

The append-only property has to be re-established in the platform, because the
triggers do not travel. Grant `INSERT` and `SELECT` on the ledgers, and nothing
else:

```sql
GRANT USAGE ON DATABASE RECO TO ROLE DPRE_ENGINE;
GRANT USAGE ON SCHEMA RECO.PUBLIC TO ROLE DPRE_ENGINE;

-- The ledgers: append and read, never amend.
GRANT SELECT, INSERT ON TABLE RECO.REVIEW_DECISION TO ROLE DPRE_ENGINE;
GRANT SELECT, INSERT ON TABLE RECO.GATE_WAIVER TO ROLE DPRE_ENGINE;
GRANT SELECT, INSERT ON TABLE RECO.CONFLICT_DECISION TO ROLE DPRE_ENGINE;
GRANT SELECT, INSERT ON TABLE RECO.METRIC_NAME_DECISION TO ROLE DPRE_ENGINE;
GRANT SELECT, INSERT ON TABLE RECO.WEIGHT_APPROVAL TO ROLE DPRE_ENGINE;
GRANT SELECT, INSERT ON TABLE RECO.CONFIG_CHANGE TO ROLE DPRE_ENGINE;

-- No UPDATE and no DELETE are granted on any of the above, to any role
-- the engine runs as. An amendment is a new row, by design.

ALTER TABLE RECO.REVIEW_DECISION SET DATA_RETENTION_TIME_IN_DAYS = 90;
```

Time Travel retention of at least 90 days gives the audit function a second,
platform-level record independent of the application's own chain.

The conversational surface reads through a semantic view and a closed set of
named queries; in Snowflake that is a view with `SELECT` granted to the chat
role and no direct table access at all. `dpre/chat/semantic_view.py` already
enumerates the objects that must stay out of reach, and a test asserts the list.

## Order of work

1. Storage dialect, keeping the hash chain and adding the grants above. Nothing
   else changes, and the test suite is the acceptance criterion.
2. The XLSX library swap, which is contained and removes the most code.
3. `EMBED_TEXT_768` behind `embedding_similarity`, then re-derive the two
   similarity thresholds and re-run the detection scorecard against the planted
   defects to confirm recall actually improved.
4. `AI_COMPLETE` behind `register_provider`, last, because it changes prose and
   not findings, and the fallback path must be exercised in production before
   anything depends on it.
