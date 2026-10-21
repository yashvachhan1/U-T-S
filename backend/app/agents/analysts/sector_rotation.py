"""Agent 08 — Sector Rotation.

Where money is going today, at index level.

There is a shortcut here that is easy to miss: the value, quality and
low-volatility factors are **listed as ETFs on NSE**. Measuring factor
exposure normally means building portfolios and running regressions; here
the market prices the factors directly, every second, and their relative
performance says which factor is being rewarded right now.

So this agent needs no modelling at all -- just the sector indices and three
ETF quotes, read against each other.
"""
from __future__ import annotations

from ...engine import feed_service
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import SectorRotationOutput
from . import _shared as sh

SCHEMA_EXTRA = '  "factor_leader": "value" | "quality" | "low_vol" | "none"'

TASK = f"""Read today's sector and factor performance and say where money is
rotating.

How to judge it:
- Relative performance is the signal, not absolute. Every sector red on a
  down day tells you nothing about rotation.
- Name a sector as positive only if it is clearly ahead of the others, not
  merely green.
- The three factor ETFs price value, quality and low volatility directly.
  Low volatility leading is a defensive market. Value leading is usually a
  cyclical or rate-driven one. Quality leading often appears mid-cycle.
- factor_leader must be "none" unless one of the three is genuinely ahead;
  a few basis points apart is not a leader.
- If sectors are tightly bunched there is no rotation to report. Say so with
  a neutral view and low confidence.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""

# NSE sector indices, resolved by name at runtime.
# Names exactly as the Upstox instrument master spells them. They are not
# interchangeable: "Nifty Fin Service", "Nifty FinSrv25 50" and
# "Nifty FinSerExBnk" are three different indices.
SECTOR_INDEXES = (
    "Nifty IT",
    "Nifty Bank",
    "Nifty Fin Service",
    "Nifty Auto",
    "Nifty Pharma",
    "Nifty FMCG",
    "Nifty Metal",
    "Nifty Realty",
    "Nifty Energy",
    "Nifty Media",
    "Nifty Infra",
    "Nifty PSU Bank",
)

# Factor ETFs -- the shortcut described above.
FACTOR_ETFS = {
    "MOVALUE (value factor)": "MOVALUE",
    "MOQUALITY (quality factor)": "MOQUALITY",
    "MOLOWVOL (low volatility factor)": "MOLOWVOL",
}

# India VIX is the fear gauge that is actually live during our session --
# FRED's US VIX is a daily number and a day late.
BENCHMARKS = ("Nifty 50", "Nifty 500", "India VIX")


class SectorRotationAgent(Agent):
    output_model = SectorRotationOutput

    def __init__(self) -> None:
        super().__init__(AgentConfig(
            name="sector_rotation",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=650,
            # Rotation is an intraday read and goes stale quickly.
            freshness_seconds=1200,
            interval_seconds=1200,
            window="market_hours",
        ))

    def instrument_keys(self) -> dict[str, str]:
        keys = {name: sh.index_key(name)
                for name in BENCHMARKS + SECTOR_INDEXES}
        keys.update({label: sh.equity_key(symbol)
                     for label, symbol in FACTOR_ETFS.items()})
        return keys

    async def gather(self) -> str:
        sector_keys = {name: sh.index_key(name) for name in SECTOR_INDEXES}
        etf_keys = {label: sh.equity_key(symbol)
                    for label, symbol in FACTOR_ETFS.items()}
        bench_keys = {name: sh.index_key(name) for name in BENCHMARKS}

        etf_rows = {}
        for label, key in etf_keys.items():
            tick = feed_service.latest(key) if key else None
            if tick is None:
                etf_rows[label] = "not subscribed"
                continue
            # Volume matters for these: a factor ETF that barely traded is
            # quoting a stale price, and a stale price is not a signal.
            etf_rows[label] = (f"{sh.num(tick.ltp)} ({sh.pct(tick.change_pct)}), "
                               f"volume {tick.volume}")

        return sh.join(
            sh.block("BENCHMARKS", sh.quote_rows(bench_keys, feed_service.latest)),
            sh.block("SECTOR INDICES", sh.quote_rows(sector_keys, feed_service.latest)),
            sh.block("FACTOR ETFs", etf_rows),
            sh.block("CAUTION", [
                "Factor ETFs are thinly traded. If volume is low, the quote "
                "may be stale -- treat the factor read as weak.",
            ]),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = SectorRotationAgent()
