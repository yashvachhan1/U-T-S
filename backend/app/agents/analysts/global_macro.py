"""Agent 04 — Global Macro.

Everything outside the US channel: policy uncertainty, geopolitics, central
banks other than the Fed.

Its job is regime, not direction. "Is today a normal day or a fragile one" is
a question this data can answer; "will the Nifty go up" is not. A regime read
is worth having anyway, because it is what the risk engine scales against --
in an elevated regime every position should be smaller regardless of which
way the signals point.

The uncertainty indices here are slow -- monthly, published with a lag. They
are deliberately given with their dates so the model can see how old they
are, and they set the backdrop rather than trigger anything.
"""
from __future__ import annotations

from ...collectors import fred
from ...store import events
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import MacroRegimeOutput
from . import _shared as sh

SCHEMA_EXTRA = '  "regime": "calm" | "elevated_risk" | "crisis"'

TASK = f"""Judge the global risk regime from the data below.

What each field is for:
- regime is the main output. "calm" is the normal state and should be the
  answer most days. "elevated_risk" means uncertainty is above its usual
  range or a live geopolitical event is running. "crisis" is rare -- reserve
  it for a genuine market-wide dislocation.
- The uncertainty indices are monthly and published with a lag. Treat them as
  background, and lower your confidence when the reading is weeks old.
- VIX is the fast one. It can contradict the slow indices, and when it does,
  it is usually right about today.
- A tense regime should raise risk_flag even when you have no directional
  view. That is the useful output here.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""

# Uncertainty and fear, as opposed to the US-specific rates channel.
REGIME_SERIES = ["INDEPUINDXM", "WUIIND", "VIXCLS", "DGS10"]

# RSS sources that carry policy and geopolitics rather than market colour.
POLICY_SOURCES = ("rbi_press", "rbi_notifications", "rbi_speeches",
                  "rbi_publications", "sebi", "pib")


class GlobalMacroAgent(Agent):
    output_model = MacroRegimeOutput

    def __init__(self, max_items: int = 15) -> None:
        super().__init__(AgentConfig(
            name="global_macro",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=600,
            # Regime changes slowly; an hour-old read is still a read.
            freshness_seconds=3600,
        ))
        self.max_items = max_items

    async def gather(self) -> str:
        snapshot = fred.macro_snapshot()
        rows = {}
        for series_id in REGIME_SERIES:
            row = snapshot.get(series_id) or {}
            rows[row.get("label", series_id)] = (
                f"{sh.num(row.get('value'))} "
                f"(observation date {row.get('as_of') or 'unknown'})")

        policy = [
            r for r in events.recent(limit=self.max_items * 3)
            if r["source"] in POLICY_SOURCES
        ][:self.max_items]

        return sh.join(
            sh.block("UNCERTAINTY AND FEAR INDICES", rows),
            sh.block("POLICY AND REGULATORY HEADLINES",
                     sh.headline_lines(policy, self.max_items)),
            sh.block("KNOWN GAPS", [
                "No geopolitical wire feed is connected yet -- judge "
                "geopolitics only from the headlines listed above, and do "
                "not assume quiet means calm.",
            ]),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = GlobalMacroAgent()
