"""One display dictionary for everything the engine names in code (review finding R-41).

Every gate, score feature, status, origin, archetype, tier, conflict pattern,
reason code, quarantine code, critique criterion and screen tab has an internal
key that engineers read fluently and a business owner does not. This module is
the single place where each key gets a plain-English label and a one-line
explanation, so the browser application, the executive pack, the dossiers and
the communications all say the same thing about the same code.

The dictionary is deliberately data, not code: ``labels_for_api`` returns it
whole for a ``GET /api/labels`` endpoint, and ``tests/test_export.py`` asserts
that every code the engine can emit is covered, so adding a gate or a reason
code without a label fails a test rather than surfacing as a bare chip.

Specification references: 8.1 (features), 8.3 (gates G1-G4), 13.2 (run
quality gates), 7 (archetypes and tiers), 5.2 and 5.4 (match tiers and
conflict patterns), 10.2 (decisions and reason codes), 3 (quarantine codes).
"""
from __future__ import annotations

from typing import Any

# Each category maps an internal key to {"label", "explanation"}. Categories are
# looked up by name from the UI and the pack; unknown keys fall back to the key
# itself with an empty explanation so a new code is visible, never hidden.
LABELS: dict[str, dict[str, dict[str, str]]] = {
    # ---- hard gates on a candidate (section 8.3) --------------------------
    "gate": {
        "G1": {"label": "Named consumer",
               "explanation": "At least one business unit with two or more users runs the "
                              "reports this product would replace. Without it the candidate "
                              "stays Exploratory until a reviewer confirms a consumer."},
        "G2": {"label": "Lineage floor",
               "explanation": "At least 60% of the columns behind the metrics were traced "
                              "confidently to the catalog. Below the floor the candidate is "
                              "listed as a catalog gap."},
        "G3": {"label": "Single grain",
               "explanation": "The metrics are evaluated at one level of detail (for example "
                              "per account). A mixed-grain candidate must be split before it "
                              "can be Proposed."},
        "G4": {"label": "Sunset source",
               "explanation": "None of the source tables is being decommissioned without a "
                              "named successor. A sunset source with no successor blocks "
                              "the candidate."},
    },
    # ---- run quality gates (section 13.2) --------------------------------
    "quality_gate": {
        "freshness": {
            "label": "Extracts current",
            "explanation": "Age of the oldest extract at the as-of date, and the spread "
                           "between inputs. Lineage taken in March against a catalog taken "
                           "in June is the usual cause of orphaned report ids."},
        "ingest_reconciliation": {
            "label": "Extracts reconciled",
            "explanation": "Row counts in the extracts match the counts the source tools "
                           "reported, within tolerance, and no extract failed validation."},
        "resolution_rate": {
            "label": "Lineage resolved",
            "explanation": "Share of lineage rows matched to a catalog column at 0.80 "
                           "confidence or better. Below 80% the run publishes only the "
                           "gap list."},
        "parse_rate": {
            "label": "Expressions parsed",
            "explanation": "Share of Cognos and DAX calculations the parser could read. "
                           "Below 70% canonicalization is degraded and every card says so."},
        "coverage_sanity": {
            "label": "Top-20 usage coverage",
            "explanation": "Share of usage-weighted KPI consumption covered by the top 20 "
                           "candidates. Below 50% the clustering resolution is re-tuned."},
        "stability": {
            "label": "Stable against last run",
            "explanation": "Share of last run's candidates that map to a candidate in this "
                           "run. Below 85% the ranking is changing too fast to act on."},
    },
    # ---- score dimensions and features (section 8.1) ----------------------
    "dimension": {
        "demand": {"label": "Demand",
                   "explanation": "How much the estate actually consumes these numbers: "
                                  "usage, breadth of business units and refresh cadence."},
        "consolidation": {"label": "Consolidation",
                          "explanation": "How much the product tidies up: reports it retires, "
                                         "duplicate definitions it collapses, conflicts it "
                                         "surfaces."},
        "feasibility": {"label": "Feasibility",
                        "explanation": "How buildable it is today: lineage traced, definitions "
                                       "present, healthy sources, calculations the parser "
                                       "could read."},
        "risk": {"label": "Risk",
                 "explanation": "What could go wrong: sensitive data, ambiguous grain and "
                                "the load of unresolved conflicts. Subtracted from the "
                                "composite."},
    },
    "feature": {
        "usage_weight": {"label": "Usage weight",
                         "explanation": "Report runs in the usage window, weighted by recency "
                                        "and by the users behind them. A demand proxy, not "
                                        "a value figure."},
        "consumer_breadth": {"label": "Business units served",
                             "explanation": "Distinct business units that run the covered "
                                            "reports."},
        "cadence": {"label": "Refresh cadence",
                    "explanation": "Share of covered reports on a schedule and how often they "
                                   "refresh; a daily operational report signals recurring "
                                   "decisions."},
        "reports_retirable": {"label": "Reports retirable",
                              "explanation": "Reports whose every metric this product covers, "
                                             "weighted by their disposition (Retire counts "
                                             "most, Migrate least)."},
        "variants_collapsed": {"label": "Duplicate definitions collapsed",
                               "explanation": "KPI definitions that fingerprint identically and "
                                              "become one certified metric."},
        "conflicts_surfaced": {"label": "Conflicts surfaced",
                               "explanation": "Competing definitions of the same number that "
                                              "the product would put in front of a steward."},
        "lineage_completeness": {"label": "Lineage traced",
                                 "explanation": "Share of the metrics' source columns resolved "
                                                "confidently to the catalog."},
        "definition_coverage": {"label": "Definitions present",
                                "explanation": "Share of source columns that carry a business "
                                               "term and a definition in the catalog."},
        "source_health": {"label": "Source health",
                          "explanation": "Whether the source tables are systems of record and "
                                         "active rather than sunset."},
        "calculation_determinism": {"label": "Calculations readable",
                                    "explanation": "Share of the KPI expressions the parser "
                                                   "could turn into a fingerprint; opaque "
                                                   "calculations need a manual definition."},
        "sensitivity": {"label": "Data sensitivity",
                        "explanation": "Highest classification and PII presence among the "
                                       "attributes; drives Stage 9 privacy review."},
        "grain_ambiguity": {"label": "Grain ambiguity",
                            "explanation": "Share of metrics evaluated at a level of detail "
                                           "other than the candidate's grain."},
        "conflict_load": {"label": "Conflict load",
                          "explanation": "Open conflicting definitions relative to the "
                                         "metrics in scope; each one is a steward decision."},
    },
    # ---- candidate status and lifecycle (sections 9.1, 10.2, 13.1) --------
    "status": {
        "Blocked": {"label": "Blocked",
                    "explanation": "A hard gate (a sunset source without a successor) blocks "
                                   "this candidate until the catalog is corrected."},
        "Exploratory": {"label": "Exploratory",
                        "explanation": "Needs a reviewer to confirm a consumer or the lineage "
                                       "before it can be Proposed."},
        "Proposed": {"label": "Proposed",
                     "explanation": "Passed every gate. The furthest the engine can take a "
                                    "candidate; only a reviewer decision moves it on."},
        "Accepted": {"label": "Accepted",
                     "explanation": "A named reviewer accepted it; it can open in the Data "
                                    "Product Factory."},
        "Rejected": {"label": "Rejected",
                     "explanation": "A named reviewer rejected it with a reason code."},
        "Merged": {"label": "Merged",
                   "explanation": "Folded into another candidate by a reviewer."},
        "Deferred": {"label": "Deferred",
                     "explanation": "Parked by a reviewer with a reason and a revisit hint."},
    },
    "origin": {
        "community": {"label": "Metric community",
                      "explanation": "Found by clustering metrics that share source tables."},
        "grain_child": {"label": "Split by grain",
                        "explanation": "Split off a community whose metrics mixed levels of "
                                       "detail."},
        "entity_master": {"label": "Entity master",
                          "explanation": "A hub table that several communities reuse, "
                                         "extracted as a shared master."},
        "composite": {"label": "Consumer composite",
                      "explanation": "Seeded from what one business unit consumes across "
                                     "several source-aligned products."},
        "reviewer_split": {"label": "Reviewer split",
                           "explanation": "Created by a reviewer's Split decision."},
    },
    "name_status": {
        "AI_DRAFT": {"label": "Draft name",
                     "explanation": "Drafted by a template or a language model; a steward "
                                    "must accept it before it reaches the catalog."},
        "ACCEPTED": {"label": "Accepted name",
                     "explanation": "A steward accepted this name."},
    },
    # ---- archetypes and tiers (section 7) ---------------------------------
    "archetype": {
        "Entity Master": {"label": "Entity master",
                          "explanation": "The single agreed record of a business entity "
                                         "(customer, account) that other products join to."},
        "Reference Data": {"label": "Reference data",
                           "explanation": "Small, slowly changing lists (codes, categories) "
                                          "used to filter and label other data."},
        "Event Stream": {"label": "Event stream",
                         "explanation": "Time-stamped events at transaction grain."},
        "Metric / KPI": {"label": "Metric / KPI product",
                         "explanation": "Certified measures over one or two fact tables."},
        "Feature Store": {"label": "Feature store",
                          "explanation": "Inputs prepared for models and scoring."},
        "Insight / Recommendation": {"label": "Insight / recommendation",
                                     "explanation": "Ranked or case-based outputs that tell "
                                                    "someone what to do next."},
    },
    "tier": {
        "Source-aligned": {"label": "Source-aligned",
                           "explanation": "Built from one source system and owned close to it."},
        "Aggregate": {"label": "Aggregate",
                      "explanation": "Combines several source-aligned products."},
        "Consumer-aligned": {"label": "Consumer-aligned",
                             "explanation": "Shaped for one business unit's decisions."},
    },
    # ---- canonicalization (sections 5.2 and 5.4) ---------------------------
    "match_tier": {
        "IDENTICAL": {"label": "Identical",
                      "explanation": "Same calculation, same columns: merged into one metric."},
        "VARIANT": {"label": "Variant",
                    "explanation": "Same calculation with a different filter."},
        "COUSIN": {"label": "Structural cousin",
                   "explanation": "Same shape over different columns; linked, not merged."},
        "OPAQUE": {"label": "Opaque",
                   "explanation": "The parser could not read the expression; needs a manual "
                                  "definition."},
    },
    "conflict_pattern": {
        "THRESHOLD": {"label": "Threshold differs",
                      "explanation": "The same number uses a different cut-off in two reports "
                                     "(for example > 60 versus >= 61 days)."},
        "EXCLUSION": {"label": "Exclusion differs",
                      "explanation": "One report excludes rows the other keeps."},
        "TIME_BASIS": {"label": "Time basis differs",
                       "explanation": "Calendar versus fiscal period, or a different date "
                                      "column."},
        "DENOMINATOR": {"label": "Denominator differs",
                        "explanation": "Same label, divided by a different base."},
        "NULL_HANDLING": {"label": "Null handling differs",
                          "explanation": "Missing values are treated differently and "
                                         "silently change the result."},
        "AGGREGATION": {"label": "Aggregation differs",
                        "explanation": "Same measure summed in one report and averaged in "
                                       "another."},
        "OPERANDS": {"label": "Source columns differ",
                     "explanation": "Different columns behind the same label."},
    },
    "steward_source": {
        "business term steward": {"label": "Confirmed via business term",
                                  "explanation": "The catalog names a steward on the metric's "
                                                 "business term."},
        "column steward": {"label": "Confirmed via column",
                           "explanation": "The catalog names a steward on the source column."},
        "most frequent report owner": {"label": "Inferred from report owner",
                                       "explanation": "No catalog steward; the most frequent "
                                                      "report owner is proposed instead."},
        "unassigned": {"label": "Unassigned",
                       "explanation": "No steward could be found or inferred."},
    },
    # ---- quarantine reason codes (section 3, ER rules) ---------------------
    "quarantine_reason": {
        "NO_CATALOG_TABLE": {"label": "Table not in catalog",
                             "explanation": "The lineage row names a table the catalog extract "
                                            "does not contain."},
        "NO_CATALOG_COLUMN": {"label": "Column not in catalog",
                              "explanation": "The table exists in the catalog but the column "
                                             "does not."},
        "MISSING_REFERENCE": {"label": "No reference in lineage",
                              "explanation": "The lineage row carries no table or column at "
                                             "all."},
        "LOW_CONFIDENCE": {"label": "Match too weak",
                           "explanation": "The best fuzzy match scored below the floor; a "
                                          "person must confirm it."},
        "MODEL_TERMINUS": {"label": "Stops at Power BI model",
                           "explanation": "A Power BI import model breaks lineage at the M "
                                          "query; the source is not visible."},
    },
    # ---- review decisions and reason codes (section 10.2) ------------------
    "decision": {
        "Accept": {"label": "Accept", "explanation": "Open this candidate in the DPF."},
        "AcceptWithException": {"label": "Accept with exception",
                                "explanation": "Accept despite a failed gate, with a waiver "
                                               "and a second approver."},
        "Reject": {"label": "Reject", "explanation": "Do not build; say why."},
        "Merge": {"label": "Merge", "explanation": "Fold into another candidate."},
        "Split": {"label": "Split", "explanation": "Divide by grain or by consumer."},
        "Defer": {"label": "Defer", "explanation": "Park with a reason and a revisit hint."},
        "Override": {"label": "Override",
                     "explanation": "Change one field (name, archetype, owner) without "
                                    "deciding the candidate."},
        "Reverse": {"label": "Reverse", "explanation": "Undo an earlier decision."},
        "CarryForward": {"label": "Carried forward",
                         "explanation": "The engine re-applied a prior reviewer decision to "
                                        "the same candidate in a later run."},
    },
    "reason_code": {
        "value_clear": {"label": "Value is clear",
                        "explanation": "The value hypothesis stands on its own."},
        "retires_reports": {"label": "Retires reports",
                            "explanation": "Accepted mainly for the reports it retires."},
        "resolves_conflicts": {"label": "Resolves conflicts",
                               "explanation": "Accepted mainly for the definitions it settles."},
        "strategic": {"label": "Strategic",
                      "explanation": "Accepted for a programme reason beyond the score."},
        "accept_with_open_dependencies": {"label": "Accepted with open dependencies",
                                          "explanation": "Accepted although it waits on another "
                                                         "candidate."},
        "gate_waived": {"label": "Gate waived",
                        "explanation": "A failed gate was waived with a second approver."},
        "no_named_consumer": {"label": "No named consumer",
                              "explanation": "Nobody would own the decision it serves."},
        "duplicate_of_existing": {"label": "Duplicates an existing product",
                                  "explanation": "Something already covers this."},
        "too_small": {"label": "Too small",
                      "explanation": "A feature of another product, not a product."},
        "wrong_boundary": {"label": "Wrong boundary",
                           "explanation": "The scope cuts across the wrong line."},
        "source_not_viable": {"label": "Source not viable",
                              "explanation": "The source cannot support it."},
        "already_planned": {"label": "Already planned",
                            "explanation": "Another initiative is building this."},
        "same_decision": {"label": "Serves the same decision",
                          "explanation": "Two candidates serve one decision."},
        "same_grain_and_sources": {"label": "Same grain and sources",
                                   "explanation": "Two candidates read the same tables at "
                                                  "the same grain."},
        "duplicate_candidate": {"label": "Duplicate candidate",
                                "explanation": "The clustering produced the same thing twice."},
        "mixed_grain": {"label": "Mixed grain",
                        "explanation": "Metrics at two levels of detail."},
        "mixed_consumers": {"label": "Mixed consumers",
                            "explanation": "Two business units with different decisions."},
        "scope_too_broad": {"label": "Scope too broad",
                            "explanation": "Too much for one product."},
        "awaiting_source_migration": {"label": "Awaiting source migration",
                                      "explanation": "The source is moving; build after."},
        "awaiting_steward": {"label": "Awaiting steward",
                             "explanation": "No steward yet to adjudicate."},
        "capacity": {"label": "No capacity",
                     "explanation": "The team cannot take it on this wave."},
        "awaiting_privacy": {"label": "Awaiting privacy review",
                             "explanation": "Stage 9 must clear it first."},
        "wrong_archetype": {"label": "Wrong archetype",
                            "explanation": "The rule picked the wrong kind of product."},
        "wrong_tier": {"label": "Wrong tier", "explanation": "The tier rule was wrong."},
        "wrong_name": {"label": "Wrong name", "explanation": "The drafted name misleads."},
        "wrong_owner": {"label": "Wrong owner", "explanation": "The proposed owner is wrong."},
        "wrong_grain": {"label": "Wrong grain",
                        "explanation": "The inferred level of detail is wrong."},
        "consumer_confirmed": {"label": "Consumer confirmed",
                               "explanation": "A reviewer named the consumer (clears G1)."},
        "decision_critical": {"label": "Decision-critical",
                              "explanation": "A low-run report matters more than its usage "
                                             "says; its weight is floored."},
        "conflict_resolved": {"label": "Conflict resolved",
                              "explanation": "A steward adjudicated a conflict."},
        "name_accepted": {"label": "Name accepted",
                          "explanation": "A steward accepted a drafted name."},
        "decided_in_error": {"label": "Decided in error",
                             "explanation": "The earlier decision was a mistake."},
        "new_evidence": {"label": "New evidence",
                         "explanation": "Something changed the picture."},
        "consumer_withdrawn": {"label": "Consumer withdrawn",
                               "explanation": "The consumer no longer wants it."},
        "source_changed": {"label": "Source changed",
                           "explanation": "The source moved or was retired."},
        "carried_from_previous_run": {"label": "Carried from previous run",
                                      "explanation": "Same candidate, same decision, new run."},
        "other": {"label": "Other (see note)",
                  "explanation": "The taxonomy lacks the reason; the note says what."},
    },
    # ---- critique criteria (Stage 1-2 exit criteria, section 10.1) ---------
    "critique_criterion": {
        "S1-consumer": {"label": "Stage 1: real consumer",
                        "explanation": "Stage 1 names a consumer who owns a decision, not a "
                                       "report audience."},
        "S1-decision": {"label": "Stage 1: blocked decision",
                        "explanation": "Stage 1 records the decision that is blocked and the "
                                       "consequence of not deciding."},
        "S1-latency": {"label": "Stage 1: latency tolerance",
                       "explanation": "Stage 1 says how stale the answer may be."},
        "S2-scope": {"label": "Stage 2: scope",
                     "explanation": "Stage 2 lists the metrics in and out of scope."},
        "S2-value": {"label": "Stage 2: value hypothesis",
                     "explanation": "Stage 2 makes a measurable value claim."},
        "S2-owner": {"label": "Stage 2: owner and steward",
                     "explanation": "Stage 2 names an owner and a steward."},
        "S2-grain": {"label": "Stage 2: one grain",
                     "explanation": "Stage 2 declares one evaluation grain."},
        "S2-definitions": {"label": "Stage 2: signable definitions",
                           "explanation": "Every metric has a definition a steward can sign."},
        "S3-sources": {"label": "Stage 3: sources",
                       "explanation": "Source discovery can start: lineage traced and "
                                      "attributes confirmed."},
        "S9-privacy": {"label": "Stage 9: privacy",
                       "explanation": "PII is in scope, so privacy review applies."},
        "standing": {"label": "Standing until a human acts",
                     "explanation": "True of every candidate until someone signs: drafted "
                                    "names and decisions to confirm."},
        "G1 Named consumer": {"label": "Gate G1 failed",
                              "explanation": "See gate G1."},
        "G2 Lineage floor": {"label": "Gate G2 failed", "explanation": "See gate G2."},
        "G3 Single grain": {"label": "Gate G3 failed", "explanation": "See gate G3."},
        "G4 Sunset source": {"label": "Gate G4 failed", "explanation": "See gate G4."},
    },
    "severity": {
        # The critic grades findings blocker/major/minor/info; the RAID log grades
        # risks high/medium/low. One category covers both, so a client never sees
        # two scales for the same idea.
        "high": {"label": "High", "explanation": "Likely, and it would hurt: mitigate now."},
        "medium": {"label": "Medium",
                   "explanation": "Real but containable; assign an owner this wave."},
        "low": {"label": "Low", "explanation": "Watch it; no action this wave."},
        "blocker": {"label": "Blocker", "explanation": "Stops Stage 2 exit until resolved."},
        "major": {"label": "Major", "explanation": "A reviewer will send it back."},
        "minor": {"label": "Minor", "explanation": "Worth fixing; will not stop acceptance."},
        "info": {"label": "Information", "explanation": "Context, no action implied."},
    },
    # ---- screen tabs ------------------------------------------------------
    "tab": {
        "Start": {"label": "Start", "explanation": "Run the engine on your extracts or a demo pack."},
        "Backlog": {"label": "Backlog", "explanation": "Ranked candidates; open one for its card."},
        "Portfolio": {"label": "Portfolio",
                      "explanation": "Coverage curve, retirement map and conflict heat map."},
        "Gaps": {"label": "Gaps", "explanation": "What the catalog could not answer."},
        "Ask": {"label": "Ask", "explanation": "Questions answered from the governed tables."},
        "Runs": {"label": "Runs", "explanation": "Every run, its gates and the feedback loop."},
        "Overview": {"label": "Overview", "explanation": "The card in one page."},
        "Score": {"label": "Why this score",
                  "explanation": "Every feature, its weight and the rows behind it."},
        "Metrics": {"label": "Metrics", "explanation": "The certified measures in scope."},
        "Consumers": {"label": "Who uses it", "explanation": "Business units and users."},
        "Reports": {"label": "Reports it replaces",
                    "explanation": "Covered reports and the Stage 12 action for each."},
        "Attributes": {"label": "Attributes", "explanation": "Columns, definitions, sensitivity."},
        "Sources": {"label": "Sources", "explanation": "Tables and systems behind it."},
        "Critique": {"label": "Reviewer findings",
                     "explanation": "What a reviewer will reject, and who must act."},
        "Decisions": {"label": "Decision register",
                      "explanation": "Drafted Stage 1 entries per business unit."},
        "Seeds": {"label": "Factory artifacts",
                  "explanation": "Pre-filled DPF stage files to download."},
        "Review": {"label": "Review", "explanation": "Accept, reject, merge, split or defer."},
    },
    # ---- executive pack workbook tabs -------------------------------------
    "workbook_tab": {
        "Candidates": {"label": "Candidates", "explanation": "The ranked backlog, one row each."},
        "Scores & Evidence": {"label": "Scores & Evidence",
                              "explanation": "Every feature value and the evidence rows behind it."},
        "Metrics": {"label": "Metrics", "explanation": "Canonical metrics with steward and status."},
        "Conflicts": {"label": "Conflicts", "explanation": "Competing definitions to adjudicate."},
        "Retirement map": {"label": "Retirement map",
                           "explanation": "Reports by candidate with the Stage 12 action."},
        "Gap register": {"label": "Gap register",
                         "explanation": "Quarantined lineage, missing definitions, missing stewards."},
        "Decisions": {"label": "Decisions", "explanation": "Reviewer decisions recorded so far."},
        "Waves": {"label": "Waves", "explanation": "The roadmap under capacity."},
        "RAID": {"label": "RAID", "explanation": "Risks, assumptions, issues, dependencies."},
        "Stakeholders": {"label": "Stakeholders", "explanation": "Who carries how much."},
        "Status": {"label": "Status", "explanation": "Progress against the 14.2 measures."},
        "Method appendix": {"label": "Method appendix",
                            "explanation": "Weights, thresholds and versions that produced the run."},
    },
}

