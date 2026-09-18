"""Traceability to specification section 14: exit criteria and falsifiers (R-40).

``docs/implementation-map.md`` points section 14.1 at ``tests/test_acceptance.py``
and stops. The phases have exit criteria and falsifiers too, and a partner
asked "how will we know Phase 1 can exit, and where is that captured" needs a
row per criterion: the feature that implements it, the test that proves the
feature, the table the evidence lands in, and an honest flag saying whether
the criterion is provable in code or only in the field.

Two criteria had no capture mechanism at all. A steward could resolve a
conflict but not reject a canonical grouping, so the Phase 1 falsifier
("stewards reject more than 20% of groupings in a 30-metric sample") could
not be measured. This module adds ``GROUPING_VERDICT``, a ledger of accept
or reject per canonical metric with the steward and a reason, and computes
the rejection rate from it. The Phase 2 falsifier (metric set changed by more
than 40% at Stage 2) is computed from ``DP_CANDIDATE_PAYLOAD_HISTORY``, which
Merge and Split already write. The Phase 2 exit "usable without rework" is
already captured on ``REVIEW_DECISION`` and summarised by
``dpre/review/feedback.py::usable_without_rework_rate``; it is reported here
per run.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

PROVABLE_IN_CODE = "provable in code"
PARTLY = "partly in code, completed in the field"
FIELD_ONLY = "only in the field"

VERDICTS = ("accept", "reject")

SCHEMA = """
CREATE TABLE IF NOT EXISTS GROUPING_VERDICT (
    verdict_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    metric_id TEXT NOT NULL, fingerprint TEXT, verdict TEXT NOT NULL, steward TEXT NOT NULL,
    reason TEXT, decided_at TEXT NOT NULL, sample_id TEXT
);
CREATE TRIGGER IF NOT EXISTS TRG_GROUPING_VERDICT_NO_UPDATE BEFORE UPDATE ON GROUPING_VERDICT
BEGIN SELECT RAISE(ABORT, 'GROUPING_VERDICT is append-only'); END;
CREATE TRIGGER IF NOT EXISTS TRG_GROUPING_VERDICT_NO_DELETE BEFORE DELETE ON GROUPING_VERDICT
BEGIN SELECT RAISE(ABORT, 'GROUPING_VERDICT is append-only'); END;
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass(frozen=True)
class Criterion:
    ref: str
    phase: str                  # "1" | "2" | "3" | "14.1"
    kind: str                   # exit | falsifier | acceptance
    criterion: str
    threshold: str
    feature: str                # path::name that implements or captures it
    tests: tuple[str, ...]
    evidence: str               # table or artefact
    provable: str               # PROVABLE_IN_CODE | PARTLY | FIELD_ONLY
    metric_key: str = ""        # key in phase_metrics(), when computable
    note: str = ""

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["tests"] = list(self.tests)
        return payload


