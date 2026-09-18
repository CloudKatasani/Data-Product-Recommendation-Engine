"""The bias assessment, written down so it can be argued with (review finding R-49).

Every ranking encodes a preference. This one prefers what is used a lot, what is
used recently, what is defined in the catalog, and what is easy to parse. Those
preferences are defensible, and they are also the reason the backlog will
under-rank certain real products - a quarterly regulatory return, a new product
line, a business unit whose reports live in a tool the extract covered badly.

A client's assurance function will ask what the ranking is blind to. The honest
answer is a list, with the mechanism, the direction, who it disadvantages and
the mitigation that is actually in the code. That list is here rather than in a
slide, so it stays true as the code changes: each entry names the module and
constant it describes, and a reader can check it.

Nothing here changes a score. It is documentation that runs.
"""
from __future__ import annotations

from typing import Any

from ..config import (
    DECISION_CRITICAL_USAGE_FLOOR, DIMENSION_WEIGHTS, GATE_MIN_USERS_PER_BU,
    RECENCY_HALF_LIFE_MONTHS, USAGE_WINDOW_MONTHS,
)

#: One entry per known bias. ``mitigation`` names what the code actually does;
#: where nothing is done, it says so rather than inventing a control.
BIASES: tuple[dict[str, str], ...] = (
    {
        "id": "BIAS-01",
        "name": "Cadence blindness in recency decay",
        "mechanism": (
            f"Demand decays usage with a {RECENCY_HALF_LIFE_MONTHS:.0f}-month half-life over a "
            f"{USAGE_WINDOW_MONTHS}-month window, applied to every report at the same rate."),
        "direction": "under-ranks",
        "affects": (
            "Quarterly and annual reports, which are structurally old on any given day. A "
            "regulatory return filed twice a year looks abandoned beside a daily operations "
            "dashboard, although missing it costs far more."),
        "mitigation": (
            "A decision-critical report is floored at "
            f"{DECISION_CRITICAL_USAGE_FLOOR:.0%} of the maximum usage weight, and "
            "the retirement list holds it whatever its usage. The floor is a mitigation, "
            "not a fix: the decay itself still ignores the report's own cadence."),
        "where": "dpre/score/scorer.py, dpre/config.py::RECENCY_HALF_LIFE_MONTHS",
        "severity": "material",
    },
    {
        "id": "BIAS-02",
        "name": "Incumbency in usage-weighted demand",
        "mechanism": (
            f"Demand carries {DIMENSION_WEIGHTS['demand']:.0%} of the composite and is driven by "
            "observed consumption of reports that already exist."),
        "direction": "under-ranks",
        "affects": (
            "Data products for decisions nobody can make today, because the data was never "
            "available to make them. A new product line, a newly regulated activity, or an "
            "analysis blocked by the very gap the product would close, all show zero usage."),
        "mitigation": (
            "None in the score, deliberately: inventing demand for an unbuilt product would be "
            "a guess dressed as a measurement. A reviewer raises these with Override and the "
            "reason is recorded, which is why the feedback loop can learn the pattern."),
        "where": "dpre/score/scorer.py::demand features",
        "severity": "material",
    },
    {
        "id": "BIAS-03",
        "name": "Catalog coverage as a proxy for feasibility",
        "mechanism": (
            f"Feasibility carries {DIMENSION_WEIGHTS['feasibility']:.0%} and rewards lineage "
            "completeness and definition coverage, both measured against the catalog extract."),
        "direction": "under-ranks",
        "affects": (
            "Domains the client catalogued last. A well-run domain with a thin catalog export "
            "scores worse than a badly-run domain that was catalogued first, which measures the "
            "catalog programme rather than the estate."),
        "mitigation": (
            "The benchmark reports definition coverage per run against reference bands, and the "
            "remediation plan names the missing catalog objects with an owner, so the gap is "
            "visible as a catalog gap rather than absorbed into the score as a quality verdict."),
        "where": "dpre/score/scorer.py, dpre/quality/remediation.py, dpre/portfolio/benchmark.py",
        "severity": "material",
    },
    {
        "id": "BIAS-04",
        "name": "Parseability as a proxy for determinism",
        "mechanism": (
            "Calculation determinism rewards expressions the parser could turn into a shape. An "
            "opaque expression scores as non-deterministic whether or not it actually is."),
        "direction": "under-ranks",
        "affects": (
            "Estates using constructs the parser does not cover, and any tool added later. It "
            "penalises the report author for the engine's coverage."),
        "mitigation": (
            "Opaque metrics are counted and reported per run, listed in the gap register with "
            "the calculation to supply by hand, and the parse rate is a published quality gate, "
            "so a low score is attributable to the parser rather than assumed about the estate."),
        "where": "dpre/canonicalize/expr.py, dpre/quality/gates.py",
        "severity": "moderate",
    },
    {
        "id": "BIAS-05",
        "name": "Tool parity is partial",
        "mechanism": (
            "Usage is normalised within each tool by percentile before the tools are combined, "
            "because a Cognos run count and a Power BI view count do not mean the same thing. "
            "Tools with sparse telemetry still land lower overall."),
        "direction": "under-ranks",
        "affects": (
            "Business units standardised on the tool with the weaker usage export, and any "
            "estate where one tool's telemetry was not requested."),
        "mitigation": (
            "Within-tool percentile normalisation removes the worst of it. The evidence row for "
            "every usage feature names the tool and the percentile, so a reviewer can see which "
            "tool a candidate's demand came from."),
        "where": "dpre/score/scorer.py::usage percentile normalisation",
        "severity": "moderate",
    },
    {
        "id": "BIAS-06",
        "name": "Consumer breadth favours large units",
        "mechanism": (
            f"Gate G1 needs business units with at least {GATE_MIN_USERS_PER_BU} users each, and "
            "consumer breadth rewards the number of units served."),
        "direction": "under-ranks",
        "affects": (
            "Small expert teams whose decisions matter far more than their headcount: an "
            "actuarial function, a regulatory reporting team, a treasury desk."),
        "mitigation": (
            "G1's second half is a human consumer confirmation naming the decision the data "
            "blocks and the consequence if it is absent, which is exactly where a small team "
            "with a large decision makes its case. The gate cannot be cleared by headcount "
            "alone."),
        "where": "dpre/score/scorer.py::_apply_gates, dpre/review/workflow.py::confirm_consumer",
        "severity": "material",
    },
    {
        "id": "BIAS-07",
        "name": "Offline similarity understates renamed labels",
        "mechanism": (
            "Rule ER-6 and nominal conflict scoring use a deterministic hash blend as a stand-in "
            "for a learned embedding, so 'DSO' and 'Days Sales Outstanding' may not be related."),
        "direction": "under-detects",
        "affects": (
            "Estates with heavy abbreviation, and any domain whose labels drifted over time. "
            "Missed conflicts mean an understated consolidation score."),
        "mitigation": (
            "Conflict candidates are additionally blocked on operand sets and glossary terms, "
            "not on labels alone, and the assumption register records the stand-in so it is not "
            "mistaken for a real embedding. Replacing it is the third step in the platform "
            "migration."),
        "where": "dpre/util/text.py::embedding_similarity, docs/snowflake-cortex-migration.md",
        "severity": "moderate",
    },
)


