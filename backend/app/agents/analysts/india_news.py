"""Agent 01 — India News.

The first agent built end to end, deliberately. Everything here that works is
reusable by the other eleven; everything that is awkward is better discovered
once than twelve times.

It reads NSE filings and wire headlines already collected by the news
collector, and turns them into a view. What it is *for* is worth repeating:
not entry signals. By the time a headline is readable the move has happened.
Its value is the blackout -- "there is fresh news on this name, do not open
something new into it" -- which is why the output carries affected symbols and
a materiality flag rather than a price target.
"""
from __future__ import annotations

from ...store import events
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import NewsAgentOutput

SCHEMA_HINT = """Return exactly this JSON shape:
{
  "view": "bullish" | "bearish" | "neutral",
  "confidence": 0.0 to 1.0,
  "horizon": "intraday" | "overnight" | "swing",
  "sectors_positive": ["IT", "PHARMA"],
  "sectors_negative": ["OMC"],
  "affected_symbols": ["RELIANCE", "DLF"],
  "risk_flag": true | false,
  "event_type": "earnings" | "order_win" | "regulatory" | "management_change"
                | "rating_change" | "corporate_action" | "legal" | "other",
  "material": true | false,
  "reasoning": "one or two sentences"
}"""

TASK = """Read the Indian market filings and headlines below and summarise what
they mean for trading today.

Judge them on substance:
- A board meeting outcome, dividend, merger, large order win, rating change or
  regulatory action can move a price. Set material to true for those.
- Routine compliance certificates, trading-window notices and standard
  disclosures move nothing. Set material to false and keep confidence low.
- Set risk_flag to true when something suggests today carries more risk than
  usual: a regulatory action, an adverse legal outcome, a sudden management
  exit, or several negative filings at once.
- List affected_symbols only for names that actually appear in the data.
- If nothing here is material, say so: view neutral, confidence below 0.3.
  That is the correct answer most of the time and is more useful than a guess."""


class IndiaNewsAgent(Agent):
    output_model = NewsAgentOutput

    def __init__(self, lookback_minutes: int = 120, max_items: int = 40):
        super().__init__(AgentConfig(
            name="india_news",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=700,
            # Filings arrive all day, so a view older than half an hour is
            # not a current view.
            freshness_seconds=1800,
            interval_seconds=900,
            window="market_hours",
        ))
        self.lookback_minutes = lookback_minutes
        self.max_items = max_items

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return SCHEMA_HINT

    async def gather(self) -> str:
        """Recent filings and headlines, newest first.

        Exchange filings come before wire copy on purpose: NSE attaches the
        symbol and lands minutes before the story is written up, so it is the
        more reliable material of the two.
        """
        filings = events.recent(limit=self.max_items // 2, source="nse")
        wires = [
            row for row in events.recent(limit=self.max_items)
            if row["source"] not in ("nse", "bse")
        ][: self.max_items // 2]

        if not filings and not wires:
            return "No filings or headlines in the recent window."

        lines: list[str] = []
        if filings:
            lines.append("EXCHANGE FILINGS (symbol attached, most reliable):")
            for row in filings:
                when = (row["published_at"] or row["fetched_at"] or "")[:19]
                lines.append(f"- [{when}] {row['symbols'] or '-'} | {row['title']}")

        if wires:
            lines.append("")
            lines.append("WIRE HEADLINES (no symbol attached):")
            for row in wires:
                when = (row["published_at"] or row["fetched_at"] or "")[:19]
                lines.append(f"- [{when}] ({row['source']}) {row['title']}")

        return "\n".join(lines)

    def blackout_symbols(self) -> list[str]:
        """Names to avoid opening into.

        Taken from the events store rather than the model: the list has to be
        complete, and a model summarising forty filings will miss some. The
        agent judges *severity*; the store decides *membership*.
        """
        return events.symbols_with_news(since_minutes=self.lookback_minutes)


agent = IndiaNewsAgent()