CRITERIA: tuple[Criterion, ...] = (
    # ---- Phase 1 ------------------------------------------------------
    Criterion("P1-E1", "1", "exit", ">= 80% lineage resolution", ">= 0.80",
              "dpre/pipeline.py::_quality_gates (resolution_rate)",
              ("tests/test_graph.py::test_graph_meets_the_run_quality_gates",
               "tests/test_acceptance.py::test_run_quality_gates_are_all_evaluated"),
              "RUN.quality_gates", PROVABLE_IN_CODE, "resolution_rate",
              "Proven on the synthetic pack; the client's figure comes from their extract."),
    Criterion("P1-E2", "1", "exit", ">= 70% parse rate", ">= 0.70",
              "dpre/pipeline.py::_quality_gates (parse_rate)",
              ("tests/test_graph.py::test_graph_meets_the_run_quality_gates",
               "tests/test_acceptance.py::test_run_quality_gates_are_all_evaluated"),
              "RUN.quality_gates", PROVABLE_IN_CODE, "parse_rate",
              "Proven on the synthetic pack; opaque expressions in the field lower it."),
    Criterion("P1-E3", "1", "exit",
              "Domain steward signs off the conflict register as correct for a 30-metric sample",
              "30 metrics adjudicated", "dpre/store.py::resolve_conflict; "
              "dpre/registers/traceability.py::record_grouping_verdict",
              ("tests/test_acceptance.py::test_the_conflict_register_is_complete_enough_to_sign_off",
               "tests/test_governance.py::test_conflict_adjudication_is_validated_and_ledgered",
               "tests/test_registers.py::test_grouping_verdicts_compute_the_phase_1_falsifier"),
              "CONFLICT_DECISION; GROUPING_VERDICT", PARTLY, "grouping_sample_size",
              "The register's completeness is tested; the sign-off is a steward's act recorded "
              "per conflict and per grouping."),
    Criterion("P1-F1", "1", "falsifier",
              "Stewards reject > 20% of the canonical groupings in the sample", "<= 0.20",
              "dpre/registers/traceability.py::grouping_rejection_rate",
              ("tests/test_registers.py::test_grouping_verdicts_compute_the_phase_1_falsifier",),
              "GROUPING_VERDICT", PARTLY, "grouping_rejection_rate",
              "Capture exists as a function; the HTTP route is wiring_needed."),
    # ---- Phase 2 ------------------------------------------------------
    Criterion("P2-E1", "2", "exit",
              "Top 20 candidates cover >= 50% of usage-weighted consumption", ">= 0.50",
              "dpre/pipeline.py::_usage_coverage; _quality_gates (coverage_sanity)",
              ("tests/test_acceptance.py::test_top_twenty_candidates_cover_half_of_usage",),
              "RUN.stats.usage_coverage_top_n", PROVABLE_IN_CODE, "usage_coverage_top_20"),
    Criterion("P2-E2", "2", "exit", "2 candidates Accepted and opened in the DPF with seeds",
              ">= 2 Accepted", "dpre/review/workflow.py::review; dpre/seeds/__init__.py::write_seeds",
              ("tests/test_workflow.py::test_seed_artifacts_cover_the_documented_stages",
               "tests/test_programme.py::test_status_report_computes_the_14_2_measures_with_rag"),
              "REVIEW_DECISION; seeds folder", PARTLY, "accepted_candidates",
              "Acceptance and seeds are in code; 'opened in the DPF' is a field fact."),
    Criterion("P2-E3", "2", "exit", "Reviewers rate >= 70% of cards usable without rework",
              ">= 0.70", "dpre/review/feedback.py::usable_without_rework_rate "
              "(REVIEW_DECISION.usable_without_rework, rework_needed)",
              ("tests/test_governance.py::test_reason_codes_are_validated_server_side",
               "tests/test_workflow.py::test_the_feedback_report_covers_all_three_re_estimations"),
              "REVIEW_DECISION.usable_without_rework", PARTLY, "usable_without_rework_rate",
              "Captured on Accept; the rating itself is the reviewer's."),
    Criterion("P2-F1", "2", "falsifier",
              "Accepted candidates need their metric set changed by > 40% at DPF Stage 2",
              "<= 0.40", "dpre/registers/traceability.py::stage2_metric_drift "
              "(DP_CANDIDATE_PAYLOAD_HISTORY written by Merge and Split)",
              ("tests/test_registers.py::test_stage_2_metric_drift_is_measured_from_payload_history",),
              "DP_CANDIDATE_PAYLOAD_HISTORY", PARTLY, "stage2_metric_drift",
              "Merge/Split before acceptance are measured; changes made inside the DPF after "
              "hand-over are not visible to the engine."),
    # ---- Phase 3 ------------------------------------------------------
    Criterion("P3-E1", "3", "exit", "Full-estate run in < 30 minutes", "< 1800 s",
              "dpre/pipeline.py::run_pipeline (agent_log seconds)",
              ("tests/test_pipeline.py::test_a_full_estate_run_is_quick",),
              "RUN.agent_log", PARTLY, "run_seconds",
              "Tested at < 60 s on ~240 reports; the client's estate sets the real figure."),
    Criterion("P3-E2", "3", "exit", "Conversational answers cite evidence on 20 test questions",
              "20 of 20 cited", "dpre/registers/traceability.py::QUESTION_BANK; citation_rate",
              ("tests/test_acceptance.py::test_the_agent_answers_the_specifications_own_questions",
               "tests/test_registers.py::test_the_question_bank_is_answered_with_citations"),
              "chat answers (citations)", PARTLY, "citation_rate",
              "A 20-question bank is provided; the client's own 20 replace it."),
    Criterion("P3-E3", "3", "exit", "First weight re-estimation approved by the council",
              "1 approved version after v1.0-initial",
              "dpre/review/feedback.py::reestimate_weights; approve_weights",
              ("tests/test_governance.py::test_approval_needs_the_floor_and_a_hold_out_win",
               "tests/test_workflow.py::test_weights_are_re_estimated_once_there_are_enough_decisions"),
              "WEIGHT_APPROVAL; SCORE_WEIGHT", PARTLY, "approved_weight_versions"),
    Criterion("P3-F1", "3", "falsifier",
              "Reviewer decisions too few (< 50) to re-estimate weights", ">= 50 decisions",
              "dpre/review/feedback.py::MIN_DECISIONS",
              ("tests/test_workflow.py::test_weights_are_not_re_estimated_from_too_few_decisions",),
              "REVIEW_DECISION", PROVABLE_IN_CODE, "final_decisions",
              "Below the floor the feedback report says the loop is designed but unproven."),
    # ---- 14.1 acceptance criteria -------------------------------------
    Criterion("A-01", "14.1", "acceptance", "Every recommendation is reproducible from stored "
              "extract IDs, weight version and parser version", "equal rankings",
              "dpre/pipeline.py::run_pipeline",
              ("tests/test_acceptance.py::test_a_run_is_reproducible_from_its_stored_inputs",),
              "RUN", PROVABLE_IN_CODE),
    Criterion("A-02", "14.1", "acceptance", "No code path other than REVIEW_DECISION moves a "
              "candidate past Proposed", "ProposeOnlyError", "dpre/store.py::save_candidates",
              ("tests/test_acceptance.py::test_no_code_path_but_a_review_decision_moves_a_candidate_past_proposed",),
              "DP_CANDIDATE; REVIEW_DECISION", PROVABLE_IN_CODE),
    Criterion("A-03", "14.1", "acceptance", "Every score row has evidence rows; the inverse "
              "cannot be written", "EvidenceMissingError", "dpre/store.py::save_candidates",
              ("tests/test_acceptance.py::test_a_score_row_without_evidence_cannot_be_written",
               "tests/test_acceptance.py::test_published_runs_have_evidence_behind_every_score"),
              "DP_CANDIDATE_EVIDENCE", PROVABLE_IN_CODE),
    Criterion("A-04", "14.1", "acceptance", "The conversational agent cannot query outside its "
              "semantic view", "grant audit", "dpre/chat/semantic_view.py::check_query",
              ("tests/test_acceptance.py::test_the_conversational_agent_cannot_query_outside_its_semantic_view",),
              "semantic_view.QUERIES", PARTLY,
              note="Offline the boundary is a blocklist on one connection; in Snowflake it is a "
                   "GRANT on the view (docs/snowflake-cortex-migration.md)."),
    Criterion("A-05", "14.1", "acceptance", "AI-drafted names and definitions are visibly "
              "marked until accepted, in the card, the seeds and the catalog payload", "AI_DRAFT",
              "dpre/seeds/catalog_payload.py::catalog_payload",
              ("tests/test_acceptance.py::test_ai_drafted_names_are_visibly_marked_everywhere",),
              "KPI_CANONICAL.name_status", PROVABLE_IN_CODE),
    Criterion("A-06", "14.1", "acceptance", "A seeded Stage 1 decision register passes the DPF "
              "exit criteria once the blocked decision is added", "three TO BE CONFIRMED fields",
              "dpre/seeds/decision_register.py",
              ("tests/test_acceptance.py::test_a_seeded_decision_register_needs_only_the_blocked_decision",),
              "stage1-decision-register.yaml", PARTLY,
              note="The DPF's own exit check is the client's; the seed's shape is tested."),
    Criterion("A-07", "14.1", "acceptance", "The conflict register for the pilot domain is "
              "signed off by the steward", "every conflict adjudicated",
              "dpre/store.py::resolve_conflict",
              ("tests/test_acceptance.py::test_the_conflict_register_is_complete_enough_to_sign_off",),
              "CONFLICT_DECISION", PARTLY, "open_conflicts",
              "Sign-off is a field act; the ledger records each one."),
)

