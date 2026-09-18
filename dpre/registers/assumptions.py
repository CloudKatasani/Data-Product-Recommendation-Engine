"""The assumption register: every constant the ranking silently runs on (R-25).

A council member cannot see from the application that a Keep report counts
at 0.5, that the usage window is 12 months, or that an archetype needs a rule
score of 0.55 to match. Those numbers live in ``dpre/config.py``,
``dpre/score/classify.py``, ``dpre/governance/reasons.py`` and a few other
modules as constants, which is the right place for the engine to read them
and the wrong place for a client to contest them (specification 15.2, D-04:
"must be visible and contestable").

This module does not move the constants. It reads them, live, and lays them
out as a register: key, value, unit, the specification section that set it,
the module and name that hold it, the code that consumes it, the open
decision it implements, the proposed owner from section 15.2, and how it can
be changed today (an ``EngineConfig`` field, or only a code change). Reading
live rather than copying means the register cannot drift from the code, and a
content hash of the register - ``config_version`` - can be stamped on a run so
two runs are comparable only when they ran on the same assumptions.

The value-model rates in ``dpre/value/assumptions.py`` are a register of their
own, versioned and approvable; they are included here by reference so one
page lists everything, and never duplicated.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from .. import config as _cfg
from ..config import EngineConfig

CATEGORIES = ("scoring", "gates", "quality_gates", "clustering", "resolution",
              "canonicalization", "classification", "vocabulary", "value")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ASSUMPTION_REGISTER (
    run_id TEXT NOT NULL, key TEXT NOT NULL, category TEXT, value TEXT, unit TEXT,
    spec_section TEXT, implemented_in TEXT, consumed_by TEXT, decision_ref TEXT,
    owner TEXT, contestable_via TEXT, config_version TEXT, snapshot_at TEXT,
    PRIMARY KEY (run_id, key)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Idempotent; safe at the top of every function that touches the table."""
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass(frozen=True)
class Assumption:
    key: str
    category: str
    value: Any
    unit: str
    description: str
    spec_section: str
    implemented_in: str
    consumed_by: tuple[str, ...]
    decision_ref: str = ""
    owner: str = "Engine team"
    contestable_via: str = "code change"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["consumed_by"] = list(self.consumed_by)
        return payload


COUNCIL = "Data product council"
STEWARD = "Domain steward"
RATIONALIZATION = "Rationalization lead"
PRIVACY = "Privacy officer"
ENGINE = "Engine team"
FINANCE = "Client finance partner"


def _const(name: str, default: Any = None) -> Any:
    """A config constant by name; tolerant of constants a later version removes."""
    return getattr(_cfg, name, default)


