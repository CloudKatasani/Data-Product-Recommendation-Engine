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
from .governance import (
    audit_report, benefits_summary, realisation_view, record_benefit_event,
)
from .governance.benefits import EVENT_TYPES
from .governance.reasons import DECISIONS, REASON_CODES
from .ingest import SourceSpec, ingest_automated, ingest_manual, inspect_file, sources_from_workbook
from .pipeline import run_pipeline
from .programme.effort import load_efforts
from .programme.raid import load_raid
from .programme.status import status_report
from .programme.waves import load_waves
from .review import approve_weights, feedback_report, reestimate_weights
from .review.workflow import review
from .seeds import write_seeds
from .store import Store
from .value.model import load_values
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
    quality = summary.get("stats", {}).get("quality") or {}
    if quality:
        detection = quality.get("detection") or {}
        print("\n  Assessment of this run")
        print(f"    {quality['dq']['rules']} data-quality rules on the inputs: "
              f"{quality['dq']['failed']} failing, {quality['dq']['warned']} warning")
        if detection.get("recall") is not None:
            print(f"    detection recall {detection['recall']:.0%} "
                  f"({detection['detected']} of {detection['planted']} planted defects found)")
            if detection.get("classes_missed"):
                print("    classes missed entirely: "
                      + ", ".join(detection["classes_missed"]))
        else:
            print("    detection not measurable: nothing was planted in this estate")
        rem = quality["remediation"]
        print(f"    {rem['units']} remediation units "
              f"({rem['by_priority'].get('high', 0)} high priority), "
              f"{quality['stewardship']['requests']} metrics awaiting a steward")

    programme = summary.get("stats", {}).get("programme") or {}
    if programme:
        print("\n  Programme")
        print(f"    {programme['candidates_sized']} candidates sized into "
              f"{programme['waves']} wave(s), {programme['unscheduled']} unscheduled, "
              f"{programme['raid_entries']} RAID entries")
        print(f"    {programme['currency']} {programme['annual_benefit']:,.0f} attributed "
              f"annual benefit against {programme['currency']} "
              f"{programme['build_cost']:,.0f} to build "
              f"(assumptions {programme['assumption_version']})")
        print(f"    {programme['currency']} {programme['double_count_removed']:,.0f} removed as "
              f"double counting across {programme['reports_counted_once']} reports")

    print("\n  Top candidates")
    print(f"    {'composite':>9} {'rank':>7}  {'status':<12} {'size':<5} {'wave':>4} "
          f"{'benefit':>12}  name")
    for candidate in result.ranked()[:12]:
        score = candidate.score.composite if candidate.score else 0.0
        effort = getattr(candidate, "_effort", {}) or {}
        value = getattr(candidate, "_value", {}) or {}
        spread = getattr(candidate, "_rank_range", {}) or {}
        # A rank that moves under a weight perturbation is a rank a reviewer
        # should not lean on, so the spread is printed beside the score.
        band = f"{spread.get('rank_low', '-')}-{spread.get('rank_high', '-')}" \
            if spread else "-"
        wave = getattr(candidate, "_wave", None)
        print(f"    {score:>9.1f} {band:>7}  {candidate.status:<12} "
              f"{effort.get('size', ''):<5} {(wave if wave else '-'):>4} "
              f"{value.get('attributed_annual_benefit', 0):>12,.0f}  "
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
    store = Store(args.db)
    # The council approves a weight vector; the code default is only the seed
    # for the very first run. Scoring under anything else would make the run
    # unreproducible against the ledger (R-03).
    config = EngineConfig(weights=store.current_weights())
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
    if args.pack:
        # The pack is cut from the run that produced it, in the same process,
        # so the numbers on the cover and the numbers in the store cannot drift.
        from .export import write_pack
        from .export.engagement import Engagement
        engagement = Engagement(
            client=args.client or "Client",
            engagement=args.engagement or "Data product rationalisation",
            reference=args.engagement_ref or "",
            prepared_for=args.prepared_for or "Data product council",
            prepared_by=args.prepared_by or "Data product engagement team",
            partner=args.partner or "", manager=args.manager or "")
        paths = write_pack(result, args.pack, store=store, engagement=engagement,
                           enrichment=result.programme)
        print(f"\n  executive pack written to {Path(args.pack) / result.run_id} "
              f"({len(paths)} files)")
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
    run_id = _run_or_latest(store, args.run)
    outcome = review(store, run_id, args.candidate_id, args.decision, args.reviewer,
                     reason_code=args.reason or "", note=args.note or "",
                     target_candidate_id=args.target or "",
                     field_overridden=args.field or "", new_value=args.value or "",
                     split_by=args.split_by, actor_role=args.role,
                     gate_waived=args.gate_waived or "",
                     waiver_reason=args.waiver_reason or "",
                     second_approver=args.second_approver or "")
    print(dumps(outcome.to_dict()))
    store.close()
    return 0


def cmd_reasons(args) -> int:
    """Print the closed vocabulary a decision may cite, so nobody guesses."""
    for decision in DECISIONS:
        codes = REASON_CODES.get(decision, ())
        print(f"  {decision}")
        for code in codes:
            print(f"    {code}")
    return 0


def cmd_feedback(args) -> int:
    store = Store(args.db)
    config = EngineConfig(weights=store.current_weights())
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


# --------------------------------------------------------------------------
# Programme and governance commands
#
# The review session is where a decision is taken, but the questions a partner
# is asked between sessions - where are we against the measures, what ships in
# wave 1, what is on the risk log, what is this worth, can the chain be trusted
# - have to be answerable without a browser. These read the governed tables
# only; none of them can move a candidate's status.
# --------------------------------------------------------------------------

def _run_or_latest(store: Store, run: str | None) -> str:
    run_id = run or store.latest_run_id()
    if not run_id:
        raise SystemExit("no runs yet: start with 'dpre run --mode automated --industry retail'")
    return run_id


_RAG = {"green": "GREEN", "amber": "AMBER", "red": "RED", "grey": "n/a"}


def cmd_status(args) -> int:
    """Section 14.2 measures, decision throughput and the blocked queue."""
    store = Store(args.db)
    run_id = _run_or_latest(store, args.run)
    previous = args.previous or store.previous_run_id(run_id)
    report = status_report(store, run_id, previous, as_of=args.as_of)
    if args.json:
        write_json(args.json, report)
        print(f"json written to {args.json}")
        store.close()
        return 0

    run = report["run"]
    print(f"\nStatus for run {run_id}  ({run['mode']}, {run['industry'] or 'manual'}, "
          f"{'published' if run['published'] else 'unpublished'})")
    if previous:
        print(f"  compared with {previous}")
    print("\n  Success measures (specification 14.2)")
    print(f"    {'':<6} {'actual':>12}  {'target':>12}  measure")
    for measure in report["measures"]:
        print(f"    {_RAG.get(measure['rag'], measure['rag']):<6} "
              f"{_fmt(measure['actual'], measure['unit']):>12}  "
              f"{_fmt(measure['target'], measure['unit']):>12}  {measure['measure']}")
        if measure["note"]:
            print(f"           {measure['note']}")

    decisions = report["decisions"]
    print(f"\n  Decisions since {decisions['since']}: {decisions['total']}")
    for name, count in sorted(decisions["by_type"].items()):
        print(f"    {name:<22} {count}")
    if decisions["carried_forward"]:
        print(f"    (plus {decisions['carried_forward']} statuses carried forward "
              "from the previous run - nobody decided those this period)")

    queues = report["queues"]
    print("\n  Queues")
    for status, count in sorted(queues["by_status"].items()):
        print(f"    {status:<22} {count}")
    for name in ("blocked", "deferred", "exploratory"):
        for entry in queues[name][:args.limit]:
            print(f"    [{name}] {entry.get('proposed_name', entry.get('candidate_id', ''))}"
                  f"  - {entry.get('reason') or entry.get('why') or ''}")

    ageing = report.get("backlog_ageing") or {}
    if ageing:
        print("\n  Backlog ageing")
        for key, value in ageing.items():
            print(f"    {key:<28} {value}")
    exits = report.get("phase_exit") or {}
    if exits:
        print("\n  Phase exit criteria (specification 14.3)")
        for entry in (exits if isinstance(exits, list) else exits.get("criteria", [])):
            mark = "met" if entry.get("met") else "not met"
            print(f"    [{mark:<7}] {entry.get('criterion', entry.get('key', ''))}")
    store.close()
    return 0


def _fmt(value, unit: str) -> str:
    if value is None:
        return "-"
    if unit == "share":
        return f"{float(value) * 100:.0f}%"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}"