# Twenty questions across the intents the agent answers today, for P3-E2. A
# client replaces them with its own twenty; the rate is computed the same way.
QUESTION_BANK: tuple[str, ...] = (
    "which candidates retire the most Finance reports",
    "which candidates retire the most Collections reports",
    "show the competing definitions of days past due",
    "show the competing definitions of days sales outstanding",
    "what would block the top candidate at Stage 9",
    "what is blocked and why",
    "who consumes the top candidate",
    "explain the demand score of the top candidate",
    "where is the lineage broken",
    "which reports have not run in a year",
    "rank the candidates by composite score",
    "explain the consolidation score of the top candidate",
    "explain the feasibility score of the top candidate",
    "show the metrics in the top candidate",
    "how much usage do the top 20 candidates cover",
    "which candidates retire the most Customer Operations reports",
    "show the competing definitions of write off",
    "what would block the second candidate at Stage 9",
    "who consumes the second candidate",
    "which reports are retirable by the top candidate",
)


def traceability_matrix() -> list[dict]:
    return [c.to_dict() for c in CRITERIA]


# --------------------------------------------------------------------------
# Capture: grouping verdicts (Phase 1 falsifier)
# --------------------------------------------------------------------------

class VerdictError(ValueError):
    """Raised for a verdict outside the vocabulary or without a steward."""


