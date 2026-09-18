"""OpenLineage-style export of the resolved KPI-to-column lineage (R-46).

A catalog admin fixing lineage gaps and a lineage tool consuming the engine's
resolution both want the same thing: the KPI-to-column edges with the rule and
confidence that produced each one, in a shape other tools already read. This
follows the OpenLineage event layout (job, inputs, outputs, facets) closely
enough to load into a viewer, with the entity-resolution rule and confidence as
a custom facet on every input field. Quarantined rows are exported too, as
inputs with an ``unresolved`` facet, so the export is the gap list as well as
the lineage.
"""
from __future__ import annotations

import datetime as _dt

from ..models import KnowledgeGraph

PRODUCER = "dpre.graph.lineage_export"
SCHEMA_URL = "https://openlineage.io/spec/1-0-5/OpenLineage.json"


def openlineage_events(graph: KnowledgeGraph, run_id: str,
                       as_of: _dt.date | str | None = None) -> dict:
    """One RunEvent per KPI: inputs are the resolved columns, outputs the reports."""
    stamp = (as_of.isoformat() if isinstance(as_of, _dt.date) else as_of) or graph.as_of_date.isoformat()
    edges_by_kpi: dict[str, list] = {}
    for edge in graph.edges_kpi_column:
        edges_by_kpi.setdefault(edge.kpi_id, []).append(edge)
    quarantine_by_kpi: dict[str, list] = {}
    for row in graph.quarantine:
        quarantine_by_kpi.setdefault(row.kpi_id, []).append(row)

    events = []
    for kpi_id in sorted(graph.kpis):
        kpi = graph.kpis[kpi_id]
        inputs = []
        for edge in sorted(edges_by_kpi.get(kpi_id, []), key=lambda e: (e.column_fqn, e.role)):
            table_fqn, column = edge.column_fqn.rsplit(".", 1)
            column_node = graph.columns.get(edge.column_fqn)
            inputs.append({
                "namespace": edge.column_fqn.split(".", 1)[0],
                "name": table_fqn,
                "facets": {
                    "dpre_resolution": {
                        "_producer": PRODUCER, "_schemaURL": SCHEMA_URL,
                        "field": column, "role": edge.role, "er_rule": edge.er_rule,
                        "confidence": edge.confidence, "raw_reference": edge.raw_reference,
                        "probable": edge.confidence < 0.80,
                    },
                    "schema": {"fields": [{
                        "name": column,
                        "type": column_node.data_type if column_node else "",
                    }]},
                },
            })
        for row in sorted(quarantine_by_kpi.get(kpi_id, []), key=lambda q: q.raw_reference):
            inputs.append({
                "namespace": row.raw_reference.split(".", 1)[0] if row.raw_reference else "",
                "name": row.raw_reference.rsplit(".", 1)[0] if "." in row.raw_reference else row.raw_reference,
                "facets": {"dpre_unresolved": {
                    "_producer": PRODUCER, "_schemaURL": SCHEMA_URL,
                    "raw_reference": row.raw_reference, "reason_code": row.reason_code,
                    "detail": row.detail, "role": row.role,
                }},
            })
        outputs = [{"namespace": kpi.tool, "name": report_id,
                    "facets": {"dpre_report": {"_producer": PRODUCER, "_schemaURL": SCHEMA_URL,
                                               "report_id": report_id}}}
                   for report_id in sorted(graph.reports_for_kpi(kpi_id) or [kpi.report_id])]
        events.append({
            "eventType": "COMPLETE",
            "eventTime": stamp,
            "producer": PRODUCER,
            "schemaURL": SCHEMA_URL,
            "run": {"runId": run_id, "facets": {"dpre_kpi": {
                "_producer": PRODUCER, "_schemaURL": SCHEMA_URL,
                "label": kpi.label, "fingerprint": kpi.fingerprint,
                "parse_status": kpi.parse_status, "grain": kpi.grain,
            }}},
            "job": {"namespace": "dpre", "name": kpi_id,
                    "facets": {"sql": {"_producer": PRODUCER, "_schemaURL": SCHEMA_URL,
                                       "query": kpi.expression}}},
            "inputs": inputs,
            "outputs": outputs,
        })
    return {"run_id": run_id, "as_of": stamp, "events": events,
            "edges": len(graph.edges_kpi_column), "unresolved": len(graph.quarantine)}
