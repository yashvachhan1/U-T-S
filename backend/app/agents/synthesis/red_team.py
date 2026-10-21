"""Agent 14 — Red Team (Devil's Advocate).

An LLM asked "is this a good trade?" will usually say yes. Agreeableness is
trained in, it survives careful prompting, and it is strongest exactly when
the plan is articulate -- which the Chief Strategist's output always is.

So this agent is never asked whether the plan is good. It is asked what is
wrong with it, and the structure is arranged so that agreement is the
expensive answer:

  * `agreement` defaults to False. Approval has to be stated.
  * Objections and agreement cannot coexist; the schema resolves that
    contradiction toward the objections.
  * An absent Red Team contributes 0.5, not 1.0. Full size means the plan
    survived a challenge, and a challenge that never ran is not one it
    survived.
  * It reads the same analyst views the Strategist read, so it can find the
    evidence that was weighed away rather than only re-reading the
    conclusion.

The asymmetry is the whole design: the Red Team can shrink a trade and has
no mechanism to enlarge one, so an unhelpfully contrarian Red Team costs
opportunities while a captured one cannot cost money.
"""
from __future__ import annotations

from pydantic import ValidationError

from ...store import agent_outputs as store
from ..base import Agent, AgentConfig
from ..llm import ModelTier
from ..schemas import RedTeamOutput, StrategistOutput, red_team_silent
from . import chief_strategist

SCHEMA_HINT = """Return exactly this JSON shape:
{
  "view": "bullish" | "bearish" | "neutral",
  "confidence": 0.0 to 1.0,
  "horizon": "intraday" | "overnight" | "swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "affected_symbols": [],
  "risk_flag": true | false,
  "objections": ["each one a specific, falsifiable reason the stance is wrong"],
  "agreement": true | false,
  "suggested_size_multiplier": 0.0 to 1.0,
  "reasoning": "your strongest objection in one or two sentences"
}"""

TASK = """You are the Red Team. Below is the Chief Strategist's stance and
the analyst views it was built from. Your job is to argue against it.

Do not evaluate whether the stance is reasonable. Assume it is wrong and
find out how.

Look specifically for:
- Evidence in the analyst views that contradicts the stance and appears to
  have been weighed away rather than addressed.
- Channels that agree only because they measure the same thing. Three
  readings of global risk appetite are one signal, not three, and treating
  them as independent is the most common way a stance becomes overconfident.
- Views the stance leaned on that are stale, low-confidence, or based on
  data the agent itself reported as missing.
- What would have to be true for this stance to be right, and whether the
  data below actually establishes it.
- The obvious crowded interpretation. If the stance is what everyone reading
  the same headlines would conclude, that is usually priced already.

Rules on your output:
- Each objection must be specific and falsifiable. "There is uncertainty" is
  not an objection; "the bullish read rests on one 55-minute-old view whose
  own agent flagged its data as unavailable" is.
- Set agreement to true only if you genuinely cannot find a substantive
  objection. If you list objections, agreement must be false.
- suggested_size_multiplier is how much of the intended size you would
  allow. It cannot exceed 1.0. Use 1.0 only when you have no objections at
  all; a serious objection should put it at 0.5 or below.
- Do not manufacture objections to look useful. An invented weakness buries
  the real one, and a Red Team that objects to everything is ignored as
  quickly as one that objects to nothing. If the stance is genuinely sound
  and cautious, say so."""


class RedTeamAgent(Agent):
    output_model = RedTeamOutput

    def __init__(self) -> None:
        super().__init__(AgentConfig(
            name="red_team",
            # Strong model: arguing against a well-written case is harder
            # than producing one.
            model=ModelTier.STRONG,
            prompt_version="1.0",
            max_tokens=900,
            # A challenge to a stance expires with the stance.
            freshness_seconds=1800,
            interval_seconds=1800,
            window="market_hours",
        ))
        self._stance: StrategistOutput | None = None

    def challenge(self, stance: StrategistOutput) -> "RedTeamAgent":
        """Set the stance to argue against for the next run."""
        self._stance = stance
        return self

    def current_stance(self) -> StrategistOutput | None:
        """The stance under challenge: the one just set, else the stored one.

        Read from the store rather than through the Strategist's `current()`,
        which substitutes an "insufficient_data" placeholder when nothing is
        stored. That placeholder is the right answer for a risk engine and
        the wrong one here -- it would look like a stance to attack.
        """
        if self._stance is not None:
            return self._stance
        row = store.latest(chief_strategist.agent.config.name,
                           self.config.freshness_seconds)
        if not row:
            return None
        try:
            return StrategistOutput.model_validate(row.get("payload") or {})
        except ValidationError:
            return None

    async def gather(self) -> str:
        stance = self.current_stance()
        if stance is None:
            return "No Chief Strategist stance is available to challenge."
        if stance.stance == "insufficient_data":
            # The Strategist declined to take a position. There is no bet
            # here to argue against, and inventing one would cost a strong
            # model call to attack a straw man.
            return ("The Chief Strategist took no stance (insufficient_data). "
                    "There is no directional bet to challenge.")

        lines = [
            "THE STANCE YOU ARE CHALLENGING:",
            f"- stance: {stance.stance}",
            f"- view: {stance.view.value} (confidence {stance.confidence})",
            f"- risk budget: {stance.risk_budget}",
            f"- max exposure: {stance.max_exposure_pct}%",
            f"- allowed sectors: {stance.allowed_sectors or 'no restriction'}",
            f"- blocked sectors: {stance.blocked_sectors or 'none'}",
            f"- analysts weighed: {stance.analysts_used}",
            f"- its reasoning: {stance.reasoning or '(none given)'}",
        ]

        views = chief_strategist.agent.usable_views()
        lines.append("")
        lines.append("THE SAME ANALYST VIEWS IT WAS BUILT FROM:")
        if not views:
            lines.append("- none available, which is itself an objection")
        for v in sorted(views, key=lambda v: v["agent"]):
            age = v.get("age_seconds", -1)
            age_text = "age unknown" if age < 0 else f"{age // 60} min old"
            lines.append(
                f"- {v['agent']}: {v.get('view', 'neutral')} "
                f"(confidence {v.get('confidence', 0)}, "
                f"risk_flag {v.get('risk_flag', False)}, {age_text})"
                + (f" | said: {str(v.get('reasoning'))[:160]}"
                   if v.get("reasoning") else ""))

        lines.append("")
        lines.append("NOTE ON INDEPENDENCE: asia_markets, us_markets, "
                     "crypto_risk and global_macro all partly measure global "
                     "risk appetite. Agreement among them is weaker evidence "
                     "than it looks.")
        return "\n".join(lines)

    def task_prompt(self) -> str:
        return TASK

    def schema_hint(self) -> str:
        return SCHEMA_HINT

    def fallback(self, reason: str) -> RedTeamOutput:
        """Silence is not approval. A Red Team that did not run allows 0.5."""
        return red_team_silent(reason)


agent = RedTeamAgent()
