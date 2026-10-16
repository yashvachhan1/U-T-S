"""Phase 4: labelling, validation, and whether the pipeline can be fooled.

The two tests that matter most are at the bottom:

  * given a feature that genuinely predicts, the pipeline must **find** it
  * given pure noise, it must **find nothing**

A pipeline that passes only the first is worse than useless, because it will
confidently report edge that does not exist.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ml import evaluate as ev  # noqa: E402
from app.ml import labels as lb  # noqa: E402
from app.ml.validation import WalkForward, check_no_leakage, stability  # noqa: E402

lgb = pytest.importorskip("lightgbm")


# ============================================================== labelling
def test_labels_ignore_moves_that_do_not_clear_costs():
    """A 0.1% move is not a signal once brokerage and slippage are paid."""
    cfg = lb.LabelConfig(horizon=1, cost_pct=0.2, threshold_multiple=1.5)  # 0.3% bar
    closes = [100.0, 100.1, 100.0, 101.0, 100.0, 99.0]
    out = lb.fixed_horizon_labels(closes, cfg)
    assert out[0].direction is lb.Direction.FLAT      # +0.1%, below the bar
    assert out[2].direction is lb.Direction.UP        # +1.0%
    assert out[3].direction is lb.Direction.DOWN      # -0.99%


def test_label_records_when_it_resolves():
    """Validation needs this to purge overlapping training rows."""
    cfg = lb.LabelConfig(horizon=5)
    out = lb.fixed_horizon_labels([100.0 + i for i in range(20)], cfg)
    assert out[0].resolves_at == 5
    assert out[3].resolves_at == 8


def test_labels_stop_before_running_off_the_end():
    cfg = lb.LabelConfig(horizon=5)
    out = lb.fixed_horizon_labels([100.0] * 10, cfg)
    assert len(out) == 5
    assert max(lab.resolves_at for lab in out) == 9


def test_triple_barrier_resolves_on_first_touch():
    cfg = lb.LabelConfig(horizon=10, cost_pct=1.0, threshold_multiple=1.0)  # 1% bars
    closes = [100.0, 100.2, 101.5, 100.0]
    highs = [100.0, 100.4, 101.6, 100.1]
    lows = [100.0, 100.0, 100.3, 99.9]
    out = lb.triple_barrier_labels(highs, lows, closes, cfg)
    assert out[0].direction is lb.Direction.UP
    assert out[0].bars_held == 2            # touched on bar 2, not held to 10


def test_triple_barrier_assumes_the_stop_hit_first():
    """Both barriers inside one bar: assume the bad one. The path is unknown,
    and assuming the good outcome is how a backtest flatters itself."""
    cfg = lb.LabelConfig(horizon=5, cost_pct=1.0, threshold_multiple=1.0)
    closes = [100.0, 100.0]
    highs = [100.0, 102.0]       # clears the target
    lows = [100.0, 98.0]         # and the stop
    out = lb.triple_barrier_labels(highs, lows, closes, cfg)
    assert out[0].direction is lb.Direction.DOWN


def test_distribution_reports_the_majority_baseline():
    cfg = lb.LabelConfig(horizon=1, cost_pct=0.2)
    out = lb.fixed_horizon_labels([100.0] * 50, cfg)
    dist = lb.label_distribution(out)
    assert dist["flat"] == 1.0
    assert dist["majority_baseline"] == 1.0      # all one class


def test_overlapping_labels_are_down_weighted():
    """Bars i and i+1 share most of their future, so they are not two
    independent observations."""
    cfg = lb.LabelConfig(horizon=10)
    out = lb.fixed_horizon_labels([100.0 + i for i in range(60)], cfg)
    weights = lb.sample_weights(out)
    assert len(weights) == len(out)
    assert all(w > 0 for w in weights)
    # Heavy overlap means the average weight must fall below one unit each.
    assert min(weights) < 1.0


# ============================================================ validation
def test_training_never_reaches_past_the_test_window():
    cv = WalkForward(n_splits=4, min_train=100)
    for split in cv.split(500):
        assert max(split.train_idx) < min(split.test_idx)
        assert check_no_leakage(split) == []


def test_purging_drops_rows_whose_outcome_is_in_the_test_window():
    """Without this, training rows already contain the test period's answer."""
    n, horizon = 300, 10
    resolves = [i + horizon for i in range(n)]

    unpurged = list(WalkForward(n_splits=3, min_train=100).split(n))
    purged = list(WalkForward(n_splits=3, min_train=100).split(n, resolves))

    assert all(s.purged > 0 for s in purged)
    assert all(p.train_size < u.train_size
               for p, u in zip(purged, unpurged))
    for split in purged:
        assert check_no_leakage(split, resolves) == []


def test_leak_detector_catches_a_bad_split():
    from app.ml.validation import Split

    bad = Split(fold=0, train_idx=list(range(50)), test_idx=list(range(40, 60)))
    problems = check_no_leakage(bad)
    assert problems
    assert any("future" in p or "overlap" in p for p in problems)