def _names(store: Store, run_id: str) -> dict[str, str]:
    """Candidate ids read like hashes; a wave plan has to name what it ships."""
    return {row["candidate_id"]: row["proposed_name"] for row in store.candidates(run_id)}


def cmd_waves(args) -> int:
    """The delivery sequence: what ships when, and what cannot be scheduled."""
    store = Store(args.db)
    run_id = _run_or_latest(store, args.run)
    plan = load_waves(store.connection, run_id)
    efforts = {row["candidate_id"]: row for row in load_efforts(store.connection, run_id)}
    names = _names(store, run_id)
    if not plan["waves"]:
        print(f"run {run_id} has no wave plan: re-run the pipeline to build one",
              file=sys.stderr)
        store.close()
        return 1

    current = None
    for row in plan["waves"]:
        if row["wave"] != current:
            current = row["wave"]
            weeks = f"weeks {row['starts_week']}-{row['ends_week']}" \
                if row.get("ends_week") else ""
            print(f"\n  Wave {current}   {weeks}")
            print(f"    {'weeks':>6} {'benefit':>14}  {'size':<6} candidate")
        effort = efforts.get(row["candidate_id"], {})
        print(f"    {effort.get('total_weeks') or 0:>6.1f} "
              f"{row.get('annual_benefit') or 0:>14,.0f}  "
              f"{row.get('size') or effort.get('size') or '':<6} "
              f"{names.get(row['candidate_id'], row['candidate_id'])}")
        if args.why and row.get("rationale"):
            print(f"           {row['rationale']}")
    if plan["unscheduled"]:
        print("\n  Unscheduled")
        for row in plan["unscheduled"]:
            print(f"    {names.get(row['candidate_id'], row['candidate_id'])}  "
                  f"- {row.get('reason') or row.get('rationale') or ''}")
    store.close()
    return 0


