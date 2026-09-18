"""What a replay needs and the manifest did not hold (review finding R-32).

Section 14.1 says every recommendation is reproducible from stored extract ids,
weight version and parser version. That was true only while nobody touched the
configuration: the clustering resolution, the similarity floor, the size
floors, the usage window and half-life, the Keep rule and the AI switches were
not in the manifest, the run's "as of" was the wall-clock day, extract ids were
bare file names and nothing checked whether a catalog export was nine months
old.

This module produces the missing pieces as plain data for the manifest:

* ``config_snapshot`` and ``config_hash``: the whole knob set that scored the
  run, including the resolution actually used after any retune;
* ``file_digests``: SHA-256 of every landed file, so "which files" has an
  answer that survives a rename;
* ``input_dates`` and ``freshness_gate``: an extract date per input (declared,
  else file modification time with a warning), the inter-input drift, and a
  three-state freshness gate with configurable warn and fail ages;
* ``rows_after_as_of``: rows dated in the future relative to the as-of date,
  which the DQ scorecard reports and a reviewer should treat as suspect.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any

from ..config import EngineConfig
from ..models import ExtractBundle

FRESHNESS_WARN_DAYS = 30
FRESHNESS_FAIL_DAYS = 90


def config_snapshot(config: EngineConfig, effective_resolution: float | None = None) -> dict:
    """Every knob that can change a ranking, in one JSON-serialisable dict."""
    cluster = config.cluster
    return {
        "weights": config.weights.to_dict(),
        "cluster": {
            "min_similarity": cluster.min_similarity,
            "same_domain_bonus": cluster.same_domain_bonus,
            "resolution": cluster.resolution,
            "effective_resolution": (cluster.resolution if effective_resolution is None
                                     else effective_resolution),
            "resolution_sweep": list(cluster.resolution_sweep),
            "min_metrics": cluster.min_metrics,
            "min_business_units": cluster.min_business_units,
            "entity_master_min_communities": cluster.entity_master_min_communities,
            "composite_min_candidates": cluster.composite_min_candidates,
            "composite_min_users": cluster.composite_min_users,
            "grain_split_min_metrics": cluster.grain_split_min_metrics,
            "random_seed": cluster.random_seed,
        },
        "usage_window_months": config.usage_window_months,
        "recency_half_life_months": config.recency_half_life_months,
        "keep_counts_toward_consolidation": config.keep_counts_toward_consolidation,
        "sensitivity_review_threshold": config.sensitivity_review_threshold,
        "min_community_size": config.min_community_size,
        "parser_version": config.parser_version,
        "engine_version": config.engine_version,
        "ai_enabled": config.ai_enabled,
        "ai_model": config.ai_model,
    }


def config_hash(snapshot: dict) -> str:
    material = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def file_digests(source_files: list[dict]) -> list[dict]:
    """SHA-256 of every landed file that still exists, keyed by its label."""
    out = []
    for entry in source_files:
        path = entry.get("path", "")
        label = entry.get("label") or Path(path).name if path else entry.get("label", "")
        item = {"label": label, "path": path, "schema_key": entry.get("schema_key", ""),
                "rows": entry.get("rows", 0), "sha256": "", "bytes": 0}
        try:
            if path and Path(path).is_file():
                digest = hashlib.sha256()
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
                item["sha256"] = digest.hexdigest()
                item["bytes"] = Path(path).stat().st_size
        except OSError:
            pass
        out.append(item)
    return out


def input_dates(bundle: ExtractBundle, as_of: _dt.date | None = None) -> list[dict]:
    """An extract date per input, with its provenance.

    A declared ``extract_date`` on the source entry wins; otherwise the file's
    modification time stands in, flagged as an assumption; a generated pack is
    dated at its as-of date. The bundle's as-of should be the latest of these.
    """
    as_of = as_of or bundle.as_of_date
    out: list[dict] = []
    if not bundle.source_files:
        out.append({"label": f"synthetic:{bundle.industry}" if bundle.synthetic else "bundle",
                    "extract_date": as_of.isoformat(), "source": "as-of date",
                    "assumed": False})
        return out
    for entry in bundle.source_files:
        label = entry.get("label") or Path(entry.get("path", "")).name
        declared = entry.get("extract_date")
        if declared:
            out.append({"label": label, "extract_date": str(declared)[:10],
                        "source": "declared on the source", "assumed": False})
            continue
        path = entry.get("path", "")
        try:
            if path and Path(path).is_file():
                mtime = _dt.datetime.fromtimestamp(Path(path).stat().st_mtime).date()
                out.append({"label": label, "extract_date": mtime.isoformat(),
                            "source": "file modification time", "assumed": True})
                continue
        except OSError:
            pass
        out.append({"label": label, "extract_date": as_of.isoformat(),
                    "source": "as-of date (no extract date available)", "assumed": True})
    return out


def freshness_gate(dates: list[dict], as_of: _dt.date, warn_days: int = FRESHNESS_WARN_DAYS,
                   fail_days: int = FRESHNESS_FAIL_DAYS) -> dict:
    """Three-state freshness gate over the per-input extract dates.

    The oldest input decides; the spread between inputs is reported because
    lineage taken in March against a catalog taken in June is the usual cause
    of orphaned report ids.
    """
    parsed = []
    for item in dates:
        try:
            parsed.append((item["label"], _dt.date.fromisoformat(item["extract_date"]),
                           item.get("assumed", False)))
        except (KeyError, ValueError):
            continue
    if not parsed:
        return {"gate": "freshness", "passed": True, "assessed": False, "state": "not_assessed",
                "value": None, "threshold": f"warn > {warn_days} days, fail > {fail_days} days",
                "detail": "no extract dates available; freshness not assessed"}
    ages = [(label, (as_of - date).days, assumed) for label, date, assumed in parsed]
    oldest = max(ages, key=lambda a: a[1])
    spread = max(a[1] for a in ages) - min(a[1] for a in ages)
    all_assumed = all(a[2] for a in ages)
    if oldest[1] > fail_days:
        state, passed = "fail", False
    elif oldest[1] > warn_days:
        state, passed = "warn", True
    else:
        state, passed = "pass", True
    detail = (f"oldest input {oldest[0]} is {oldest[1]} days old; inputs span {spread} days")
    if all_assumed:
        detail += "; extract dates were assumed from file times or the as-of date"
    if any(a[1] < 0 for a in ages):
        detail += "; an input is dated after the as-of date"
    return {"gate": "freshness", "passed": passed, "assessed": True, "state": state,
            "value": oldest[1], "threshold": f"warn > {warn_days} days, fail > {fail_days} days",
            "oldest_input": oldest[0], "spread_days": spread, "inputs": [
                {"label": label, "age_days": age, "assumed": assumed} for label, age, assumed in ages],
            "detail": detail}


def rows_after_as_of(bundle: ExtractBundle, as_of: _dt.date | None = None) -> list[str]:
    """Report ids whose last run is dated after the as-of date (a suspect extract)."""
    as_of = as_of or bundle.as_of_date
    return sorted(r.report_id for r in bundle.reports
                  if r.last_run_date and r.last_run_date > as_of)


def replay_record(bundle: ExtractBundle, config: EngineConfig,
                  effective_resolution: float | None = None,
                  as_of: _dt.date | None = None) -> dict[str, Any]:
    """Everything the manifest should carry for section 14.1, in one dict."""
    snapshot = config_snapshot(config, effective_resolution)
    dates = input_dates(bundle, as_of)
    return {
        "config_snapshot": snapshot,
        "config_hash": config_hash(snapshot),
        "effective_resolution": snapshot["cluster"]["effective_resolution"],
        "files": file_digests(bundle.source_files),
        "input_dates": dates,
        "freshness": freshness_gate(dates, as_of or bundle.as_of_date),
        "rows_after_as_of": rows_after_as_of(bundle, as_of),
        "generation_id": bundle.generation_id,
        "parser_version": config.parser_version,
        "engine_version": config.engine_version,
        "weight_version": config.weights.weight_version,
    }