def record_grouping_verdict(connection: sqlite3.Connection, run_id: str, metric_id: str,
                            verdict: str, steward: str, reason: str = "",
                            fingerprint: str = "", sample_id: str = "",
                            decided_at: str | None = None) -> dict:
    """A steward's verdict on one canonical grouping: accept or reject, with a reason."""
    verdict = (verdict or "").strip().lower()
    if verdict not in VERDICTS:
        raise VerdictError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not (steward or "").strip():
        raise VerdictError("a grouping verdict must name the steward")
    if verdict == "reject" and not (reason or "").strip():
        raise VerdictError("rejecting a grouping needs a reason the engine team can act on")
    ensure_schema(connection)
    at = decided_at or _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    cursor = connection.execute(
        "INSERT INTO GROUPING_VERDICT (run_id, metric_id, fingerprint, verdict, steward, reason, "
        "decided_at, sample_id) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, metric_id, fingerprint, verdict, steward.strip(), reason.strip(), at, sample_id))
    connection.commit()
    return {"verdict_id": cursor.lastrowid, "run_id": run_id, "metric_id": metric_id,
            "verdict": verdict, "steward": steward.strip(), "reason": reason.strip(),
            "decided_at": at, "sample_id": sample_id}


def grouping_verdicts(connection: sqlite3.Connection, run_id: str) -> list[dict]:
    ensure_schema(connection)
    cursor = connection.execute(
        "SELECT * FROM GROUPING_VERDICT WHERE run_id = ? ORDER BY verdict_id", (run_id,))
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def grouping_rejection_rate(connection: sqlite3.Connection, run_id: str,
                            threshold: float = 0.20, sample_target: int = 30) -> dict:
    """Latest verdict per metric; share rejected against the Phase 1 falsifier."""
    latest: dict[str, str] = {}
    for row in grouping_verdicts(connection, run_id):
        latest[row["metric_id"]] = row["verdict"]
    sampled = len(latest)
    rejected = sum(1 for v in latest.values() if v == "reject")
    rate = round(rejected / sampled, 4) if sampled else None
    return {"sampled": sampled, "sample_target": sample_target, "rejected": rejected,
            "rate": rate, "threshold": threshold,
            "sample_complete": sampled >= sample_target,
            "falsified": (rate > threshold) if rate is not None else None}


# --------------------------------------------------------------------------
# Computation: Stage 2 metric drift (Phase 2 falsifier)
# --------------------------------------------------------------------------

def stage2_metric_drift(store: Any, run_id: str, threshold: float = 0.40) -> dict:
    """Jaccard drift between each Accepted candidate's first and current metric set.

    ``DP_CANDIDATE_PAYLOAD_HISTORY`` holds the payload before every Merge, Split
    or Override touched it; the first row is the engine's proposal and the
    live ``DP_CANDIDATE_METRIC`` rows are the set the reviewer accepted.
    """
    accepted = store.query(
        "SELECT candidate_id FROM DP_CANDIDATE WHERE run_id = ? AND status = 'Accepted'",
        (run_id,))
    per_candidate = []
    for row in accepted:
        candidate_id = row["candidate_id"]
        history = store.query(
            "SELECT payload FROM DP_CANDIDATE_PAYLOAD_HISTORY WHERE run_id = ? AND "
            "candidate_id = ? ORDER BY history_id LIMIT 1", (run_id, candidate_id))
        current = {r["metric_id"] for r in store.query(
            "SELECT metric_id FROM DP_CANDIDATE_METRIC WHERE run_id = ? AND candidate_id = ?",
            (run_id, candidate_id))}
        if history:
            payload = history[0]["payload"]
            payload = json.loads(payload) if isinstance(payload, str) else payload
            original = set(payload.get("metric_ids") or [])
        else:
            original = set(current)
        union = original | current
        jaccard = len(original & current) / len(union) if union else 1.0
        per_candidate.append({"candidate_id": candidate_id, "original_metrics": len(original),
                              "current_metrics": len(current), "drift": round(1.0 - jaccard, 4)})
    drifted = [c for c in per_candidate if c["drift"] > threshold]
    share = round(len(drifted) / len(per_candidate), 4) if per_candidate else None
    return {"accepted": len(per_candidate), "drifted_over_threshold": len(drifted),
            "share_drifted": share, "threshold": threshold, "candidates": per_candidate,
            "falsified": (share > 0.0 and len(drifted) > len(per_candidate) / 2)
            if per_candidate else None}


# --------------------------------------------------------------------------
# Computation: citation rate on the question bank (Phase 3 exit)
# --------------------------------------------------------------------------

def citation_rate(store: Any, run_id: str, questions: tuple[str, ...] = QUESTION_BANK) -> dict:
    from ..chat import ConversationalAgent
    agent = ConversationalAgent(store, run_id)
    cited = 0
    uncited: list[dict] = []
    for question in questions:
        answer = agent.ask(question)
        if answer.citations:
            cited += 1
        else:
            uncited.append({"question": question, "intent": answer.intent})
    return {"asked": len(questions), "cited": cited,
            "rate": round(cited / len(questions), 4) if questions else None,
            "target": 1.0, "uncited": uncited}


# --------------------------------------------------------------------------
# All computable criteria for one run
# --------------------------------------------------------------------------

def phase_metrics(store: Any, run_id: str, ask_questions: bool = False) -> dict:
    """Every criterion the store can measure for a run, with threshold and verdict.

    ``ask_questions`` runs the twenty-question bank through the agent, which
    costs a few hundred milliseconds; off by default for the status report.
    """
    run_rows = store.query("SELECT * FROM RUN WHERE run_id = ?", (run_id,))
    if not run_rows:
        raise KeyError(f"run {run_id} not found")
    run = run_rows[0]
    gates = run.get("quality_gates") or []
    gates = json.loads(gates) if isinstance(gates, str) else gates
    by_gate = {g["gate"]: g for g in gates}
    stats = run.get("stats") or {}
    stats = json.loads(stats) if isinstance(stats, str) else stats
    agent_log = run.get("agent_log") or []
    agent_log = json.loads(agent_log) if isinstance(agent_log, str) else agent_log

    accepted = store.query(
        "SELECT COUNT(*) AS n FROM DP_CANDIDATE WHERE run_id = ? AND status = 'Accepted'",
        (run_id,))[0]["n"]
    final = store.query(
        "SELECT COUNT(DISTINCT candidate_id) AS n FROM REVIEW_DECISION WHERE run_id = ? AND "
        "decision IN ('Accept', 'AcceptWithException', 'Reject')", (run_id,))[0]["n"]
    rated = store.query(
        "SELECT usable_without_rework AS u FROM REVIEW_DECISION WHERE run_id = ? AND "
        "new_status = 'Accepted' AND usable_without_rework IS NOT NULL", (run_id,))
    usable = sum(1 for r in rated if r["u"])
    open_conflicts = store.query(
        "SELECT COUNT(*) AS n FROM KPI_CONFLICT WHERE run_id = ? AND resolution_status = 'OPEN'",
        (run_id,))[0]["n"]
    approved_versions = store.query(
        "SELECT COUNT(DISTINCT weight_version) AS n FROM WEIGHT_APPROVAL "
        "WHERE weight_version <> 'v1.0-initial'")
    approved = approved_versions[0]["n"] if approved_versions else 0
    grouping = grouping_rejection_rate(store.connection, run_id)
    drift = stage2_metric_drift(store, run_id)

    def metric(key: str, value: Any, threshold: Any, met: bool | None, unit: str = "") -> dict:
        return {"key": key, "value": value, "threshold": threshold, "met": met, "unit": unit}

    resolution = by_gate.get("resolution_rate", {})
    parse = by_gate.get("parse_rate", {})
    coverage = by_gate.get("coverage_sanity", {})
    seconds = round(sum(float(step.get("seconds", 0.0)) for step in agent_log), 3)
    metrics = [
        metric("resolution_rate", resolution.get("value"), resolution.get("threshold"),
               bool(resolution.get("passed")) if resolution else None, "share"),
        metric("parse_rate", parse.get("value"), parse.get("threshold"),
               bool(parse.get("passed")) if parse else None, "share"),
        metric("grouping_sample_size", grouping["sampled"], grouping["sample_target"],
               grouping["sample_complete"], "metrics"),
        metric("grouping_rejection_rate", grouping["rate"], grouping["threshold"],
               (not grouping["falsified"]) if grouping["falsified"] is not None else None,
               "share"),
        metric("usage_coverage_top_20", coverage.get("value", stats.get("usage_coverage_top_n")),
               coverage.get("threshold"), bool(coverage.get("passed")) if coverage else None,
               "share"),
        metric("accepted_candidates", accepted, 2, accepted >= 2, "candidates"),
        metric("usable_without_rework_rate",
               round(usable / len(rated), 4) if rated else None, 0.70,
               (usable / len(rated) >= 0.70) if rated else None, "share"),
        metric("stage2_metric_drift", drift["share_drifted"], drift["threshold"],
               (not drift["falsified"]) if drift["falsified"] is not None else None,
               "share of accepted candidates over the drift threshold"),
        metric("run_seconds", seconds, 1800, seconds < 1800, "seconds"),
        metric("approved_weight_versions", approved, 1, approved >= 1, "versions"),
        metric("final_decisions", final, 50, final >= 50, "candidates decided"),
        metric("open_conflicts", open_conflicts, 0, open_conflicts == 0, "conflicts"),
    ]
    if ask_questions:
        cited = citation_rate(store, run_id)
        metrics.append(metric("citation_rate", cited["rate"], cited["target"],
                              cited["rate"] == 1.0 if cited["rate"] is not None else None,
                              "share of questions answered with citations"))
    return {"run_id": run_id, "metrics": metrics,
            "by_key": {m["key"]: m for m in metrics}}


def render_markdown() -> str:
    lines = ["| Ref | Phase | Kind | Criterion | Threshold | Feature | Tests | Evidence | Provable |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for c in CRITERIA:
        tests = "<br>".join(f"`{t}`" for t in c.tests)
        lines.append(f"| {c.ref} | {c.phase} | {c.kind} | {c.criterion} | {c.threshold} | "
                     f"`{c.feature}` | {tests} | {c.evidence} | {c.provable} |")
    return "\n".join(lines)
