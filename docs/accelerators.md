# Industry accelerators

*Review finding R-38. Implemented in `dpre/accelerators/packs.py`.*

The synthetic packs were built to exercise the engine, not to help a client.
Their glossaries were templated and their stewards were random names. That is
fine for a test and useless on day one of an engagement, where the question is
always the same: what should this estate's KPI dictionary, backbone and steward
roles look like before anyone has agreed anything?

An accelerator answers that question for one industry. It is curated content,
not generated data, and nothing in it reaches a score.

## What an accelerator carries

`IndustryAccelerator` holds, for each of the nine industries in
`ACCELERATOR_KEYS`:

- **backbone** — the conformed entities, coarsest first, ready to pass as
  `build_graph(bundle, backbone=...)`.
- **domains** — the data domains this estate is organised into.
- **kpis** — `KpiEntry` records with a label, a canonical name, the formula in
  words, the aggregation, the grain, the domain, whether it is regulatory, and
  the conflicts this KPI typically produces. The last field is the useful one:
  it tells a steward what argument to expect before the argument happens.
- **glossary** — `GlossaryEntry` starter terms with real definitions.
- **personas** — business unit to the role that reads its reports.
- **steward_roles** — domain to the role-shaped steward, never a person's name.
- **regulatory** — `RegulatoryPattern` records naming the report-name patterns
  that mark a decision-critical report, with the regime and why it matters.

Every accelerator is aligned with its synthetic pack: same backbone, same
domains, same business units. A test asserts it, so the demonstration estate and
the accelerator cannot drift apart.

## Using one

```python
from dpre.accelerators import (
    backbone_for_industry, merge_starter_glossary, persona_for, steward_role_for,
    is_regulatory_report, write_accelerator_workbook,
)

graph = build_graph(bundle, backbone=backbone_for_industry("utility"))
added = merge_starter_glossary(bundle, "utility")     # returns how many were added
```

`merge_starter_glossary(bundle, key)` fills gaps and never overwrites: matching
is case-insensitive on the term name, and a term the client already defines wins
every time. What it adds carries the `Starter` status, so a candidate card can
say the definition is the accelerator's and not the client's.

`persona_for(key, business_unit)` and `steward_role_for(key, domain)` answer in
the client's own words. "Credit and Collections Team" finds the persona filed
under "Credit & Collections"; "Astrophysics" finds nothing rather than the
nearest thing. Matching ignores punctuation and organisational filler, and
requires one name's meaningful words to contain the other's, so a partial
overlap like "Credit Risk" does not answer for "Credit & Collections".

`is_regulatory_report(key, report_name)` returns the pattern a report name
matches, which is a hint to mark the report decision-critical — a hint for a
human, never an automatic flag.

## The workbook

`accelerator_sheets(key)` returns seven tabs — README, Backbone, KPI_Dictionary,
Starter_Glossary, Personas, Steward_Roles, Regulatory_Patterns — and
`write_accelerator_workbook(key, path)` writes them. This is the artifact to send
a client's BI and catalog teams before the first workshop: it says what the
engine expects to find and what good looks like in their industry, in a file
they can edit and hand back.

The Starter_Glossary tab leaves the steward column empty on purpose. Naming a
steward is the client's act, and a pre-filled name would be read as a decision
nobody took.
