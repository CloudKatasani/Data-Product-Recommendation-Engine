"""The agent pipeline (specification section 10).

Chartered agents and one human gate: Ingestor, Resolver, Canonicalizer,
Clusterer, Scorer, Narrator and Critic find the backlog; Programme prices and
sequences it; Assessor measures the run that produced it. Each writes only its
own output tables and stamps a run id, so any recommendation can be replayed
from the extracts that produced it. No agent can move a candidate's status past
Proposed.

The last two exist because a backlog is not a deliverable. A board cannot
sequence work it cannot price, and an assurance function will not accept a
ranking whose inputs, blind spots and detection rate nobody measured.

A run is not stateless. Decisions a human already took - a report marked
decision-critical, a consumer confirmed, a conflict adjudicated, a metric name
accepted - are held in run-independent ledgers and applied to this run before it
is scored, so the estate's governance survives re-running the engine
(specification sections 10.2 and 13.1).
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
from .governance import carry_forward
from .governance.identity import lineage_id
from .governance.seeding import seed_from_ledgers
from .programme import enrich_run
from .narrate.critic import critique
from .narrate.narrator import narrate
from .portfolio.views import portfolio_views
from .quality.replay import replay_record
from .quality import (bias_register, detection_scorecard, dq_scorecard, remediation_plan,
                      save_detection_scorecard, save_dq_scorecard, save_remediation_plan,
                      save_stewardship_requests, stewardship_register)
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
    programme: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
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

    # ---- reviewer memory: overrides recorded in earlier runs ----------
    overrides_applied = _apply_report_overrides(bundle, store)
    if overrides_applied:
        agent_log.append({
            "agent": "Ingestor", "note": f"{overrides_applied} report override(s) carried "
            "forward from earlier reviewer decisions", "rows": overrides_applied,
            "seconds": 0.0,
        })

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
    confirmed = _apply_consumer_confirmations(cluster.candidates, canonical, store)
    score_candidates(cluster.candidates, canonical, graph, config, as_of)
    step("Scorer", f"{len(cluster.candidates)} candidates scored on weight version "
                   f"{config.weights.weight_version}"
                   + (f"; {confirmed} with a consumer confirmed by a human" if confirmed else ""),
         t0, len(cluster.candidates))

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

    # Replayability (R-32): the manifest carried the parser and weight versions
    # but not the clustering configuration, so a run tuned with a different
    # resolution could not be told from one that was not. The snapshot and its
    # hash go on the manifest, and a stale extract is a gate rather than a
    # surprise discovered in a committee.
    replay = replay_record(bundle, config,
                           effective_resolution=cluster.stats.get("effective_resolution"),
                           as_of=as_of)
    gates.append(replay["freshness"])
    stats = _stats(bundle, graph, canonical, cluster, coverage)
    stats["replay"] = {k: v for k, v in replay.items() if k != "freshness"}
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

    # ---- Programme ----------------------------------------------------
    # Prices, sizes and sequences what the other agents found. It runs before
    # persistence so the effort, value, wave and rank range reach the candidate
    # payload rather than being recomputed on every read.
    t0 = time.time()
    result.programme = enrich_run(result, store=store)
    stats = _programme_stats(result.programme)
    step("Programme", f"{stats['candidates_sized']} candidates priced and sized, "
                      f"{stats['waves']} delivery wave(s), "
                      f"{stats['raid_entries']} RAID entries, "
                      f"{stats['currency']} {stats['annual_benefit']:,.0f} attributed "
                      "annual benefit", t0, len(cluster.candidates))
    manifest.stats["programme"] = _programme_stats(result.programme)

    # ---- Assessor -----------------------------------------------------
    # Measures the run rather than the estate: how clean the inputs were, how
    # much of what was planted the engine actually found, what the gaps would
    # take to close and who would close them, and what the ranking is blind to.
    # None of it moves a score; all of it is what an assurance function asks
    # about before trusting one.
    t0 = time.time()
    result.quality = _assess(result, ingest, store)
    q = result.quality
    step("Assessor",
         f"{q['dq']['rules']} data-quality rules on the inputs "
         f"({q['dq']['failed']} failing), "
         f"{q['remediation']['units']} remediation units, "
         f"{q['stewardship']['requests']} steward requests"
         + (f", detection recall {q['detection']['recall']:.0%}"
            if q["detection"].get("recall") is not None else ""),
         t0, q["dq"]["rules"])
    manifest.stats["quality"] = q
    for warning in q.get("warnings", []):
        manifest.warnings.append(warning)

    if store is not None:
        # One transaction: a run that persists no candidates is never published,
        # and a half-written run cannot be read as a complete one (section 13.1).
        outcome = store.persist_run(manifest, graph, canonical, cluster.candidates,
                                    label=label, weights=config.weights)
        manifest.published = bool(outcome.get("published"))
        # Re-apply what humans already decided about these metrics and conflicts.
        seeded = seed_from_ledgers(store, run_id)
        if seeded:
            manifest.stats["ledger_seeding"] = seeded
        _persist_quality(store, run_id, result)
        if previous_run_id:
            delta = carry_forward(store, run_id, previous_run_id)
            manifest.stats["run_delta"] = {
                k: v for k, v in delta.items() if k != "rows"
            } if isinstance(delta, dict) else delta

    if seed_dir:
        _write_seeds(result, seed_dir, bundle.catalog)
    return result


# --------------------------------------------------------------------------

def _assess(result: RunResult, ingest: IngestResult, store: Store | None) -> dict:
    """The Assessor's findings, as plain dicts for the manifest.

    Kept whole here rather than spread through the agents above, because the
    question it answers - can this run be trusted - is asked once, about the
    run, and is easier to argue with when the answers sit together.
    """
    from .quality.bias import bias_summary
    from .quality.detection import detection_summary
    from .quality.remediation import remediation_summary
    from .quality.stewardship import stewardship_summary

    bundle = ingest.bundle
    as_of = bundle.as_of_date

    dq_rows = dq_scorecard(bundle, as_of=as_of)
    failed = [r for r in dq_rows if r["result"] == "fail"]

    # Only a synthetic estate knows what was planted; a client estate returns an
    # empty scorecard, which is the honest answer rather than a perfect score.
    detection_rows = detection_scorecard(result, planted=list(bundle.planted_defects or []))

    previous_plan = []
    if store is not None and getattr(store, "connection", None) is not None:
        previous_run = store.previous_run_id(result.run_id)
        if previous_run:
            from .quality.remediation import load_remediation_plan
            try:
                previous_plan = load_remediation_plan(store.connection, previous_run)
            except Exception:                                  # noqa: BLE001
                previous_plan = []

    plan = remediation_plan(result, previous=previous_plan)
    requests = stewardship_register(result)

    warnings: list[str] = []
    for row in failed:
        warnings.append(f"DQ-{row['rule_id']}: {row['description']} "
                        f"({row['rows_failed']} of {row['rows_checked']} rows)")
    high = [u for u in plan if u["priority"] == "high"]
    if high:
        warnings.append(f"{len(high)} high-priority lineage gaps stand between this backlog and "
                        "a defensible business case; see the remediation plan.")

    return {
        "dq": {"rules": len(dq_rows), "failed": len(failed),
               "warned": sum(1 for r in dq_rows if r["result"] == "warn"),
               "rows": dq_rows},
        "detection": (detection_summary(detection_rows) | {"rows": detection_rows}
                      if detection_rows else {"rows": [], "recall": None,
                                              "note": "nothing planted: not a synthetic estate"}),
        "remediation": remediation_summary(plan) | {"rows": plan},
        "stewardship": stewardship_summary(requests, result) | {"rows": requests},
        "bias": bias_summary(bias_register(result)),
        "warnings": warnings,
    }


def _persist_quality(store: Store, run_id: str, result: RunResult) -> None:
    """Write the Assessor's rows. Each module owns its own schema."""
    quality = result.quality or {}
    connection = store.connection
    if quality.get("dq", {}).get("rows"):
        save_dq_scorecard(connection, run_id, quality["dq"]["rows"])
    if quality.get("detection", {}).get("rows"):
        save_detection_scorecard(connection, run_id, quality["detection"]["rows"])
    if quality.get("remediation", {}).get("rows"):
        save_remediation_plan(connection, run_id, quality["remediation"]["rows"])
    if quality.get("stewardship", {}).get("rows"):
        save_stewardship_requests(connection, run_id, quality["stewardship"]["rows"])


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


