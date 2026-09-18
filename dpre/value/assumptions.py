"""Value assumptions: every rate the value model multiplies by, versioned.

The engine holds the counts (reports, users, conflicts, packages); the client
holds the money. A value figure is only as defensible as the assumption behind
it, so every rate lives here with a version, is stored in ``VALUE_ASSUMPTION``
like ``SCORE_WEIGHT``, and is labelled illustrative until the client's finance
partner approves a version (review finding R-08).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

ILLUSTRATIVE = "illustrative - to be contested by the client"

SCHEMA = """
CREATE TABLE IF NOT EXISTS VALUE_ASSUMPTION (
    version TEXT PRIMARY KEY, json TEXT, approved_by TEXT, effective_from TEXT, basis TEXT
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Idempotent: safe to call at the top of every function that touches the table."""
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass
class ValueAssumptions:
    """A versioned rate card. Defaults are plausible mid-market figures, no more.

    ``basis`` is carried onto every value row so that an audit committee never
    sees a number without the sentence that says where its rates came from.
    """

    version: str = "va-1.0-illustrative"
    currency: str = "USD"
    basis: str = ILLUSTRATIVE
    approved_by: str = ""
    effective_from: str = "2026-01-01"
    # Analyst and developer hours to keep one report alive for a year, by the
    # complexity band of the report (complexity_score < 0.34 low, < 0.67 medium).
    maintenance_hours_per_report: dict[str, float] = field(
        default_factory=lambda: {"low": 24.0, "medium": 60.0, "high": 120.0})
    complexity_band_edges: tuple[float, float] = (0.34, 0.67)
    loaded_hourly_rate: float = 95.0
    # Licence and refresh cost that a retired report releases, per tool per year.
    licence_cost_per_report_per_year: dict[str, float] = field(
        default_factory=lambda: {"cognos": 1200.0, "powerbi": 420.0})
    # Platform cost released when a whole package or semantic model is emptied.
    infra_cost_per_package_per_year: float = 15000.0
    # Steward and analyst hours spent reconciling one competing definition each period.
    reconciliation_hours_per_conflict_per_period: float = 6.0
    periods_per_year: int = 12
    # Expected annual cost of a mis-stated number reaching a decision, by band.
    mis_decision_cost_band: dict[str, float] = field(
        default_factory=lambda: {"regulatory": 50000.0, "financial": 20000.0,
                                 "operational": 5000.0})
    # Share of the retirement saving realised per disposition: a Keep report is
    # re-pointed, not retired, so it releases no maintenance.
    disposition_realisation: dict[str, float] = field(
        default_factory=lambda: {"retire": 1.0, "merge": 1.0, "migrate": 0.5, "keep": 0.0})
    # One effort point (programme/effort.py) is roughly one loaded team-day.
    build_cost_per_effort_point: float = 1800.0
    build_cost_by_size: dict[str, float] = field(
        default_factory=lambda: {"XS": 25000.0, "S": 60000.0, "M": 140000.0,
                                 "L": 260000.0, "XL": 420000.0})
    build_cost_method: str = "points"        # points | size
    discount_rate: float = 0.08
    horizon_years: int = 3
    ramp_year1_share: float = 0.5            # benefit realised in the build year

    def complexity_band(self, complexity_score: float) -> str:
        low, high = self.complexity_band_edges
        if complexity_score < low:
            return "low"
        if complexity_score < high:
            return "medium"
        return "high"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["complexity_band_edges"] = list(self.complexity_band_edges)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ValueAssumptions":
        defaults = cls()
        kwargs: dict[str, Any] = {}
        for name in defaults.__dataclass_fields__:
            if name in payload:
                kwargs[name] = payload[name]
        if "complexity_band_edges" in kwargs:
            kwargs["complexity_band_edges"] = tuple(kwargs["complexity_band_edges"])
        for name in ("maintenance_hours_per_report", "licence_cost_per_report_per_year",
                     "mis_decision_cost_band", "disposition_realisation", "build_cost_by_size"):
            if name in kwargs:
                kwargs[name] = {str(k).lower() if name != "build_cost_by_size" else str(k): float(v)
                                for k, v in kwargs[name].items()}
        return cls(**kwargs)


def save_assumptions(connection: sqlite3.Connection, assumptions: ValueAssumptions) -> None:
    ensure_schema(connection)
    connection.execute(
        "INSERT OR REPLACE INTO VALUE_ASSUMPTION VALUES (?,?,?,?,?)",
        (assumptions.version, json.dumps(assumptions.to_dict(), sort_keys=True),
         assumptions.approved_by, assumptions.effective_from, assumptions.basis))
    connection.commit()


def load_assumptions(connection: sqlite3.Connection,
                     version: str | None = None) -> ValueAssumptions | None:
    """The named version, or the latest effective one; None when nothing is stored."""
    ensure_schema(connection)
    if version:
        rows = connection.execute("SELECT json FROM VALUE_ASSUMPTION WHERE version = ?",
                                  (version,)).fetchall()
    else:
        rows = connection.execute(
            "SELECT json FROM VALUE_ASSUMPTION ORDER BY effective_from DESC, version DESC "
            "LIMIT 1").fetchall()
    if not rows:
        return None
    return ValueAssumptions.from_dict(json.loads(rows[0][0]))


def approve_assumptions(connection: sqlite3.Connection, assumptions: ValueAssumptions,
                        approver: str, effective_from: str) -> ValueAssumptions:
    """A client-approved rate card drops the illustrative label; the approver is kept."""
    if not approver:
        raise PermissionError("approving value assumptions requires a named approver")
    approved = ValueAssumptions.from_dict(assumptions.to_dict())
    approved.approved_by = approver
    approved.effective_from = effective_from
    approved.basis = f"approved by {approver}, effective {effective_from}"
    save_assumptions(connection, approved)
    return approved
