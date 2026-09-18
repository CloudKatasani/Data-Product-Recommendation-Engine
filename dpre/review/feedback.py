"""The feedback loop (specification section 13.3).

Reviewer decisions are the training signal. Each Accept, Reject, Merge, Split or
override is stored with a reason code, and three things are re-estimated:

1. Score weights, by L2-regularised logistic regression of Accepted against
   Rejected on the four standardised dimensions. The result is a *proposal*;
   a data product council approves it before it becomes the new weight
   version, and only when it beats the majority-class baseline out of sample.
2. Archetype thresholds. Rules overridden more than 30% of the time are listed
   for rewriting, not re-weighting.
3. Clustering resolution, from Merge and Split decisions, per domain.

Statistical discipline (R-17): one row per candidate (the latest decision per
lineage id), at least 50 candidates with at least 10 in each class, the
regularisation strength chosen by leave-one-out, hold-out accuracy reported
against the majority-class baseline, bootstrap 95% intervals per coefficient
from a fixed seed, and a proposal withheld when a coefficient's sign
contradicts the model's own prior (demand, consolidation and feasibility
positive; risk negative) with an interval that excludes zero. A wrong sign the
interval cannot distinguish from zero is reported as unsupported and carries
no weight rather than a hidden clip, and every positive dimension keeps a
floor so a proposal can never drop a dimension outright. Weight changes never
re-score Accepted candidates; they apply to the next run.
"""
from __future__ import annotations

import datetime as _dt
import math
import random
from dataclasses import dataclass, field

from ..config import DIMENSION_WEIGHTS, ScoreWeights
from ..store import Store

FEATURES = ("demand", "consolidation", "feasibility", "risk")
PRIOR_SIGNS = {"demand": 1, "consolidation": 1, "feasibility": 1, "risk": -1}
MIN_DECISIONS = 50          # distinct candidates; section 14: below this the loop is unproven
MIN_PER_CLASS = 10
LAMBDA_GRID = (0.01, 0.1, 1.0, 10.0)
BOOTSTRAP_SAMPLES = 200
BOOTSTRAP_SEED = 17
LOO_MAX = 200               # above this, deterministic 20-fold replaces leave-one-out
MIN_DIMENSION_WEIGHT = 0.05  # no proposal may drop a positive dimension outright
OVERRIDE_REWRITE_THRESHOLD = 0.30

STATUS_INSUFFICIENT = "insufficient sample"
STATUS_CONTRADICTION = "contradiction, not proposed"
STATUS_PROPOSED = "proposed"


class FeedbackGateError(ValueError):
    """Raised when a proposal does not meet the bar for council approval."""


@dataclass
class WeightProposal:
    proposed: ScoreWeights
    current_version: str
    sample_size: int                    # distinct candidates after de-duplication
    accepted: int
    rejected: int
    coefficients: dict[str, float] = field(default_factory=dict)   # standardised scale
    intervals: dict[str, tuple[float, float]] = field(default_factory=dict)
    baseline_accuracy: float = 0.0      # majority class
    holdout_accuracy: float = 0.0       # leave-one-out (or 20-fold) at the chosen lambda
    accuracy: float = 0.0               # in-sample, kept for comparison only
    regularisation: float = 0.0
    cv_scheme: str = ""
    status: str = STATUS_INSUFFICIENT
    contradictions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    approved: bool = False
    note: str = ""

    @property
    def beats_baseline(self) -> bool:
        return self.holdout_accuracy > self.baseline_accuracy

    def to_dict(self) -> dict:
        return {
            "current_version": self.current_version,
            "proposed_version": self.proposed.weight_version,
            "status": self.status,
            "sample_size": self.sample_size,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "coefficients": self.coefficients,
            "intervals_95": {k: list(v) for k, v in self.intervals.items()},
            "baseline_accuracy": round(self.baseline_accuracy, 3),
            "holdout_accuracy": round(self.holdout_accuracy, 3),
            "in_sample_accuracy": round(self.accuracy, 3),
            "beats_baseline": self.beats_baseline,
            "regularisation": self.regularisation,
            "cv_scheme": self.cv_scheme,
            "contradictions": self.contradictions,
            "unsupported": self.unsupported,
            "dimensions": self.proposed.dimensions,
            "approved": self.approved,
            "note": self.note,
        }