def _apply_report_overrides(bundle: ExtractBundle, store: Store | None) -> int:
    """Re-apply reviewer overrides recorded against reports in earlier runs.

    A report marked decision-critical stays decision-critical: the override lives
    in a run-independent ledger, not in the run that recorded it, which is what
    makes the section 15.1 mitigation hold across re-runs.
    """
    if store is None:
        return 0
    overrides = {row["report_id"]: row["value"]
                 for row in store.report_overrides("decision_critical")}
    if not overrides:
        return 0
    applied = 0
    for report in bundle.reports:
        value = overrides.get(report.report_id)
        if value is not None:
            report.decision_critical = str(value) in ("1", "true", "True")
            applied += 1
    return applied


def _apply_consumer_confirmations(candidates: list[Candidate],
                                  canonical: CanonicalizationResult,
                                  store: Store | None) -> int:
    """Mark candidates whose consumer a human has already confirmed.

    Gate G1 asks two things: that the data shows a real consumer, and that a
    human confirmed one. The second half is a fact about the metric set, not
    about a run, so it is matched by lineage id and survives re-clustering.
    """
    if store is None:
        return 0
    confirmed = {row["lineage_id"] for row in store.consumer_confirmations()
                 if row.get("lineage_id")}
    if not confirmed:
        return 0
    fingerprints = {metric_id: metric.fingerprint
                    for metric_id, metric in canonical.metrics.items()}
    applied = 0
    for candidate in candidates:
        identity = lineage_id([fingerprints.get(m, m) for m in candidate.metric_ids],
                              candidate.grain)
        setattr(candidate, "_lineage_id", identity)
        if identity in confirmed:
            setattr(candidate, "_consumer_confirmed", True)
            applied += 1
    return applied