def test_embargo_removes_rows_adjacent_to_the_test_window():
    """Rolling-window features make neighbouring rows share inputs."""
    plain = list(WalkForward(n_splits=3, min_train=100, embargo=0).split(400))
    gapped = list(WalkForward(n_splits=3, min_train=100, embargo=20).split(400))
    assert any(s.embargoed > 0 for s in gapped)
    assert gapped[-1].train_size < plain[-1].train_size


def test_expanding_window_grows_rolling_does_not():
    exp = list(WalkForward(n_splits=4, min_train=100, expanding=True).split(500))
    roll = list(WalkForward(n_splits=4, min_train=100, expanding=False).split(500))
    assert exp[-1].train_size > exp[0].train_size
    assert roll[-1].train_size <= roll[0].train_size + 1


def test_too_few_samples_is_refused():
    with pytest.raises(ValueError, match="cannot give"):
        list(WalkForward(n_splits=5, min_train=100).split(102))


def test_stability_flags_an_inconsistent_model():
    from app.ml.validation import FoldResult

    steady = [FoldResult(i, 100, 50, {"auc": a}) for i, a in enumerate([.61, .60, .62, .59])]
    erratic = [FoldResult(i, 100, 50, {"auc": a}) for i, a in enumerate([.75, .45, .70, .42])]

    assert stability(steady, "auc")["all_folds_positive"] is True
    assert stability(erratic, "auc")["all_folds_positive"] is False
    assert stability(steady, "auc")["std"] < stability(erratic, "auc")["std"]