def assumption_register(config: EngineConfig | None = None,
                        value_assumptions: Any = None,
                        include_value: bool = True) -> list[Assumption]:
    """The register, read live from the code and the given configuration.

    ``value_assumptions`` is a ``dpre.value.assumptions.ValueAssumptions``; when
    omitted and ``include_value`` is true, the default rate card is used and
    labelled with its own ``basis`` (illustrative until approved).
    """
    config = config or EngineConfig()
    rows: list[Assumption] = []
    add = rows.append

    # ---- scoring (section 8) ----------------------------------------
    for dimension, weight in config.weights.dimensions.items():
        add(Assumption(
            f"weights.dimensions.{dimension}", "scoring", weight, "share of composite",
            f"Weight of the {dimension} dimension in the composite score",
            "8.2", f"dpre/config.py::DIMENSION_WEIGHTS (version {config.weights.weight_version})",
            ("dpre/score/scorer.py::score_candidates",), "D-04", COUNCIL,
            "ScoreWeights version, approved through Store.approve_weight_version"))
    for dimension, features in config.weights.features.items():
        for feature, weight in features.items():
            add(Assumption(
                f"weights.features.{dimension}.{feature}", "scoring", weight,
                "share of dimension", f"Weight of {feature} inside {dimension}",
                "8.1", f"dpre/config.py::FEATURE_WEIGHTS (version {config.weights.weight_version})",
                ("dpre/score/scorer.py::score_candidates",), "D-04", COUNCIL,
                "ScoreWeights version, approved through Store.approve_weight_version"))
    for disposition, weight in (_const("DISPOSITION_WEIGHT") or {}).items():
        add(Assumption(
            f"disposition_weight.{disposition}", "scoring", weight, "multiplier",
            f"How much a '{disposition}' report counts toward reports retirable",
            "8.1", "dpre/config.py::DISPOSITION_WEIGHT",
            ("dpre/score/scorer.py::_retirable_weight",),
            "D-03" if disposition == "keep" else "", RATIONALIZATION, "code change"))
    add(Assumption(
        "keep_counts_toward_consolidation", "scoring", config.keep_counts_toward_consolidation,
        "boolean", "Whether Keep reports count toward consolidation at all", "15.2",
        "dpre/config.py::EngineConfig.keep_counts_toward_consolidation",
        ("dpre/score/scorer.py::_retirable_weight",), "D-03", RATIONALIZATION,
        "EngineConfig field; POST /api/v1/config"))
    add(Assumption(
        "usage_window_months", "scoring", config.usage_window_months, "months",
        "Usage window the demand features are read over", "15.2",
        "dpre/config.py::EngineConfig.usage_window_months",
        ("dpre/usage.py::report_weights (via run_count_12m)",), "D-01", COUNCIL,
        "EngineConfig field (recorded; the 12-month extract columns are what is read)"))
    add(Assumption(
        "recency_half_life_months", "scoring", config.recency_half_life_months, "months",
        "Half-life of the recency decay 0.5^(months_since_last_run / half_life)", "8.1",
        "dpre/config.py::EngineConfig.recency_half_life_months",
        ("dpre/usage.py::recency_decay",), "D-01", COUNCIL, "EngineConfig field"))
    add(Assumption(
        "decision_critical_usage_floor", "scoring", _const("DECISION_CRITICAL_USAGE_FLOOR"),
        "share of the maximum usage weight",
        "Floor applied to a report a reviewer marked decision-critical", "15.1",
        "dpre/config.py::DECISION_CRITICAL_USAGE_FLOOR", ("dpre/usage.py::report_weights",),
        "", COUNCIL, "code change"))
    for cls, rank in (_const("SENSITIVITY_RANK") or {}).items():
        add(Assumption(
            f"sensitivity_rank.{cls}", "scoring", rank, "0..1",
            f"Risk contribution of the '{cls}' sensitivity class", "8.1",
            "dpre/config.py::SENSITIVITY_RANK", ("dpre/score/scorer.py::_sensitivity",),
            "D-06", PRIVACY, "code change"))
    add(Assumption(
        "sensitivity_review_threshold", "scoring", config.sensitivity_review_threshold,
        "sensitivity class", "Sensitivity class at or above which privacy review is forced "
        "before Proposed", "15.2", "dpre/config.py::EngineConfig.sensitivity_review_threshold",
        ("written to EngineConfig.to_dict only; the critic's S9-privacy finding is fixed at "
         "severity 'minor' in dpre/narrate/critic.py",), "D-06", PRIVACY,
        "EngineConfig field (recorded, not enforced)"))

    # ---- hard gates (section 8.3) -------------------------------------
    add(Assumption(
        "gate.G1.min_users_per_bu", "gates", _const("GATE_MIN_USERS_PER_BU"), "users",
        "Business unit must have at least this many users to count as a named consumer",
        "8.3", "dpre/config.py::GATE_MIN_USERS_PER_BU", ("dpre/score/scorer.py::_apply_gates",),
        "", COUNCIL, "code change"))
    add(Assumption(
        "gate.G2.lineage_floor", "gates", _const("GATE_LINEAGE_FLOOR"), "share",
        "Lineage completeness below which status is capped at Exploratory", "8.3",
        "dpre/config.py::GATE_LINEAGE_FLOOR",
        ("dpre/score/scorer.py::_apply_gates", "dpre/narrate/critic.py::_critique_one",
         "dpre/programme/raid.py"), "", ENGINE, "code change"))
    add(Assumption(
        "gate.G3.grain_ambiguity_max", "gates", _const("GATE_GRAIN_AMBIGUITY_MAX"), "share",
        "Grain ambiguity above which a candidate must be split before Proposed", "8.3",
        "dpre/config.py::GATE_GRAIN_AMBIGUITY_MAX",
        ("dpre/score/scorer.py::_apply_gates", "dpre/programme/raid.py"), "", ENGINE,
        "code change"))
    add(Assumption(
        "engine_max_status", "gates", _const("ENGINE_MAX_STATUS"), "status",
        "The furthest status any engine path may write", "13.1",
        "dpre/config.py::ENGINE_MAX_STATUS", ("dpre/store.py::Store.save_candidates",),
        "D-08", "Programme sponsor", "not contestable: the propose-only guardrail"))

    # ---- run quality gates (section 13.2) -----------------------------
    for key, value in (_const("QUALITY_GATES") or {}).items():
        add(Assumption(
            f"quality_gate.{key}", "quality_gates", value,
            "count" if key == "coverage_top_n" else "share",
            f"Run quality gate parameter '{key}'", "13.2", "dpre/config.py::QUALITY_GATES",
            ("dpre/pipeline.py::_quality_gates", "dpre/ingest/validator.py::validate",
             "dpre/governance/identity.py::carry_forward"), "", ENGINE, "code change"))

    # ---- clustering (section 6.1) -------------------------------------
    cluster = config.cluster
    for name, unit, desc, ref in (
            ("min_similarity", "Jaccard", "Minimum metric-metric similarity to keep an edge", ""),
            ("same_domain_bonus", "added similarity", "Bonus for metrics in the same catalog domain", ""),
            ("resolution", "Louvain resolution", "Community resolution in force", ""),
            ("min_metrics", "metrics", "Size floor: fewer metrics means Exploratory", "D-02"),
            ("min_business_units", "business units", "Size floor on distinct consumers", "D-02"),
            ("entity_master_min_communities", "communities",
             "Communities a hub table must serve to become an Entity Master", ""),
            ("composite_min_candidates", "candidates",
             "Candidates a business unit must span to seed a composite", ""),
            ("composite_min_users", "users", "Users a business unit needs to seed a composite", ""),
    ):
        add(Assumption(
            f"cluster.{name}", "clustering", getattr(cluster, name, None), unit, desc, "6.1",
            f"dpre/config.py::ClusterConfig.{name}",
            ("dpre/cluster/generator.py::generate_candidates",), ref,
            COUNCIL if ref else ENGINE,
            "EngineConfig.cluster field; POST /api/v1/config"
            if name in ("min_similarity", "same_domain_bonus", "resolution", "min_metrics",
                        "min_business_units") else "code change"))
    add(Assumption(
        "cluster.resolution_sweep", "clustering", list(cluster.resolution_sweep),
        "Louvain resolutions", "Resolutions re-run for reviewer comparison and coverage re-tune",
        "6.1", "dpre/config.py::ClusterConfig.resolution_sweep",
        ("dpre/pipeline.py::_coverage_with_retune",), "", ENGINE, "code change"))

    # ---- entity resolution (section 4.2) ------------------------------
    for rule, confidence in (_const("ER_CONFIDENCE") or {}).items():
        add(Assumption(
            f"er_confidence.{rule}", "resolution", confidence, "confidence",
            f"Confidence stored on an edge resolved by {rule}", "4.2",
            "dpre/config.py::ER_CONFIDENCE", ("dpre/graph/resolver.py",), "", ENGINE,
            "code change"))
    add(Assumption(
        "er_probable_threshold", "resolution", _const("ER_PROBABLE_THRESHOLD"), "confidence",
        "Below this an edge is 'probable' and never claims a conflict", "4.2",
        "dpre/config.py::ER_PROBABLE_THRESHOLD",
        ("dpre/graph/builder.py", "dpre/cluster/bipartite.py", "dpre/score/scorer.py",
         "dpre/canonicalize/grouping.py"), "", ENGINE, "code change"))
    add(Assumption(
        "er4_name_similarity", "resolution", _const("ER4_NAME_SIMILARITY"), "Jaro-Winkler",
        "Name similarity floor for ER-4", "4.2", "dpre/config.py::ER4_NAME_SIMILARITY",
        ("dpre/graph/resolver.py",), "", ENGINE, "code change"))
    add(Assumption(
        "er6_embedding_similarity", "resolution", _const("ER6_EMBEDDING_SIMILARITY"),
        "cosine (offline stand-in)", "Embedding similarity floor for ER-6; offline the engine "
        "uses dpre/util/text.py::embedding_similarity, a hash blend, not EMBED_TEXT_768",
        "4.2", "dpre/config.py::ER6_EMBEDDING_SIMILARITY", ("dpre/graph/resolver.py",),
        "", ENGINE, "code change"))

    # ---- canonicalization (section 5) ---------------------------------
    add(Assumption(
        "nominal_conflict_label_similarity", "canonicalization",
        _const("NOMINAL_CONFLICT_LABEL_SIMILARITY"), "similarity",
        "Label similarity at or above which two metrics with different fingerprints are a "
        "nominal conflict", "5.2", "dpre/config.py::NOMINAL_CONFLICT_LABEL_SIMILARITY",
        ("dpre/canonicalize/grouping.py::_find_conflicts",), "", STEWARD, "code change"))
    add(Assumption(
        "parser_version", "canonicalization", config.parser_version, "version",
        "Expression parser version stamped on every run", "13.1",
        "dpre/config.py::PARSER_VERSION", ("dpre/pipeline.py::run_pipeline",), "", ENGINE,
        "code change"))
    steward_order = _const("STEWARD_RESOLUTION_ORDER")
    if steward_order:
        add(Assumption(
            "steward_resolution_order", "canonicalization",
            [f"{source} ({confidence})" for source, confidence in steward_order], "ordered list",
            "Where a metric's steward is looked for, in order, with the confidence each carries",
            "5.1", "dpre/config.py::STEWARD_RESOLUTION_ORDER",
            ("dpre/canonicalize/grouping.py",), "", STEWARD, "code change"))
    credit = _const("GLOSSARY_STATUS_CREDIT")
    if credit:
        add(Assumption(
            "glossary_status_credit", "canonicalization", dict(credit), "credit per status",
            "How much a glossary term counts toward definition coverage by its status",
            "5.1", "dpre/config.py::GLOSSARY_STATUS_CREDIT",
            ("dpre/score/scorer.py::_definition_coverage",), "", STEWARD, "code change"))

    # ---- classification (section 7) -----------------------------------
    try:
        _classify = importlib.import_module("dpre.score.classify")
        for archetype, threshold in _classify.MATCH_THRESHOLDS.items():
            add(Assumption(
                f"archetype_threshold.{archetype}", "classification", threshold, "rule score",
                f"Rule score '{archetype}' must clear to match (first match in order wins)",
                "7.1", "dpre/score/classify.py::MATCH_THRESHOLDS",
                ("dpre/score/classify.py::classify",), "", COUNCIL,
                "code change (feedback loop lists rules overridden > 30% for rewriting)"))
        add(Assumption(
            "reference_row_ceiling", "classification",
            getattr(_classify, "REFERENCE_ROW_CEILING", None), "rows",
            "Row count below which a table can be Reference Data", "7.1",
            "dpre/score/classify.py::REFERENCE_ROW_CEILING",
            ("dpre/score/classify.py::_reference_data_score",), "", ENGINE, "code change"))
    except ImportError:  # pragma: no cover - the score package is part of the build
        pass
    add(Assumption(
        "archetype_confidence_two_choice", "classification", 0.6, "margin",
        "Below this margin the archetype is shown as a choice between the top two", "7.3",
        "dpre/score/classify.py::classify (literal)", ("dpre/labels.py::classification_margin_note",),
        "", ENGINE, "code change"))

    # ---- vocabularies ------------------------------------------------
    try:
        _reasons = importlib.import_module("dpre.governance.reasons")
        add(Assumption(
            "reason_codes", "vocabulary",
            {decision: list(codes) for decision, codes in _reasons.REASON_CODES.items()},
            "closed vocabulary", "Reason codes a reviewer may give, per decision", "10.2",
            "dpre/governance/reasons.py::REASON_CODES",
            ("dpre/governance/reasons.py::validate_reason", "dpre/store.py::Store.record_decision"),
            "", COUNCIL, "code change (tests assert every code is labelled)"))
        add(Assumption(
            "overridable_fields", "vocabulary", list(_reasons.OVERRIDABLE_FIELDS), "fields",
            "Candidate fields a reviewer may override", "10.2",
            "dpre/governance/reasons.py::OVERRIDABLE_FIELDS",
            ("dpre/governance/reasons.py::validate_override",), "", COUNCIL, "code change"))
    except ImportError:  # pragma: no cover
        pass
    try:
        _critic = importlib.import_module("dpre.narrate.critic")
        _narrator = importlib.import_module("dpre.narrate.narrator")
        add(Assumption(
            "persona_map", "vocabulary",
            {keyword: role for keyword, role in _narrator.ROLE_BY_KEYWORD}, "keyword -> role",
            "Business-unit keyword to persona used in Stage 1 drafts (first hit wins)", "9.2",
            "dpre/narrate/narrator.py::ROLE_BY_KEYWORD", ("dpre/narrate/narrator.py::persona_for",),
            "", "Named consumer", "code change; dpre/accelerators/packs.py::persona_for per industry"))
        add(Assumption(
            "stage_criteria", "vocabulary", [c for c, _ in _critic.STAGE_CRITERIA], "criteria",
            "DPF Stage 1 and 2 exit criteria the critic checks", "10.1",
            "dpre/narrate/critic.py::STAGE_CRITERIA", ("dpre/narrate/critic.py::critique",),
            "", "Data product council", "code change"))
    except ImportError:  # pragma: no cover
        pass
    try:
        _agent = importlib.import_module("dpre.chat.agent")
        add(Assumption(
            "suggested_questions", "vocabulary", list(_agent.SUGGESTED_QUESTIONS), "questions",
            "Questions the conversational surface advertises", "10.3",
            "dpre/chat/agent.py::SUGGESTED_QUESTIONS", ("dpre/server/app.py::semantic_view",),
            "", ENGINE, "code change"))
    except ImportError:  # pragma: no cover
        pass
    add(Assumption(
        "conformed_backbone_default", "resolution", list(_const("CONFORMED_BACKBONE") or []),
        "entities", "Backbone used only when the catalog shows no chain and none was declared",
        "4.3", "dpre/config.py::CONFORMED_BACKBONE",
        ("dpre/graph/grain.py::backbone_for",), "", STEWARD,
        "build_graph(backbone=...) or dpre/accelerators/packs.py::backbone_for_industry"))

    # ---- value model, by reference (R-08) -----------------------------
    if include_value:
        rows.extend(value_assumption_rows(value_assumptions))
    return rows