def cmd_raid(args) -> int:
    """Risks, assumptions, issues and dependencies, as a steering pack expects."""
    store = Store(args.db)
    run_id = _run_or_latest(store, args.run)
    rows = load_raid(store.connection, run_id, args.type)
    if args.severity:
        rows = [row for row in rows if row["severity"] == args.severity]
    # Highest severity first: a steering pack does not open on a low assumption.
    order = {"high": 0, "medium": 1, "low": 2}
    rows.sort(key=lambda r: (order.get(r["severity"], 3), r["type"], r["title"]))
    if args.csv:
        import csv as _csv
        target = Path(args.csv)
        target.parent.mkdir(parents=True, exist_ok=True)
        fields = ["raid_id", "type", "title", "severity", "owner_role", "due_hint", "status",
                  "candidate_ids", "evidence_ids", "detail", "source"]
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = _csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            # The id lists come back decoded; a spreadsheet wants one cell, not
            # a Python repr, so they are joined the way raid_csv_rows joins them.
            writer.writerows([dict(row, candidate_ids=";".join(row["candidate_ids"]),
                                   evidence_ids=";".join(row["evidence_ids"]))
                              for row in rows])
        print(f"{len(rows)} RAID entries written to {target}")
        store.close()
        return 0
    print(f"\n  RAID log for run {run_id} ({len(rows)} entries)")
    print(f"    {'type':<12} {'severity':<10} {'owner':<22} title")
    for row in rows[:args.limit]:
        print(f"    {row['type']:<12} {row['severity']:<10} {row['owner_role']:<22} "
              f"{row['title']}")
        if args.detail and row.get("detail"):
            print(f"      {row['detail']}")
    store.close()
    return 0


