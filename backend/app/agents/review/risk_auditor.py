"""Agent 16 — Risk Auditor.

Weekly. It asks one question: are we taking the risk we think we are?

Limits drift, and the drift always starts with "just this once". The
auditor is the thing that notices a month later that "just this once"
became the setting. It reads the config, the week's trades, the drawdown
and the book, and writes warnings.

**It can read config and cannot change it.** Not as a rule it is asked to
follow -- there is no field in `AuditOutput` through which a setting could
be written, and no code path from here to `config`. The strongest version
of "must not" is "cannot".

**It does not shrink positions either.** `size_multiplier()` returns 1.0.
It raises `risk_flag` so a human sees it, and that flag deliberately stops
there: an advisory agent with a channel into live sizing would be a second
risk engine that nobody designed and nobody tests.

The arithmetic it needs most is done in Python. Effective position count
-- whether five positions are five bets or one -- is the inverse Herfindahl
of exposure by industry, and a model asked to estimate it would guess.
"""
from __future__ import annotations

from typing import Any

from ... import config
from ...risk import state as risk_state
from ...store import audit
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import AuditOutput
from . import concentration

SCHEMA_HINT = """Return exactly this JSON shape:
{
  "view": "bullish" | "bearish" | "neutral",
  "confidence": 0.0 to 1.0,
  "horizon": "intraday" | "overnight" | "swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "affected_symbols": [],
  "risk_flag": true | false,
  "config_drift": true | false,
  "hidden_correlation": ["TCS", "INFY"],
  "warnings": ["each one specific and actionable"],
  "reasoning": "two or three sentences"
}"""

TASK = """You are auditing this system's risk setup for the week. Below
are the configured limits, how much risk was actually taken, the drawdown
record, and what the book actually holds.

The question is not "did we lose money". It is: **are we taking the risk
we think we are?**

What to look for:
- Limits set close to their hard ceilings. A limit at its ceiling is a
  limit that has been raised until it stopped binding.
- The gap between the nominal position count and the effective one. Five
  positions in one industry are one bet, and they will be discovered as
  one bet on the day that industry moves. The number is given to you
  below -- it is computed, not estimated, so use it rather than judging
  by eye.
- Realised losses larger than the per-trade risk budget implies. That
  means stops are not holding, or sizing is wrong.
- A drawdown pattern that is getting deeper rather than noisier.
- Days where the system traded through conditions it should have stood
  out of.

On warnings: each one must be specific enough to act on. "Risk is high"
is not a warning. "Four of five positions are in Financial Services, so
the effective position count is 1.6 against a configured maximum of 5"
is one.

If the setup is sound, say so and return no warnings. A weekly audit that
always finds something is one that gets ignored, and then it finds nothing
on the week that mattered.

You cannot change any setting. Everything here is a warning for a human."""

LOOKBACK_DAYS = 7


