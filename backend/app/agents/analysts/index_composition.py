"""Agent 11 — Index Composition.

The Nifty is not a thing that moves; it is the weighted sum of fifty things
that move. If we are watching all fifty live, we can see what the index
number hides.

The key output is **breadth**, and it is computed in code, not by the model.
Counting advancers among fifty stocks is arithmetic: a model summarising
fifty quotes will miscount some, and a miscount here is silent. So Python
counts, and the agent is asked only what the count means.

Breadth also sidesteps the one thing NSE does not publish. Constituents are
public, weightings are not. Breadth needs no weights at all -- and
"Nifty +0.5% with 40 of 50 stocks down" is a more useful sentence than any
weighted figure, because a move carried by four heavyweights is the kind
that reverses.
"""
from __future__ import annotations

from ...collectors import constituents
from ...engine import feed_service
from ...store import events
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import IndexCompositionOutput
from . import _shared as sh

SCHEMA_EXTRA = ('  "index": "NIFTY" | "BANKNIFTY",\n'
                '  "breadth_read": "broad" | "narrow" | "mixed"')

TASK = f"""Judge the quality of today's index move from its constituents.

The numbers below are computed from live constituent prices, not estimated.
Trust them and interpret them:
- A move with most constituents going the same way is broad, and broad moves
  tend to continue.
- A move where the index and the median stock disagree, or where the gap
  between them is wide, is narrow: a few heavyweights are carrying it. Narrow
  moves reverse more often, so a narrow move in your favour is a reason to
  take less size, not more.
- An index up on poor breadth deserves risk_flag even though it looks
  bullish. That combination is the main thing this agent exists to catch.
- Coverage matters. If only a fraction of constituents are quoting, say so
  and lower your confidence -- breadth from fifteen of fifty stocks is not
  breadth.
- Name individual stocks in affected_symbols only when they appear below.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""

# label -> (constituent list, index name as the instrument master spells it)
INDEXES = {
    "NIFTY": ("nifty50", "Nifty 50"),
    "BANKNIFTY": ("banknifty", "Nifty Bank"),
}

# Beyond this the constituent list is reported as old in the prompt.
STALE_LIST_DAYS = 14


class IndexCompositionAgent(Agent):
    # Hundreds of constituents, and breadth needs only the last
    # price and the previous close. Full depth on five hundred
    # stocks would be most of the feed budget for data nothing
    # here reads.
    feed_mode = "ltpc"
    output_model = IndexCompositionOutput

    def __init__(self, index: str = "NIFTY", top_n: int = 5) -> None:
        if index not in INDEXES:
            raise KeyError(f"Unknown index: {index}")
        self.index = index
        self.list_name, self.index_name = INDEXES[index]
        self.top_n = top_n
        super().__init__(AgentConfig(
            name=f"index_composition_{index.lower()}",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=700,
            # Breadth is a fast-moving intraday read.
            freshness_seconds=900,
        ))

    def instrument_keys(self) -> dict[str, str]:
        """The index itself plus every constituent -- breadth needs all of them."""
        keys = {self.index_name: sh.index_key(self.index_name)}
        keys.update(constituents.instrument_keys(self.list_name))
        return keys

    def breadth(self) -> dict:
        """Advance/decline and narrowness, computed here rather than guessed.

        Returns the raw dict from the constituents module so the API and the
        tests can read the same numbers the prompt shows.
        """
        changes = constituents.live_changes(self.list_name, feed_service.latest)
        key = sh.index_key(self.index_name)
        tick = feed_service.latest(key) if key else None
        index_change = tick.change_pct if tick else None
        out = constituents.breadth(changes, index_change)
        out["members"] = len(constituents.load(self.list_name))
        out["changes"] = changes
        return out

    async def gather(self) -> str:
        data = self.breadth()
        members = data["members"]

        if not members:
            return (f"The {self.index} constituent list has not been "
                    f"downloaded yet, so breadth cannot be computed.")

        if not data.get("available"):
            return (f"{self.index} has {members} constituents but none are "
                    f"quoting on the live feed right now, so breadth cannot "
                    f"be computed. The market may be closed or the "
                    f"constituents may not be subscribed.")

        changes = data["changes"]
        ranked = sorted(changes.items(), key=lambda kv: kv[1], reverse=True)
        gainers = [f"{s} {sh.pct(v)}" for s, v in ranked[:self.top_n]]
        losers = [f"{s} {sh.pct(v)}" for s, v in ranked[-self.top_n:][::-1]]

        age = constituents.age_days(self.list_name)
        rows = {
            "index": self.index,
            "index change": sh.pct(data.get("index_change_pct")),
            "constituents quoting": f"{data['covered']} of {members}",
            "advancing": data["advancing"],
            "declining": data["declining"],
            "advance ratio": data["advance_ratio"],
            "median stock change": sh.pct(data["median_change_pct"]),
            "average stock change": sh.pct(data["average_change_pct"]),
        }
        if "breadth_gap_pct" in data:
            rows["index minus median (breadth gap)"] = sh.pct(data["breadth_gap_pct"])
            rows["computed as narrow"] = data["narrow"]
            rows["index and median disagree"] = data["divergent"]

        # Filings on constituents are a separate reason for a stock to move,
        # and reading breadth without them invites a wrong explanation.
        with_news = set(events.symbols_with_news(since_minutes=120))
        affected = sorted(with_news.intersection(changes))

        return sh.join(
            sh.block(f"{self.index} BREADTH (computed from live prices)", rows),
            sh.block("TOP GAINERS", gainers),
            sh.block("TOP LOSERS", losers),
            sh.block("CONSTITUENTS WITH FRESH FILINGS",
                     affected or ["none in the last two hours"]),
            sh.block("DATA AGE", {
                "constituent list age (days)": sh.num(age, digits=1),
                "list is old": bool(age is not None and age > STALE_LIST_DAYS),
            }),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


nifty = IndexCompositionAgent("NIFTY")
banknifty = IndexCompositionAgent("BANKNIFTY")

# The Nifty one is the default reference for other modules.
agent = nifty