def cmd_value(args) -> int:
    """What the backlog is worth, with the assumption version behind the number."""
    store = Store(args.db)
    run_id = _run_or_latest(store, args.run)
    rows = load_values(store.connection, run_id)
    if not rows:
        print(f"run {run_id} has no value model: re-run the pipeline", file=sys.stderr)
        store.close()
        return 1
    rows.sort(key=lambda r: -(r.get("attributed_annual_benefit") or 0))
    names = _names(store, run_id)
    gross = sum(r.get("gross_annual_benefit") or 0 for r in rows)
    attributed = sum(r.get("attributed_annual_benefit") or 0 for r in rows)
    currency = rows[0].get("currency") or ""
    version = rows[0].get("assumption_version") or ""
    print(f"\n  Value model for run {run_id}  ({currency}, assumptions {version})")
    print(f"    {'benefit':>14} {'build cost':>14} {'payback':>9} {'NPV 3y':>14}  candidate")
    for row in rows[:args.limit]:
        payback = row.get("payback_months")
        print(f"    {row.get('attributed_annual_benefit') or 0:>14,.0f} "
              f"{row.get('build_cost') or 0:>14,.0f} "
              f"{(f'{payback:.0f}m' if payback else '-'):>9} "
              f"{row.get('npv_3y') or 0:>14,.0f}  "
              f"{names.get(row['candidate_id'], row['candidate_id'])}")
    print(f"\n    gross claims {gross:,.0f}; after attribution {attributed:,.0f}. A report "
          "retired once is a saving once, however many candidates would retire it.")
    store.close()
    return 0


def cmd_assess(args) -> int:
    """What the run's own inputs and blind spots look like, from the store."""
    store = Store(args.db)
    run_id = _run_or_latest(store, args.run)
    from .quality.detection import load_detection_scorecard
    from .quality.remediation import load_remediation_plan, write_remediation_plan_csv
    from .quality.stewardship import load_stewardship_requests

    detection = load_detection_scorecard(store.connection, run_id)
    plan = load_remediation_plan(store.connection, run_id)
    requests = load_stewardship_requests(store.connection, run_id)

    if args.csv:
        path = write_remediation_plan_csv(plan, args.csv)
        print(f"{len(plan)} remediation units written to {path}")
        store.close()
        return 0

    print(f"\n  Assessment of run {run_id}")
    if detection:
        planted = sum(r["planted"] for r in detection)
        detected = sum(r["detected"] for r in detection)
        print(f"\n  Detection against what was planted "
              f"({detected} of {planted}, {detected / planted:.0%})")
        print(f"    {'class':<24} {'found':>7}  how it is recognised")
        for row in detection:
            print(f"    {row['defect_class']:<24} "
                  f"{row['detected']:>3}/{row['planted']:<3}  {row['method'][:60]}")
            if row["missed"] and args.detail:
                print(f"           missed: {', '.join(row['missed'])}")
    else:
        print("\n  Nothing was planted in this estate, so detection is not measurable here.")

    if plan:
        print(f"\n  Remediation plan ({len(plan)} units)")
        print(f"    {'rank':>4} {'priority':<8} {'owner':<22} {'runs':>8}  what to fix")
        for unit in plan[:args.limit]:
            print(f"    {unit['rank']:>4} {unit['priority']:<8} {unit['owner_role']:<22} "
                  f"{unit['usage_at_stake']:>8,.0f}  "
                  f"{(unit.get('label') or unit['subject'])[:48]}")
            if args.detail:
                print(f"           {unit['action']}")
    if requests:
        print(f"\n  Metrics awaiting a steward ({len(requests)})")
        for row in requests[:args.limit]:
            print(f"    {row['canonical_name'][:40]:<40} {row['state']:<11} "
                  f"{row['suggested_steward'] or '-'}")
        print("\n    A report owner is accountable for a report, not for a definition. "
              "Every row above is a question for a domain owner, not an assignment.")

    from .quality.bias import bias_register
    if args.bias:
        print("\n  What this ranking is blind to")
        for entry in bias_register():
            print(f"    {entry['id']} {entry['name']} ({entry['severity']}, "
                  f"{entry['direction']})")
            print(f"           {entry['affects']}")
            print(f"           mitigation: {entry['mitigation']}")
    store.close()
    return 0