# Composite score bands, so 72.2 reads as a word and not only a number.
COMPOSITE_BANDS = (
    (75.0, "Strong", "Clear demand and consolidation with low build risk."),
    (60.0, "Good", "Worth taking to the council; one dimension needs work."),
    (45.0, "Moderate", "Real but weaker; check feasibility and risk before committing."),
    (0.0, "Weak", "Low demand or hard to build; likely a fill-in or a merge."),
)


def label(category: str, key: str) -> str:
    """Plain-English name for an internal key; the key itself when unknown."""
    entry = LABELS.get(category, {}).get(str(key))
    return entry["label"] if entry else str(key)


def explanation(category: str, key: str) -> str:
    entry = LABELS.get(category, {}).get(str(key))
    return entry["explanation"] if entry else ""


def describe(category: str, key: str) -> dict[str, str]:
    """``{"key", "label", "explanation"}`` for one code, always well-formed."""
    return {"key": str(key), "label": label(category, key),
            "explanation": explanation(category, key)}


def composite_band(composite: float) -> dict[str, Any]:
    """Band name and meaning for a composite score on the 0-100 scale."""
    for floor, name, meaning in COMPOSITE_BANDS:
        if composite >= floor:
            return {"band": name, "floor": floor, "meaning": meaning}
    return {"band": COMPOSITE_BANDS[-1][1], "floor": 0.0, "meaning": COMPOSITE_BANDS[-1][2]}


