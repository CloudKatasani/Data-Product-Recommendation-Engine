# Demo script

*Review finding R-55. Forty minutes, no slides, nothing pre-baked.*

The demonstration runs on a synthetic estate. Say so in the first sentence and
again when a number appears: every pack the engine produces carries a banner to
the same effect, and the credibility of the whole conversation rests on nobody
being able to mistake a generated figure for a finding about their estate.

## Before the room

```bash
python -m dpre generate --industry utility --out demo/
python -m dpre --db demo/engine.db run automated --industry utility \
    --as-of 2026-09-17 --workspace demo --pack demo/pack --client "Acme Utilities"
python -m dpre serve --workspace demo
```

Have the workbook open in a spreadsheet and the browser application at
`http://127.0.0.1:8000`. Sign in with your own name — the decisions you record
in the demonstration will carry it, which is the point.

## 1. The estate, not the engine (5 minutes)

Open the generated workbook and go to the **Planted_Defects** tab first, before
anything else. It lists every problem deliberately built into this estate: the
same KPI defined identically in seven reports, a denominator swapped between two
teams, a threshold that drifted from 60 to 59, a metric reading a sunset source,
a report nobody has run in a year.

Saying out loud what was planted, before showing what was found, is what
separates a demonstration from a magic trick. Offer the tab to the audience and
invite them to check the engine's findings against it afterwards.

## 2. One run, eight agents (5 minutes)

Run it from the Start tab, Automated side, and let the agent log render. Each
line names what that agent wrote and how long it took: the Resolver's resolution
rate and quarantine count, the Canonicalizer's canonical metrics and conflicts,
the Clusterer's candidates, the Scorer's weight version, the Programme step's
sizing and attributed benefit.

The useful observation here is the quarantine count. The engine did not resolve
everything, and it says which rows it could not place and why.

## 3. A candidate, and the evidence under it (10 minutes)

Open the top candidate. Move through the tabs deliberately:

- **Score** — every feature with its value, its weight, its contribution, and
  the evidence rows behind it. Ask the room to pick a feature and follow it to
  the report or column it came from. Nothing scores without evidence; that is
  enforced at the storage layer, not by convention.
- **Delivery** — the size, the money, the wave and the rank spread. Note that
  the candidate's own benefit claim and the attributed figure differ: a report
  retired once is a saving once, however many candidates would retire it.
- **Critique** — what a reviewer is likely to object to, written before the
  reviewer objects.

## 4. The gate that cannot be computed (5 minutes)

Filter the backlog to Exploratory and open one. Gate G1 has failed and the
detail says why: the engine can see that two business units read these metrics,
but it cannot know which decision the data blocks, how fresh it must be, or what
happens without it.

Fill in the confirmation form. The candidate becomes acceptable, and the record
is keyed by lineage rather than by candidate id, so it survives into the next
run even when the clusterer renumbers everything.

This is the moment that usually lands: the engine refused to promote its own
finding until a human supplied something only a human has.

## 5. Try to break it (5 minutes)

Invite the room to try. The three worth demonstrating:

- Accept a Blocked candidate. Refused, naming the gate and what it would take.
- Accept a composite whose entity master someone deferred. Refused, until the
  decision cites `accept_with_open_dependencies` and says so on the record.
- Edit a decision row in the database directly, then run `dpre audit`. The hash
  chain reports the break and names the first row that does not verify.

## 6. What leaves the room (5 minutes)

Open `demo/pack/<run id>/executive-summary.md` and the backlog workbook. These
are cut in the same process as the run, so the cover and the store cannot
disagree. Then `dpre status` for the measures, `dpre waves` for the sequence and
`dpre raid` for the risk log.

Close on the honest limitation: the engine proposes, and every number in the
pack is illustrative until the client's own rates replace the assumption
register. The engine's job is to make the argument checkable, not to win it.
