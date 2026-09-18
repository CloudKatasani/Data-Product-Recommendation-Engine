"""The agent pipeline (specification section 10).

Six chartered agents and one human gate: Ingestor, Resolver, Canonicalizer,
Clusterer, Scorer, Narrator, Critic. Each writes only its own output tables and
stamps a run id, so any recommendation can be replayed from the extracts that
produced it. No agent can move a candidate's status past Proposed.
"""
from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .canonicalize import canonicalize
from .canonicalize.grouping import CanonicalizationResult
from .cluster import generate_candidates
from .cluster.generator import ClusterResult
from .config import QUALITY_GATES, EngineConfig
from .graph import build_graph
from .ingest import IngestResult
from .models import Candidate, ExtractBundle, KnowledgeGraph, RunManifest
from .narrate.critic import critique
from .narrate.narrator import narrate
from .portfolio.views import portfolio_views
from .score import apply_classification, score_candidates
from .store import Store
from .util.ids import run_id as make_run_id

Progress = Callable[[str, str], None]


@dataclass
class RunResult:
    manifest: RunManifest
    graph: KnowledgeGraph
    canonical: CanonicalizationResult
    cluster: ClusterResult
    candidates: list[Candidate] = field(default_factory=list)
    portfolio: dict = field(default_factory=dict)
    config: EngineConfig = field(default_factory=EngineConfig)

    @property
    def run_id(self) -> str:
        return self.manifest.run_id

    def ranked(self) -> list[Candidate]:
        return sorted(self.candidates,
                      key=lambda c: -(c.score.composite if c.score else 0.0))

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "mode": self.manifest.mode,
            "industry": self.manifest.industry,
            "catalog": self.manifest.catalog,
            "as_of_date": self.manifest.as_of_date,
            "published": self.manifest.published,
            "weight_version": self.manifest.weight_version,
            "parser_version": self.manifest.parser_version,
            "stats": self.manifest.stats,
            "quality_gates": self.manifest.quality_gates,
            "warnings": self.manifest.warnings,
            "agents": self.manifest.agent_log,
        }


