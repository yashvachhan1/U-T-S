"""Agent 13 — Chief Strategist.

Twelve analysts will contradict each other on most days, and that is the
point. Asia closes weak, US futures grind higher, crude stress is building,
and sector rotation says money is going into IT anyway. Weighing that is a
reasoning task, which is the one thing an LLM is genuinely better at than a
rule.

What it returns is a **stance**, not a trade: a regime, a risk budget, a
sector allow/block list and an exposure ceiling. Something else turns that
into orders.

Two properties worth stating plainly.

**It never sees raw text.** Every input has already been through an
analyst's schema, so a hostile filing reaching this agent has been reduced
to a bounded enum and a float. This is the second wall, and it is the one
that holds even if the first is breached.

**Thin input is refused in code, not in the prompt.** Below three usable
analyst views the agent does not call the model at all: synthesis is the act
of weighing disagreement, and with two opinions there is nothing to weigh.
Asking anyway would buy a confident answer derived from a single agent.
"""
from __future__ import annotations

from typing import Any

from ...clock import now_ist
from ...store import agent_outputs as store
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import MIN_ANALYSTS, StoredOutput, StrategistOutput

SCHEMA_HINT = """Return exactly this JSON shape:
{
  "view": "bullish" | "bearish" | "neutral",
  "confidence": 0.0 to 1.0,
  "horizon": "intraday" | "overnight" | "swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "affected_symbols": [],
  "risk_flag": true | false,
  "stance": "risk_on" | "neutral" | "risk_off" | "insufficient_data",
  "risk_budget": "normal" | "reduced" | "minimal",
  "allowed_sectors": ["IT", "PHARMA"],
  "blocked_sectors": ["OMC"],
  "max_exposure_pct": 0 to 100,
  "analysts_used": integer, how many analyst views you actually weighed,
  "reasoning": "two or three sentences naming the disagreement and how you resolved it"
}"""

TASK = """You are the Chief Strategist. Below are the current views of the
analyst agents, each already validated and structured. Produce today's
stance.

What a stance is: a posture for the session. Which regime we are in, how
much risk is appropriate, which sectors are open and which are closed. It is
not a trade and must not name a trade.

How to weigh the inputs:
- Agreement across independent channels is the strongest signal. Asia, US
  futures and crude pointing the same way is worth far more than any one of
  them alone, because they are different mechanisms rather than three
  readings of the same thing.
- Disagreement is information, not noise. When channels conflict, the
  correct stance is usually neutral with a reduced risk budget -- not a
  guess at which one is right.
- A risk_flag anywhere deserves weight even when that agent's view is
  neutral. The agents that raise it are the ones watching for trouble.
- Stale views are weaker. Each line shows its age; an hour-old read of a
  market that has been moving is close to worthless.
- Low-confidence views should barely move your stance at all.

On the sector lists:
- blocked_sectors is the important one. Block a sector when an agent has
  flagged a specific risk in it, not merely because it is underperforming.
- allowed_sectors empty means "no restriction". Only narrow it when you
  have a positive reason.

On max_exposure_pct: this can only tighten the configured limit, never
loosen it, so there is nothing to gain from a high number. Set it below 100
when the regime warrants caution.

Be willing to say the day is unremarkable. A neutral stance with a normal
risk budget is the correct answer most days, and claiming to see a regime
shift every session is the fastest way to make this agent worthless."""

# Views older than this are shown with their age and treated as weak.
MAX_VIEW_AGE_SECONDS = 3600


