"""Agent 15 — Post-Trade Review.

Runs once, after the close. It reads the audit log, the day's trades and
what each agent had said at the time, and writes the account of the day
that a person will actually read.

What it is for, and what it is not for:

**It is not a measurement.** Its `agents_correct` and `agents_wrong` are
one afternoon's impression. The real per-agent scoring lives in
`forward/attribution.py`, computed over hundreds of trades against a base
rate. Treating a day's read as a verdict is how a good agent gets removed
for one bad session -- so this output is a prompt to go and look, and the
prompt says so.

**It never touches a trade.** `ReviewOutput.size_multiplier()` returns 1.0
always. Its view is a judgement about the past, and letting that into
position sizing would mean yesterday's results shrinking today's trades
through a channel nobody designed.

The useful output is the one the schema calls `lesson`: the sentence worth
remembering tomorrow. Most days it should be "nothing notable", and an
agent that finds a profound lesson every single day has found none.
"""
from __future__ import annotations

from typing import Any

from ...clock import now_ist
from ...risk import state as risk_state
from ...store import agent_outputs, audit
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import ReviewOutput

SCHEMA_HINT = """Return exactly this JSON shape:
{
  "view": "bullish" | "bearish" | "neutral",
  "confidence": 0.0 to 1.0,
  "horizon": "intraday" | "overnight" | "swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "affected_symbols": [],
  "risk_flag": true | false,
  "trades_today": integer,
  "agents_correct": ["agent_name"],
  "agents_wrong": ["agent_name"],
  "lesson": "one sentence worth remembering tomorrow, or 'nothing notable'",
  "reasoning": "two or three sentences: what happened and why"
}"""

TASK = """You are reviewing a single trading day for the humans who run
this system. Below are the decisions taken, the trades that resulted, and
what each agent had said at the time.

Write the account of the day.

What to look for:
- Trades that lost money for a reason visible in advance. Those are the
  expensive ones, because they are repeatable.
- Trades that made money for a reason that was NOT visible in advance.
  Those are luck, and reading them as skill is how a strategy gets worse.
- Decisions the risk engine refused. A day with many refusals and no
  trades is not a failure; it may be the system working.
- Agents whose view matched what happened, and agents whose did not.

On naming agents: this is one day. One day cannot establish that an agent
is good or bad -- that measurement exists elsewhere and uses hundreds of
trades. Name an agent only when its call was clearly and specifically
right or wrong, and leave both lists empty when the day was ordinary.

On the lesson: most days do not have one. "Nothing notable" is the
correct answer on a normal day, and it is more useful than an invented
insight, because a report that finds something profound every day stops
being read.

risk_flag is for a process problem you noticed -- a halt, a rejected
order, a position left unprotected -- not for a losing day. Losing days
are normal."""

# How far back a day's material is read from.
LOOKBACK_HOURS = 24


class PostTradeReviewAgent(Agent):
    output_model = ReviewOutput

    def __init__(self, max_items: int = 40) -> None:
        super().__init__(AgentConfig(
            name="post_trade_review",
            # The strong model: this is writing for a person, and the
            # judgement about what mattered is the whole job.
            model=ModelTier.STRONG,
            prompt_version="1.0",
            max_tokens=900,
            # A daily report stays current for a day.
            freshness_seconds=86_400,
        ))
        self.max_items = max_items

    def day_trades(self) -> list[dict[str, Any]]:
        """Today's closed trades, newest first."""
        from ... import db

        risk_state.ensure_schema()
        return db.query(
            """SELECT closed_at, symbol, pnl, source FROM risk_trades
               WHERE trade_date = ? ORDER BY id DESC LIMIT ?""",
            (risk_state.trade_date(), self.max_items))

    def decisions(self) -> list[dict[str, Any]]:
        return audit.recent(limit=self.max_items * 3, kind="decision")[:self.max_items]

    async def gather(self) -> str:
        from ..analysts import _shared as sh

        snap = risk_state.snapshot()
        trades = self.day_trades()
        decisions = self.decisions()

        day = {
            "date": snap["trade_date"],
            "realised P&L": sh.num(snap["realised_pnl_today"]),
            "trades closed": snap["trades_today"],
            "wins / losses": f"{snap['wins']} / {snap['losses']}",
            "orders sent": snap["orders_sent"],
            "orders rejected": snap["orders_rejected"],
            "consecutive losses now": snap["consecutive_losses"],
            "daily loss so far": sh.pct(snap["daily_loss_pct"]),
        }

        trade_lines = [
            f"[{(t['closed_at'] or '')[11:19]}] {t['symbol']} "
            f"{'+' if t['pnl'] >= 0 else ''}{t['pnl']:.2f} ({t['source']})"
            for t in trades
        ] or ["koi trade close nahi hua"]

        approved = [d for d in decisions if d["outcome"] == "approved"]
        rejected = [d for d in decisions if d["outcome"] == "rejected"]

        reject_lines = []
        for d in rejected[:self.max_items // 2]:
            detail = d.get("detail") or {}
            reasons = detail.get("reasons") or []
            reject_lines.append(
                f"{d['subject'] or '?'}: {'; '.join(str(r) for r in reasons)[:160]}")

        # What each agent said today. Read from the store, which is what
        # makes this a replay of the day rather than a fresh opinion of it.
        from .. import registry
        from ...agents.schemas import NON_ANALYST_AGENTS

        views = []
        for name in registry.names():
            if name in NON_ANALYST_AGENTS:
                continue
            row = agent_outputs.latest(name, 86_400)
            if not row:
                continue
            payload = row.get("payload") or {}
            views.append(
                f"{name}: {payload.get('view', 'neutral')} "
                f"(confidence {payload.get('confidence', 0)}, "
                f"risk_flag {payload.get('risk_flag', False)})"
                + (f" -- {str(payload.get('reasoning'))[:120]}"
                   if payload.get("reasoning") else ""))

        events = [
            f"{r['kind']}: {r['subject'] or ''} {r['outcome']}"
            for r in audit.recent(limit=self.max_items * 4)
            if r["kind"] in ("kill_switch", "stop_failed", "stop_missing",
                             "reconcile_mismatch", "order_rejected",
                             "order_blocked")
        ][:self.max_items]

        return sh.join(
            sh.block("THE DAY", day),
            sh.block("TRADES CLOSED", trade_lines),
            sh.block(f"DECISIONS: {len(approved)} approved, "
                     f"{len(rejected)} refused", reject_lines
                     or ["koi refusal nahi"]),
            sh.block("WHAT THE AGENTS SAID TODAY", views or ["koi view nahi"]),
            sh.block("PROCESS EVENTS (halts, rejects, unprotected positions)",
                     events or ["kuch nahi -- saaf din"]),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return SCHEMA_HINT


agent = PostTradeReviewAgent()