def classification_margin_note(confidence: float) -> str:
    """Section 7.3: archetype confidence is a rule margin, not confidence in the candidate."""
    if confidence >= 0.6:
        return (f"classification margin {confidence:.2f}: the winning archetype rule cleared "
                "the runner-up comfortably")
    return (f"classification margin {confidence:.2f}: two archetype rules scored close, so "
            "the runner-up is shown as an alternative")


def labels_for_api() -> dict[str, Any]:
    """The whole dictionary plus the composite bands, JSON-serialisable."""
    return {
        "categories": {category: dict(entries) for category, entries in LABELS.items()},
        "composite_bands": [{"floor": floor, "band": name, "meaning": meaning}
                            for floor, name, meaning in COMPOSITE_BANDS],
        "notes": {
            "archetype_confidence": "Shown as 'classification margin': the gap between the "
                                    "winning archetype rule and the runner-up (section 7.3).",
            "usage_weight": "Unit: recency-weighted report runs times users; a demand proxy.",
            "composite": "0-100; the dimension weights carry a version a reviewer can contest.",
        },
    }


def glossary_rows() -> list[dict[str, str]]:
    """Flat rows for a glossary page or a workbook tab."""
    rows = []
    for category, entries in LABELS.items():
        for key, entry in entries.items():
            rows.append({"category": category, "key": key, "label": entry["label"],
                         "explanation": entry["explanation"]})
    return rows
