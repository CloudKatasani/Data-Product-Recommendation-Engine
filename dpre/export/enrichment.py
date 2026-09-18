"""A defensive view over the optional programme enrichment (review finding R-12).

``dpre.programme.enrich_run`` returns value, effort, dependencies, waves, RAID,
sensitivity, benchmark and stakeholders for a run. The pack must build whether
or not that enrichment was computed, whether or not a given key is present, and
whether or not a future version renames something: an engagement pack that
raises a KeyError on a partial enrichment is worse than one that prints a
smaller pack and says which part is missing.

Every accessor here therefore returns an empty structure rather than raising,
and ``missing()`` names what the pack left out, so the reader is told rather
than left to wonder.
"""
from __future__ import annotations

from typing import Any

# The parts a full pack expects, with the words used when one is absent.
PARTS = {
    "value": "value model (benefit, build cost, payback)",
    "effort": "effort estimates",
    "waves": "wave plan",
    "raid": "RAID log",
    "status": "status against the 14.2 success measures",
    "benchmark": "estate benchmark",
    "stakeholders": "stakeholder map",
    "sensitivity": "rank sensitivity",
    "dependencies": "candidate dependencies",
}


class EnrichmentView:
    """Read-only, never-raising view over the enrichment dict."""

    def __init__(self, enrichment: Any = None) -> None:
        self._data: dict[str, Any] = enrichment if isinstance(enrichment, dict) else {}

    def __bool__(self) -> bool:
        return bool(self._data)

    def has(self, key: str) -> bool:
        return bool(self._data.get(key))

    def missing(self) -> list[str]:
        """Human words for the parts that were not supplied, in a stable order."""
        return [words for key, words in PARTS.items() if not self.has(key)]

    # -- value ------------------------------------------------------------
    @property
    def value(self) -> dict[str, Any]:
        return _as_dict(self._data.get("value"))

    @property
    def value_portfolio(self) -> dict[str, Any]:
        return _as_dict(self.value.get("portfolio"))

    @property
    def currency(self) -> str:
        return str(self.value.get("currency", "") or "")

    @property
    def assumption_version(self) -> str:
        return str(self.value.get("assumption_version", "") or "")

    @property
    def value_basis(self) -> str:
        return str(self.value.get("basis", "") or "")

    def value_for(self, candidate_id: str) -> dict[str, Any]:
        return _as_dict(_as_dict(self.value.get("candidates")).get(candidate_id))

    # -- effort, waves, sensitivity ---------------------------------------
    def effort_for(self, candidate_id: str) -> dict[str, Any]:
        return _as_dict(_as_dict(self._data.get("effort")).get(candidate_id))

    @property
    def waves(self) -> list[dict[str, Any]]:
        return _as_list(_as_dict(self._data.get("waves")).get("waves"))

    @property
    def unscheduled(self) -> list[dict[str, Any]]:
        return _as_list(_as_dict(self._data.get("waves")).get("unscheduled"))

    @property
    def wave_config(self) -> dict[str, Any]:
        return _as_dict(_as_dict(self._data.get("waves")).get("config"))

    def wave_of(self, candidate_id: str) -> Any:
        return _as_dict(_as_dict(self._data.get("waves")).get("wave_of")).get(candidate_id)

    @property
    def quadrants(self) -> dict[str, Any]:
        return _as_dict(_as_dict(self._data.get("waves")).get("quadrants"))

    def sensitivity_for(self, candidate_id: str) -> dict[str, Any]:
        return _as_dict(_as_dict(self._data.get("sensitivity")).get("candidates")).get(
            candidate_id) or {}

    # -- raid, status, benchmark, stakeholders, dependencies --------------
    @property
    def raid(self) -> list[dict[str, Any]]:
        return _as_list(self._data.get("raid"))

    def raid_of_type(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.raid if str(r.get("type", "")).lower() == kind.lower()]

    def raid_for(self, candidate_id: str) -> list[dict[str, Any]]:
        return [r for r in self.raid if candidate_id in _as_list(r.get("candidate_ids"))]

    @property
    def status(self) -> dict[str, Any]:
        return _as_dict(self._data.get("status"))

    @property
    def measures(self) -> list[dict[str, Any]]:
        return _as_list(self.status.get("measures"))

    @property
    def benchmark(self) -> dict[str, Any]:
        return _as_dict(self._data.get("benchmark"))

    @property
    def stakeholders(self) -> list[dict[str, Any]]:
        return _as_list(_as_dict(self._data.get("stakeholders")).get("people"))

    @property
    def dependencies(self) -> list[dict[str, Any]]:
        return _as_list(self._data.get("dependencies"))

    def dependencies_for(self, candidate_id: str) -> list[dict[str, Any]]:
        return [d for d in self.dependencies if d.get("candidate_id") == candidate_id]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []
