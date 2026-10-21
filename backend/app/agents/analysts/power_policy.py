"""Agent 10 — Power & Policy Intelligence.

Two things that are public but under-read.

**Disclosed insider activity.** SEBI requires promoters to disclose when they
buy or sell their own company's shares, and pledge changes and bulk deals are
disclosed too. That is "what the people who know most are doing", filed by
law, free to read.

**Policy and leader events.** A state visit, a budget line, a tariff. The
tempting failure here is to reason from the story: "the PM is visiting
Russia, so defence stocks will rise." That is a narrative, and narratives
sound equally convincing whether or not they are true.

So this agent is required to supply `historical_n`: how many comparable past
events the data below actually shows. When that count is thin the schema
caps confidence at 0.35, no matter how certain the model sounds. A view
built on two precedents is a view built on noise, and the cap enforces that
without depending on the model's self-restraint.

Industrial power demand sits here too. It is a weekly read on real activity
roughly six weeks ahead of the official IIP print -- slow-loop context, not
a trade trigger.
"""
from __future__ import annotations

import re

from ...collectors import altdata
from ...store import events
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import PowerPolicyOutput
from . import _shared as sh

SCHEMA_EXTRA = ('  "event_type": "policy" | "regulatory" | "state_visit" | '
                '"budget" | "insider_deal" | "bulk_deal" | "legal_order" | "other",\n'
                '  "historical_n": integer, how many comparable past events '
                'appear in the data below')

TASK = f"""Read the policy filings, disclosures and activity data below and
judge the effect on Indian sectors.

The rule that matters here: **historical_n must be a count, not an
impression.** Count only comparable past events that actually appear in the
data given to you. If none appear, historical_n is 0. Do not count events you
remember from training -- you have no way to check those against this market,
and a confident wrong base rate is worse than an honest zero.

Confidence is automatically capped when historical_n is below 6, so there is
nothing to gain by inflating either number.

How to read each kind of item:
- Promoter buying is a mild positive and promoter selling a mild negative,
  but both are noisy. Pledge increases are a stronger warning -- they signal
  funding pressure at the promoter level.
- Regulatory action against a company is a direct negative for that name and
  often for its sector.
- Policy announcements usually act on a sector, not on the index.
- Power demand is slow background context. A sustained change points to
  industrial activity; one reading does not.
- A scheduled future event is a reason to flag risk, not to take a side.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""

POLICY_SOURCES = ("pib", "sebi", "rbi_press", "rbi_notifications",
                  "rbi_speeches", "rbi_publications")

# Filing titles that carry disclosed insider or large-trade activity.
INSIDER_PATTERN = re.compile(
    r"insider trading|sast|substantial acquisition|pledge|encumbr|"
    r"bulk deal|block deal|promoter (group )?(buy|sell|acquisition|disposal)|"
    r"acquisition of shares|disposal of shares|reg(ulation)? 29|reg(ulation)? 31",
    re.I,
)


class PowerPolicyAgent(Agent):
    output_model = PowerPolicyOutput

    def __init__(self, max_items: int = 20, scan_limit: int = 250) -> None:
        super().__init__(AgentConfig(
            name="power_policy",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=750,
            # Policy context holds for a while; this is a slow-loop agent.
            freshness_seconds=3600,
            interval_seconds=3600,
            window="always",
        ))
        self.max_items = max_items
        self.scan_limit = scan_limit

    def insider_filings(self) -> list[dict]:
        rows = events.recent(limit=self.scan_limit)
        return [r for r in rows
                if r["source"] in ("nse", "bse")
                and INSIDER_PATTERN.search(r.get("title") or "")][:self.max_items]

    async def gather(self) -> str:
        policy = [r for r in events.recent(limit=self.scan_limit)
                  if r["source"] in POLICY_SOURCES][:self.max_items]
        insider = self.insider_filings()

        power = altdata.power_read()
        if power.get("available"):
            power_rows = {
                "all-India demand": sh.num(power["demand_gw"], suffix=" GW"),
                "change vs yesterday": sh.pct(power["change_vs_yesterday_pct"]),
                "activity read": power["activity"],
                "as of": power["as_of"],
            }
        else:
            power_rows = {"status": "no power data collected yet"}

        return sh.join(
            sh.block("POLICY AND REGULATORY ANNOUNCEMENTS",
                     sh.headline_lines(policy, self.max_items)),
            sh.block("DISCLOSED INSIDER AND LARGE-TRADE ACTIVITY",
                     sh.headline_lines(insider, self.max_items)),
            sh.block("INDUSTRIAL POWER DEMAND", power_rows),
            sh.block("ON HISTORICAL ANALOGUES", [
                "No historical analogue database is connected yet. Unless "
                "comparable past events appear in the items above, "
                "historical_n is 0 and confidence will be capped.",
            ]),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = PowerPolicyAgent()
