"""Scoring a model honestly, and deciding whether it may be used.

Accuracy on its own is close to meaningless here. With cost-aware labels the
classes are imbalanced, so a model that always predicts the majority class
scores well and knows nothing. Every metric below is therefore reported
against a baseline, and the gate at the bottom refuses a model that does not
clear all of them.

The gate exists because the temptation at this point is enormous: a backtest
showing a good number is the easiest thing in the world to produce and the
hardest to disbelieve.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .validation import FoldResult, stability


def classification_metrics(probs: Sequence[float], actuals: Sequence[int],
                           threshold: float = 0.5) -> dict[str, float]:
    """Accuracy, precision, recall and AUC -- each against its baseline."""
    if not probs:
        return {}
    n = len(probs)
    preds = [1 if p >= threshold else 0 for p in probs]

    tp = sum(1 for p, a in zip(preds, actuals) if p == 1 and a == 1)
    fp = sum(1 for p, a in zip(preds, actuals) if p == 1 and a == 0)
    fn = sum(1 for p, a in zip(preds, actuals) if p == 0 and a == 1)
    tn = sum(1 for p, a in zip(preds, actuals) if p == 0 and a == 0)

    positives = sum(actuals)
    baseline = max(positives, n - positives) / n

    accuracy = (tp + tn) / n
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "baseline": round(baseline, 4),
        # The only accuracy number worth reading.
        "edge_over_baseline": round(accuracy - baseline, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "auc": round(auc_score(probs, actuals), 4),
        "positives_rate": round(positives / n, 4),
        "predicted_positive_rate": round(sum(preds) / n, 4),
        "n": n,
    }


def auc_score(probs: Sequence[float], actuals: Sequence[int]) -> float:
    """ROC AUC by rank, no sklearn needed. 0.5 is a coin flip."""
    pairs = sorted(zip(probs, actuals))
    n_pos = sum(actuals)
    n_neg = len(actuals) - n_pos
    if not n_pos or not n_neg:
        return 0.5

    # Average ranks so ties do not bias the result.
    ranks: list[float] = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1

    rank_sum = sum(r for r, (_, a) in zip(ranks, pairs) if a == 1)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


@dataclass
class StrategyResult:
    total_return_pct: float
    n_trades: int
    win_rate: float
    avg_return_pct: float
    max_drawdown_pct: float


def simulate_from_probabilities(
    probs: Sequence[float],
    forward_returns: Sequence[float],
    threshold: float = 0.6,
    cost_pct: float = 0.20,
) -> StrategyResult:
    """Turn probabilities into a crude equity curve.

    Not a backtest -- the real one lives in engine/backtester.py with fills
    and slippage. This answers a narrower question: do the model's confident
    calls actually pay after costs?
    """
    equity = 100.0
    peak = 100.0
    max_dd = 0.0
    returns: list[float] = []

    for p, fwd in zip(probs, forward_returns):
        if p < threshold:
            continue
        net = fwd - cost_pct
        returns.append(net)
        equity *= 1 + net / 100
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)

    if not returns:
        return StrategyResult(0.0, 0, 0.0, 0.0, 0.0)

    wins = sum(1 for r in returns if r > 0)
    return StrategyResult(
        total_return_pct=round(equity - 100, 2),
        n_trades=len(returns),
        win_rate=round(wins / len(returns) * 100, 2),
        avg_return_pct=round(sum(returns) / len(returns), 4),
        max_drawdown_pct=round(max_dd, 2),
    )


def buy_and_hold(closes: Sequence[float]) -> float:
    if len(closes) < 2 or not closes[0]:
        return 0.0
    return round((closes[-1] - closes[0]) / closes[0] * 100, 2)


@dataclass
class Gate:
    """The Phase 4 gate. Every check must pass before a model is used."""

    passed: bool
    checks: dict[str, dict]

    def summary(self) -> str:
        lines = ["PASS" if self.passed else "FAIL"]
        for name, c in self.checks.items():
            mark = "ok  " if c["passed"] else "FAIL"
            lines.append(f"  {mark} {name}: {c['detail']}")
        return "\n".join(lines)


def evaluate_gate(
    fold_results: list[FoldResult],
    oos_probs: Sequence[float],
    oos_actuals: Sequence[int],
    forward_returns: Sequence[float],
    closes: Sequence[float],
    threshold: float = 0.6,
    cost_pct: float = 0.20,
) -> Gate:
    """Decide whether this model is allowed past Phase 4."""
    checks: dict[str, dict] = {}

    # No out-of-sample predictions at all. Usually every fold was skipped
    # because one class was absent -- labels too tight for the data's
    # volatility. Either way there is nothing to judge, so fail plainly
    # rather than letting an empty metrics dict raise later.
    if not oos_probs:
        return Gate(passed=False, checks={"has_predictions": {
            "passed": False,
            "detail": "koi out-of-sample prediction nahi mili -- shayad har fold "
                      "me ek hi class thi (labels data ki volatility ke liye "
                      "bahut tight hain)",
        }})

    if len(set(oos_actuals)) < 2:
        return Gate(passed=False, checks={"both_classes_present": {
            "passed": False,
            "detail": f"saare labels ek hi class ke hain ({oos_actuals[0]}) -- "
                      f"model ke liye seekhne ko kuch nahi",
        }})

    overall = classification_metrics(oos_probs, oos_actuals)

    # 1. Beat the majority class, out of sample.
    edge = overall.get("edge_over_baseline", 0.0)
    checks["beats_baseline"] = {
        "passed": edge > 0.01,
        "detail": f"accuracy {overall.get('accuracy')} vs baseline "
                  f"{overall.get('baseline')} (edge {edge:+.4f})",
    }

    # 2. AUC meaningfully above a coin flip.
    auc = overall.get("auc", 0.5)
    checks["auc_above_chance"] = {
        "passed": auc > 0.55,
        "detail": f"AUC {auc} (need > 0.55)",
    }

    # 3. Works in every fold, not just on average. One good window and three
    #    bad ones averages to "fine" and is worth nothing.
    auc_stability = stability(fold_results, "auc")
    checks["consistent_across_folds"] = {
        "passed": bool(auc_stability.get("all_folds_positive")),
        "detail": f"AUC per fold min {auc_stability.get('min')} "
                  f"max {auc_stability.get('max')} "
                  f"std {auc_stability.get('std')}",
    }

    # 4. Survives costs.
    strat = simulate_from_probabilities(oos_probs, forward_returns, threshold, cost_pct)
    checks["profitable_after_costs"] = {
        "passed": strat.total_return_pct > 0 and strat.n_trades >= 20,
        "detail": f"{strat.total_return_pct}% over {strat.n_trades} trades "
                  f"(win rate {strat.win_rate}%)",
    }

    # 5. Beat buy and hold. The gate from the roadmap: if it cannot, the
    #    model is an expensive way to own the index.
    bh = buy_and_hold(closes)
    checks["beats_buy_and_hold"] = {
        "passed": strat.total_return_pct > bh,
        "detail": f"strategy {strat.total_return_pct}% vs buy & hold {bh}%",
    }

    # 6. Enough trades to mean anything.
    checks["enough_trades"] = {
        "passed": strat.n_trades >= 30,
        "detail": f"{strat.n_trades} trades (need >= 30 for a usable sample)",
    }

    return Gate(passed=all(c["passed"] for c in checks.values()), checks=checks)


def noise_check(oos_probs: Sequence[float], shuffled_actuals: Sequence[int]) -> dict:
    """Score the model against shuffled labels.

    The most important test in this file. Run the whole pipeline on randomised
    outcomes: AUC must land near 0.5. If it does not, the validation itself is
    leaking, and every other number produced here is fiction.
    """
    auc = auc_score(oos_probs, shuffled_actuals)
    return {
        "auc_on_noise": round(auc, 4),
        "passed": 0.42 < auc < 0.58,
        "detail": f"AUC {auc:.4f} on shuffled labels (must sit near 0.5; "
                  f"anything else means the validation leaks)",
    }