def collect_training_rows(store: Store) -> list[dict]:
    """One row per candidate: the latest Accepted/Rejected outcome per lineage id.

    A reversed acceptance therefore trains as Rejected, and ten decisions on the
    same candidate count once.
    """
    rows = store.query(
        "SELECT d.decision_id, d.decision, d.new_status, d.reason_code, d.run_id, d.candidate_id, "
        "       COALESCE(NULLIF(d.lineage_id, ''), NULLIF(c.lineage_id, ''), "
        "                d.run_id || '/' || d.candidate_id) AS lineage_key, "
        "       s.demand, s.consolidation, s.feasibility, s.risk, s.composite, "
        "       c.archetype, c.tier, c.domain "
        "FROM REVIEW_DECISION d "
        "JOIN DP_CANDIDATE_SCORE s ON s.run_id = d.run_id AND s.candidate_id = d.candidate_id "
        "JOIN DP_CANDIDATE c ON c.run_id = d.run_id AND c.candidate_id = d.candidate_id "
        "WHERE COALESCE(d.subject_type, 'candidate') = 'candidate' "
        "  AND (d.new_status IN ('Accepted', 'Rejected') "
        "       OR (d.new_status IS NULL AND d.decision IN ('Accept', 'Reject'))) "
        "ORDER BY d.decision_id")
    latest: dict[str, dict] = {}
    for row in rows:
        status = row["new_status"] or ("Accepted" if row["decision"] == "Accept" else "Rejected")
        row["label"] = 1.0 if status == "Accepted" else 0.0
        row["decision"] = "Accept" if status == "Accepted" else "Reject"
        latest[row["lineage_key"]] = row
    return sorted(latest.values(), key=lambda r: r["lineage_key"])


