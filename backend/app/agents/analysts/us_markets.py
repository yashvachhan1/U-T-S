"""Agent 03 — US Markets & Fed.

The US matters most, and the usual objection -- "it is closed during our
session" -- is wrong in the way that counts. US **cash** is closed. CME
futures trade about 23 hours a day, with a single one-hour break at
02:30 IST, so S&P and Nasdaq futures are live through our entire session.
They are an intraday input, not just an overnight one.

The big scheduled US events also land in IST evening and night: CPI and jobs
around 18:00, the cash open at 19:00, a Fed decision at 23:30, big-tech
earnings past 01:00. All of it after India closes, which is precisely where
tomorrow's gap is formed.
"""
from __future__ import annotations

from ...collectors import fred, global_markets
from ...engine import feed_service
from ... import markets
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import MarketGapOutput
from . import _shared as sh

SCHEMA_EXTRA = '  "gap_bias": "positive" | "negative" | "flat"'

TASK = f"""Read the US market data below and judge its effect on Indian
equities.

How to weigh it:
- US futures are live during our session. Treat a move in them as happening
  now, not as yesterday's news.
- A rising US 10-year yield pulls money out of emerging markets. It hurts
  rate-sensitive sectors here first: BANK, FINANCIAL, REALTY.
- A rising dollar index does the same through the currency channel.
- A high VIX means position size should come down regardless of direction.
- Scheduled events (CPI, jobs, Fed) land after our close. If one is imminent,
  that is a reason to flag risk for overnight positions rather than to pick a
  side.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""

# The macro series that act through the US channel specifically.
US_SERIES = ["DGS10", "DTWEXBGS", "VIXCLS", "FEDFUNDS", "CPIAUCSL"]


class USMarketsAgent(Agent):
    output_model = MarketGapOutput

    def __init__(self) -> None:
        super().__init__(AgentConfig(
            name="us_markets",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=600,
            freshness_seconds=1200,
            interval_seconds=1200,
            window="always",
        ))

    def instrument_keys(self) -> dict[str, str]:
        return {
            symbol: key
            for symbol, (key, tracks) in markets.ETF_PROXIES.items()
            if "S&P" in tracks or "Nasdaq" in tracks or "US" in tracks
        }

    async def gather(self) -> str:
        futures = global_markets.us_futures_read()
        if futures.get("available"):
            fut_rows = {
                "S&P 500 futures": f"{sh.num(futures['sp_futures'])} "
                                   f"({sh.pct(futures['sp_change_pct'])})",
                "Nasdaq futures": f"{sh.num(futures['nasdaq_futures'])} "
                                  f"({sh.pct(futures['nasdaq_change_pct'])})",
                "trading right now": futures["live_now"],
            }
        else:
            fut_rows = {"status": "no US futures data collected yet"}

        snapshot = fred.macro_snapshot()
        macro_rows = {}
        for series_id in US_SERIES:
            row = snapshot.get(series_id) or {}
            macro_rows[row.get("label", series_id)] = (
                f"{sh.num(row.get('value'))} (as of {row.get('as_of') or 'unknown'})")

        us_cash = markets.MARKETS_BY_KEY["us_cash"]
        return sh.join(
            sh.block("US FUTURES (live during our session)", fut_rows),
            sh.block("US MACRO SERIES", macro_rows),
            sh.block("NSE-LISTED US PROXIES (our own live feed)",
                     sh.quote_rows(self.instrument_keys(), feed_service.latest)),
            sh.block("SESSION", {
                "US cash market open now": us_cash.is_open(),
                "note": "US cash trades 19:00-01:30 IST; futures almost all day",
            }),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = USMarketsAgent()
