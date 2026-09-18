"""Engine configuration: versioned score weights, thresholds and constants.

Everything a reviewer could reasonably contest lives here, versioned, so a
candidate card can always name the ``weight_version`` that scored it
(specification sections 8.2 and 13.3).
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field, replace
from typing import Any

PARSER_VERSION = "parser-1.2.0"
ENGINE_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Entity resolution (section 4.2)
# --------------------------------------------------------------------------

ER_CONFIDENCE = {
    "ER-1": 1.00,
    "ER-2": 0.95,
    "ER-3": 0.90,
    "ER-4": 0.80,
    "ER-5": 0.70,
    "ER-6": 0.60,
}
ER_PROBABLE_THRESHOLD = 0.80        # below this an edge is "probable" only
ER4_NAME_SIMILARITY = 0.92
ER6_EMBEDDING_SIMILARITY = 0.85

QUARANTINE_REASONS = {
    "NO_CATALOG_TABLE": "Table absent from the catalog extract",
    "NO_CATALOG_COLUMN": "Table found but column absent from the catalog extract",
    "MISSING_REFERENCE": "Lineage row carries no table or column reference",
    "LOW_CONFIDENCE": "Best match scored below the ER-6 floor",
    "MODEL_TERMINUS": "Power BI import model breaks lineage at the M query",
}

# --------------------------------------------------------------------------
# Canonicalization (section 5)
# --------------------------------------------------------------------------

NOMINAL_CONFLICT_LABEL_SIMILARITY = 0.90
# Functions that change how a number is *displayed* but not what it is. ``int``
# was removed after review finding R-20b: ``int([days]/30)`` truncates, so a
# bucketed figure is not the raw ratio and must not fingerprint alike. ``value``
# and ``convert`` stay because they widen a text or numeric type without
# rounding; ``convert`` to an integer type is caught in ``expr.py`` instead.
FORMATTING_FUNCTIONS = {
    "round", "cast", "to_char", "tochar", "format", "currency", "tonumber",
    "to_number", "convert", "trim", "coalesce_display", "value",
}

# How a nominal conflict was proposed, stored on the conflict row so a steward
# can tell an offline token match from a model-scored one (R-18).
SIMILARITY_METHOD = "offline-token"

# Blocking keys for nominal-conflict candidate generation (R-18). A 12-character
# label prefix missed 'DSO' against 'Days Sales Outstanding'; a metric now blocks
# on its expanded label token key, on its operand-column set, and on its
# glossary term, and every pair the blocks produce is scored.
CONFLICT_BLOCKING_KEYS = ("label-token", "operand-set", "glossary-term")
CONFLICT_MIN_SHARED_LABEL_TOKENS = 1
COMMUTATIVE_OPERATORS = {"+", "*"}

# --------------------------------------------------------------------------
# Business glossary as the term of record (section 5.1 step 6, finding R-34)
# --------------------------------------------------------------------------

# A term only counts fully toward definition coverage once it is Approved. A
# Draft term is half-credit and flagged; a Deprecated or Retired term in use is
# a gap, because the estate is reporting on a definition the glossary withdrew.
GLOSSARY_STATUS_CREDIT = {
    "approved": 1.0, "certified": 1.0, "published": 1.0, "accepted": 1.0,
    "draft": 0.5, "proposed": 0.5, "in review": 0.5,
    "deprecated": 0.0, "retired": 0.0, "rejected": 0.0,
}
GLOSSARY_DEFAULT_CREDIT = 0.5          # an unlabelled term is treated as Draft

# Stewardship resolution order with the confidence each step carries (R-44).
# Ownership (accountable) and stewardship (responsible) are different roles: a
# report owner is never promoted to steward, only suggested as one.
STEWARD_RESOLUTION_ORDER = (
    ("glossary term steward", 1.00),
    ("table steward", 0.85),
    ("domain steward", 0.70),
    ("domain owner (escalation)", 0.55),
    ("report owner (suggestion, not a steward)", 0.30),
)
STEWARD_SUGGESTION_SOURCE = "report owner (suggestion, not a steward)"
TIME_INTELLIGENCE_FUNCTIONS = {
    "sameperiodlastyear": "PY",
    "dateadd": "SHIFT",
    "datesytd": "YTD",
    "datesmtd": "MTD",
    "datesqtd": "QTD",
    "totalytd": "YTD",
    "totalmtd": "MTD",
    "totalqtd": "QTD",
    "parallelperiod": "PY",
    "previousmonth": "PM",
    "previousyear": "PY",
}

# --------------------------------------------------------------------------
# Clustering (section 6.1)
# --------------------------------------------------------------------------


@dataclass
class ClusterConfig:
    min_similarity: float = 0.25
    same_domain_bonus: float = 0.20
    resolution: float = 1.0
    resolution_sweep: tuple[float, ...] = (0.7, 1.0, 1.3)
    min_metrics: int = 3
    min_business_units: int = 2
    entity_master_min_communities: int = 3
    composite_min_candidates: int = 2
    composite_min_users: int = 3
    grain_split_min_metrics: int = 1
    random_seed: int = 17


# --------------------------------------------------------------------------
# Scoring (section 8)
# --------------------------------------------------------------------------

DIMENSION_WEIGHTS = {
    "demand": 0.35,
    "consolidation": 0.30,
    "feasibility": 0.25,
    "risk": -0.10,
}

FEATURE_WEIGHTS: dict[str, dict[str, float]] = {
    "demand": {
        "usage_weight": 0.50,
        "consumer_breadth": 0.30,
        "cadence": 0.20,
    },
    "consolidation": {
        "reports_retirable": 0.50,
        "variants_collapsed": 0.30,
        "conflicts_surfaced": 0.20,
    },
    "feasibility": {
        "lineage_completeness": 0.35,
        "definition_coverage": 0.25,
        "source_health": 0.25,
        "calculation_determinism": 0.15,
    },
    "risk": {
        "sensitivity": 0.40,
        "grain_ambiguity": 0.30,
        "conflict_load": 0.30,
    },
}

DISPOSITION_WEIGHT = {"retire": 1.0, "merge": 0.8, "keep": 0.5, "migrate": 0.3}
SENSITIVITY_RANK = {"public": 0.0, "internal": 0.35, "confidential": 0.7, "restricted": 1.0}

RECENCY_HALF_LIFE_MONTHS = 6.0
USAGE_WINDOW_MONTHS = 12            # open decision D-01
DECISION_CRITICAL_USAGE_FLOOR = 0.60  # section 15.1 mitigation

# A six-month half-life punishes a report for running on its own schedule: an
# annual return last run eleven months ago is not stale, it is on time. The
# applied half-life is at least twice the report's cadence (R-49).
RECENCY_CADENCE_MULTIPLE = 2.0

# Hard gates (section 8.3)
GATE_MIN_USERS_PER_BU = 2
GATE_LINEAGE_FLOOR = 0.60
GATE_GRAIN_AMBIGUITY_MAX = 0.30

# Run quality gates (section 13.2)
QUALITY_GATES = {
    "ingest_reconciliation_tolerance": 0.005,
    "resolution_rate_floor": 0.80,
    "parse_rate_floor": 0.70,
    "coverage_top_n": 20,
    "coverage_floor": 0.50,
    "stability_floor": 0.85,
    "stability_jaccard": 0.60,
}

# A gate has three outcomes, not two. "Not assessed" is neither a pass nor a
# failure and must never be reported as 1.0 (R-06): the first run in a database
# has nothing comparable to be stable against.
GATE_PASS = "pass"
GATE_FAIL = "fail"
GATE_NOT_ASSESSED = "not_assessed"
GATE_OUTCOMES = (GATE_PASS, GATE_FAIL, GATE_NOT_ASSESSED)

# --------------------------------------------------------------------------
# Extract freshness and input data quality (sections 3 and 11, findings R-32/R-35)
# --------------------------------------------------------------------------

# Age of the oldest input against the run's as-of date.
FRESHNESS_WARN_DAYS = 30
FRESHNESS_FAIL_DAYS = 90
# Spread between the newest and the oldest input in one bundle. Extracts pulled
# weeks apart explain orphan KPI rows that look like a lineage defect.
EXTRACT_DRIFT_WARN_DAYS = 7

# Share of rows a rule may fail before it is reported as failed rather than
# warned. Duplicate identifiers have no tolerance: one is a defect.
DQ_RULE_TOLERANCE = 0.0
DQ_SAMPLE_LIMIT = 10                 # identifiers shown per failing rule

# --------------------------------------------------------------------------
# Grain backbone (section 4.3)
# --------------------------------------------------------------------------

CONFORMED_BACKBONE = ["Customer", "Account", "Premise", "Service Point", "Meter"]
# A date dimension is joined by everything and identifies nothing, so it can
# never be the coarsest business entity of an estate (R-47).
BACKBONE_EXCLUDED_TOKENS = {
    "date", "time", "calendar", "period", "day", "month", "quarter", "year",
    "week", "fiscal", "datetime", "clock",
}
# Grains that describe a load step rather than a business entity. A KPI never
# inherits one of these, and a table carrying one is reported as ungrained.
NON_BUSINESS_GRAINS = {"Staging", "Batch", "Load", "Stage", "Landing", "Work"}
GRAIN_FINENESS = {
    "Customer": 1, "Account": 2, "Premise": 3, "Service Point": 4, "Meter": 5,
    "Event": 9, "Transaction": 8, "unknown": 0,
}

STATUS_ORDER = ["Blocked", "Exploratory", "Proposed", "Accepted", "Rejected", "Merged", "Deferred"]
ENGINE_MAX_STATUS = "Proposed"       # propose-only guardrail, section 13.1


@dataclass
class ScoreWeights:
    """A versioned weight vector, as stored in ``RECO.SCORE_WEIGHT``."""

    weight_version: str = "v1.0-initial"
    effective_from: str = field(default_factory=lambda: _dt.date.today().isoformat())
    dimensions: dict[str, float] = field(default_factory=lambda: dict(DIMENSION_WEIGHTS))
    features: dict[str, dict[str, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in FEATURE_WEIGHTS.items()}
    )
    note: str = "Initial weights: favour products that retire the most reports for the least build risk."
    approved_by: str = "data product council"

    def rows(self) -> list[dict]:
        out = []
        for dimension, features in self.features.items():
            for feature, weight in features.items():
                out.append({
                    "weight_version": self.weight_version,
                    "dimension": dimension,
                    "feature": feature,
                    "weight": weight,
                    "effective_from": self.effective_from,
                })
        for dimension, weight in self.dimensions.items():
            out.append({
                "weight_version": self.weight_version,
                "dimension": dimension,
                "feature": "__composite__",
                "weight": weight,
                "effective_from": self.effective_from,
            })
        return out

    def with_version(self, version: str, **changes: Any) -> "ScoreWeights":
        return replace(self, weight_version=version, **changes)

    def to_dict(self) -> dict:
        return {
            "weight_version": self.weight_version,
            "effective_from": self.effective_from,
            "dimensions": self.dimensions,
            "features": self.features,
            "note": self.note,
            "approved_by": self.approved_by,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "ScoreWeights":
        return cls(
            weight_version=payload.get("weight_version", "v1.0-initial"),
            effective_from=payload.get("effective_from", _dt.date.today().isoformat()),
            dimensions=dict(payload.get("dimensions") or DIMENSION_WEIGHTS),
            features={k: dict(v) for k, v in (payload.get("features") or FEATURE_WEIGHTS).items()},
            note=payload.get("note", ""),
            approved_by=payload.get("approved_by", ""),
        )


@dataclass
class EngineConfig:
    """The full knob set for one run."""

    weights: ScoreWeights = field(default_factory=ScoreWeights)
    cluster: ClusterConfig = field(default_factory=ClusterConfig)
    usage_window_months: int = USAGE_WINDOW_MONTHS
    recency_half_life_months: float = RECENCY_HALF_LIFE_MONTHS
    keep_counts_toward_consolidation: bool = True     # open decision D-03
    sensitivity_review_threshold: str = "Restricted"  # open decision D-06
    min_community_size: int = ClusterConfig.min_metrics
    parser_version: str = PARSER_VERSION
    engine_version: str = ENGINE_VERSION
    ai_enabled: bool = False                          # Cortex/LLM naming, off by default
    ai_model: str = ""

    def to_dict(self) -> dict:
        return {
            "weights": self.weights.to_dict(),
            "cluster": {
                "min_similarity": self.cluster.min_similarity,
                "same_domain_bonus": self.cluster.same_domain_bonus,
                "resolution": self.cluster.resolution,
                "resolution_sweep": list(self.cluster.resolution_sweep),
                "min_metrics": self.cluster.min_metrics,
                "min_business_units": self.cluster.min_business_units,
            },
            "usage_window_months": self.usage_window_months,
            "recency_half_life_months": self.recency_half_life_months,
            "keep_counts_toward_consolidation": self.keep_counts_toward_consolidation,
            "sensitivity_review_threshold": self.sensitivity_review_threshold,
            "parser_version": self.parser_version,
            "engine_version": self.engine_version,
            "ai_enabled": self.ai_enabled,
            "ai_model": self.ai_model,
        }