def reestimate_weights(store: Store, current: ScoreWeights, as_of: _dt.date | None = None,
                       seed: int = BOOTSTRAP_SEED) -> WeightProposal:
    """Fit, validate and either propose a new weight vector or explain why not."""
    rows = collect_training_rows(store)
    accepted = sum(1 for r in rows if r["label"] == 1.0)
    rejected = len(rows) - accepted
    stamp = as_of or _dt.date.today()
    proposal = ScoreWeights(
        weight_version=_next_version(stamp),
        effective_from=stamp.isoformat(),
        dimensions=dict(current.dimensions),
        features={k: dict(v) for k, v in current.features.items()},
        note="proposed from reviewer decisions; not yet approved",
        approved_by="",
    )
    result = WeightProposal(proposed=proposal, current_version=current.weight_version,
                            sample_size=len(rows), accepted=accepted, rejected=rejected)
    if len(rows) < MIN_DECISIONS or min(accepted, rejected) < MIN_PER_CLASS:
        result.note = (
            f"{len(rows)} distinct candidates with a final Accept/Reject ({accepted} accepted, "
            f"{rejected} rejected); at least {MIN_DECISIONS} candidates with at least "
            f"{MIN_PER_CLASS} in each class are needed to re-estimate weights. The feedback "
            "loop is designed but unproven until then.")
        return result

    xs_raw = [[float(r[f]) for f in FEATURES] for r in rows]
    ys = [r["label"] for r in rows]
    xs, means, stds = _standardise(xs_raw)
    result.baseline_accuracy = max(accepted, rejected) / len(rows)

    # lambda by cross-validation: best hold-out accuracy, then lowest log-loss,
    # then the stronger regularisation.
    scheme = "leave-one-out" if len(rows) <= LOO_MAX else "20-fold"
    folds = _folds(len(rows), 1 if scheme == "leave-one-out" else 20)
    scores = []
    for lam in LAMBDA_GRID:
        warm = _fit(xs, ys, lam)
        acc, loss = _cross_validate(xs, ys, lam, folds, warm)
        scores.append((acc, -loss, lam))
    best_acc, neg_loss, lam = max(scores)
    params = _fit(xs, ys, lam)
    coefficients = {f: round(params[i + 1], 4) for i, f in enumerate(FEATURES)}
    result.coefficients = coefficients
    result.regularisation = lam
    result.cv_scheme = scheme
    result.holdout_accuracy = best_acc
    result.accuracy = _accuracy(xs, ys, params)
    result.intervals = _bootstrap_intervals(xs, ys, lam, params, seed)

    def excludes_zero(f: str) -> bool:
        lo, hi = result.intervals[f]
        return lo > 0 or hi < 0

    contradictions = [f for f in FEATURES
                      if coefficients[f] * PRIOR_SIGNS[f] < 0 and excludes_zero(f)]
    unsupported = [f for f in FEATURES if not excludes_zero(f)]
    result.contradictions = contradictions
    result.unsupported = unsupported
    if contradictions:
        result.status = STATUS_CONTRADICTION
        result.note = (
            "the estimate contradicts the model's sign prior on "
            + ", ".join(f"{f} ({coefficients[f]:+.3f}, 95% {list(result.intervals[f])})"
                        for f in contradictions)
            + "; no weight change is proposed. Either the reviewers are trading against the "
              "dimension or the dimension is mis-specified; the council should look at the "
              "reason codes before any re-weighting.")
        return result

    # A wrong sign the data cannot distinguish from zero carries no weight, and
    # says so; it is not silently clipped into the positive budget.
    effective = {f: (coefficients[f] if coefficients[f] * PRIOR_SIGNS[f] > 0 else 0.0)
                 for f in FEATURES}
    proposal.dimensions = _coefficients_to_dimensions(effective)
    result.status = STATUS_PROPOSED
    verdict = "beats" if result.beats_baseline else "does not beat"
    proposal.note = (
        f"L2 logistic regression (lambda {lam}) over {len(rows)} candidates "
        f"({accepted} accepted, {rejected} rejected); {scheme} accuracy "
        f"{best_acc:.2f} {verdict} the majority-class baseline {result.baseline_accuracy:.2f}."
        + (f" Not distinguishable from zero: {', '.join(unsupported)}." if unsupported else "")
        + " Requires council approval.")
    result.note = proposal.note
    return result


def approve_weights(store: Store, proposal: WeightProposal, approver: str, note: str = "",
                    at: str | None = None) -> ScoreWeights:
    """A data product council approves a proposal before it becomes the new version.

    Refused unless the proposal is a proposal (sufficient sample, no sign
    contradiction) and its hold-out accuracy beats the majority-class baseline.
    """
    if not approver:
        raise PermissionError("a weight version must be approved by a named council member")
    if proposal.status != STATUS_PROPOSED:
        raise FeedbackGateError(f"nothing to approve: proposal status is '{proposal.status}' "
                                f"({proposal.note})")
    if not proposal.beats_baseline:
        raise FeedbackGateError(
            f"hold-out accuracy {proposal.holdout_accuracy:.2f} does not beat the majority-class "
            f"baseline {proposal.baseline_accuracy:.2f}; the proposal is not better than "
            "accepting everything the reviewers mostly accept")
    weights = proposal.proposed
    weights.approved_by = approver
    weights.note = (weights.note or "") + f" Approved by {approver}."
    with store.transaction():
        store.save_weights(weights, approved_by=approver)
        store.approve_weight_version(weights.weight_version, approver,
                                     note=note or weights.note, at=at)
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


