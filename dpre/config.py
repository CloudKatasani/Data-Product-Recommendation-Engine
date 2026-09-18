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
FORMATTING_FUNCTIONS = {
    "round", "cast", "to_char", "tochar", "format", "currency", "tonumber",
    "to_number", "convert", "trim", "coalesce_display", "value", "int",
}
COMMUTATIVE_OPERATORS = {"+", "*"}
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

# --------------------------------------------------------------------------
# Grain backbone (section 4.3)
# --------------------------------------------------------------------------

CONFORMED_BACKBONE = ["Customer", "Account", "Premise", "Service Point", "Meter"]
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
