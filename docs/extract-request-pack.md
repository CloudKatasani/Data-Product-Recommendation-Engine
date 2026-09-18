# The extract request pack

*Review finding R-55. Implemented in `dpre/engagement/extract_pack.py`.*

The slowest part of a rationalisation engagement is usually the first two weeks,
spent explaining to four different teams which export is wanted. This pack is
the artifact that replaces those conversations: one workbook a Cognos
administrator, a Power BI administrator and a catalog administrator can each
read their own tab of and act on without another meeting.

## Producing it

```python
from dpre.engagement import extract_request_pack, request_letter, write_extract_request_pack

write_extract_request_pack("out/extract-request.xlsx", engagement=record, cut_date="2026-09-17")
print(request_letter(engagement=record, cut_date="2026-09-17"))
```

`extract_request_pack(engagement, cut_date)` returns the tabs as plain rows.
`write_extract_request_pack(path, ...)` writes the workbook.
`request_letter(...)` returns the covering note in prose, naming the client, the
cut date and what is being asked for, ready to paste into an email.

## What it asks for

The tabs are generated from `dpre/ingest`'s own schema definitions through
`contract_rows(schema)`, so the pack cannot ask for a column the engine does not
read, and cannot omit one it requires. `SOURCES` names the systems: Cognos
rationalization and KPI lineage, Power BI inventory and DAX measure lineage, and
the catalog — Collibra or Alation — with its metadata, lineage and business
glossary.

Each field row carries the field name, whether it is required, what it is for,
and an example value. Required and recommended are distinguished honestly: the
engine runs on the required set, and every recommended field that is missing
costs a specific, named thing, which the pack says rather than implies.

## The cut date

One date, on the cover, applied to every extract. It is the single fact that
makes the run reproducible and the pack defensible: every number in the eventual
committee pack is as of that date, and `dpre/engagement/scope.py` compares it
against what actually arrives, raising `AS_OF_AFTER_CUT_DATE` when the extract is
newer than what was agreed. Agreeing the date before the extracts are pulled
costs one sentence; discovering the drift afterwards costs a re-cut.
