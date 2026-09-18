# Decision register: D-01 to D-08

Specification section 15.2 lists eight decisions the client, not the engine,
must take. `docs/implementation-map.md` row 15.2 said they were "exposed as
configuration"; checked against the code, that is true of four of them, and
one of those four (D-06) is written to the configuration and read by nothing.
This register says, for each decision, what position the engine is running
on, whether that position is **enforced**, **recorded only**, **assumed** or
**not addressed**, who owns the decision, and where a decision taken by a
named person is recorded (review finding R-25).

Rendered from `dpre/registers/decisions.py::decision_register`, which reads
the engine position live from `EngineConfig`; the enforcement notes are
verified against the code paths they cite and
`tests/test_registers.py::test_the_decision_register_states_the_engine_position_and_honest_enforcement`
holds them to it.

## The register (engine defaults, no decision taken)

| # | Decision | Engine position | Enforcement | Owner | Status | Implemented in |
| --- | --- | --- | --- | --- | --- | --- |
| D-01 | Usage window: 12 or 24 months, and how seasonal reports are treated | 12 months, recency half-life 6.0 months | recorded only | Data product council | engine default | `dpre/config.py::EngineConfig.usage_window_months, recency_half_life_months` |
| D-02 | Minimum community size before a candidate is Proposed rather than Exploratory | at least 3 canonical metrics and 2 business units | enforced | Data product council | engine default | `dpre/config.py::ClusterConfig.min_metrics, min_business_units` |
| D-03 | Whether 'Keep' reports count toward consolidation at all | yes - Keep reports count at the disposition weight | enforced | Rationalization lead | engine default | `dpre/config.py::EngineConfig.keep_counts_toward_consolidation, DISPOSITION_WEIGHT['keep']` |
| D-04 | Initial score weights, and who approves a change to them | weight version v1.0-initial, approval recorded for: data product council | enforced | Data product council | engine default | `dpre/config.py::ScoreWeights; dpre/store.py::Store.approve_weight_version` |
| D-05 | Whether Power BI lineage is a Phase 3 adapter or a separate programme | one graph over Cognos and Power BI (adapter in the first release) | assumed | Programme sponsor | engine default | `dpre/ingest/adapters/powerbi.py; dpre/graph/builder.py (one KPI node, many report edges)` |
| D-06 | Sensitivity threshold that forces privacy review before Proposed | privacy review at or above Restricted (recorded only) | recorded only | Privacy officer | engine default | `dpre/config.py::EngineConfig.sensitivity_review_threshold` |
| D-07 | Catalog asset type and attributes for a Proposed data product | payload shaped for collibra; asset type not confirmed by the catalog admin | assumed | Catalog admin | engine default | `dpre/seeds/catalog_payload.py::catalog_payload` |
| D-08 | Which reviewer role may Accept, per domain | HTTP: role 'reviewer' with domain scope; library: any named reviewer string | enforced | Programme sponsor | engine default | `dpre/server/security.py::ROLE_ACTIONS, DOMAIN_SCOPED; dpre/engagement/model.py::reviewer_may (engagement roster)` |

**D-01** - The window is recorded on the run; the demand features read the 12-month extract columns (run_count_12m, distinct_users_12m) whatever the field says. Seasonal reports are handled only through the decision-critical override (section 15.1).

**D-02** - Applied in dpre/cluster/generator.py::generate_candidates; changeable through POST /api/v1/config, and the change is written to CONFIG_CHANGE when the server records it.

**D-03** - dpre/score/scorer.py zeroes a Keep report's contribution when the flag is false and otherwise counts it at the disposition weight (0.5).

**D-04** - The initial version ships unapproved (store note 'pending council approval (D-04)'); versions are immutable, approval is a separate ledger row, and an approver may not have trained the proposal (dpre/server/security.py::assert_weight_approver_independent).

**D-05** - The build treats Power BI as a first-release adapter into one graph. That is an engineering position, not a programme decision; the sponsor still has to take it.

**D-06** - Written to EngineConfig.to_dict and read by nothing. The critic raises S9-privacy at severity 'minor' when any PII attribute is in scope (dpre/narrate/critic.py), regardless of the threshold. Enforcement would be a status cap in dpre/score/scorer.py::_apply_gates or a blocker in the critic.

**D-07** - The payload is written in a fixed shape for the catalog named on the run (collibra or alation) with no record that the catalog admin agreed the asset type or its attributes; import is blocked while any name is AI_DRAFT.

**D-08** - Over HTTP the 'review' action needs the reviewer role and a domain in the principal's scope. The engagement roster (ENGAGEMENT_REVIEWER) records who holds which role for which domains on this engagement; the library path (dpre/review/workflow.py::review) still trusts the reviewer string it is given.

## Taking a decision

A decision is an append-only `DECISION_LOG` row with the position, the person,
the timestamp and a rationale; UPDATE and DELETE are refused by trigger:

```python
from dpre.registers import decide, decision_register
decide(store.connection, "D-03", "Keep reports are excluded from consolidation",
       decided_by="r.lead", rationale="a Keep report left untouched is not a win")
decision_register(config, connection=store.connection)   # D-03 now 'decided', by r.lead
```

`snapshot_decisions(connection, run_id, config, catalog)` writes the register
as it stood into `DECISION_REGISTER` for the run, so the executive pack's
"Open decisions for the council" section (`dpre/export/summary.py::_open_decisions`)
can show who decided what and when, instead of only what the run assumed.

Taking the decision in the log does not change the engine's position by
itself. For D-01, D-02, D-03 and D-04 the position is an `EngineConfig`
field or a weight version and the operator sets it to match, ledgered in
`CONFIG_CHANGE`; the register then shows both and `tests` can assert they
agree. For D-06 there is nothing to set (see below).

## Enforcement gaps, stated plainly

- **D-06 is not enforced.** `EngineConfig.sensitivity_review_threshold` reaches
  `to_dict()` and nowhere else. The critic's `S9-privacy` finding
  (`dpre/narrate/critic.py::_critique_one`) is raised at severity `minor`
  whenever any PII attribute is in scope, whatever the threshold. Enforcing it
  is a status cap in `dpre/score/scorer.py::_apply_gates` (a fifth gate,
  "G5 privacy review", capping at Exploratory when the maximum sensitivity
  class is at or above the threshold) or a `blocker` in the critic. Both files
  are owned elsewhere; the decision row says so rather than pretending.
- **D-07 is assumed.** `dpre/seeds/catalog_payload.py::catalog_payload` writes a
  fixed shape for the catalog named on the run. The register asks the catalog
  admin to confirm the asset type and attributes; until a `DECISION_LOG` row
  says they did, the payload is a proposal about the proposal.
- **D-05 is a build fact.** The Power BI adapter exists
  (`dpre/ingest/adapters/powerbi.py`) and both tools land in one graph. Whether
  Power BI is a Phase 3 adapter or a separate programme is still the sponsor's
  call about scope and funding, not something the code can settle.
- **D-08 is enforced over HTTP only.** `dpre/server/security.py::authorize`
  needs the `reviewer` role and a domain in scope for `review`. The library
  path (`dpre/review/workflow.py::review`) trusts the reviewer string it is
  given. The engagement roster (`dpre/engagement/model.py::reviewer_may`) is
  the per-engagement appointment record; wiring it into `review` is listed
  in [engagement-model.md](engagement-model.md).
