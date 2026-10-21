"""Phase 7: the paper forward-test, and the evidence it is supposed to produce.

The roadmap puts it bluntly -- *"ye skip karne ka mann karega, mat karna"*.
The temptation is strong because a backtest already said the strategy works,
and two months of paper trading feels like waiting for permission.

It is not waiting. A backtest is a claim fitted to the past by someone who
knew the answers. A forward test is the first evidence that nobody knew the
answers in advance, and it is also the only thing that tests the half of the
system a backtest cannot touch: whether the process runs every day without
crashing, whether orders fill, whether the agents are worth their latency.

    journal.py      one row per day -- drawdown, execution, uptime
    attribution.py  per-agent value-add, replayed from stored views
    compare.py      paper against the registered backtest baseline
    report.py       all of it, plus the go-live checklist

Nothing here changes a decision. It only measures, which is the point: the
measuring has to be independent of the thing being measured.
"""
from . import attribution, compare, journal, report  # noqa: F401

__all__ = ["attribution", "compare", "journal", "report"]
