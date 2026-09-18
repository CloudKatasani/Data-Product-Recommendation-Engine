"""The feedback loop (specification section 13.3).

Reviewer decisions are the training signal. Each Accept, Reject, Merge, Split or
override is stored with a reason code, and three things are re-estimated:

1. Score weights, by logistic regression of Accept against Reject on the four
   dimensions. The result is a *proposal*; a data product council approves it
   before it becomes the new weight version.
2. Archetype thresholds. Rules overridden more than 30% of the time are listed
   for rewriting, not re-weighting.
3. Clustering resolution, from Merge and Split decisions, per domain.

Weight changes never re-score Accepted candidates; they apply to the next run.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field

from ..config import DIMENSION_WEIGHTS, ScoreWeights
from ..store import Store

MIN_DECISIONS = 50          # section 14: below this the loop is designed but unproven
OVERRIDE_REWRITE_THRESHOLD = 0.30


@dataclass
class WeightProposal:
    proposed: ScoreWeights
    current_version: str
    sample_size: int
    accepted: int
    rejected: int
    coefficients: dict[str, float] = field(default_factory=dict)
    accuracy: float = 0.0
    approved: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "current_version": self.current_version,
            "proposed_version": self.proposed.weight_version,
            "sample_size": self.sample_size,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "coefficients": self.coefficients,
            "in_sample_accuracy": round(self.accuracy, 3),
            "dimensions": self.proposed.dimensions,
            "approved": self.approved,
            "note": self.note,
        }


def collect_training_rows(store: Store) -> list[dict]:
    """Accept and Reject decisions joined to the dimension scores that produced them."""
    rows = store.query(
        "SELECT d.decision, d.reason_code, d.run_id, d.candidate_id, "
        "       s.demand, s.consolidation, s.feasibility, s.risk, s.composite, "
        "       c.archetype, c.tier, c.domain "
        "FROM REVIEW_DECISION d "
        "JOIN DP_CANDIDATE_SCORE s ON s.run_id = d.run_id AND s.candidate_id = d.candidate_id "
        "JOIN DP_CANDIDATE c ON c.run_id = d.run_id AND c.candidate_id = d.candidate_id "
        "WHERE d.decision IN ('Accept', 'Reject')")
    return rows


def reestimate_weights(store: Store, current: ScoreWeights,
                       learning_rate: float = 0.35, epochs: int = 600) -> WeightProposal:
    rows = collect_training_rows(store)
    accepted = sum(1 for r in rows if r["decision"] == "Accept")
    rejected = len(rows) - accepted
    proposal = ScoreWeights(
        weight_version=_next_version(current.weight_version),
        effective_from=_dt.date.today().isoformat(),
        dimensions=dict(current.dimensions),
        features={k: dict(v) for k, v in current.features.items()},
        note="proposed from reviewer decisions; not yet approved",
        approved_by="",
    )
    if len(rows) < MIN_DECISIONS or accepted == 0 or rejected == 0:
        return WeightProposal(
            proposed=proposal, current_version=current.weight_version, sample_size=len(rows),
            accepted=accepted, rejected=rejected,
            note=(f"only {len(rows)} Accept/Reject decisions with both classes present; "
                  f"at least {MIN_DECISIONS} are needed to re-estimate weights. The feedback "
                  "loop is designed but unproven until then."),
        )

    features = ["demand", "consolidation", "feasibility", "risk"]
    xs = [[row[f] / 100.0 for f in features] for row in rows]
    ys = [1.0 if row["decision"] == "Accept" else 0.0 for row in rows]
    coefficients, bias = _logistic_regression(xs, ys, learning_rate, epochs)
    accuracy = _accuracy(xs, ys, coefficients, bias)

    dimensions = _coefficients_to_dimensions(dict(zip(features, coefficients)))
    proposal.dimensions = dimensions
    proposal.note = (f"logistic regression over {len(rows)} reviewer decisions "
                     f"({accepted} accept, {rejected} reject); in-sample accuracy "
                     f"{accuracy:.2f}. Requires council approval.")
    return WeightProposal(
        proposed=proposal, current_version=current.weight_version, sample_size=len(rows),
        accepted=accepted, rejected=rejected,
        coefficients={f: round(c, 4) for f, c in zip(features, coefficients)},
        accuracy=accuracy, note=proposal.note,
    )


def approve_weights(store: Store, proposal: WeightProposal, approver: str) -> ScoreWeights:
    """A data product council approves a proposal before it becomes the new version."""
    if not approver:
        raise PermissionError("a weight version must be approved by a named council member")
    weights = proposal.proposed
    weights.approved_by = approver
    weights.note = (weights.note or "") + f" Approved by {approver}."
    store.save_weights(weights)
    proposal.approved = True
    return weights


def archetype_override_rates(store: Store) -> list[dict]:
    """Rules overridden more than 30% of the time are rewritten, not re-weighted."""
    totals = store.query(
        "SELECT archetype, COUNT(*) AS n FROM DP_CANDIDATE GROUP BY archetype")
    overrides = store.query(
        "SELECT c.archetype, COUNT(*) AS n FROM REVIEW_DECISION d "
        "JOIN DP_CANDIDATE c ON c.run_id = d.run_id AND c.candidate_id = d.candidate_id "
        "WHERE d.decision = 'Override' AND d.field_overridden IN ('archetype', 'tier') "
        "GROUP BY c.archetype")
    override_by = {row["archetype"]: row["n"] for row in overrides}
    out = []
    for row in totals:
        archetype = row["archetype"] or "unclassified"
        count = row["n"] or 0
        overridden = override_by.get(row["archetype"], 0)
        rate = round(overridden / count, 3) if count else 0.0
        out.append({
            "archetype": archetype,
            "candidates": count,
            "overrides": overridden,
            "override_rate": rate,
            "action": ("rewrite the rule" if rate > OVERRIDE_REWRITE_THRESHOLD
                       else "keep and re-weight thresholds"),
        })
    out.sort(key=lambda r: -r["override_rate"])
    return out


def resolution_advice(store: Store, current_resolution: float = 1.0) -> list[dict]:
    """Merge and Split decisions tune the Louvain resolution per domain."""
    rows = store.query(
        "SELECT c.domain, d.decision, COUNT(*) AS n FROM REVIEW_DECISION d "
        "JOIN DP_CANDIDATE c ON c.run_id = d.run_id AND c.candidate_id = d.candidate_id "
        "WHERE d.decision IN ('Merge', 'Split') GROUP BY c.domain, d.decision")
    by_domain: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = by_domain.setdefault(row["domain"] or "Unassigned", {"Merge": 0, "Split": 0})
        bucket[row["decision"]] = row["n"]
    out = []
    for domain, counts in sorted(by_domain.items()):
        merges, splits = counts["Merge"], counts["Split"]
        delta = 0.0
        if splits > merges:
            delta = 0.15 * min(2, splits - merges)       # split means clusters are too coarse
        elif merges > splits:
            delta = -0.15 * min(2, merges - splits)      # merge means clusters are too fine
        out.append({
            "domain": domain, "merges": merges, "splits": splits,
            "current_resolution": current_resolution,
            "proposed_resolution": round(max(0.4, min(2.0, current_resolution + delta)), 2),
            "rationale": ("reviewers split candidates more often than they merge them, so the "
                          "clusters are too coarse" if splits > merges else
                          "reviewers merge candidates more often than they split them, so the "
                          "clusters are too fine" if merges > splits else
                          "merge and split decisions are balanced; leave the resolution alone"),
        })
    return out


def feedback_report(store: Store, current: ScoreWeights) -> dict:
    proposal = reestimate_weights(store, current)
    return {
        "weights": proposal.to_dict(),
        "archetype_rules": archetype_override_rates(store),
        "clustering_resolution": resolution_advice(store, 1.0),
        "decisions": len(store.decisions()),
        "note": "Weight changes never re-score Accepted candidates; they apply to the next run.",
    }


# --------------------------------------------------------------------------

def _logistic_regression(xs: list[list[float]], ys: list[float], learning_rate: float,
                         epochs: int) -> tuple[list[float], float]:
    n_features = len(xs[0])
    weights = [0.0] * n_features
    bias = 0.0
    n = float(len(xs))
    for _ in range(epochs):
        gradients = [0.0] * n_features
        gradient_bias = 0.0
        for x, y in zip(xs, ys):
            z = bias + sum(w * v for w, v in zip(weights, x))
            prediction = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            error = prediction - y
            for i, value in enumerate(x):
                gradients[i] += error * value
            gradient_bias += error
        for i in range(n_features):
            weights[i] -= learning_rate * gradients[i] / n
        bias -= learning_rate * gradient_bias / n
    return weights, bias


def _accuracy(xs, ys, weights, bias) -> float:
    correct = 0
    for x, y in zip(xs, ys):
        z = bias + sum(w * v for w, v in zip(weights, x))
        prediction = 1.0 if z >= 0 else 0.0
        correct += int(prediction == y)
    return correct / len(xs) if xs else 0.0


def _coefficients_to_dimensions(coefficients: dict[str, float]) -> dict[str, float]:
    """Turn regression coefficients into a composite weight vector.

    The three positive dimensions keep their combined budget of 0.90 and risk
    keeps its negative sign, so the composite stays on the same scale and a
    reviewer can compare versions.
    """
    positives = {k: max(0.0, coefficients.get(k, 0.0))
                 for k in ("demand", "consolidation", "feasibility")}
    total = sum(positives.values())
    if total <= 0:
        return dict(DIMENSION_WEIGHTS)
    budget = 0.90
    out = {k: round(budget * v / total, 3) for k, v in positives.items()}
    risk = coefficients.get("risk", 0.0)
    magnitude = min(0.25, max(0.02, abs(risk) / (abs(risk) + total) if (abs(risk) + total) else 0.1))
    out["risk"] = -round(magnitude, 3)
    return out


def _next_version(current: str) -> str:
    stamp = _dt.date.today().strftime("%Y%m%d")
    return f"v{stamp}-proposed"
