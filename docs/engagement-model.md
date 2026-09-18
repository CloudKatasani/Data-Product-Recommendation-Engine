# The engagement model

*Review finding R-24. Implemented in `dpre/engagement/`.*

The engine knows the estate. It does not know whose estate it is, who sponsored
the work, what was agreed to be in scope, or which date the data was cut on.
Those facts do not affect a score and must never affect one, but they decide
what a pack may claim, which reviewer may accept which candidate, and whether a
run should have been produced at all.

## What an engagement is

`dpre/engagement/model.py` defines `EngagementRecord`: the client, an engagement
code, the sponsor, the lead partner and manager, the start and end dates, the
scope statement in prose, the domains agreed to be in scope, the data cut date,
a confidentiality line, and a reviewer roster.

The roster is a tuple of `RosterEntry(identity, role, domains)`. Roles come from
`ROSTER_ROLES` and map onto the same actions the HTTP surface authorises, so the
engagement record is the source a token map is generated from rather than a
second, drifting list of people. `token_map_entries(record)` produces exactly
that map, and `hosted_roles(record)` prints who holds what for a pack appendix.

`reviewer_may(record, identity, action, domain)` answers one question — may this
person take this action in this domain — and returns the sentence explaining the
answer alongside the verdict, so a refusal can be shown rather than merely
logged.

## Storage

Three tables, created by `ensure_schema(connection)`:

| Table | What it holds |
| --- | --- |
| `ENGAGEMENT` | One row per engagement, keyed by `engagement_id` |
| `ENGAGEMENT_REVIEWER` | The roster: identity, role and domain scope |
| `RUN_ENGAGEMENT` | Which engagement a run belongs to, and who attached it |

`engagement_id_for(client, code)` derives a stable id from the client and code,
so the same engagement recreated from the same facts keeps its identity.

A run is attached to exactly one engagement, once. `attach_run` refuses to move
a run that is already attached — an engagement boundary that can be edited after
the fact is not a boundary. `engagement_for_run` and `run_attribution` read it
back, `runs_for_engagement` lists an engagement's runs, and
`unattributed_runs(connection)` names the runs nobody has claimed, which is the
list to work through before a pack goes out.

## Scope drift

`dpre/engagement/scope.py` compares what was agreed with what arrived.
`check_scope(record, bundle)` returns issues in the same shape as the ingestion
validator's, so the pipeline can append them to the manifest warnings without
translation:

| Code | Severity | What it means |
| --- | --- | --- |
| `AS_OF_AFTER_CUT_DATE` | warning | The extract is newer than the date the client agreed to cut |
| `USAGE_AFTER_CUT_DATE` | info | Reports carry a last-run date after the cut |
| `DOMAINS_OUT_OF_SCOPE` | info | The catalog export reaches past the statement of work |
| `SCOPED_DOMAIN_ABSENT` | warning | An agreed domain appears nowhere in the extract |
| `NO_CUT_DATE` / `NO_SCOPED_DOMAINS` | info | The engagement record is silent on the point |
| `ENGAGEMENT_WINDOW_INVERTED` | warning | The end date precedes the start date |

A domain is only called absent when nothing in the extract names it — not the
catalog columns, not the glossary, not a report's business unit. Regulatory
reporting owns no tables of its own in most estates; declaring it missing on the
column evidence alone would send the team looking for an extract that was never
missing.

`scope_summary(record, bundle)` returns the cover-page version, with `ok` false
when any warning stands.

## Branding

`dpre/engagement/branding.py` carries the cover-page presentation and nothing
else: `load_branding()` reads a file or the environment, `branding_from_engagement`
derives it from the record, and `topbar`, `footer_line`, `seed_header_line`,
`written_by` and `logo_data_uri` supply the strings and the embedded logo the
browser application and the exports use. No function here is reachable from a
score.

## The boundary with the export package

`dpre/export/engagement.py` has its own small `Engagement` dataclass for pack
covers, deliberately independent of the database. `to_export_engagement(record)`
and `from_export_engagement(engagement)` convert between them, so a pack can be
cut for an engagement that was never saved, and a saved engagement never has to
be retyped onto a cover.