def bias_register(result: Any = None) -> list[dict]:
    """The register, optionally annotated with what this run actually shows.

    Called without a run it is the standing list for a controls pack. Called
    with one, each entry gains an ``observed`` line quantifying the bias in this
    estate, so the conversation is about numbers rather than principles.
    """
    rows = [dict(entry) for entry in BIASES]
    if result is None:
        return rows

    graph, canonical = result.graph, result.canonical
    reports = list(graph.reports.values())
    metrics = list(canonical.metrics.values())
    critical = [r for r in reports if getattr(r, "decision_critical", False)]
    opaque = [m for m in metrics if getattr(m, "opaque", False)]
    tools: dict[str, int] = {}
    for report in reports:
        tool = (getattr(report, "tool", "") or "").lower()
        if tool:
            tools[tool] = tools.get(tool, 0) + 1

    observed = {
        "BIAS-01": (f"{len(critical)} of {len(reports)} reports are decision-critical and "
                    "carry the usage floor"),
        "BIAS-02": "not measurable from the extracts: a product nobody can use has no usage",
        "BIAS-03": (f"{sum(1 for c in graph.columns.values() if not (c.definition or '').strip())}"
                    f" of {len(graph.columns)} catalog columns carry no definition"),
        "BIAS-04": f"{len(opaque)} of {len(metrics)} canonical metrics are opaque to the parser",
        "BIAS-05": "reports by tool: " + ", ".join(f"{t} {n}" for t, n in sorted(tools.items())),
        "BIAS-06": (f"{sum(1 for c in result.candidates if c.status == 'Exploratory')} candidates "
                    "are Exploratory pending a consumer confirmation"),
        "BIAS-07": f"{len(canonical.conflicts)} nominal conflicts found with the offline blend",
    }
    for row in rows:
        row["observed"] = observed.get(row["id"], "")
    return rows


def bias_summary(rows: list[dict]) -> dict:
    return {
        "entries": len(rows),
        "material": sum(1 for r in rows if r["severity"] == "material"),
        "unmitigated": [r["id"] for r in rows if r["mitigation"].startswith("None")],
    }