def value_assumption_rows(value_assumptions: Any = None) -> list[Assumption]:
    """The value rate card as register rows; the rates themselves stay in dpre/value."""
    from ..value.assumptions import ValueAssumptions
    va = value_assumptions or ValueAssumptions()
    rows = []
    for name, value in va.to_dict().items():
        if name in ("version", "basis", "approved_by", "effective_from"):
            continue
        rows.append(Assumption(
            f"value.{name}", "value", value, "rate", f"Value-model rate '{name}' ({va.basis})",
            "R-08", f"dpre/value/assumptions.py::ValueAssumptions.{name} (version {va.version})",
            ("dpre/value/model.py",), "", FINANCE,
            "dpre/value/assumptions.py::approve_assumptions"))
    return rows


def register_hash(rows: list[Assumption]) -> str:
    """Content hash over (key, value): provenance fields are not part of it."""
    material = sorted((r.key, json.dumps(r.value, sort_keys=True, default=str)) for r in rows)
    return hashlib.sha256(json.dumps(material).encode("utf-8")).hexdigest()


def config_version(config: EngineConfig | None = None, value_assumptions: Any = None) -> str:
    """A short, stable stamp for the assumptions a run ran on."""
    return "cfg-" + register_hash(assumption_register(config, value_assumptions))[:12]


