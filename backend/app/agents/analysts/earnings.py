"""Agent 09 — Earnings.

Results move prices through **surprise**, not through quality. A company
reporting 20% growth that everyone expected does nothing; the same company
reporting 12% when 20% was priced in falls hard. So the honest position
without a consensus estimate is that we cannot compute surprise, and the
agent says so rather than grading the result on its own merits.

What an LLM genuinely adds here is tone. Hedged guidance, a change in how
management describes demand, a number met through one-off items -- those
live in language, not in the figures, and they are a documented lead on
post-earnings drift.

Filings are untrusted text, which matters more for this agent than most: a
results PDF is attacker-writable in principle. The fence and the schema are
what contain that; nothing here parses free text into an action.
"""
from __future__ import annotations

import re

from ...store import events
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import EarningsOutput
from . import _shared as sh

SCHEMA_EXTRA = ('  "guidance": "raised" | "maintained" | "lowered" | "unknown",\n'
                '  "tone": "confident" | "cautious" | "defensive" | "unclear",\n'
                '  "drift_expectation": "positive" | "negative" | "none"')

TASK = f"""Read the results-related filings below and judge their effect.

What matters:
- Only surprise moves a price. If you have no estimate to compare against,
  say guidance is "unknown" and keep confidence low. Do not grade the result
  on how good the numbers look in isolation.
- Guidance usually matters more than the quarter just reported. A good
  quarter with lowered guidance is a bearish event.
- Tone is where you add value: hedged language, vaguer demand commentary, or
  a result met through one-off items are all warnings the numbers do not
  show.
- drift_expectation is about the days after the result, not the day of it.
  Post-earnings drift is a real effect but a weak one -- only call it when
  the surprise is clear.
- List affected_symbols only for companies that actually appear below.

A board-meeting notice is not a result. Those are scheduling announcements;
treat them as a reason to avoid the name, not as information about it.

{sh.HONESTY_RULE}

{sh.SECTOR_NOTE}"""

# Filing titles that are about results rather than housekeeping.
RESULTS_PATTERN = re.compile(
    r"financial result|quarterly result|audited result|unaudited result|"
    r"earnings|q[1-4]\s*fy|outcome of board meeting|investor presentation|"
    r"earnings call|analyst meet|guidance",
    re.I,
)


class EarningsAgent(Agent):
    output_model = EarningsOutput

    def __init__(self, max_items: int = 25, scan_limit: int = 200) -> None:
        super().__init__(AgentConfig(
            name="earnings",
            model=ModelTier.FAST,
            prompt_version="1.0",
            max_tokens=700,
            # Results land in bursts and stay relevant for the session.
            freshness_seconds=3600,
            interval_seconds=3600,
            window="market_hours",
        ))
        self.max_items = max_items
        self.scan_limit = scan_limit

    def results_filings(self) -> list[dict]:
        """Exchange filings that look like results, newest first."""
        rows = events.recent(limit=self.scan_limit, with_body=True)
        return [r for r in rows
                if r["source"] in ("nse", "bse")
                and RESULTS_PATTERN.search(r.get("title") or "")][:self.max_items]

    async def gather(self) -> str:
        filings = self.results_filings()
        if not filings:
            return ("No results-related filings in the recent window. "
                    "This is normal outside a results season.")

        lines = []
        for row in filings:
            when = (row["published_at"] or row["fetched_at"] or "")[:19]
            body = (row.get("body") or "")[:300]
            lines.append(f"[{when}] {row['symbols'] or '-'} | {row['title']}"
                         + (f"\n    {body}" if body else ""))

        return sh.join(
            sh.block("RESULTS FILINGS", lines),
            sh.block("WHAT WE DO NOT HAVE", [
                "No analyst consensus estimates are connected, so surprise "
                "cannot be computed. Judge guidance and tone only, and keep "
                "confidence low on the numbers themselves.",
            ]),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return sh.schema_block(SCHEMA_EXTRA)


agent = EarningsAgent()