class RiskAuditorAgent(Agent):
    output_model = AuditOutput

    def __init__(self, lookback_days: int = LOOKBACK_DAYS) -> None:
        super().__init__(AgentConfig(
            name="risk_auditor",
            model=ModelTier.STRONG,
            prompt_version="1.0",
            max_tokens=900,
            # A weekly audit stays current for a week.
            freshness_seconds=7 * 86_400,
        ))
        self.lookback_days = lookback_days

    # ---- the computed parts -------------------------------------------
    def limits_against_ceilings(self) -> dict[str, str]:
        """Each configured limit next to the hard ceiling it cannot pass.

        Shown as a ratio because the interesting case is a limit that has
        crept up to its ceiling, not its absolute value.
        """
        pairs = [
            ("max risk per trade %", config.MAX_RISK_PER_TRADE_PCT,
             config.ABSOLUTE_MAX_RISK_PER_TRADE_PCT),
            ("max position %", config.MAX_POSITION_PCT,
             config.ABSOLUTE_MAX_POSITION_PCT),
            ("max open positions", config.MAX_OPEN_POSITIONS,
             config.ABSOLUTE_MAX_POSITIONS),
            ("max total exposure %", config.MAX_TOTAL_EXPOSURE_PCT,
             config.ABSOLUTE_MAX_TOTAL_EXPOSURE_PCT),
            ("max sector exposure %", config.MAX_SECTOR_EXPOSURE_PCT,
             config.ABSOLUTE_MAX_SECTOR_EXPOSURE_PCT),
            ("daily loss halt %", config.DAILY_LOSS_HALT_PCT,
             config.ABSOLUTE_DAILY_LOSS_HALT_PCT),
            ("max drawdown halt %", config.MAX_DRAWDOWN_HALT_PCT,
             config.ABSOLUTE_MAX_DRAWDOWN_HALT_PCT),
            ("max trades per day", config.MAX_TRADES_PER_DAY,
             config.ABSOLUTE_MAX_TRADES_PER_DAY),
        ]
        out = {}
        for label, value, ceiling in pairs:
            share = (value / ceiling * 100) if ceiling else 0
            flag = "  <-- at the ceiling" if share >= 90 else ""
            out[label] = f"{value} of max {ceiling} ({share:.0f}%){flag}"
        return out

    def week_trades(self) -> list[dict[str, Any]]:
        from ... import db
        from datetime import timedelta

        from ...clock import now_ist

        risk_state.ensure_schema()
        cutoff = (now_ist() - timedelta(days=self.lookback_days)).date().isoformat()
        return db.query(
            """SELECT trade_date, symbol, pnl, source FROM risk_trades
               WHERE trade_date >= ? ORDER BY id DESC LIMIT 200""", (cutoff,))

    async def book(self) -> list[Any]:
        from ...decision.engine import build_book

        try:
            return (await build_book()).positions
        except Exception:                          # noqa: BLE001
            return []

    # ---- the prompt ----------------------------------------------------
    async def gather(self) -> str:
        from ..analysts import _shared as sh

        snap = risk_state.snapshot()
        trades = self.week_trades()
        positions = await self.book()
        shape = concentration.analyse(positions)

        budget = config.TRADING_CAPITAL * config.MAX_RISK_PER_TRADE_PCT / 100
        losses = [t["pnl"] for t in trades if t["pnl"] < 0]
        over_budget = [t for t in trades
                       if budget > 0 and -t["pnl"] > budget * 1.1]

        week = {
            "trades this week": len(trades),
            "losing trades": len(losses),
            "worst single loss": sh.num(min(losses) if losses else 0),
            "per-trade risk budget": sh.num(budget),
            "losses BIGGER than that budget": len(over_budget),
            "weekly loss so far": sh.pct(snap["weekly_loss_pct"]),
            "drawdown from peak": sh.pct(snap["drawdown_pct"]),
            "consecutive losses": snap["consecutive_losses"],
        }

        if shape.get("available"):
            book_rows = {
                "positions held": shape["positions"],
                "EFFECTIVE positions (computed)": shape["effective_positions"],
                "industries": shape["industries"],
                "diversification that is illusory":
                    f"{shape['illusion_pct']}%",
                "flagged as concentrated": shape["concentrated"],
            }
            clusters = [f"{ind}: {', '.join(syms)}"
                        for ind, syms in shape["shared_industries"].items()]
        else:
            book_rows = {"status": "koi position nahi"}
            clusters = []

        halts = [f"{r['ts'][:16]} {r['outcome']}"
                 for r in audit.recent(limit=200, kind="kill_switch")][:10]

        return sh.join(
            sh.block("CONFIGURED LIMITS vs HARD CEILINGS",
                     self.limits_against_ceilings()),
            sh.block("RISK ACTUALLY TAKEN THIS WEEK", week),
            sh.block("WHAT THE BOOK HOLDS", book_rows),
            sh.block("NAMES SHARING AN INDUSTRY (these move together)",
                     clusters or ["koi overlap nahi"]),
            sh.block("KILL SWITCHES THAT FIRED", halts or ["koi nahi"]),
            sh.block("HOW TO READ THE EFFECTIVE COUNT", [
                "Inverse Herfindahl of exposure by industry. Five equal "
                "positions across five industries = 5.0; five in one "
                "industry = 1.0. Computed from the book, not estimated.",
            ]),
        )

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return SCHEMA_HINT


agent = RiskAuditorAgent()