def snapshot_assumptions(connection: sqlite3.Connection, run_id: str,
                         rows: list[Assumption] | None = None,
                         config: EngineConfig | None = None, snapshot_at: str = "") -> str:
    """Persist the register for a run; returns the config_version it was stamped with."""
    ensure_schema(connection)
    rows = rows if rows is not None else assumption_register(config)
    version = "cfg-" + register_hash(rows)[:12]
    connection.executemany(
        "INSERT OR REPLACE INTO ASSUMPTION_REGISTER VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(run_id, r.key, r.category, json.dumps(r.value, sort_keys=True, default=str), r.unit,
          r.spec_section, r.implemented_in, "; ".join(r.consumed_by), r.decision_ref, r.owner,
          r.contestable_via, version, snapshot_at) for r in rows])
    connection.commit()
    return version


def assumptions_for_run(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    ensure_schema(connection)
    cursor = connection.execute(
        "SELECT * FROM ASSUMPTION_REGISTER WHERE run_id = ? ORDER BY category, key", (run_id,))
    names = [c[0] for c in cursor.description]
    out = []
    for row in cursor.fetchall():
        record = dict(zip(names, row))
        record["value"] = json.loads(record["value"]) if record["value"] else None
        out.append(record)
    return out


def diff_registers(before: list[Assumption] | list[dict],
                   after: list[Assumption] | list[dict]) -> list[dict]:
    """Keys whose value differs between two registers; what changed between two runs."""
    def as_map(rows):
        out = {}
        for r in rows:
            key = r.key if isinstance(r, Assumption) else r["key"]
            value = r.value if isinstance(r, Assumption) else r["value"]
            out[key] = json.dumps(value, sort_keys=True, default=str)
        return out
    left, right = as_map(before), as_map(after)
    changes = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            changes.append({"key": key, "before": left.get(key), "after": right.get(key)})
    return changes


def render_markdown(rows: list[Assumption]) -> str:
    """The register as one Markdown table per category, for docs/assumptions-register.md."""
    lines: list[str] = []
    for category in CATEGORIES:
        subset = [r for r in rows if r.category == category]
        if not subset:
            continue
        lines.append(f"### {category.replace('_', ' ').title()}")
        lines.append("")
        lines.append("| Key | Value | Unit | Spec | Implemented in | Decision | Owner | Contestable via |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in subset:
            value = json.dumps(r.value, default=str) if isinstance(r.value, (dict, list)) else str(r.value)
            if len(value) > 60:
                value = value[:57] + "..."
            lines.append(f"| `{r.key}` | {value} | {r.unit} | {r.spec_section} | "
                         f"`{r.implemented_in}` | {r.decision_ref} | {r.owner} | {r.contestable_via} |")
        lines.append("")
    return "\n".join(lines)
