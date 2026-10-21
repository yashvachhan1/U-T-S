"""Agent 05 — Commodity.

Crude is India's single largest macro input: roughly 85% of consumption is
imported and paid for in dollars, so its price feeds inflation, the current
account and a specific set of sectors within days.

The timing is the part worth exploiting. The commodity segment trades until
23:30 IST, eight hours after equities close. That evening crude move is a
direct read on tomorrow's open for OMCs, airlines and paints, and it is
available long before anyone writes about it. For BTST decisions it is the
best overnight input we have.

Gold is read differently -- as a hedge. Gold rising while equities fall is
risk-off confirmation; gold rising with everything else is usually a
currency story.
"""
from __future__ import annotations

from ...collectors import fred
from ...engine import feed_service
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import CommodityOutput
from . import _shared as sh

SCHEMA_EXTRA = '  "driver": "crude" | "gold" | "silver" | "metals" | "none"'

TASK = f"""Judge what the commodity moves below mean for Indian equities.

The transmission is well established, so use it rather than guessing:
- Crude up hurts OMC, AVIATION, PAINTS and CHEMICALS -- all of them buy it as
  an input. Crude up helps oil producers (ENERGY, OIL_GAS exploration names).
- Crude down reverses both.
- A large crude move also matters at the index level through inflation and
  the current account, but that works over weeks, not hours. Keep the horizon
  honest.
- Gold up while equities are weak is risk-off confirmation: raise risk_flag.
  Gold up on its own is usually currency-driven and is not an equity signal.
- "driver" names the commodity actually doing the work. If nothing has moved
  much, say "none" and return a neutral view.

Judge the size of the move, not just its sign. Crude moving 0.3% is noise;
2% is a signal.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""

# Nearest-expiry futures, resolved at runtime. These contracts roll monthly,
# so a hardcoded key is a key that breaks on expiry day.
FUTURES = {
    "Crude oil future (MCX)": ("CRUDEOIL", "NSE_COM"),
    "Gold future (MCX)": ("GOLD", "NSE_COM"),
    "Silver future (MCX)": ("SILVER", "NSE_COM"),
}

# Equity-hours ETFs. They stop at 15:30 but need no extra source and are
# liquid, so they cross-check the futures while both are trading.
ETFS = {
    "GOLDBEES": "GOLDBEES",
    "SILVERBEES": "SILVERBEES",
}


class CommodityAgent(Agent):
    output_model = CommodityOutput

    def __init__(self) -> None:
        super().__init__(AgentConfig(
            name="commodity",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=600,
            freshness_seconds=1800,
            interval_seconds=1800,
            window="always",
        ))

    def _usdinr_change(self) -> float | None:
        """Rupee move, used to combine with crude into one stress number.

        Taken from the live USDINR future rather than a stored series: there
        is no daily rupee feed, and a stale rupee number would quietly
        corrupt the combined stress figure rather than leaving it absent.
        """
        key = sh.nearest_future("USDINR", "NCD_FO")
        tick = feed_service.latest(key) if key else None
        return tick.change_pct if tick else None

    def instrument_keys(self) -> dict[str, str]:
        keys = {label: sh.nearest_future(query, segment)
                for label, (query, segment) in FUTURES.items()}
        keys.update({label: sh.equity_key(symbol)
                     for label, symbol in ETFS.items()})
        # The rupee leg of the crude-plus-rupee stress read.
        keys["USDINR future"] = sh.nearest_future("USDINR", "NCD_FO")
        return keys

    async def gather(self) -> str:
        futures_keys = {
            label: sh.nearest_future(query, segment)
            for label, (query, segment) in FUTURES.items()
        }
        etf_keys = {label: sh.equity_key(symbol) for label, symbol in ETFS.items()}

        stress = fred.crude_rupee_stress(self._usdinr_change())
        if stress.get("available"):
            stress_rows = {
                "Brent (FRED, daily)": sh.num(stress["brent"]),
                "Brent change over the series window": sh.pct(stress["brent_change_pct"]),
                "rupee change included": sh.pct(stress.get("usdinr_change_pct")),
                "combined crude+rupee stress": sh.num(stress["stress_index"]),
                "elevated": stress["elevated"],
                "sectors flagged by the stress rule": ", ".join(
                    stress["sectors_hurt"]) or "none",
            }
        else:
            stress_rows = {"status": "no Brent history collected yet"}

        return sh.join(
            sh.block("MCX FUTURES (live until 23:30 IST)",
                     sh.quote_rows(futures_keys, feed_service.latest)),
            sh.block("COMMODITY ETFs (equity hours only)",
                     sh.quote_rows(etf_keys, feed_service.latest)),
            sh.block("CRUDE AND RUPEE TOGETHER", stress_rows),
            sh.block("TIMING", [
                "Commodities trade until 23:30 IST, eight hours after "
                "equities close. An evening move here is tomorrow's gap.",
            ]),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = CommodityAgent()