def usable_without_rework_rate(store: Store) -> dict:
    """Phase 2 exit criterion: reviewers rate >= 70% of cards usable without rework."""
    rows = store.query(
        "SELECT usable_without_rework, rework_needed FROM REVIEW_DECISION "
        "WHERE new_status = 'Accepted' AND usable_without_rework IS NOT NULL")
    rated = len(rows)
    usable = sum(1 for r in rows if r["usable_without_rework"])
    return {"rated": rated, "usable": usable,
            "rate": round(usable / rated, 3) if rated else None, "target": 0.70,
            "meets_target": (usable / rated >= 0.70) if rated else None}


def feedback_report(store: Store, current: ScoreWeights) -> dict:
    proposal = reestimate_weights(store, current)
    in_force = store.current_weights()
    return {
        "weights": proposal.to_dict(),
        "weights_in_force": in_force.to_dict(),
        "archetype_rules": archetype_override_rates(store),
        "clustering_resolution": resolution_advice(store, 1.0),
        "usable_without_rework": usable_without_rework_rate(store),
        "decisions": len(store.decisions()),
        "note": "Weight changes never re-score Accepted candidates; they apply to the next run.",
    }


# --------------------------------------------------------------------------
# Numerics: standardisation, damped Newton for L2 logistic regression,
# cross-validation and a seeded bootstrap. Pure Python, deterministic.
# --------------------------------------------------------------------------

