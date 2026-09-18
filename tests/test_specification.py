"""The build specification must describe the code that exists.

SPECIFICATION.md is written so somebody can rebuild this system from it. A
constant that has drifted since it was written makes it worse than useless: it
would be followed, and the reference bands, quality gates and calibrated tests
would then disagree with the build. Every load-bearing number in the document is
checked here against the running code.
"""
from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path

import pytest

from dpre import config as engine_config
from dpre.chat.semantic_view import describe
from dpre.cli import build_parser
from dpre.governance.reasons import DECISIONS
from dpre.ingest import SCHEMAS
from dpre.programme.effort import DRIVER_RATES, SIZE_BANDS
from dpre.programme.waves import ProgrammeConfig
from dpre.score.classify import ARCHETYPES, MATCH_THRESHOLDS
from dpre.server.security import ROLE_ACTIONS
from dpre.store import Store
from dpre.synth import INDUSTRY_KEYS
from dpre.synth.defects import DEFECT_CLASSES
from dpre.value.assumptions import ValueAssumptions

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def spec() -> str:
    path = ROOT / "SPECIFICATION.md"
    assert path.is_file(), "the build specification is part of the deliverable"
    return path.read_text(encoding="utf-8")


def test_the_scoring_weights_are_quoted_exactly(spec):
    """A wrong weight silently produces a different ranking."""
    for dimension, features in engine_config.FEATURE_WEIGHTS.items():
        for feature, weight in features.items():
            assert re.search(rf"\| {feature} \| {weight:.2f} \|", spec), \
                f"{dimension}.{feature} = {weight}"
    for dimension, weight in engine_config.DIMENSION_WEIGHTS.items():
        assert f"{abs(weight):.2f}·{dimension}" in spec.replace("− ", "−"), dimension


def test_the_thresholds_a_gate_turns_on_are_quoted_exactly(spec):
    gates = engine_config.QUALITY_GATES
    for claim in (f"≥ {gates['resolution_rate_floor']:.2f}",
                  f"≥ {gates['parse_rate_floor']:.2f}",
                  f"≥ {gates['coverage_floor']:.2f}",
                  f"≥ {gates['stability_floor']:.2f}",
                  f"Jaccard ≥ {gates['stability_jaccard']:.2f}"):
        assert claim in spec, claim
    assert f"{engine_config.GATE_MIN_USERS_PER_BU} users" in spec
    assert f"≥ {engine_config.GATE_LINEAGE_FLOOR:.2f}" in spec
    assert f"≤ {engine_config.GATE_GRAIN_AMBIGUITY_MAX:.2f}" in spec


def test_the_resolution_rules_carry_their_real_confidences(spec):
    for rule, confidence in engine_config.ER_CONFIDENCE.items():
        assert f"| {rule} |" in spec, rule
        assert f"| {confidence:.2f} |" in spec, f"{rule} {confidence}"
    assert f"ER_PROBABLE_THRESHOLD = {engine_config.ER_PROBABLE_THRESHOLD:.2f}" in spec
    assert f"≥ {engine_config.ER4_NAME_SIMILARITY}" in spec
    assert f"≥ {engine_config.ER6_EMBEDDING_SIMILARITY}" in spec
    for code in engine_config.QUARANTINE_REASONS:
        assert code in spec, code


def test_every_vocabulary_is_listed_in_full(spec):
    """A partial list reads as complete and produces a partial build."""
    for name, values in (("decisions", DECISIONS),
                         ("industries", INDUSTRY_KEYS),
                         ("defect classes", tuple(DEFECT_CLASSES)),
                         ("input schemas", tuple(SCHEMAS)),
                         ("roles", tuple(ROLE_ACTIONS))):
        missing = [v for v in values if v not in spec]
        assert not missing, f"{name}: {missing}"


def test_the_classification_table_matches_the_rules(spec):
    for archetype in ARCHETYPES:
        assert f"| {archetype} | {MATCH_THRESHOLDS[archetype]:.2f} |" in spec, archetype
    # The confidence definition is the one thing most likely to be re-derived
    # wrongly, so the document has to be unambiguous about it.
    assert "bare margin over the runner-up" in spec


def test_the_effort_and_value_models_are_reproducible_from_the_document(spec):
    for driver, rate in DRIVER_RATES.items():
        assert f"`{driver}` {rate}" in spec, driver
    assert [band[1] for band in SIZE_BANDS[:4]] == [12.0, 24.0, 45.0, 75.0]
    assert "XS ≤ 12 < S ≤ 24 < M ≤ 45 < L ≤ 75 < XL" in spec

    programme = ProgrammeConfig()
    assert f"`products_per_wave = {programme.products_per_wave}`" in spec
    assert f"`wave_length_weeks = {programme.wave_length_weeks}`" in spec
    assert f"`max_points_per_wave = {programme.max_points_per_wave}`" in spec

    value = ValueAssumptions()
    assert f"`loaded_hourly_rate = {value.loaded_hourly_rate}`" in spec
    assert f"`discount_rate = {value.discount_rate}`" in spec
    assert f"`horizon_years = {value.horizon_years}`" in spec
    assert value.version in spec


def test_the_counts_the_document_quotes_are_the_real_ones(spec):
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "spec.db")
        tables = [row[0] for row in store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        store.close()
    assert f"{len(tables)} tables" in spec

    routes = len(re.findall(r'add\("', (ROOT / "dpre/server/app.py").read_text()))
    assert f"{routes} routes" in spec

    commands = build_parser()._subparsers._group_actions[0].choices
    assert f"{len(commands)} commands" in spec
    missing = [c for c in commands if f"`{c}`" not in spec]
    assert not missing, f"commands absent from the document: {missing}"

    assert f"**{len(describe())} named queries**" in spec


def test_the_guardrails_are_stated_and_numbered(spec):
    """These define the system; a build that omits one is a different system."""
    for guardrail in ("G-1", "G-2", "G-3", "G-4", "G-5",
                      "G-6", "G-7", "G-8", "G-9", "G-10"):
        assert f"### {guardrail} " in spec, guardrail
    assert "### 8.3 Hard gates" in spec
    for gate in ("**G1**", "**G2**", "**G3**", "**G4**"):
        assert gate in spec, gate


def test_every_document_the_specification_points_at_exists(spec):
    for reference in set(re.findall(r"\]\((docs/[^)]+)\)", spec)):
        assert (ROOT / reference).is_file(), reference


def test_the_charter_it_defers_to_is_intact(spec):
    """The charter's section numbers are cited by the traceability matrix and
    by tests, so the build specification must not replace or renumber it."""
    charter = ROOT / "docs/specification.md"
    assert charter.is_file()
    assert "docs/specification.md" in spec
    assert "Do not renumber it." in spec