def _programme_stats(programme: dict) -> dict:
    """Headline numbers from the programme layer, for the run manifest.

    The benefit reported is the attributed figure, which counts each report and
    each conflict once across the estate, not the sum of the candidates' own
    claims - those double-count wherever two candidates cover the same report.
    """
    value = programme.get("value") or {}
    portfolio = value.get("portfolio") or {}
    waves = programme.get("waves") or {}
    return {
        "candidates_sized": len(programme.get("effort") or {}),
        "waves": len(waves.get("waves") or []),
        "unscheduled": len(waves.get("unscheduled") or []),
        "raid_entries": len(programme.get("raid") or []),
        "dependencies": len(programme.get("dependencies") or []),
        "currency": value.get("currency", ""),
        "assumption_version": value.get("assumption_version", ""),
        "annual_benefit": portfolio.get("attributed_annual_benefit", 0.0),
        "double_count_removed": portfolio.get("double_count_removed", 0.0),
        "build_cost": portfolio.get("build_cost", 0.0),
        "npv_3y": portfolio.get("npv_3y", 0.0),
        "reports_counted_once": portfolio.get("reports_counted_once", 0),
    }


def _write_seeds(result: RunResult, seed_dir: str | Path, catalog: str) -> None:
    from .seeds import write_seeds
    base = Path(seed_dir) / result.run_id
    for candidate in result.candidates:
        write_seeds(candidate, result.canonical, result.graph, base, catalog)
