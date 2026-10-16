"""Model training and calibration.

Gradient boosting on tabular features, because that is what works on this kind
of data. An LLM reading "price was 100, 101, 99" cannot beat it, which is why
the agents handle language and judgement while this handles numbers.

Calibration is not optional here. Position sizing reads the probability, so
"0.7" has to mean "right about 70% of the time". Raw boosting output is
confident in a way it has not earned, and an uncalibrated 0.7 sizing a trade
as though it were 70% is how a model with real edge still loses money.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .validation import FoldResult, Split, WalkForward, check_no_leakage


def _import_lightgbm():
    try:
        import lightgbm as lgb
        return lgb
    except ImportError as exc:                     # pragma: no cover
        raise RuntimeError(
            "lightgbm nahi mila. Chalayein: pip install lightgbm"
        ) from exc


@dataclass
class ModelConfig:
    # Deliberately small. The honest constraint is data, not compute: with a
    # few thousand rows, a deep model memorises the sample.
    n_estimators: int = 200
    learning_rate: float = 0.05
    max_depth: int = 4
    num_leaves: int = 15
    min_child_samples: int = 50
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_lambda: float = 1.0
    random_state: int = 42

    def to_lgb(self) -> dict[str, Any]:
        return {
            "n_estimators": self.n_estimators,
            "learning_rate": self.learning_rate,
            "max_depth": self.max_depth,
            "num_leaves": self.num_leaves,
            "min_child_samples": self.min_child_samples,
            "subsample": self.subsample,
            "subsample_freq": 1,
            "colsample_bytree": self.colsample_bytree,
            "reg_lambda": self.reg_lambda,
            "random_state": self.random_state,
            "verbose": -1,
            "n_jobs": 1,
        }


class IsotonicCalibrator:
    """Maps raw scores onto probabilities that mean what they say.

    Fitted on held-out predictions only. Calibrating on training predictions
    just relearns the overfit.
    """

    def __init__(self) -> None:
        self._iso = None

    def fit(self, scores: Sequence[float], outcomes: Sequence[int]) -> IsotonicCalibrator:
        from sklearn.isotonic import IsotonicRegression

        self._iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        self._iso.fit(list(scores), list(outcomes))
        return self

    def transform(self, scores: Sequence[float]) -> list[float]:
        if self._iso is None:
            return list(scores)
        return [float(p) for p in self._iso.predict(list(scores))]

    @property
    def fitted(self) -> bool:
        return self._iso is not None


def calibration_error(probs: Sequence[float], outcomes: Sequence[int],
                      bins: int = 10) -> dict[str, float]:
    """Expected calibration error, plus the worst bin.

    Groups predictions by confidence and compares each group's stated
    probability with how often it was actually right.
    """
    if not probs:
        return {}
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in zip(probs, outcomes):
        idx = min(int(p * bins), bins - 1)
        buckets[idx].append((p, y))

    total = len(probs)
    ece = 0.0
    worst = 0.0
    detail = []
    for b, items in enumerate(buckets):
        if not items:
            continue
        avg_p = sum(p for p, _ in items) / len(items)
        actual = sum(y for _, y in items) / len(items)
        gap = abs(avg_p - actual)
        ece += gap * len(items) / total
        worst = max(worst, gap)
        detail.append({"bin": b, "n": len(items),
                       "predicted": round(avg_p, 3), "actual": round(actual, 3)})

    return {"ece": round(ece, 4), "max_gap": round(worst, 4), "bins": detail}


@dataclass
class TrainedModel:
    booster: Any
    calibrator: IsotonicCalibrator
    feature_names: list[str]
    feature_set_hash: str = ""
    config: ModelConfig = field(default_factory=ModelConfig)

    def predict_proba(self, X: Sequence[Sequence[float]]) -> list[float]:
        raw = self.booster.predict_proba(list(X))[:, 1]
        return self.calibrator.transform([float(p) for p in raw])

    def importance(self) -> dict[str, float]:
        gains = self.booster.feature_importances_
        total = sum(gains) or 1
        return {
            name: round(float(g) / total, 4)
            for name, g in sorted(zip(self.feature_names, gains),
                                  key=lambda kv: -kv[1])
        }


def train_once(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    feature_names: list[str],
    config: ModelConfig | None = None,
    sample_weight: Sequence[float] | None = None,
) -> Any:
    lgb = _import_lightgbm()
    config = config or ModelConfig()
    model = lgb.LGBMClassifier(**config.to_lgb())
    model.fit(list(X), list(y),
              sample_weight=list(sample_weight) if sample_weight else None)
    return model


def walk_forward_train(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    feature_names: list[str],
    resolves_at: Sequence[int],
    cv: WalkForward | None = None,
    config: ModelConfig | None = None,
    sample_weight: Sequence[float] | None = None,
) -> tuple[list[FoldResult], list[tuple[int, float, int]]]:
    """Train and score fold by fold, always forward.

    Returns per-fold metrics and every out-of-sample prediction as
    (index, probability, actual). Those predictions are the only honest
    material for calibration and for the backtest.
    """
    from .evaluate import classification_metrics

    cv = cv or WalkForward(n_splits=5, min_train=max(100, len(X) // 4), embargo=5)
    config = config or ModelConfig()

    results: list[FoldResult] = []
    oos: list[tuple[int, float, int]] = []

    for split in cv.split(len(X), resolves_at):
        problems = check_no_leakage(split, resolves_at)
        if problems:
            raise RuntimeError(f"fold {split.fold} leaks: {problems}")

        X_tr = [X[i] for i in split.train_idx]
        y_tr = [y[i] for i in split.train_idx]
        w_tr = [sample_weight[i] for i in split.train_idx] if sample_weight else None

        # A fold where one class is absent teaches nothing.
        if len(set(y_tr)) < 2:
            continue

        booster = train_once(X_tr, y_tr, feature_names, config, w_tr)

        X_te = [X[i] for i in split.test_idx]
        y_te = [y[i] for i in split.test_idx]
        probs = [float(p) for p in booster.predict_proba(X_te)[:, 1]]

        for idx, prob, actual in zip(split.test_idx, probs, y_te):
            oos.append((idx, prob, actual))

        results.append(FoldResult(
            fold=split.fold,
            train_size=split.train_size,
            test_size=split.test_size,
            metrics=classification_metrics(probs, y_te),
        ))

    return results, oos


def fit_final(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    feature_names: list[str],
    oos_predictions: list[tuple[int, float, int]],
    config: ModelConfig | None = None,
    feature_set_hash: str = "",
    sample_weight: Sequence[float] | None = None,
) -> TrainedModel:
    """Fit on everything, calibrate on the out-of-sample predictions.

    The split matters: the booster may see all the data, but the calibrator
    may only see predictions the model made on data it had not trained on.
    """
    booster = train_once(X, y, feature_names, config, sample_weight)

    calibrator = IsotonicCalibrator()
    if oos_predictions:
        scores = [p for _, p, _ in oos_predictions]
        actuals = [a for _, _, a in oos_predictions]
        if len(set(actuals)) > 1:
            calibrator.fit(scores, actuals)

    return TrainedModel(
        booster=booster,
        calibrator=calibrator,
        feature_names=list(feature_names),
        feature_set_hash=feature_set_hash,
        config=config or ModelConfig(),
    )


def importance_stability(
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    feature_names: list[str],
    resolves_at: Sequence[int],
    cv: WalkForward | None = None,
    config: ModelConfig | None = None,
) -> dict[str, dict[str, float]]:
    """Does the model lean on the same features in every window?

    A feature that dominates one fold and vanishes in the next was fitting
    that window, not the market. Rank stability is a better guide to what to
    keep than average importance.
    """
    cv = cv or WalkForward(n_splits=5, min_train=max(100, len(X) // 4), embargo=5)
    per_fold: list[dict[str, float]] = []

    for split in cv.split(len(X), resolves_at):
        X_tr = [X[i] for i in split.train_idx]
        y_tr = [y[i] for i in split.train_idx]
        if len(set(y_tr)) < 2:
            continue
        booster = train_once(X_tr, y_tr, feature_names, config)
        gains = booster.feature_importances_
        total = sum(gains) or 1
        per_fold.append({n: float(g) / total for n, g in zip(feature_names, gains)})

    if not per_fold:
        return {}

    out: dict[str, dict[str, float]] = {}
    for name in feature_names:
        vals = [f.get(name, 0.0) for f in per_fold]
        mean = sum(vals) / len(vals)
        std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        out[name] = {
            "mean": round(mean, 4),
            "std": round(std, 4),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "stable": bool(mean > 0.01 and std < mean),
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["mean"]))
