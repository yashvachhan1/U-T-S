"""Agent 06 — Currency.

Asking "is a weak rupee good or bad for the market" is the wrong question.
The index effect is small and slow; the **sector** effect is immediate and
reliably signed:

  rupee weaker  ->  IT and PHARMA earn in dollars, so they gain
  rupee weaker  ->  OMC, AVIATION, PAINTS pay in dollars, so they lose

That is a rotation, not a direction, which is why this agent is expected to
fill sectors_positive and sectors_negative and usually leave the view
neutral.

One practical detail drives the implementation: USDINR futures expire
**weekly**. A hardcoded instrument key is correct for at most five trading
days, so the contract is resolved at runtime every single run.
"""
from __future__ import annotations

from ...collectors import fred
from ...engine import feed_service
from ...store import events
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import CurrencyOutput
from . import _shared as sh

SCHEMA_EXTRA = ('  "rupee_direction": "weakening" | "stable" | "strengthening",\n'
                '  "rbi_intervention_flag": true | false')

TASK = f"""Judge what the rupee is doing and which sectors it moves.

Use the known transmission rather than inventing one:
- Rupee weaker (USDINR up) helps exporters: IT, PHARMA.
- Rupee weaker hurts importers: OMC, AVIATION, PAINTS, CHEMICALS, and
  capital-goods importers.
- Rupee stronger reverses both.
- The index-level effect is weak. Prefer a neutral view with clear sector
  lists over a directional call on the market as a whole.

On intervention: the RBI manages the rupee actively. If the headlines below
suggest intervention, or the rupee has stalled at a round level despite
pressure, set rbi_intervention_flag. An intervened rupee is a rupee whose
move should not be extrapolated -- lower your confidence when you flag it.

A move under about 0.15% in a day is noise for USDINR. That currency moves
in small steps, so do not read a direction into one.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""

# Weekly expiries -- resolved at runtime, never written down.
PAIRS = {
    "USDINR future": "USDINR",
    "EURINR future": "EURINR",
    "GBPINR future": "GBPINR",
}

RBI_SOURCES = ("rbi_press", "rbi_notifications", "rbi_speeches",
               "rbi_publications")


class CurrencyAgent(Agent):
    output_model = CurrencyOutput

    def __init__(self, max_items: int = 10) -> None:
        super().__init__(AgentConfig(
            name="currency",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=600,
            freshness_seconds=1800,
        ))
        self.max_items = max_items

    def instrument_keys(self) -> dict[str, str]:
        return {label: sh.nearest_future(query, "NCD_FO")
                for label, query in PAIRS.items()}

    async def gather(self) -> str:
        keys = self.instrument_keys()

        snapshot = fred.macro_snapshot()
        dxy = snapshot.get("DTWEXBGS") or {}
        yields = snapshot.get("DGS10") or {}

        rbi = [r for r in events.recent(limit=self.max_items * 4)
               if r["source"] in RBI_SOURCES][:self.max_items]

        return sh.join(
            sh.block("CURRENCY FUTURES (live until 17:00 IST)",
                     sh.quote_rows(keys, feed_service.latest)),
            sh.block("DOLLAR CONTEXT", {
                "Dollar index (FRED, daily)":
                    f"{sh.num(dxy.get('value'))} (as of {dxy.get('as_of') or 'unknown'})",
                "US 10-year yield":
                    f"{sh.num(yields.get('value'))} (as of {yields.get('as_of') or 'unknown'})",
                "note": "A stronger dollar or higher US yields pull money out "
                        "of emerging markets, which pressures the rupee.",
            }),
            sh.block("RBI HEADLINES", sh.headline_lines(rbi, self.max_items)),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = CurrencyAgent()
