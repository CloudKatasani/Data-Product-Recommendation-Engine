"""Programme layer: effort, dependencies, waves, RAID, status (review findings R-10,
R-11, R-13, R-39). ``enrich_run`` is the one call the pipeline makes after scoring;
it returns everything a partner needs before the first client conversation and
persists it to the programme tables when a store is given.
"""
from __future__ import annotations

from typing import Any

from .dependencies import DependencyRow, build_dependencies, save_dependencies
from .effort import EffortEstimate, effort_for_run, estimate_effort, save_efforts
from .raid import RaidRow, build_raid, save_raid
from .status import status_report
from .waves import ProgrammeConfig, plan_waves, save_waves

__all__ = [
    "enrich_run", "ProgrammeConfig", "EffortEstimate", "estimate_effort", "effort_for_run",
    "DependencyRow", "build_dependencies", "plan_waves", "RaidRow", "build_raid",
    "status_report",
]


def enrich_run(result: Any, store: Any = None, assumptions: Any = None,
               programme_config: ProgrammeConfig | None = None) -> dict[str, Any]:
    """Value, effort, dependencies, waves, RAID, sensitivity, benchmark, stakeholders.

    Pure function of the RunResult plus the rate card and capacity config; the
    store is only written to, never read, so the result is the same with or
    without one. Persisting uses the programme tables' own ensure_schema, so it
    works before integration registers them.
    """
    from ..portfolio.benchmark import benchmark_run, save_benchmark
    from ..portfolio.stakeholders import stakeholder_map
    from ..score.sensitivity import rank_sensitivity, save_sensitivity
    from ..value.assumptions import ValueAssumptions, save_assumptions
    from ..value.model import save_values, value_for_run, value_sentence

    assumptions = assumptions or ValueAssumptions()
    programme_config = programme_config or ProgrammeConfig()

    efforts = effort_for_run(result)
    value = value_for_run(result, assumptions, efforts)
    dependencies = build_dependencies(result)
    waves = plan_waves(result, efforts, value["candidates"], programme_config, dependencies)
    raid = build_raid(result, dependencies, assumptions.version)
    sensitivity = rank_sensitivity(result.candidates, result.config.weights)
    benchmark = benchmark_run(result)
    stakeholders = stakeholder_map(result, store)

    # Carry the headline numbers onto each candidate so cards and seeds can show
    # them without re-running the models. Attributes only; no status is touched.
    for candidate in result.candidates:
        cid = candidate.candidate_id
        setattr(candidate, "_effort", efforts[cid].to_dict())
        setattr(candidate, "_value", value["candidates"][cid])
        setattr(candidate, "_wave", waves["wave_of"].get(cid))
        setattr(candidate, "_rank_range", sensitivity["candidates"].get(cid))
        candidate.narrative["value_hypothesis_quantified"] = value_sentence(
            _as_value(value["candidates"][cid]))

    if store is not None:
        connection = store.connection
        run_id = result.manifest.run_id
        save_assumptions(connection, assumptions)
        save_values(connection, run_id, value["candidates"])
        save_efforts(connection, run_id, efforts)
        save_dependencies(connection, run_id, dependencies)
        save_waves(connection, run_id, waves)
        save_raid(connection, run_id, raid)
        save_sensitivity(connection, run_id, sensitivity)
        save_benchmark(connection, run_id, benchmark)

    return {
        "value": value,
        "effort": {cid: e.to_dict() for cid, e in efforts.items()},
        "dependencies": [d.to_dict() for d in dependencies],
        "waves": waves,
        "raid": [r.to_dict() for r in raid],
        "sensitivity": sensitivity,
        "benchmark": benchmark,
        "stakeholders": stakeholders,
    }


def _as_value(payload: dict):
    from ..value.model import CandidateValue, ValueComponent
    from ..models import EvidenceRow
    return CandidateValue(
        candidate_id=payload["candidate_id"], assumption_version=payload["assumption_version"],
        currency=payload["currency"],
        components=[ValueComponent(**c) for c in payload["components"]],
        gross_annual_benefit=payload["gross_annual_benefit"],
        attributed_annual_benefit=payload["attributed_annual_benefit"],
        build_cost=payload["build_cost"], build_basis=payload["build_basis"],
        payback_months=payload["payback_months"], npv_3y=payload["npv_3y"],
        evidence=[EvidenceRow(**e) for e in payload["evidence"]], basis=payload["basis"],
        model_version=payload["model_version"],
    )