def _standardise(xs: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    n, p = len(xs), len(xs[0])
    means = [sum(x[j] for x in xs) / n for j in range(p)]
    stds = []
    for j in range(p):
        var = sum((x[j] - means[j]) ** 2 for x in xs) / max(1, n - 1)
        stds.append(math.sqrt(var) if var > 1e-12 else 1.0)
    return [[(x[j] - means[j]) / stds[j] for j in range(p)] for x in xs], means, stds


def _sigmoid(z: float) -> float:
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _objective(xs, ys, params, lam) -> float:
    total = 0.0
    for x, y in zip(xs, ys):
        z = params[0] + sum(params[j + 1] * x[j] for j in range(len(x)))
        pr = _sigmoid(z)
        total -= y * math.log(max(pr, 1e-12)) + (1 - y) * math.log(max(1 - pr, 1e-12))
    return total + 0.5 * lam * sum(w * w for w in params[1:])


def _fit(xs: list[list[float]], ys: list[float], lam: float,
         warm: list[float] | None = None, iterations: int = 25) -> list[float]:
    """Damped Newton on the L2-penalised negative log-likelihood; bias unpenalised."""
    p = len(xs[0])
    params = list(warm) if warm else [0.0] * (p + 1)
    current = _objective(xs, ys, params, lam)
    for _ in range(iterations):
        grad = [0.0] * (p + 1)
        hess = [[0.0] * (p + 1) for _ in range(p + 1)]
        for x, y in zip(xs, ys):
            xb = [1.0] + list(x)
            pr = _sigmoid(sum(params[i] * xb[i] for i in range(p + 1)))
            err = pr - y
            wgt = pr * (1.0 - pr)
            for i in range(p + 1):
                grad[i] += err * xb[i]
                row = hess[i]
                for j in range(p + 1):
                    row[j] += wgt * xb[i] * xb[j]
        for j in range(1, p + 1):
            grad[j] += lam * params[j]
            hess[j][j] += lam
        step = _solve(hess, grad)
        scale = 1.0
        for _halving in range(12):
            trial = [params[i] - scale * step[i] for i in range(p + 1)]
            value = _objective(xs, ys, trial, lam)
            if value <= current + 1e-12:
                break
            scale *= 0.5
        else:
            break
        moved = max(abs(scale * s) for s in step)
        params, current = trial, value
        if moved < 1e-8:
            break
    return params


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting on a small SPD system."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        if abs(a[col][col]) < 1e-12:
            a[col][col] = 1e-12
        for r in range(col + 1, n):
            factor = a[r][col] / a[col][col]
            if factor:
                for c in range(col, n + 1):
                    a[r][c] -= factor * a[col][c]
    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        out[r] = (a[r][n] - sum(a[r][c] * out[c] for c in range(r + 1, n))) / a[r][r]
    return out


def _predict(x: list[float], params: list[float]) -> float:
    return _sigmoid(params[0] + sum(params[j + 1] * x[j] for j in range(len(x))))


def _accuracy(xs, ys, params) -> float:
    correct = sum(int((_predict(x, params) >= 0.5) == (y >= 0.5)) for x, y in zip(xs, ys))
    return round(correct / len(xs), 4) if xs else 0.0


def _folds(n: int, k: int) -> list[list[int]]:
    """Leave-one-out when k == 1, otherwise a deterministic modulo assignment."""
    if k == 1:
        return [[i] for i in range(n)]
    return [[i for i in range(n) if i % k == f] for f in range(k)]


def _cross_validate(xs, ys, lam, folds, warm) -> tuple[float, float]:
    correct = 0
    loss = 0.0
    for held in folds:
        held_set = set(held)
        train_x = [x for i, x in enumerate(xs) if i not in held_set]
        train_y = [y for i, y in enumerate(ys) if i not in held_set]
        params = _fit(train_x, train_y, lam, warm=warm, iterations=8)
        for i in held:
            pr = _predict(xs[i], params)
            correct += int((pr >= 0.5) == (ys[i] >= 0.5))
            loss -= ys[i] * math.log(max(pr, 1e-12)) + (1 - ys[i]) * math.log(max(1 - pr, 1e-12))
    return round(correct / len(xs), 4), round(loss / len(xs), 6)


def _bootstrap_intervals(xs, ys, lam, warm, seed: int) -> dict[str, tuple[float, float]]:
    rng = random.Random(seed)
    n = len(xs)
    draws: dict[str, list[float]] = {f: [] for f in FEATURES}
    for _ in range(BOOTSTRAP_SAMPLES):
        idx = [rng.randrange(n) for _ in range(n)]
        sample_y = [ys[i] for i in idx]
        if not 0.0 < sum(sample_y) < len(sample_y):
            continue
        params = _fit([xs[i] for i in idx], sample_y, lam, warm=warm, iterations=10)
        for j, f in enumerate(FEATURES):
            draws[f].append(params[j + 1])
    out = {}
    for f in FEATURES:
        values = sorted(draws[f])
        if not values:
            out[f] = (0.0, 0.0)
            continue
        lo = values[max(0, int(math.floor(0.025 * (len(values) - 1))))]
        hi = values[min(len(values) - 1, int(math.ceil(0.975 * (len(values) - 1))))]
        out[f] = (round(lo, 4), round(hi, 4))
    return out


def _coefficients_to_dimensions(coefficients: dict[str, float]) -> dict[str, float]:
    """Turn sign-checked coefficients into a composite weight vector.

    The three positive dimensions keep their combined budget of 0.90, each with
    a floor of MIN_DIMENSION_WEIGHT, and risk keeps its negative sign, so the
    composite stays on the same scale and a reviewer can compare versions.
    Signs were verified before this is called, so nothing is clipped here.
    """
    positives = {k: coefficients.get(k, 0.0) for k in ("demand", "consolidation", "feasibility")}
    total = sum(positives.values())
    if total <= 0:
        return dict(DIMENSION_WEIGHTS)
    budget = 0.90
    spread = budget - MIN_DIMENSION_WEIGHT * len(positives)
    out = {k: round(MIN_DIMENSION_WEIGHT + spread * v / total, 3) for k, v in positives.items()}
    risk = abs(coefficients.get("risk", 0.0))
    magnitude = min(0.25, max(0.02, risk / (risk + total))) if (risk + total) else 0.1
    out["risk"] = -round(magnitude, 3)
    return out


def _next_version(stamp: _dt.date) -> str:
    return f"v{stamp.strftime('%Y%m%d')}-proposed"