def cmd_audit(args) -> int:
    """Verify the hash chain and print what an audit function would ask for."""
    store = Store(args.db)
    run_id = args.run or None
    report = audit_report(store, run_id)
    if args.json:
        write_json(args.json, report)
        print(f"json written to {args.json}")
        store.close()
        return 0
    chain = report["chain"]
    print(f"\n  Audit of {run_id or 'every run'}  (schema {report['schema_version']})")
    print(f"    hash chain          {'intact' if chain['ok'] else 'BROKEN'} "
          f"over {chain['rows']} decision rows")
    if chain.get("first_break"):
        print(f"    first break at      {chain['first_break']}")
    if chain.get("orphans"):
        print(f"    statuses with no decision behind them: {len(chain['orphans'])}")
        for orphan in chain["orphans"][:args.limit]:
            print(f"      {orphan['run_id']} {orphan['candidate_id']} -> {orphan['status']}")
    weights = report["weights_in_force"]
    approval = f"approved by {weights['approved_by']}" if weights.get("approved_by") \
        else "NOT APPROVED - council sign-off outstanding"
    print(f"    weights in force    {weights['weight_version']} ({approval})")
    if report["open_waivers"]:
        print(f"    open gate waivers   {len(report['open_waivers'])}")
        for waiver in report["open_waivers"][:args.limit]:
            print(f"      {waiver['candidate_id']} waived {waiver['gate_waived']} "
                  f"- {waiver['waiver_reason']}")
    if report["config_changes"]:
        print(f"    config changes      {len(report['config_changes'])}")
    for finding in report["findings"]:
        print(f"    finding: {finding}")
    print(f"\n    overall: {'clean' if report['ok'] else 'exceptions outstanding'}")
    store.close()
    return 0 if report["ok"] or args.report_only else 1


def cmd_weights(args) -> int:
    """List weight versions, or record a council approval of one."""
    store = Store(args.db)
    if args.approve:
        outcome = store.approve_weight_version(args.approve, args.approver,
                                               note=args.note or "")
        print(f"weight version {outcome['weight_version']} approved by {args.approver}")
        store.close()
        return 0
    current = store.current_weights()
    print(f"\n  Weight versions (in force: {current.weight_version})")
    print(f"    {'version':<20} {'approved by':<24} note")
    for row in store.weight_versions():
        print(f"    {row['weight_version']:<20} "
              f"{(row.get('approved_by') or '(pending)'):<24} {row.get('note') or ''}")
    store.close()
    return 0


def cmd_confirm(args) -> int:
    """Record the named consumer that lets a candidate leave Exploratory (G1).

    This is the human half of the first gate: a business unit, the decision the
    data blocks, how fresh it has to be and what happens without it. The record
    is keyed by lineage id, so it survives into the next run even when the
    candidate id changes.
    """
    store = Store(args.db)
    run_id = _run_or_latest(store, args.run)
    row = store.candidate(run_id, args.candidate_id)
    if row is None:
        print(f"candidate {args.candidate_id} not found in run {run_id}", file=sys.stderr)
        store.close()
        return 1
    identity = (row["payload"] or {}).get("lineage_id") or row.get("lineage_id") or ""
    if not identity:
        print("this candidate carries no lineage id: re-run the pipeline", file=sys.stderr)
        store.close()
        return 1
    outcome = store.record_consumer_confirmation(
        identity, run_id, args.candidate_id, args.business_unit, args.blocked_decision,
        args.latency, args.consequence, args.confirmed_by, note=args.note or "")
    print(f"consumer confirmed for {args.candidate_id} (lineage {identity[:12]}) "
          f"by {args.confirmed_by}")
    print(dumps(outcome))
    store.close()
    return 0