def run_pipeline(ingest: IngestResult, config: EngineConfig | None = None,
                 store: Store | None = None, previous_run_id: str | None = None,
                 label: str = "", progress: Progress | None = None,
                 seed_dir: str | Path | None = None) -> RunResult:
    config = config or EngineConfig()
    bundle: ExtractBundle = ingest.bundle
    as_of = bundle.as_of_date
    started = _dt.datetime.now()
    run_id = make_run_id(f"{bundle.mode}|{bundle.industry}|{bundle.generation_id}|"
                         f"{started.isoformat()}", as_of)
    agent_log: list[dict] = []

    def step(agent: str, note: str, started_at: float, rows: int = 0) -> None:
        agent_log.append({
            "agent": agent, "note": note, "rows": rows,
            "seconds": round(time.time() - started_at, 3),
        })
        if progress:
            progress(agent, note)

    # ---- Ingestor -----------------------------------------------------
    t0 = time.time()
    step("Ingestor", f"{len(bundle.reports)} reports, {len(bundle.kpis)} lineage rows, "
                     f"{len(bundle.columns)} catalog columns", t0,
         len(bundle.reports) + len(bundle.kpis) + len(bundle.columns))

    # ---- Resolver -----------------------------------------------------
    t0 = time.time()
    graph = build_graph(bundle)
    step("Resolver", f"{graph.stats['edges']} KPI-column edges, "
                     f"{graph.stats['quarantined_rows']} quarantined, "
                     f"resolution rate {graph.stats['resolution_rate']:.2%}", t0,
         graph.stats["edges"])

    # ---- Canonicalizer ------------------------------------------------
    t0 = time.time()
    canonical = canonicalize(graph, config, as_of)
    step("Canonicalizer", f"{canonical.stats['canonical_metrics']} canonical metrics from "
                          f"{canonical.stats['kpi_nodes']} KPI nodes, "
                          f"{canonical.stats['conflicts']} conflicts", t0,
         canonical.stats["canonical_metrics"])

    # ---- Clusterer ----------------------------------------------------
    t0 = time.time()
    cluster = generate_candidates(canonical, graph, config, run_id, as_of)
    step("Clusterer", f"{len(cluster.candidates)} candidates from "
                      f"{cluster.stats['communities']} communities", t0,
         len(cluster.candidates))

    # ---- Scorer -------------------------------------------------------
    t0 = time.time()
    apply_classification(cluster.candidates, canonical, graph)
    score_candidates(cluster.candidates, canonical, graph, config, as_of)
    step("Scorer", f"{len(cluster.candidates)} candidates scored on weight version "
                   f"{config.weights.weight_version}", t0, len(cluster.candidates))

    # Coverage sanity gate can re-tune the clustering resolution before publication.
    coverage, cluster, retuned = _coverage_with_retune(
        cluster, canonical, graph, config, run_id, as_of)
    warnings: list[str] = []
    if retuned:
        warnings.append(
            f"clustering resolution re-tuned to {config.cluster.resolution} because the top "
            f"{QUALITY_GATES['coverage_top_n']} candidates covered less than "
            f"{QUALITY_GATES['coverage_floor']:.0%} of usage-weighted consumption")

    # ---- Narrator -----------------------------------------------------
    t0 = time.time()
    narrate(cluster.candidates, canonical, graph)
    step("Narrator", f"{sum(len(c.decisions_drafted) for c in cluster.candidates)} "
                     "decision-register entries drafted (AI_DRAFT)", t0,
         len(cluster.candidates))

    # ---- Critic -------------------------------------------------------
    t0 = time.time()
    critique(cluster.candidates, canonical, graph, config)
    findings = sum(len(c.critique) for c in cluster.candidates)
    step("Critic", f"{findings} findings a reviewer is likely to raise", t0, findings)

    # ---- quality gates and manifest -----------------------------------
    gates = _quality_gates(ingest, graph, canonical, cluster, coverage, store, previous_run_id)
    stats = _stats(bundle, graph, canonical, cluster, coverage)
    manifest = RunManifest(
        run_id=run_id, mode=bundle.mode, industry=bundle.industry, catalog=bundle.catalog,
        as_of_date=as_of.isoformat(), started_at=started.isoformat(timespec="seconds"),
        finished_at=_dt.datetime.now().isoformat(timespec="seconds"),
        weight_version=config.weights.weight_version, parser_version=config.parser_version,
        generation_id=bundle.generation_id, synthetic=bundle.synthetic,
        extract_ids=[s.get("label") or s.get("path", "") for s in bundle.source_files],
        quality_gates=gates, stats=stats, warnings=warnings,
        published=all(g["passed"] for g in gates), agent_log=agent_log,
    )
    for issue in ingest.validation.issues:
        if issue.severity in ("error", "warning"):
            manifest.warnings.append(f"{issue.code}: {issue.message}")

    portfolio = portfolio_views(cluster.candidates, canonical, graph)
    result = RunResult(manifest=manifest, graph=graph, canonical=canonical, cluster=cluster,
                       candidates=cluster.candidates, portfolio=portfolio, config=config)

    if store is not None:
        store.save_weights(config.weights)
        store.save_run(manifest, label)
        store.save_graph(run_id, graph)
        store.save_canonicalization(run_id, canonical)
        store.save_candidates(run_id, cluster.candidates)
        if manifest.published:
            store.publish(run_id)

    if seed_dir:
        _write_seeds(result, seed_dir, bundle.catalog)
    return result


# --------------------------------------------------------------------------

def _usage_coverage(candidates: list[Candidate], canonical: CanonicalizationResult,
                    top_n: int) -> float:
    """Share of usage-weighted KPI consumption covered by the top N candidates."""
    total = sum(m.usage_weight for m in canonical.metrics.values())
    if total <= 0:
        return 0.0
    ranked = sorted(candidates, key=lambda c: -(c.score.composite if c.score else 0.0))[:top_n]
    covered: set[str] = set()
    for candidate in ranked:
        covered.update(candidate.metric_ids)
    return round(sum(canonical.metrics[m].usage_weight for m in covered if m in canonical.metrics)
                 / total, 4)


def _coverage_with_retune(cluster: ClusterResult, canonical: CanonicalizationResult,
                          graph: KnowledgeGraph, config: EngineConfig, run_id: str,
                          as_of: _dt.date) -> tuple[float, ClusterResult, bool]:
    top_n = QUALITY_GATES["coverage_top_n"]
    coverage = _usage_coverage(cluster.candidates, canonical, top_n)
    if coverage >= QUALITY_GATES["coverage_floor"]:
        return coverage, cluster, False

    best = (coverage, cluster, config.cluster.resolution)
    original = config.cluster.resolution
    for resolution in config.cluster.resolution_sweep:
        if abs(resolution - original) < 1e-9:
            continue
        config.cluster.resolution = resolution
        retried = generate_candidates(canonical, graph, config, run_id, as_of)
        apply_classification(retried.candidates, canonical, graph)
        score_candidates(retried.candidates, canonical, graph, config, as_of)
        candidate_coverage = _usage_coverage(retried.candidates, canonical, top_n)
        if candidate_coverage > best[0]:
            best = (candidate_coverage, retried, resolution)
    config.cluster.resolution = best[2]
    return best[0], best[1], best[2] != original


