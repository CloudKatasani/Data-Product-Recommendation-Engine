"""The conversational surface (specification section 10.3).

It answers questions such as "which candidates retire the most Finance reports",
"show the competing definitions of days sales outstanding", or "what would block
the arrears candidate at Stage 9". The agent picks a named query with bound
parameters from the semantic view and never composes SQL, and every answer cites
candidate and evidence IDs.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field

from ..store import Store
from ..util.text import name_similarity
from .semantic_view import run_named_query

STOPWORDS = {
    "the", "a", "an", "of", "for", "to", "in", "on", "what", "which", "who", "how", "many",
    "much", "show", "me", "list", "tell", "about", "is", "are", "do", "does", "we", "our",
    "can", "would", "should", "most", "top", "best", "give", "please", "and", "with",
    "candidate", "candidates", "product", "products", "data", "at", "by", "from",
}


@dataclass
class Answer:
    question: str
    intent: str
    answer: str
    rows: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    query_used: str = ""
    parameters: dict = field(default_factory=dict)
    followups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question, "intent": self.intent, "answer": self.answer,
            "rows": self.rows, "citations": self.citations, "query_used": self.query_used,
            "parameters": self.parameters, "followups": self.followups,
            "bound_to": "semantic view over DP_CANDIDATE*, KPI_* and GRAPH.* tables",
        }


SUGGESTED_QUESTIONS = (
    "Which candidates retire the most Finance reports?",
    "Show the competing definitions of days past due",
    "What would block the top candidate at Stage 9?",
    "What are the highest scoring candidates?",
    "Which conflicts have the most usage at stake?",
    "What is blocked and why?",
    "Where is the lineage broken?",
    "Which reports have not run in a year?",
    "Who consumes the top candidate?",
    "Explain the demand score of the top candidate",
)


class ConversationalAgent:
    def __init__(self, store: Store, run_id: str | None = None) -> None:
        self.store = store
        self.run_id = run_id or store.latest_run_id() or ""

    # -- public ---------------------------------------------------------
    def ask(self, question: str) -> Answer:
        text = (question or "").strip()
        if not text:
            return Answer(question, "empty", "Ask me about the candidates, the conflicts, "
                                             "the gaps or what blocks a candidate.",
                          followups=list(SUGGESTED_QUESTIONS[:4]))
        if not self.run_id:
            return Answer(text, "no_run", "There is no published run to query yet. Start a "
                                          "run from the Automated or Manual path first.")
        low = text.lower()
        for matcher, handler in self._intents():
            if matcher(low):
                return handler(text, low)
        return self._fallback(text, low)

    # -- intents --------------------------------------------------------
    def _intents(self):
        return (
            (lambda q: _has(q, "retire", "retirement", "decommission") and "conflict" not in q,
             self._retirement),
            (lambda q: _has(q, "competing", "conflict", "disagree", "two definitions",
                            "different definition"), self._conflicts),
            (lambda q: _has(q, "block", "stage 9", "privacy", "pii", "sensitive", "reject"),
             self._blockers),
            (lambda q: _has(q, "consume", "consumer", "who uses", "business unit", "users"),
             self._consumers),
            (lambda q: _has(q, "explain", "why does", "evidence", "behind the score",
                            "how was", "score of"), self._evidence),
            (lambda q: _has(q, "gap", "unresolved", "quarantine", "lineage", "quarantined",
                            "missing definition"), self._gaps),
            (lambda q: _has(q, "zombie", "not run", "stale", "hasn't run", "has not run"),
             self._zombies),
            (lambda q: _has(q, "metric", "kpi", "measure") and not _has(q, "candidate"),
             self._metrics),
            (lambda q: _has(q, "coverage", "how much usage", "top 20", "cumulative"),
             self._coverage),
            (lambda q: _has(q, "rank", "highest", "top", "best", "score", "backlog",
                            "shortlist"), self._ranking),
            (lambda q: _has(q, "tell me about", "describe", "what is"), self._detail),
        )

    # -- handlers -------------------------------------------------------
    def _ranking(self, question: str, low: str) -> Answer:
        domain = self._domain_filter(low)
        status = self._status_filter(low)
        limit = _limit(low, 10)
        rows = run_named_query(self.store, "candidate_ranking",
                               (self.run_id, domain, domain, status, status, limit))
        if not rows:
            return Answer(question, "candidate_ranking",
                          "No candidates matched that filter in this run.",
                          query_used="candidate_ranking")
        lead = rows[0]
        answer = (f"{len(rows)} candidates{' in ' + domain if domain else ''}"
                  f"{' with status ' + status if status else ''}, ranked by composite score. "
                  f"{lead['proposed_name']} leads at {lead['composite']:.0f} "
                  f"(demand {lead['demand']:.0f}, consolidation {lead['consolidation']:.0f}, "
                  f"feasibility {lead['feasibility']:.0f}, risk {lead['risk']:.0f}).")
        return Answer(question, "candidate_ranking", answer, rows,
                      _cite(rows, "candidate_id", "proposed_name"), "candidate_ranking",
                      {"domain": domain, "status": status, "limit": limit},
                      ["Which of these retires the most reports?",
                       f"What would block {lead['proposed_name']}?"])

    def _retirement(self, question: str, low: str) -> Answer:
        filter_text = self._domain_filter(low) or self._business_unit_filter(low)
        limit = _limit(low, 10)
        rows = run_named_query(self.store, "retirement_ranking",
                               (self.run_id, filter_text, filter_text, filter_text, limit))
        if not rows:
            return Answer(question, "retirement_ranking",
                          "No candidate covers every metric of any report in that filter, so "
                          "nothing can be retired outright yet.",
                          query_used="retirement_ranking",
                          parameters={"filter": filter_text})
        total = sum(r["reports_retirable"] for r in rows)
        lead = rows[0]
        answer = (f"{lead['proposed_name']} retires the most: {lead['reports_retirable']} reports "
                  f"fully covered, affecting {lead['users_affected']} users. Across the top "
                  f"{len(rows)} candidates{' matching ' + filter_text if filter_text else ''}, "
                  f"{total} reports are covered outright.")
        return Answer(question, "retirement_ranking", answer, rows,
                      _cite(rows, "candidate_id", "proposed_name"), "retirement_ranking",
                      {"filter": filter_text, "limit": limit},
                      [f"Who consumes {lead['proposed_name']}?",
                       f"What would block {lead['proposed_name']}?"])

    def _conflicts(self, question: str, low: str) -> Answer:
        label = self._metric_label(low)
        limit = _limit(low, 8)
        rows = run_named_query(self.store, "conflicts_by_label",
                               (self.run_id, label, label, limit))
        if not rows:
            return Answer(question, "conflicts_by_label",
                          f"No competing definitions were found{' for ' + label if label else ''} "
                          "in this run.", query_used="conflicts_by_label",
                          parameters={"label": label})
        lead = rows[0]
        answer = (f"{len(rows)} competing definition(s). The heaviest is \"{lead['label']}\": "
                  f"{lead['difference_summary']}. Usage at stake "
                  f"{lead['usage_weight_a'] + lead['usage_weight_b']:.0f}. "
                  f"The Stage 6 decision is: {lead['semantic_model_decision']}. "
                  f"Steward: {lead['steward_id'] or 'UNASSIGNED'}.")
        citations = [{"type": "conflict", "id": r["conflict_id"], "label": r["label"]}
                     for r in rows]
        citations += [{"type": "metric", "id": r["metric_id_a"]} for r in rows[:3]]
        return Answer(question, "conflicts_by_label", answer, rows, citations,
                      "conflicts_by_label", {"label": label, "limit": limit},
                      ["Which candidates carry these conflicts?",
                       "Which conflicts have the most usage at stake?"])

    def _blockers(self, question: str, low: str) -> Answer:
        candidate = self._resolve_candidate(low)
        if candidate is None:
            rows = run_named_query(self.store, "blocked_candidates", (self.run_id,))
            gates = []
            for row in rows:
                for gate in json.loads(row.get("gate_results") or "[]"):
                    if not gate.get("passed"):
                        gates.append({"candidate_id": row["candidate_id"],
                                      "candidate": row["proposed_name"],
                                      "status": row["status"], "gate": gate["gate"],
                                      "detail": gate["detail"]})
            answer = (f"{len(rows)} candidates are capped by a hard gate. "
                      + "; ".join(f"{g['candidate']} ({g['gate']})" for g in gates[:4]))
            return Answer(question, "blocked_candidates", answer or "Nothing is blocked.",
                          gates, _cite(rows, "candidate_id", "proposed_name"),
                          "blocked_candidates")
        rows = run_named_query(self.store, "candidate_blockers",
                               (self.run_id, candidate["candidate_id"]))
        sensitive = run_named_query(self.store, "candidate_sensitive_attributes",
                                    (self.run_id, candidate["candidate_id"]))
        blockers = [r for r in rows if r["severity"] in ("blocker", "major")]
        pii = [s for s in sensitive if s["pii_flag"]]
        answer = (f"{candidate['proposed_name']} has {len(blockers)} blocking or major findings"
                  + (f" and {len(pii)} PII attributes that pull it into the Stage 9 privacy "
                     "review" if pii else "") + ". "
                  + (blockers[0]["finding"] if blockers else "Nothing blocking was found."))
        citations = [{"type": "candidate", "id": candidate["candidate_id"],
                      "label": candidate["proposed_name"]}]
        citations += [{"type": "column", "id": s["column_fqn"], "label": s["sensitivity"]}
                      for s in sensitive[:6]]
        return Answer(question, "candidate_blockers", answer, rows + sensitive, citations,
                      "candidate_blockers", {"candidate_id": candidate["candidate_id"]},
                      [f"Who consumes {candidate['proposed_name']}?",
                       f"Explain the score of {candidate['proposed_name']}"])

    def _consumers(self, question: str, low: str) -> Answer:
        candidate = self._resolve_candidate(low)
        if candidate is None:
            return self._ranking(question, low)
        rows = run_named_query(self.store, "candidate_consumers",
                               (self.run_id, candidate["candidate_id"]))
        if not rows:
            return Answer(question, "candidate_consumers",
                          f"No business unit could be attributed to {candidate['proposed_name']}, "
                          "which is why gate G1 caps it at Exploratory.",
                          query_used="candidate_consumers")
        lead = rows[0]
        answer = (f"{candidate['proposed_name']} is consumed by {len(rows)} business unit(s). "
                  f"{lead['business_unit']} is the largest with {lead['users']} users across "
                  f"{lead['report_count']} reports, {lead['scheduled_share']:.0%} of them "
                  f"scheduled ({lead['cadence']}).")
        return Answer(question, "candidate_consumers", answer, rows,
                      [{"type": "candidate", "id": candidate["candidate_id"],
                        "label": candidate["proposed_name"]}]
                      + [{"type": "business_unit", "id": r["business_unit"]} for r in rows],
                      "candidate_consumers", {"candidate_id": candidate["candidate_id"]})

    def _evidence(self, question: str, low: str) -> Answer:
        candidate = self._resolve_candidate(low)
        if candidate is None:
            return self._ranking(question, low)
        feature = ""
        for known in ("usage_weight", "consumer_breadth", "cadence", "reports_retirable",
                      "variants_collapsed", "conflicts_surfaced", "lineage_completeness",
                      "definition_coverage", "source_health", "calculation_determinism",
                      "sensitivity", "grain_ambiguity", "conflict_load"):
            if known.replace("_", " ") in low or known in low:
                feature = known
                break
        if not feature:
            for word, mapped in (("demand", "usage_weight"), ("usage", "usage_weight"),
                                 ("consolidation", "reports_retirable"),
                                 ("feasibility", "lineage_completeness"),
                                 ("risk", "sensitivity")):
                if word in low:
                    feature = mapped
                    break
        rows = run_named_query(self.store, "evidence_for_feature",
                               (self.run_id, candidate["candidate_id"], feature, feature, 25))
        detail = run_named_query(self.store, "candidate_detail",
                                 (self.run_id, candidate["candidate_id"]))
        score = detail[0] if detail else {}
        answer = (f"{candidate['proposed_name']} scores {score.get('composite', 0):.0f} overall "
                  f"(demand {score.get('demand', 0):.0f}, consolidation "
                  f"{score.get('consolidation', 0):.0f}, feasibility "
                  f"{score.get('feasibility', 0):.0f}, risk {score.get('risk', 0):.0f}). "
                  f"{len(rows)} evidence rows"
                  + (f" for {feature}" if feature else "") + " sit behind that number.")
        return Answer(question, "evidence_for_feature", answer, rows,
                      [{"type": r["evidence_type"], "id": r["evidence_id"],
                        "label": r["feature"]} for r in rows[:12]],
                      "evidence_for_feature",
                      {"candidate_id": candidate["candidate_id"], "feature": feature})

    def _gaps(self, question: str, low: str) -> Answer:
        rows = run_named_query(self.store, "gap_list", (self.run_id,))
        total = sum(r["rows_affected"] for r in rows)
        if not rows:
            answer = "Every lineage row in this run resolved to a catalog column."
        else:
            answer = (f"{total} lineage rows could not be resolved. "
                      + ", ".join(f"{r['reason_code']}: {r['rows_affected']}" for r in rows)
                      + ". These are the catalog gaps a steward has to close before the "
                        "affected candidates can pass gate G2.")
        return Answer(question, "gap_list", answer, rows,
                      [{"type": "reason_code", "id": r["reason_code"]} for r in rows],
                      "gap_list")

    def _zombies(self, question: str, low: str) -> Answer:
        run = self.store.run(self.run_id) or {}
        as_of = run.get("as_of_date") or _dt.date.today().isoformat()
        cutoff = (_dt.date.fromisoformat(as_of) - _dt.timedelta(days=365)).isoformat()
        rows = run_named_query(self.store, "zombie_reports", (self.run_id, cutoff, 15))
        if not rows:
            return Answer(question, "zombie_reports",
                          "Every report in this run has run within the last twelve months.",
                          query_used="zombie_reports")
        answer = (f"{len(rows)} reports have historic usage but have not run since {cutoff}. "
                  f"The heaviest is {rows[0]['name']} with {rows[0]['run_count_12m']} recorded "
                  f"runs and a last run of {rows[0]['last_run']}. Recency decay drops these out "
                  "of the demand score.")
        return Answer(question, "zombie_reports", answer, rows,
                      [{"type": "report", "id": r["report_id"], "label": r["name"]}
                       for r in rows], "zombie_reports", {"cutoff": cutoff})

    def _metrics(self, question: str, low: str) -> Answer:
        term = self._metric_label(low)
        rows = run_named_query(self.store, "metric_search", (self.run_id, term, term, 12))
        if not rows:
            return Answer(question, "metric_search",
                          f"No canonical metric matches '{term}' in this run.",
                          query_used="metric_search", parameters={"term": term})
        lead = rows[0]
        answer = (f"{len(rows)} canonical metric(s) match '{term}'. {lead['canonical_name']} "
                  f"({lead['name_status']}) is the heaviest: {lead['definition']} "
                  f"It appears in {lead['report_count']} reports with "
                  f"{lead['variant_count']} filter variant(s); steward "
                  f"{lead['steward_id'] or 'UNASSIGNED'}.")
        return Answer(question, "metric_search", answer, rows,
                      [{"type": "metric", "id": r["metric_id"], "label": r["canonical_name"]}
                       for r in rows], "metric_search", {"term": term},
                      [f"Show the competing definitions of {lead['canonical_name']}"])

    def _coverage(self, question: str, low: str) -> Answer:
        run = self.store.run(self.run_id) or {}
        stats = run.get("stats") or {}
        coverage = stats.get("usage_coverage_top_n", 0.0)
        rows = run_named_query(self.store, "candidate_ranking",
                               (self.run_id, "", "", "", "", 20))
        answer = (f"The top 20 candidates cover {coverage:.0%} of usage-weighted KPI "
                  f"consumption in this run. The coverage sanity gate needs at least 50%.")
        return Answer(question, "candidate_ranking", answer, rows,
                      _cite(rows, "candidate_id", "proposed_name"), "candidate_ranking",
                      {"limit": 20})

    def _detail(self, question: str, low: str) -> Answer:
        candidate = self._resolve_candidate(low)
        if candidate is None:
            return self._fallback(question, low)
        detail = run_named_query(self.store, "candidate_detail",
                                 (self.run_id, candidate["candidate_id"]))
        metrics = run_named_query(self.store, "candidate_metrics",
                                  (self.run_id, candidate["candidate_id"]))
        sources = run_named_query(self.store, "candidate_sources",
                                  (self.run_id, candidate["candidate_id"]))
        row = detail[0] if detail else {}
        answer = (f"{row.get('proposed_name')} - {row.get('purpose')} "
                  f"Archetype {row.get('archetype')}, tier {row.get('tier')}, grain "
                  f"{row.get('grain')}, status {row.get('status')}. It carries "
                  f"{len(metrics)} canonical metrics over {len(sources)} source tables. "
                  f"Owner {row.get('owner_candidate') or 'UNASSIGNED'}, steward "
                  f"{row.get('steward_candidate') or 'UNASSIGNED'}.")
        return Answer(question, "candidate_detail", answer, detail + metrics + sources,
                      [{"type": "candidate", "id": candidate["candidate_id"],
                        "label": candidate["proposed_name"]}]
                      + [{"type": "metric", "id": m["metric_id"], "label": m["canonical_name"]}
                         for m in metrics[:8]],
                      "candidate_detail", {"candidate_id": candidate["candidate_id"]})

    def _fallback(self, question: str, low: str) -> Answer:
        candidate = self._resolve_candidate(low)
        if candidate is not None:
            return self._detail(question, low)
        return Answer(
            question, "unrecognised",
            "I answer from the engine's own output tables only: candidates, canonical metrics, "
            "conflicts, evidence, gaps and the reports behind them. Try one of the questions "
            "below.", followups=list(SUGGESTED_QUESTIONS))

    # -- helpers --------------------------------------------------------
    def _candidates(self) -> list[dict]:
        return self.store.query(
            "SELECT candidate_id, proposed_name, domain, status FROM DP_CANDIDATE "
            "WHERE run_id = ?", (self.run_id,))

    def _resolve_candidate(self, low: str) -> dict | None:
        rows = self._candidates()
        if not rows:
            return None
        for row in rows:
            if row["candidate_id"].lower() in low:
                return row
        if _has(low, "top candidate", "best candidate", "leading candidate", "the top one"):
            ranked = run_named_query(self.store, "candidate_ranking",
                                     (self.run_id, "", "", "", "", 1))
            if ranked:
                return {"candidate_id": ranked[0]["candidate_id"],
                        "proposed_name": ranked[0]["proposed_name"]}
        # Prefer the candidate whose name contains the most of the asker's own
        # distinctive words; fall back to overall name similarity only to break ties.
        tokens = [t for t in _tokens(low) if t not in
                  {"consumes", "consume", "consumer", "consumers", "block", "blocks",
                   "blocked", "stage", "score", "scores", "explain", "uses", "using",
                   "describe", "tell", "retire", "retires", "who", "what"}]
        if not tokens:
            return None
        best, best_score, best_similarity = None, 0.0, 0.0
        for row in rows:
            name_tokens = set(_tokens(row["proposed_name"]))
            hits = len([t for t in tokens if t in name_tokens])
            score = hits / len(tokens)
            similarity = name_similarity(" ".join(tokens), row["proposed_name"])
            if (score, similarity) > (best_score, best_similarity):
                best, best_score, best_similarity = row, score, similarity
        if best_score >= 0.34 or best_similarity >= 0.75:
            return best
        return None

    def _domain_filter(self, low: str) -> str:
        domains = {row["domain"] for row in self._candidates() if row["domain"]}
        for domain in sorted(domains, key=len, reverse=True):
            for token in _tokens(domain):
                if len(token) > 3 and token in low:
                    return domain
        return ""

    def _business_unit_filter(self, low: str) -> str:
        rows = self.store.query(
            "SELECT DISTINCT business_unit FROM GRAPH_NODE_REPORT WHERE run_id = ?",
            (self.run_id,))
        for row in sorted((r["business_unit"] or "" for r in rows), key=len, reverse=True):
            for token in _tokens(row):
                if len(token) > 3 and token in low:
                    return row
        return ""

    def _status_filter(self, low: str) -> str:
        for status in ("Proposed", "Accepted", "Rejected", "Exploratory", "Blocked",
                       "Deferred", "Merged"):
            if status.lower() in low:
                return status
        return ""

    def _metric_label(self, low: str) -> str:
        match = re.search(r"(?:definitions?|metric|kpi|measure)\s+(?:of|for|called|named)?\s*"
                          r"([a-z0-9 _-]{3,60})", low)
        if match:
            return _clean(match.group(1))
        tokens = [t for t in _tokens(low) if t not in
                  {"competing", "definition", "definitions", "conflict", "conflicts", "metric",
                   "metrics", "kpi", "kpis", "measure", "measures", "show", "differ"}]
        return " ".join(tokens[:4])


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t and t not in STOPWORDS]


def _has(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _limit(text: str, default: int) -> int:
    match = re.search(r"\btop\s+(\d{1,3})\b", text) or re.search(r"\b(\d{1,3})\s+candidates\b", text)
    if match:
        return max(1, min(100, int(match.group(1))))
    return default


def _clean(text: str) -> str:
    text = text.strip().strip("?.,")
    for tail in (" in this run", " in the run", " please"):
        text = text.replace(tail, "")
    return text.strip()


def _cite(rows: list[dict], id_key: str, label_key: str) -> list[dict]:
    return [{"type": "candidate", "id": r.get(id_key), "label": r.get(label_key)}
            for r in rows[:12]]
