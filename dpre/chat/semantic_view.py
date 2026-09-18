"""The semantic view the conversational surface is bound to (section 10.3).

A Cortex Agent is bound to one semantic view over the engine's own governed
output tables. Raw extracts and warehouse tables are not exposed, consistent
with the no-free-form-text-to-SQL rule: the agent chooses a named query with
bound parameters, never composes SQL.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..store import Store

# The only objects the conversational surface may read. A grant audit is the
# test: nothing outside this list is reachable from the agent.
ALLOWED_OBJECTS = (
    "DP_CANDIDATE", "DP_CANDIDATE_METRIC", "DP_CANDIDATE_SOURCE", "DP_CANDIDATE_CONSUMER",
    "DP_CANDIDATE_REPORT", "DP_CANDIDATE_SCORE", "DP_CANDIDATE_EVIDENCE",
    "DP_CANDIDATE_CRITIQUE", "DP_CANDIDATE_NARRATIVE", "KPI_CANONICAL", "KPI_VARIANT",
    "KPI_CONFLICT", "GRAPH_NODE_REPORT", "GRAPH_NODE_KPI", "GRAPH_NODE_COLUMN",
    "GRAPH_NODE_TABLE", "GRAPH_EDGE_KPI_COLUMN", "GRAPH_ER_QUARANTINE", "RUN",
)
FORBIDDEN_OBJECTS = ("RAW_", "REVIEW_DECISION", "SCORE_WEIGHT")


class SemanticViewError(PermissionError):
    """Raised when a query would reach outside the semantic view."""


@dataclass
class NamedQuery:
    name: str
    description: str
    sql: str
    parameters: tuple[str, ...] = ()

    def objects(self) -> set[str]:
        upper = self.sql.upper()
        return {obj for obj in ALLOWED_OBJECTS if obj in upper}


QUERIES: dict[str, NamedQuery] = {
    "candidate_ranking": NamedQuery(
        "candidate_ranking",
        "Candidates ranked by composite score, optionally filtered by domain or status.",
        """
        SELECT c.candidate_id, c.proposed_name, c.archetype, c.tier, c.grain, c.domain,
               c.status, s.composite, s.demand, s.consolidation, s.feasibility, s.risk
        FROM DP_CANDIDATE c
        LEFT JOIN DP_CANDIDATE_SCORE s
               ON s.run_id = c.run_id AND s.candidate_id = c.candidate_id
        WHERE c.run_id = ?
          AND (? = '' OR LOWER(c.domain) LIKE '%' || LOWER(?) || '%')
          AND (? = '' OR c.status = ?)
        ORDER BY s.composite DESC
        LIMIT ?
        """,
        ("run_id", "domain", "domain", "status", "status", "limit"),
    ),
    "retirement_ranking": NamedQuery(
        "retirement_ranking",
        "Candidates ranked by the number of reports they would retire outright, "
        "optionally filtered by the reports' business unit or the candidate's domain.",
        """
        SELECT c.candidate_id, c.proposed_name, c.domain, c.status,
               COUNT(*) AS reports_retirable, SUM(r.users) AS users_affected
        FROM DP_CANDIDATE_REPORT r
        JOIN DP_CANDIDATE c ON c.run_id = r.run_id AND c.candidate_id = r.candidate_id
        JOIN GRAPH_NODE_REPORT g ON g.run_id = r.run_id AND g.report_id = r.report_id
        WHERE r.run_id = ? AND r.coverage >= 1.0
          AND (? = '' OR LOWER(g.business_unit) LIKE '%' || LOWER(?) || '%'
                      OR LOWER(c.domain) LIKE '%' || LOWER(?) || '%')
        GROUP BY c.candidate_id, c.proposed_name, c.domain, c.status
        ORDER BY reports_retirable DESC
        LIMIT ?
        """,
        ("run_id", "filter", "filter", "filter", "limit"),
    ),
    "conflicts_by_label": NamedQuery(
        "conflicts_by_label",
        "Competing definitions of a metric label, with the usage weight behind each side.",
        """
        SELECT conflict_id, label, metric_id_a, metric_id_b, usage_weight_a, usage_weight_b,
               difference_summary, pattern, semantic_model_decision, resolution_status,
               steward_id, expression_a, expression_b
        FROM KPI_CONFLICT
        WHERE run_id = ? AND (? = '' OR LOWER(label) LIKE '%' || LOWER(?) || '%')
        ORDER BY (usage_weight_a + usage_weight_b) DESC
        LIMIT ?
        """,
        ("run_id", "label", "label", "limit"),
    ),
    "candidate_detail": NamedQuery(
        "candidate_detail",
        "One candidate with its score and purpose.",
        """
        SELECT c.candidate_id, c.proposed_name, c.purpose, c.archetype, c.tier, c.grain,
               c.domain, c.status, c.owner_candidate, c.steward_candidate,
               s.composite, s.demand, s.consolidation, s.feasibility, s.risk, s.gate_results
        FROM DP_CANDIDATE c
        LEFT JOIN DP_CANDIDATE_SCORE s
               ON s.run_id = c.run_id AND s.candidate_id = c.candidate_id
        WHERE c.run_id = ? AND c.candidate_id = ?
        """,
        ("run_id", "candidate_id"),
    ),
    "candidate_blockers": NamedQuery(
        "candidate_blockers",
        "Critic findings for one candidate, most severe first.",
        """
        SELECT criterion, finding, severity
        FROM DP_CANDIDATE_CRITIQUE
        WHERE run_id = ? AND candidate_id = ?
        ORDER BY CASE severity WHEN 'blocker' THEN 0 WHEN 'major' THEN 1
                               WHEN 'minor' THEN 2 ELSE 3 END
        """,
        ("run_id", "candidate_id"),
    ),
    "candidate_consumers": NamedQuery(
        "candidate_consumers",
        "Business units consuming one candidate.",
        """
        SELECT business_unit, users, report_count, scheduled_share, cadence
        FROM DP_CANDIDATE_CONSUMER
        WHERE run_id = ? AND candidate_id = ?
        ORDER BY users DESC
        """,
        ("run_id", "candidate_id"),
    ),
    "candidate_metrics": NamedQuery(
        "candidate_metrics",
        "Canonical metrics carried by one candidate.",
        """
        SELECT k.metric_id, k.canonical_name, k.definition, k.aggregation, k.grain,
               k.name_status, k.steward_id, k.usage_weight, k.report_count, k.variant_count
        FROM DP_CANDIDATE_METRIC m
        JOIN KPI_CANONICAL k ON k.run_id = m.run_id AND k.metric_id = m.metric_id
        WHERE m.run_id = ? AND m.candidate_id = ?
        ORDER BY k.usage_weight DESC
        """,
        ("run_id", "candidate_id"),
    ),
    "candidate_sensitive_attributes": NamedQuery(
        "candidate_sensitive_attributes",
        "Sensitive and PII columns in one candidate, which drive the Stage 9 privacy review.",
        """
        SELECT DISTINCT col.column_fqn, col.sensitivity, col.pii_flag, col.business_term,
               col.steward_id
        FROM DP_CANDIDATE_METRIC m
        JOIN KPI_CANONICAL k ON k.run_id = m.run_id AND k.metric_id = m.metric_id
        JOIN KPI_VARIANT v ON v.run_id = m.run_id AND v.metric_id = m.metric_id
        JOIN GRAPH_EDGE_KPI_COLUMN e ON e.run_id = m.run_id AND e.kpi_id = v.kpi_id
        JOIN GRAPH_NODE_COLUMN col ON col.run_id = m.run_id AND col.column_fqn = e.column_fqn
        WHERE m.run_id = ? AND m.candidate_id = ?
          AND (col.pii_flag = 1 OR col.sensitivity IN ('Confidential', 'Restricted'))
        ORDER BY col.pii_flag DESC, col.sensitivity DESC
        """,
        ("run_id", "candidate_id"),
    ),
    "candidate_sources": NamedQuery(
        "candidate_sources",
        "Source tables behind one candidate, with system-of-record and lifecycle status.",
        """
        SELECT table_fqn, system, share_of_metrics, sor_flag, lifecycle_status
        FROM DP_CANDIDATE_SOURCE
        WHERE run_id = ? AND candidate_id = ?
        ORDER BY share_of_metrics DESC
        """,
        ("run_id", "candidate_id"),
    ),
    "evidence_for_feature": NamedQuery(
        "evidence_for_feature",
        "The rows behind one score feature of one candidate.",
        """
        SELECT feature, evidence_type, evidence_id, detail
        FROM DP_CANDIDATE_EVIDENCE
        WHERE run_id = ? AND candidate_id = ? AND (? = '' OR feature = ?)
        LIMIT ?
        """,
        ("run_id", "candidate_id", "feature", "feature", "limit"),
    ),
    "metric_search": NamedQuery(
        "metric_search",
        "Canonical metrics matching a label.",
        """
        SELECT metric_id, canonical_name, definition, aggregation, grain, domain,
               steward_id, name_status, usage_weight, report_count, variant_count, labels
        FROM KPI_CANONICAL
        WHERE run_id = ? AND (LOWER(canonical_name) LIKE '%' || LOWER(?) || '%'
                              OR LOWER(labels) LIKE '%' || LOWER(?) || '%')
        ORDER BY usage_weight DESC
        LIMIT ?
        """,
        ("run_id", "term", "term", "limit"),
    ),
    "gap_list": NamedQuery(
        "gap_list",
        "Unresolved lineage rows by reason code.",
        """
        SELECT reason_code, COUNT(*) AS rows_affected
        FROM GRAPH_ER_QUARANTINE
        WHERE run_id = ?
        GROUP BY reason_code
        ORDER BY rows_affected DESC
        """,
        ("run_id",),
    ),
    "blocked_candidates": NamedQuery(
        "blocked_candidates",
        "Candidates blocked or capped by a hard gate.",
        """
        SELECT c.candidate_id, c.proposed_name, c.status, s.gate_results
        FROM DP_CANDIDATE c
        LEFT JOIN DP_CANDIDATE_SCORE s
               ON s.run_id = c.run_id AND s.candidate_id = c.candidate_id
        WHERE c.run_id = ? AND c.status IN ('Blocked', 'Exploratory')
        ORDER BY c.status
        """,
        ("run_id",),
    ),
    "zombie_reports": NamedQuery(
        "zombie_reports",
        "Reports with historic usage that have not run recently.",
        """
        SELECT report_id, name, business_unit, owner, run_count_12m, distinct_users, last_run
        FROM GRAPH_NODE_REPORT
        WHERE run_id = ? AND last_run < ?
        ORDER BY run_count_12m DESC
        LIMIT ?
        """,
        ("run_id", "cutoff", "limit"),
    ),
}


WRITE_TOKENS = ("ATTACH", "PRAGMA", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE",
                "ALTER", "REPLACE", "TRUNCATE", "GRANT", "VACUUM")


def check_query(sql: str) -> None:
    """Refuse anything that writes, or that reads outside the semantic view."""
    upper = f" {sql.upper()} "
    for token in WRITE_TOKENS:
        if f" {token} " in upper or upper.strip().startswith(token):
            raise SemanticViewError(
                f"the conversational surface is read-only; '{token}' refused")
    for forbidden in FORBIDDEN_OBJECTS:
        if forbidden in upper:
            raise SemanticViewError(
                f"the conversational surface may not read {forbidden}; it is bound to the "
                "semantic view over the engine's own output tables")


def run_named_query(store: Store, name: str, params: tuple) -> list[dict]:
    query = QUERIES.get(name)
    if query is None:
        raise SemanticViewError(f"unknown named query '{name}'")
    check_query(query.sql)
    return store.query(query.sql, params)


def describe() -> list[dict]:
    return [
        {"name": q.name, "description": q.description, "parameters": list(q.parameters),
         "objects": sorted(q.objects())}
        for q in QUERIES.values()
    ]
