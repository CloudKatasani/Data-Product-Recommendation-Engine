"""Shared fixtures. The tests run on the synthetic pack, which is deterministic."""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dpre.canonicalize import canonicalize            # noqa: E402
from dpre.cluster import generate_candidates          # noqa: E402
from dpre.config import EngineConfig                  # noqa: E402
from dpre.graph import build_graph                    # noqa: E402
from dpre.ingest import ingest_automated              # noqa: E402
from dpre.narrate.critic import critique              # noqa: E402
from dpre.narrate.narrator import narrate             # noqa: E402
from dpre.pipeline import run_pipeline                # noqa: E402
from dpre.score import apply_classification, score_candidates  # noqa: E402
from dpre.store import Store                          # noqa: E402
from dpre.synth import generate_pack                  # noqa: E402

AS_OF = _dt.date(2026, 9, 17)


@pytest.fixture(scope="session")
def as_of() -> _dt.date:
    return AS_OF


@pytest.fixture(scope="session")
def pack():
    return generate_pack("utility", as_of=AS_OF)


@pytest.fixture(scope="session")
def ingest():
    return ingest_automated("utility", as_of=AS_OF)


@pytest.fixture(scope="session")
def graph(ingest):
    return build_graph(ingest.bundle)


@pytest.fixture(scope="session")
def canonical(graph):
    return canonicalize(graph, EngineConfig(), AS_OF)


@pytest.fixture(scope="session")
def clustered(canonical, graph):
    config = EngineConfig()
    result = generate_candidates(canonical, graph, config, run_id="TEST-RUN", as_of=AS_OF)
    apply_classification(result.candidates, canonical, graph)
    score_candidates(result.candidates, canonical, graph, config, AS_OF)
    narrate(result.candidates, canonical, graph)
    critique(result.candidates, canonical, graph, config)
    return result


@pytest.fixture(scope="session")
def run(tmp_path_factory, ingest):
    store = Store(tmp_path_factory.mktemp("store") / "engine.db")
    result = run_pipeline(ingest, store=store, label="test")
    yield result, store
    store.close()
