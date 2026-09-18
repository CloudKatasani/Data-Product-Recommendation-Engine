"""Data Product Recommendation Engine.

Converts report-level KPI lineage and catalog metadata into a ranked,
evidence-backed backlog of data product candidates. Humans approve; the engine
only proposes.
"""
from .config import ENGINE_VERSION, PARSER_VERSION, EngineConfig, ScoreWeights

__version__ = ENGINE_VERSION
__all__ = ["EngineConfig", "ScoreWeights", "ENGINE_VERSION", "PARSER_VERSION", "__version__"]
