"""Agent 02 — Asia Markets.

Asia is the most useful foreign read we have, and for a boring reason: it is
**open while we are**. Nikkei and KOSPI start three and a half hours before
India, Hang Seng and Shanghai run across our morning. The US cash market, by
contrast, is shut for almost our entire session.

What the agent is asked for is breadth, not a level. One index can move on a
local story; five moving together is a regional risk signal, and that is the
part that travels to Mumbai.
"""
from __future__ import annotations

from ...collectors import global_markets
from ...engine import feed_service
from ... import markets
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import MarketGapOutput
from . import _shared as sh

SCHEMA_EXTRA = '  "gap_bias": "positive" | "negative" | "flat"'

TASK = f"""Read the Asian market session below and judge what it means for
Indian equities today.

How to weigh it:
- Breadth beats magnitude. Five Asian markets down together is a regional
  risk signal; one down alone is usually a local story and means little.
- Korea and Taiwan lead our IT sector -- their semiconductor names trade
  before ours and have historically led the same cycle.
- Shanghai and Hang Seng matter most for metals and commodity-linked names.
- Markets still open now carry more information than ones that already
  closed, because they can still move against us.
- gap_bias is about India's next open, not about Asia.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""


class AsiaMarketsAgent(Agent):
    output_model = MarketGapOutput

    def __init__(self) -> None:
        super().__init__(AgentConfig(
            name="asia_markets",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=600,
            # Asia trades through our morning, so a view from half an hour
            # ago may already be wrong.
            freshness_seconds=1200,
            interval_seconds=1200,
            window="always",
        ))

    def instrument_keys(self) -> dict[str, str]:
        # NSE-listed proxies are the only Asian exposure that is live on our
        # own feed, so they cross-check Yahoo rather than replacing it.
        return {
            symbol: key
            for symbol, (key, tracks) in markets.ETF_PROXIES.items()
            if "hang" in tracks.lower() or "asia" in tracks.lower()
        }

    async def gather(self) -> str:
        read = global_markets.asia_read()

        if not read.get("available"):
            moves = {}
            summary = {"status": "no Asian market data collected yet"}
        else:
            moves = {k: sh.pct(v) for k, v in read["moves_pct"].items()}
            summary = {
                "breadth": f"{read['up_count']} up of {read['total']}",
                "average move": sh.pct(read["average_pct"]),
                "bias": read["bias"],
                "open right now": ", ".join(read["markets_live_now"]) or "none",
            }

        return sh.join(
            sh.block("ASIAN MARKET MOVES TODAY", moves),
            sh.block("SESSION SUMMARY", summary),
            sh.block("NSE-LISTED ASIA PROXIES (our own live feed)",
                     sh.quote_rows(self.instrument_keys(), feed_service.latest)),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = AsiaMarketsAgent()