class ChiefStrategistAgent(Agent):
    output_model = StrategistOutput

    def __init__(self, max_age_seconds: int = MAX_VIEW_AGE_SECONDS):
        super().__init__(AgentConfig(
            name="chief_strategist",
            # The strong model: this is the one job in the system that is
            # actually reasoning rather than classifying.
            model=ModelTier.STRONG,
            prompt_version="1.0",
            max_tokens=900,
            freshness_seconds=1800,
            interval_seconds=1800,
            window="market_hours",
        ))
        self.max_age_seconds = max_age_seconds

    # ---- inputs ---------------------------------------------------------
    def analyst_views(self) -> list[dict[str, Any]]:
        """Every analyst's latest stored view, with its age.

        Read from the store rather than by running the analysts: a backtest
        replaying this moment must see exactly the views that existed then,
        and re-running them would quietly use today's data.
        """
        from .. import registry

        now = now_ist()
        out: list[dict[str, Any]] = []
        for name in registry.names():
            if name in SYNTHESIS_AGENTS:
                continue
            row = store.latest(name, self.max_age_seconds)
            if not row:
                continue
            payload = row.get("payload") or {}
            try:
                age = int((now - _parse_ts(row["ts"])).total_seconds())
            except Exception:                      # noqa: BLE001
                age = -1
            out.append({"agent": name, "age_seconds": age, **payload})
        return out

    def usable_views(self) -> list[dict[str, Any]]:
        """Views that failed outright contribute nothing to a stance."""
        return [v for v in self.analyst_views() if not v.get("_error")]

    async def gather(self) -> str:
        views = self.usable_views()
        if not views:
            return "No analyst views are available."

        lines = []
        for v in sorted(views, key=lambda v: v["agent"]):
            age = v.get("age_seconds", -1)
            age_text = "age unknown" if age < 0 else f"{age // 60} min old"
            extras = {k: val for k, val in v.items()
                      if k not in _CORE_FIELDS and val not in (None, [], "", False)}
            extra_text = (" | " + ", ".join(f"{k}={val}" for k, val in extras.items())
                          if extras else "")
            lines.append(
                f"- {v['agent']}: {v.get('view', 'neutral')} "
                f"(confidence {v.get('confidence', 0)}, "
                f"risk_flag {v.get('risk_flag', False)}, {age_text})"
                f"{extra_text}"
            )
            sectors_pos = v.get("sectors_positive") or []
            sectors_neg = v.get("sectors_negative") or []
            if sectors_pos or sectors_neg:
                lines.append(f"    sectors +{sectors_pos} -{sectors_neg}")
            if v.get("reasoning"):
                lines.append(f"    said: {str(v['reasoning'])[:200]}")

        header = (f"ANALYST VIEWS ({len(views)} available, "
                  f"{MIN_ANALYSTS} is the minimum for a stance):")
        return header + "\n" + "\n".join(lines) + "\n\n" + _GAPS

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return SCHEMA_HINT

    def parse(self, raw: str) -> StrategistOutput:
        """Force analysts_used to the real count.

        The schema downgrades a stance built on fewer than three views, and
        that defence is worthless if the model supplies the count itself. It
        is overwritten here with what the store actually returned.
        """
        out = super().parse(raw)
        out.analysts_used = len(self.usable_views())
        # Re-run the validator so the corrected count is what gets enforced.
        return StrategistOutput.model_validate(out.model_dump(mode="json"))

    async def run(self, *, persist: bool = True) -> StrategistOutput:
        """Skip the model entirely when there is nothing to synthesise."""
        views = self.usable_views()
        if len(views) < MIN_ANALYSTS:
            out = StrategistOutput(
                stance="insufficient_data",
                analysts_used=len(views),
                reasoning=(f"only {len(views)} analyst views available, "
                           f"{MIN_ANALYSTS} needed -- no stance taken"),
            )
            self.stats.calls += 1
            self.stats.last_run = now_ist().isoformat(timespec="seconds")
            self.last_output = out
            if persist:
                store.save(StoredOutput(
                    agent=self.config.name, ts=now_ist(),
                    payload=out.to_payload(), model="(not called)",
                    prompt_version=self.config.prompt_version))
            return out
        return await super().run(persist=persist)

    def fallback(self, reason: str) -> StrategistOutput:
        """No stance is the safe absence, not a neutral one."""
        return StrategistOutput(stance="insufficient_data", reasoning=reason)


# Fields shown explicitly; everything else an analyst returns is appended as
# an extra so agent-specific detail (gap_bias, breadth_read, regime) survives.
_CORE_FIELDS = {"agent", "age_seconds", "view", "confidence", "risk_flag",
                "sectors_positive", "sectors_negative", "reasoning",
                "horizon", "affected_symbols"}

SYNTHESIS_AGENTS = {"chief_strategist", "red_team"}

_GAPS = """NOT YET CONNECTED (do not assume these are quiet):
- ML model scores: the model exists but is not wired into this prompt.
- Rule signals and current positions: not available here yet.
Judge only from the analyst views above."""


def _parse_ts(value: Any):
    from datetime import datetime
    return datetime.fromisoformat(str(value))


agent = ChiefStrategistAgent()
