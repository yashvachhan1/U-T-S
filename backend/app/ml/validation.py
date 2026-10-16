"""Walk-forward validation, purged and embargoed.

This file is the one that decides whether any later number means anything.

A random train/test split on a time series lets the model learn from the
future and then be graded on the past. It will look superb. It will also be
worthless. So every split here is strictly forward: train on the past, test on
what came next.

Two subtler leaks remain even then, and both are handled:

**Purging.** A label on bar i built from the next N bars is not resolved until
bar i+N. If bar i sits in training and i+N falls inside the test window, the
training row already contains the test period's outcome. Those rows are
dropped from training.

**Embargo.** Features are built from rolling windows, so a training row just
before the test window shares inputs with it. A gap after the test window
keeps that overlap out of the next training fold.

Both come from Lopez de Prado's work on financial cross-validation. Skipping
them is the most common way a backtest ends up lying.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence


@dataclass
class Split:
    fold: int
    train_idx: list[int]
    test_idx: list[int]
    purged: int = 0
    embargoed: int = 0

    @property
    def train_size(self) -> int:
        return len(self.train_idx)

    @property
    def test_size(self) -> int:
        return len(self.test_idx)

    def summary(self) -> dict:
        return {
            "fold": self.fold,
            "train": self.train_size,
            "test": self.test_size,
            "purged": self.purged,
            "embargoed": self.embargoed,
        }


class WalkForward:
    """Expanding or rolling window, always forward in time.

    `expanding=True` keeps all history in training, which suits slow-moving
    relationships. `expanding=False` uses a fixed lookback, which adapts
    faster when the regime changes but has less data per fold.
    """

    def __init__(
        self,
        n_splits: int = 5,
        test_size: int | None = None,
        min_train: int = 100,
        expanding: bool = True,
        embargo: int = 0,
    ):
        if n_splits < 1:
            raise ValueError("n_splits must be >= 1")
        self.n_splits = n_splits
        self.test_size = test_size
        self.min_train = min_train
        self.expanding = expanding
        self.embargo = embargo

    def split(
        self, n_samples: int, resolves_at: Sequence[int] | None = None
    ) -> Iterator[Split]:
        """Yield folds. `resolves_at[i]` is the bar where row i's label is known."""
        test_size = self.test_size or max(
            1, (n_samples - self.min_train) // self.n_splits)

        usable = n_samples - self.min_train
        if usable < self.n_splits:
            raise ValueError(
                f"{n_samples} samples cannot give {self.n_splits} folds "
                f"with min_train={self.min_train}"
            )

        for fold in range(self.n_splits):
            test_start = self.min_train + fold * test_size
            test_end = min(test_start + test_size, n_samples)
            if test_start >= n_samples:
                break

            train_start = 0 if self.expanding else max(0, test_start - self.min_train)
            train_candidates = list(range(train_start, test_start))

            purged = 0
            if resolves_at is not None:
                kept = []
                for i in train_candidates:
                    # Drop any training row whose outcome lands inside the
                    # test window -- it already knows the answer.
                    if resolves_at[i] >= test_start:
                        purged += 1
                    else:
                        kept.append(i)
                train_candidates = kept

            embargoed = 0
            if self.embargo and fold > 0:
                # Rows immediately before this test window share rolling
                # windows with it; hold them out too.
                cutoff = test_start - self.embargo
                before = len(train_candidates)
                train_candidates = [i for i in train_candidates if i < cutoff]
                embargoed = before - len(train_candidates)

            if not train_candidates:
                continue

            yield Split(
                fold=fold,
                train_idx=train_candidates,
                test_idx=list(range(test_start, test_end)),
                purged=purged,
                embargoed=embargoed,
            )


def check_no_leakage(split: Split, resolves_at: Sequence[int] | None = None) -> list[str]:
    """Assert the invariants a split must satisfy. Empty list means clean."""
    problems = []

    if not split.train_idx or not split.test_idx:
        return ["empty train or test set"]

    if max(split.train_idx) >= min(split.test_idx):
        problems.append(
            f"train reaches index {max(split.train_idx)} but test starts at "
            f"{min(split.test_idx)} -- training on the future"
        )

    if set(split.train_idx) & set(split.test_idx):
        problems.append("train and test overlap")

    if resolves_at is not None:
        test_start = min(split.test_idx)
        leaked = [i for i in split.train_idx if resolves_at[i] >= test_start]
        if leaked:
            problems.append(
                f"{len(leaked)} training rows resolve inside the test window "
                f"-- purging failed"
            )

    return problems


@dataclass
class FoldResult:
    fold: int
    train_size: int
    test_size: int
    metrics: dict[str, float]


def stability(results: list[FoldResult], metric: str) -> dict[str, float]:
    """How much a metric moves between folds.

    A model that scores 0.65 on one fold and 0.48 on the next has not found
    something durable; it has fitted each window separately. Consistency
    across folds matters more than the average.
    """
    values = [r.metrics[metric] for r in results if metric in r.metrics]
    if not values:
        return {}
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    std = var ** 0.5
    return {
        "mean": round(mean, 4),
        "std": round(std, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "folds": n,
        # Low spread relative to the mean is the thing worth having.
        "consistency": round(1 - (std / abs(mean)), 4) if mean else 0.0,
        "all_folds_positive": all(v > 0.5 for v in values),
    }