def cmd_benefits(args) -> int:
    """Planned against realised: the promise at Accept, and what has landed.

    Benefit is tracked in the units a client can verify - reports actually
    retired, conflicts actually adjudicated, the charter actually approved -
    not in a money figure nobody can audit. The money follows from these counts
    through the assumption register.
    """
    store = Store(args.db)
    if args.record:
        outcome = record_benefit_event(store, args.lineage, args.record, args.subject,
                                       args.occurred_at, args.confirmed_by,
                                       note=args.note or "")
        print(dumps(outcome))
        store.close()
        return 0
    if args.lineage:
        print(dumps(benefits_summary(store, args.lineage)))
        store.close()
        return 0
    rows = realisation_view(store)
    if not rows:
        print("nothing accepted yet: a benefit plan is written when a candidate is Accepted")
        store.close()
        return 0
    names = {row["lineage_id"]: row["proposed_name"] for row in store.query(
        "SELECT p.lineage_id, c.proposed_name FROM BENEFIT_PLAN p "
        "JOIN DP_CANDIDATE c ON c.run_id = p.run_id AND c.candidate_id = p.candidate_id")}
    print(f"\n  Benefit realisation ({len(rows)} accepted)")
    print(f"    {'reports':>18} {'conflicts':>18} {'charter':<10} candidate")
    for row in rows[:args.limit]:
        planned, realised = row["planned"], row["realised"]
        charter = "approved" if realised.get("charter_approved") else "open"
        print(f"    {realised['reports_retired']:>8}/{planned['reports_retired']:<9} "
              f"{realised['conflicts_resolved']:>8}/{planned['conflicts_resolved']:<9} "
              f"{charter:<10} "
              f"{names.get(row['lineage_id'], row['lineage_id'])}")
    print("\n    realised/planned. Record what lands with "
          "'dpre benefits --lineage LIN-... --record report_retired --subject COG-00123'.")
    store.close()
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
    p.add_argument("--pack", help="write the executive pack, backlog workbook and dossiers here")
    p.add_argument("--client", help="client name for the pack cover")
    p.add_argument("--engagement", help="engagement name for the pack cover")
    p.add_argument("--engagement-ref", dest="engagement_ref", help="engagement reference code")
    p.add_argument("--prepared-for", dest="prepared_for", help="the committee the pack is tabled at")
    p.add_argument("--prepared-by", dest="prepared_by")
    p.add_argument("--partner", help="engagement partner named on the cover")
    p.add_argument("--manager", help="engagement manager named on the cover")
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
    p.add_argument("decision", choices=list(DECISIONS))
    p.add_argument("--reviewer", required=True)
    p.add_argument("--reason", help="reason code; 'dpre reasons' lists the vocabulary")
    p.add_argument("--note")
    p.add_argument("--target", help="the surviving candidate, for Merge")
    p.add_argument("--field", help="the field an Override changes")
    p.add_argument("--value", help="the value an Override sets")
    p.add_argument("--role", default="reviewer",
                   choices=["reviewer", "steward", "council", "operator"])
    p.add_argument("--gate-waived", dest="gate_waived",
                   help="the gate an AcceptWithException waives (G1..G5)")
    p.add_argument("--waiver-reason", dest="waiver_reason",
                   help="why the gate may be waived, for the exception register")
    p.add_argument("--second-approver", dest="second_approver",
                   help="the second signature an exception needs")
    p.add_argument("--split-by", default="grain", choices=["grain", "consumer"])
    p.add_argument("--run")
    p.set_defaults(func=cmd_review)

    p = subparsers.add_parser("reasons", help="list the decision and reason-code vocabulary")
    p.set_defaults(func=cmd_reasons)

    p = subparsers.add_parser("feedback", help="show the feedback loop report")
    p.add_argument("--approve", help="approve the proposed weights as this council member")
    p.set_defaults(func=cmd_feedback)

    p = subparsers.add_parser("ask", help="ask the conversational surface a question")
    p.add_argument("question")
    p.add_argument("--run")
    p.set_defaults(func=cmd_ask)

    p = subparsers.add_parser("status", help="programme status against the 14.2 measures")
    p.add_argument("--run")
    p.add_argument("--previous", help="run id to compare against (default: the run before)")
    p.add_argument("--as-of", dest="as_of", help="treat this timestamp as now")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", help="write the full report to this file instead of printing")
    p.set_defaults(func=cmd_status)

    p = subparsers.add_parser("waves", help="the delivery sequence for a run")
    p.add_argument("--run")
    p.add_argument("--why", action="store_true", help="print why each item sits in its wave")
    p.set_defaults(func=cmd_waves)

    p = subparsers.add_parser("raid", help="risks, assumptions, issues and dependencies")
    p.add_argument("--run")
    p.add_argument("--type", choices=["Risk", "Assumption", "Issue", "Dependency"])
    p.add_argument("--severity", choices=["high", "medium", "low"])
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--detail", action="store_true", help="print the detail line too")
    p.add_argument("--csv", help="write the log to this CSV file instead of printing")
    p.set_defaults(func=cmd_raid)

    p = subparsers.add_parser("value", help="the value model behind the backlog")
    p.add_argument("--run")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_value)

    p = subparsers.add_parser("assess",
                              help="what the run's inputs, blind spots and gaps look like")
    p.add_argument("--run")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--detail", action="store_true", help="print the action and the misses")
    p.add_argument("--bias", action="store_true", help="print the bias register too")
    p.add_argument("--csv", help="write the remediation plan to this CSV instead of printing")
    p.set_defaults(func=cmd_assess)

    p = subparsers.add_parser("audit", help="verify the decision chain and list exceptions")
    p.add_argument("--run", help="one run (default: every run)")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", help="write the full report to this file instead of printing")
    p.add_argument("--report-only", action="store_true",
                   help="always exit 0, even with exceptions outstanding")
    p.set_defaults(func=cmd_audit)

    p = subparsers.add_parser("weights", help="list weight versions or approve one")
    p.add_argument("--approve", help="the weight version to approve")
    p.add_argument("--approver", default="", help="the council member approving it")
    p.add_argument("--note")
    p.set_defaults(func=cmd_weights)

    p = subparsers.add_parser("confirm",
                              help="record the named consumer that clears gate G1")
    p.add_argument("candidate_id")
    p.add_argument("--run")
    p.add_argument("--business-unit", required=True, dest="business_unit")
    p.add_argument("--blocked-decision", required=True, dest="blocked_decision",
                   help="the decision this data unblocks")
    p.add_argument("--latency", required=True,
                   help="how fresh it has to be, in the consumer's words")
    p.add_argument("--consequence", required=True,
                   help="what happens if the data does not arrive")
    p.add_argument("--confirmed-by", required=True, dest="confirmed_by")
    p.add_argument("--note")
    p.set_defaults(func=cmd_confirm)

    p = subparsers.add_parser("benefits", help="planned against realised benefit")
    p.add_argument("--lineage", help="one candidate lineage id")
    p.add_argument("--record", choices=list(EVENT_TYPES),
                   help="record a realisation event of this type")
    p.add_argument("--subject", default="", help="the report, charter or conflict realised")
    p.add_argument("--occurred-at", dest="occurred_at", default="",
                   help="when it happened (ISO 8601)")
    p.add_argument("--confirmed-by", dest="confirmed_by", default="")
    p.add_argument("--note")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_benefits)

    p = subparsers.add_parser("serve", help="start the web application")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--workspace", default="data")
    p.set_defaults(func=cmd_serve)
    return parser


#: Refusals the engine makes on purpose. A reviewer who asks for something the
#: charter forbids should read the sentence that explains it, not a traceback.
REFUSALS = ("GateError", "TransitionError", "ReasonCodeError", "OverrideValueError",
            "ProposeOnlyError", "EvidenceMissingError", "PublishError", "WeightVersionError",
            "RunExistsError", "ConflictStatusError")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except Exception as exc:                                  # noqa: BLE001
        if type(exc).__name__ not in REFUSALS:
            raise
        print(f"refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
