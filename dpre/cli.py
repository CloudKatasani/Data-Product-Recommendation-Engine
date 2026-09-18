"""Command line interface.

Everything the browser application can do is available here too, so the engine
can run in a batch schedule as easily as in a review session.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

from .config import EngineConfig
from .ingest import SourceSpec, ingest_automated, ingest_manual, inspect_file, sources_from_workbook
from .pipeline import run_pipeline
from .review import approve_weights, feedback_report, reestimate_weights
from .review.workflow import review
from .seeds import write_seeds
from .store import Store
from .synth import INDUSTRY_KEYS, generate_all_workbooks, generate_pack, list_industries
from .synth.workbook import write_pack_workbook
from .util.jsonio import dumps, write_json


def _date(value: str | None) -> _dt.date | None:
    if not value:
        return None
    return _dt.date.fromisoformat(value)


def _print_run(result) -> None:
    summary = result.summary()
    print(f"\nRun {summary['run_id']}  ({summary['mode']}, {summary['industry'] or 'manual'}, "
          f"as of {summary['as_of_date']})")
    print(f"  weights {summary['weight_version']}  parser {summary['parser_version']}  "
          f"published {summary['published']}")
    stats = summary["stats"]
    print(f"  {stats['reports']} reports, {stats['kpi_rows']} lineage rows -> "
          f"{stats['canonicalization']['canonical_metrics']} canonical metrics, "
          f"{stats['canonicalization']['conflicts']} conflicts, "
          f"{stats['clustering']['candidates']} candidates")
    print("\n  Quality gates")
    for gate in summary["quality_gates"]:
        mark = "pass" if gate["passed"] else "FAIL"
        print(f"    [{mark}] {gate['gate']:<22} {gate['value']} (threshold {gate['threshold']})")
    print("\n  Agents")
    for entry in summary["agents"]:
        print(f"    {entry['agent']:<14} {entry['seconds']:>6.2f}s  {entry['note']}")
    print("\n  Top candidates")
    print(f"    {'composite':>9}  {'status':<12} {'archetype':<24} name")
    for candidate in result.ranked()[:12]:
        score = candidate.score.composite if candidate.score else 0.0
        print(f"    {score:>9.1f}  {candidate.status:<12} {candidate.archetype:<24} "
              f"{candidate.proposed_name}")
    if summary["warnings"]:
        print("\n  Warnings")
        for warning in summary["warnings"][:10]:
            print(f"    - {warning}")


def cmd_industries(args) -> int:
    for industry in list_industries():
        print(f"{industry['key']:<15} {industry['label']}")
        print(f"                domains: {', '.join(industry['domains'])}")
        print(f"                backbone: {' -> '.join(industry['backbone'])}")
    return 0


def cmd_generate(args) -> int:
    out = Path(args.out)
    if args.industry == "all":
        paths = generate_all_workbooks(out, as_of=_date(args.as_of), seed=args.seed)
        for path in paths:
            print(f"wrote {path}")
        return 0
    pack = generate_pack(args.industry, seed=args.seed, as_of=_date(args.as_of))
    target = out if out.suffix == ".xlsx" else out / f"synthetic_pack_{args.industry}.xlsx"
    write_pack_workbook(pack, target)
    print(f"wrote {target}")
    manifest = pack.bundle.manifest
    print(f"  seed {manifest['seed']}, generation id {manifest['generation_id']}")
    for tab, rows in pack.tabs.items():
        print(f"    {tab:<26} {len(rows):>6} rows")
    print(f"  {manifest['planted_defects']} planted defects across "
          f"{len(manifest['defect_summary'])} classes")
    return 0


def cmd_run(args) -> int:
    config = EngineConfig()
    store = Store(args.db)
    previous = store.latest_run_id()
    if args.mode == "automated":
        workbook = Path(args.workspace) / "synthetic" / f"synthetic_pack_{args.industry}.xlsx"
        ingest = ingest_automated(args.industry, seed=args.seed, as_of=_date(args.as_of),
                                  catalog=args.catalog, workbook_path=workbook)
    else:
        specs: list[SourceSpec] = []
        for raw in args.input or []:
            if ":" in raw and not Path(raw).exists():
                path, _, schema_key = raw.rpartition(":")
                specs.append(SourceSpec(path=path, schema_key=schema_key))
            else:
                specs.extend(sources_from_workbook(raw, catalog=args.catalog))
        if not specs:
            print("error: manual mode needs at least one --input FILE (or FILE:schema_key)",
                  file=sys.stderr)
            return 2
        ingest = ingest_manual(specs, as_of=_date(args.as_of), catalog_preference=args.catalog)

    for issue in ingest.validation.issues:
        print(f"  [{issue.severity}] {issue.code}: {issue.message}")
    if not ingest.ok and not args.force:
        print("\ningestion failed validation; re-run with --force to proceed anyway",
              file=sys.stderr)
        return 1

    result = run_pipeline(ingest, config=config, store=store, previous_run_id=previous,
                          label=args.label or args.mode)
    _print_run(result)

    if args.seeds:
        base = Path(args.seeds) / result.run_id
        for candidate in result.candidates:
            write_seeds(candidate, result.canonical, result.graph, base, ingest.bundle.catalog)
        print(f"\n  seeds written to {base}")
    if args.json:
        write_json(args.json, {"summary": result.summary(),
                               "candidates": [c for c in result.ranked()],
                               "portfolio": result.portfolio})
        print(f"  json written to {args.json}")
    store.close()
    return 0


def cmd_inspect(args) -> int:
    info = inspect_file(args.path)
    print(f"{info['file']}")
    for table in info["tables"]:
        print(f"  table {table['sheet'] or '(single)'}: {table['rows']} rows, "
              f"{len(table['columns'])} columns")
        print(f"    detected: {table['suggested_schema'] or 'unrecognised'} "
              f"(confidence {table['confidence']})")
        if table["missing_required"]:
            print(f"    missing required fields: {', '.join(table['missing_required'])}")
    return 0


def cmd_candidates(args) -> int:
    store = Store(args.db)
    run_id = args.run or store.latest_run_id()
    if not run_id:
        print("no runs yet", file=sys.stderr)
        return 1
    rows = store.candidates(run_id, args.status)
    rows.sort(key=lambda r: -(r["payload"].get("score", {}) or {}).get("composite", 0))
    print(f"{'composite':>9}  {'status':<12} {'archetype':<24} {'grain':<14} name")
    for row in rows:
        score = (row["payload"].get("score") or {}).get("composite", 0)
        print(f"{score:>9.1f}  {row['status']:<12} {row['archetype']:<24} "
              f"{row['grain']:<14} {row['proposed_name']}")
    store.close()
    return 0


def cmd_show(args) -> int:
    store = Store(args.db)
    run_id = args.run or store.latest_run_id()
    row = store.candidate(run_id, args.candidate_id)
    if row is None:
        print(f"candidate {args.candidate_id} not found in run {run_id}", file=sys.stderr)
        return 1
    print(dumps(row["payload"]))
    store.close()
    return 0


def cmd_review(args) -> int:
    store = Store(args.db)
    run_id = args.run or store.latest_run_id()
    outcome = review(store, run_id, args.candidate_id, args.decision, args.reviewer,
                     reason_code=args.reason or "", note=args.note or "",
                     target_candidate_id=args.target or "", split_by=args.split_by)
    print(dumps(outcome.to_dict()))
    store.close()
    return 0


def cmd_feedback(args) -> int:
    store = Store(args.db)
    config = EngineConfig()
    report = feedback_report(store, config.weights)
    print(dumps(report))
    if args.approve:
        proposal = reestimate_weights(store, config.weights)
        weights = approve_weights(store, proposal, args.approve)
        print(f"\napproved {weights.weight_version} by {args.approve}")
    store.close()
    return 0


def cmd_ask(args) -> int:
    from .chat import ConversationalAgent
    store = Store(args.db)
    agent = ConversationalAgent(store, args.run or store.latest_run_id())
    answer = agent.ask(args.question)
    print(answer.answer)
    if answer.citations:
        print("\ncitations: " + ", ".join(
            f"{c['type']}:{c['id']}" for c in answer.citations[:12]))
    print(f"\n(named query: {answer.query_used or answer.intent})")
    store.close()
    return 0


def cmd_serve(args) -> int:
    from .server.app import serve
    serve(host=args.host, port=args.port, root=args.workspace)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dpre",
        description="Data Product Recommendation Engine: turn report-level KPI lineage and "
                    "catalog metadata into a ranked, evidence-backed backlog of data product "
                    "candidates.")
    parser.add_argument("--db", default="data/engine.db", help="SQLite database of runs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("industries", help="list the industry packs")
    p.set_defaults(func=cmd_industries)

    p = subparsers.add_parser("generate", help="write a synthetic data pack workbook")
    p.add_argument("industry", choices=list(INDUSTRY_KEYS) + ["all"])
    p.add_argument("--out", default="data/synthetic")
    p.add_argument("--seed", type=int)
    p.add_argument("--as-of")
    p.set_defaults(func=cmd_generate)

    p = subparsers.add_parser("run", help="run the engine")
    p.add_argument("mode", choices=["manual", "automated"])
    p.add_argument("--industry", default="generic", choices=list(INDUSTRY_KEYS))
    p.add_argument("--input", action="append",
                   help="manual mode: a file, or FILE:schema_key to bind it explicitly")
    p.add_argument("--catalog", default="collibra", choices=["collibra", "alation"])
    p.add_argument("--as-of")
    p.add_argument("--seed", type=int)
    p.add_argument("--label", default="")
    p.add_argument("--seeds", help="directory for the DPF seed artifacts")
    p.add_argument("--json", help="write the run result to this JSON file")
    p.add_argument("--workspace", default="data")
    p.add_argument("--force", action="store_true", help="run even if ingestion validation failed")
    p.set_defaults(func=cmd_run)

    p = subparsers.add_parser("inspect", help="describe an extract file and its auto-mapping")
    p.add_argument("path")
    p.set_defaults(func=cmd_inspect)

    p = subparsers.add_parser("candidates", help="list the candidates of a run")
    p.add_argument("--run")
    p.add_argument("--status")
    p.set_defaults(func=cmd_candidates)

    p = subparsers.add_parser("show", help="print one candidate record")
    p.add_argument("candidate_id")
    p.add_argument("--run")
    p.set_defaults(func=cmd_show)

    p = subparsers.add_parser("review", help="record a reviewer decision")
    p.add_argument("candidate_id")
    p.add_argument("decision", choices=["Accept", "Reject", "Merge", "Split", "Defer", "Override"])
    p.add_argument("--reviewer", required=True)
    p.add_argument("--reason")
    p.add_argument("--note")
    p.add_argument("--target")
    p.add_argument("--split-by", default="grain", choices=["grain", "consumer"])
    p.add_argument("--run")
    p.set_defaults(func=cmd_review)

    p = subparsers.add_parser("feedback", help="show the feedback loop report")
    p.add_argument("--approve", help="approve the proposed weights as this council member")
    p.set_defaults(func=cmd_feedback)

    p = subparsers.add_parser("ask", help="ask the conversational surface a question")
    p.add_argument("question")
    p.add_argument("--run")
    p.set_defaults(func=cmd_ask)

    p = subparsers.add_parser("serve", help="start the web application")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--workspace", default="data")
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
