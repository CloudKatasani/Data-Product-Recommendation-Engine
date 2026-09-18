"""The audit view an internal auditor or audit committee reads (R-15, R-02, R-03).

Everything here is a read: chain verification, orphan statuses, open waivers,
weight versions with their approvals, configuration changes and the schema
version. It is what ``dpre audit`` and ``GET /api/audit`` should render.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .ledger import ensure_schema
from .transitions import transitions_table

if TYPE_CHECKING:  # pragma: no cover
    from ..store import Store


def audit_report(store: "Store", run_id: str | None = None) -> dict:
    ensure_schema(store.connection)
    chain = store.verify_audit_chain(run_id)
    waivers = store.open_waivers(run_id)
    versions = store.weight_versions()
    current = store.current_weights()
    changes = store.config_changes()
    findings: list[str] = []
    if not chain["ok"]:
        if chain["first_break"]:
            b = chain["first_break"]
            findings.append(f"audit chain broken at decision {b['decision_id']}: {b['reason']}")
        for orphan in chain["orphans"]:
            findings.append(f"{orphan['run_id']}/{orphan['candidate_id']} is {orphan['status']} "
                            "with no decision row behind it")
    if not current.approved_by:
        findings.append(f"weights in force ({current.weight_version}) are {current.note}")
    for waiver in waivers:
        findings.append(f"open waiver #{waiver['waiver_id']} on {waiver['run_id']}/"
                        f"{waiver['candidate_id']}: {waiver['gate_waived']} waived by "
                        f"{waiver['reviewer']} with {waiver['second_approver']}")
    return {
        "run_id": run_id,
        "ok": chain["ok"] and not waivers and bool(current.approved_by),
        "schema_version": store.schema_version(),
        "chain": chain,
        "open_waivers": waivers,
        "weights_in_force": current.to_dict(),
        "weight_versions": versions,
        "config_changes": changes,
        "transitions": transitions_table(),
        "findings": findings,
    }