def _quality_gates(ingest: IngestResult, graph: KnowledgeGraph,
                   canonical: CanonicalizationResult, cluster: ClusterResult,
                   coverage: float, store: Store | None,
                   previous_run_id: str | None) -> list[dict]:
    gates: list[dict] = []

    reconciliation = ingest.validation.reconciliation
    reconciled = all(r["within_tolerance"] for r in reconciliation) if reconciliation else True
    gates.append({
        "gate": "ingest_reconciliation",
        "passed": reconciled and not ingest.validation.errors,
        "value": len([r for r in reconciliation if not r["within_tolerance"]]),
        "threshold": f"row counts within {QUALITY_GATES['ingest_reconciliation_tolerance']:.1%}",
        "detail": "row counts reconciled to the extract manifest and no ingest errors",
    })

    resolution_rate = graph.stats.get("resolution_rate", 0.0)
    gates.append({
        "gate": "resolution_rate",
        "passed": resolution_rate >= QUALITY_GATES["resolution_rate_floor"],
        "value": resolution_rate,
        "threshold": QUALITY_GATES["resolution_rate_floor"],
        "detail": "share of lineage rows resolved at 0.80 confidence or better; below the "
                  "floor the run publishes only the catalog gap list",
    })

    parse_rate = graph.stats.get("parse_rate", 0.0)
    gates.append({
        "gate": "parse_rate",
        "passed": parse_rate >= QUALITY_GATES["parse_rate_floor"],
        "value": parse_rate,
        "threshold": QUALITY_GATES["parse_rate_floor"],
        "detail": "share of expressions parsed; below the floor canonicalization is degraded "
                  "and every card is flagged",
    })

    gates.append({
        "gate": "coverage_sanity",
        "passed": coverage >= QUALITY_GATES["coverage_floor"],
        "value": coverage,
        "threshold": QUALITY_GATES["coverage_floor"],
        "detail": f"usage-weighted KPI consumption covered by the top "
                  f"{QUALITY_GATES['coverage_top_n']} candidates",
    })

    stability, detail = _stability(cluster.candidates, store, previous_run_id)
    gates.append({
        "gate": "stability",
        "passed": stability >= QUALITY_GATES["stability_floor"],
        "value": stability,
        "threshold": QUALITY_GATES["stability_floor"],
        "detail": detail,
    })
    return gates


def _stability(candidates: list[Candidate], store: Store | None,
               previous_run_id: str | None) -> tuple[float, str]:
    if store is None or not previous_run_id:
        return 1.0, "no previous run to compare against; stability not assessed"
    previous = store.candidates(previous_run_id)
    if not previous:
        return 1.0, "previous run holds no candidates; stability not assessed"
    previous_sets = [set(row["payload"].get("metric_ids", [])) for row in previous]
    matched = 0
    for row_set in previous_sets:
        best = 0.0
        for candidate in candidates:
            current = set(candidate.metric_ids)
            union = row_set | current
            if not union:
                continue
            best = max(best, len(row_set & current) / len(union))
        if best >= QUALITY_GATES["stability_jaccard"]:
            matched += 1
    rate = round(matched / len(previous_sets), 4) if previous_sets else 1.0
    return rate, (f"{matched} of {len(previous_sets)} candidates from run {previous_run_id} map "
                  f"to a candidate in this run at Jaccard >= "
                  f"{QUALITY_GATES['stability_jaccard']}")


def _stats(bundle: ExtractBundle, graph: KnowledgeGraph, canonical: CanonicalizationResult,
           cluster: ClusterResult, coverage: float) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for candidate in cluster.candidates:
        statuses[candidate.status] = statuses.get(candidate.status, 0) + 1
    return {
        "reports": len(bundle.reports),
        "kpi_rows": len(bundle.kpis),
        "catalog_columns": len(bundle.columns),
        "graph": graph.stats,
        "canonicalization": canonical.stats,
        "clustering": {k: v for k, v in cluster.stats.items() if k != "resolution_sweep"},
        "resolution_sweep": cluster.stats.get("resolution_sweep", []),
        "candidates_by_status": statuses,
        "usage_coverage_top_n": coverage,
        "data_gap_metrics": len(cluster.data_gap_metrics),
    }


def _write_seeds(result: RunResult, seed_dir: str | Path, catalog: str) -> None:
    from .seeds import write_seeds
    base = Path(seed_dir) / result.run_id
    for candidate in result.candidates:
        write_seeds(candidate, result.canonical, result.graph, base, catalog)