# ================================================================ metrics
def test_auc_endpoints():
    assert ev.auc_score([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    assert ev.auc_score([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == 0.0
    assert ev.auc_score([0.5] * 4, [1, 0, 1, 0]) == 0.5       # all ties


def test_accuracy_is_reported_against_the_baseline():
    """90% accuracy on a 90/10 split is zero skill, and must read that way."""
    probs = [0.9] * 90 + [0.9] * 10
    actuals = [1] * 90 + [0] * 10
    m = ev.classification_metrics(probs, actuals)
    assert m["accuracy"] == 0.9
    assert m["baseline"] == 0.9
    assert m["edge_over_baseline"] == 0.0


def test_calibration_error_detects_overconfidence():
    from app.ml.model import calibration_error

    honest = ev_probs = [0.8] * 100
    outcomes_good = [1] * 80 + [0] * 20            # says 0.8, is right 80%
    outcomes_bad = [1] * 50 + [0] * 50             # says 0.8, is right 50%

    assert calibration_error(honest, outcomes_good)["ece"] < 0.05
    assert calibration_error(ev_probs, outcomes_bad)["ece"] > 0.25


def test_simulation_charges_costs_on_every_trade():
    probs = [0.9] * 10
    forward = [0.1] * 10                 # gross gains below the 0.2% cost
    out = ev.simulate_from_probabilities(probs, forward, threshold=0.6, cost_pct=0.2)
    assert out.n_trades == 10
    assert out.total_return_pct < 0      # net negative, correctly


def test_simulation_skips_low_confidence_calls():
    out = ev.simulate_from_probabilities([0.3] * 10, [5.0] * 10, threshold=0.6)
    assert out.n_trades == 0


# ================================== the two that decide if any of this works
def _make_dataset(n: int, signal_strength: float, horizon: int = 3, seed: int = 7):
    """Features where one column genuinely predicts the FUTURE.

    The direction matters and is easy to get wrong: the label on bar i is the
    return from i to i+horizon, so the signal at bar i has to drive the moves
    that come *after* it. Wiring it to the move into bar i instead produces a
    dataset with no forward edge at all -- which is what a correct pipeline
    will (rightly) report.
    """
    random.seed(seed)
    signals = [random.gauss(0, 1) for _ in range(n)]

    closes = [100.0]
    for i in range(n):
        # Price at step i is pushed by the signals from the preceding
        # `horizon` bars, so each signal shows up in the window its own label
        # measures.
        drift = sum(signals[max(0, i - horizon):i]) / horizon * signal_strength
        closes.append(closes[-1] * (1 + (drift + random.gauss(0, 0.004)) / 100))

    X = [[signals[i], random.gauss(0, 1), random.gauss(0, 1), random.gauss(0, 1)]
         for i in range(n)]
    return X, closes[1:], signals


def test_pipeline_finds_a_real_signal():
    """Sanity: with a planted edge, walk-forward must detect it."""
    from app.ml.model import ModelConfig, walk_forward_train

    X, closes, _ = _make_dataset(1200, signal_strength=0.8)
    cfg = lb.LabelConfig(horizon=3, cost_pct=0.05, threshold_multiple=1.0)
    labels = lb.fixed_horizon_labels(closes, cfg)

    X = X[:len(labels)]
    y = [1 if lab.direction is lb.Direction.UP else 0 for lab in labels]
    resolves = [lab.resolves_at for lab in labels]

    results, oos = walk_forward_train(
        X, y, ["signal", "n1", "n2", "n3"], resolves,
        cv=WalkForward(n_splits=4, min_train=300, embargo=5),
        config=ModelConfig(n_estimators=120),
    )

    probs = [p for _, p, _ in oos]
    actuals = [a for _, _, a in oos]
    auc = ev.auc_score(probs, actuals)
    assert auc > 0.60, f"planted signal not found, AUC {auc:.3f}"


def test_pipeline_finds_nothing_in_pure_noise():
    """The important one.

    Prices are a random walk with no relationship to the features. Walk-forward
    AUC must sit near 0.5. If it does not, the validation leaks and every
    other number this pipeline produces is fiction.
    """
    from app.ml.model import ModelConfig, walk_forward_train

    random.seed(99)
    X = [[random.gauss(0, 1) for _ in range(6)] for _ in range(1200)]
    closes, p = [], 100.0
    for _ in range(1200):
        p *= 1 + random.gauss(0, 0.01)
        closes.append(p)

    cfg = lb.LabelConfig(horizon=3, cost_pct=0.05, threshold_multiple=1.0)
    labels = lb.fixed_horizon_labels(closes, cfg)
    X = X[:len(labels)]
    y = [1 if lab.direction is lb.Direction.UP else 0 for lab in labels]
    resolves = [lab.resolves_at for lab in labels]

    results, oos = walk_forward_train(
        X, y, [f"noise{i}" for i in range(6)], resolves,
        cv=WalkForward(n_splits=4, min_train=300, embargo=5),
        config=ModelConfig(n_estimators=120),
    )

    probs = [p for _, p, _ in oos]
    actuals = [a for _, _, a in oos]
    auc = ev.auc_score(probs, actuals)
    assert 0.40 < auc < 0.60, (
        f"AUC {auc:.3f} on pure noise -- the validation is leaking and "
        f"every other result from this pipeline is meaningless"
    )


def test_noise_check_helper_flags_a_leaking_pipeline():
    # Probabilities unrelated to outcomes -- AUC should land near 0.5.
    random.seed(3)
    probs = [random.random() for _ in range(400)]
    outcomes = [random.randint(0, 1) for _ in range(400)]
    assert ev.noise_check(probs, outcomes)["passed"]

    # Perfect separation on supposedly shuffled labels means a leak.
    leaking = ev.noise_check([0.9, 0.9, 0.1, 0.1] * 25, [1, 1, 0, 0] * 25)
    assert not leaking["passed"]


# ===================================================================== gate
def test_gate_refuses_a_model_that_loses_to_buy_and_hold():
    from app.ml.validation import FoldResult

    folds = [FoldResult(i, 100, 50, {"auc": 0.62}) for i in range(4)]
    probs = [0.8] * 60
    actuals = [1] * 36 + [0] * 24
    forward = [0.3] * 60                 # +0.1% net each after 0.2% cost
    closes = [100.0, 500.0]              # buy & hold +400%

    gate = ev.evaluate_gate(folds, probs, actuals, forward, closes)
    assert not gate.passed
    assert not gate.checks["beats_buy_and_hold"]["passed"]


def test_gate_refuses_a_model_that_only_works_on_some_folds():
    from app.ml.validation import FoldResult

    folds = [FoldResult(i, 100, 50, {"auc": a}) for i, a in enumerate([.72, .41, .68, .45])]
    gate = ev.evaluate_gate(folds, [0.8] * 60, [1] * 40 + [0] * 20,
                            [2.0] * 60, [100.0, 101.0])
    assert not gate.checks["consistent_across_folds"]["passed"]


def test_gate_refuses_a_model_with_too_few_trades():
    from app.ml.validation import FoldResult

    folds = [FoldResult(i, 100, 50, {"auc": 0.65}) for i in range(4)]
    probs = [0.9] * 5 + [0.1] * 95       # only 5 confident calls
    actuals = [1] * 5 + [0] * 95
    gate = ev.evaluate_gate(folds, probs, actuals, [3.0] * 100, [100.0, 101.0])
    assert not gate.checks["enough_trades"]["passed"]


def test_gate_summary_lists_every_check():
    from app.ml.validation import FoldResult

    folds = [FoldResult(i, 100, 50, {"auc": 0.6}) for i in range(3)]
    gate = ev.evaluate_gate(folds, [0.7] * 50, [1] * 30 + [0] * 20,
                            [1.0] * 50, [100.0, 105.0])
    text = gate.summary()
    for name in ("beats_baseline", "auc_above_chance", "beats_buy_and_hold"):
        assert name in text


def test_gate_fails_cleanly_when_no_predictions_exist():
    """Every fold skipped (one class only) must fail, not raise."""
    gate = ev.evaluate_gate([], [], [], [], [100.0, 101.0])
    assert not gate.passed
    assert "has_predictions" in gate.checks
    assert "ek hi class" in gate.checks["has_predictions"]["detail"]


def test_gate_fails_when_labels_are_single_class():
    from app.ml.validation import FoldResult

    folds = [FoldResult(0, 100, 50, {"auc": 0.6})]
    gate = ev.evaluate_gate(folds, [0.7] * 50, [0] * 50, [1.0] * 50, [100.0, 101.0])
    assert not gate.passed
    assert not gate.checks["both_classes_present"]["passed"]
