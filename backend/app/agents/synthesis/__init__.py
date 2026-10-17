"""Synthesis layer — twelve opinions into one stance, then challenged.

The order is fixed and matters: the Chief Strategist forms a stance from the
analysts, and the Red Team then argues against *that specific stance*. The
challenger must never be the author, because a model asked to review its own
reasoning agrees with it.

`run_pair()` is the entry point. It returns both outputs and the single
multiplier the risk engine reads, and it is written so that every failure
path lands on less size rather than more:

    no stance            ->  insufficient_data, 0.5
    no challenge         ->  silent Red Team,   0.5
    stance + objection   ->  capped at          0.5
    both fail            ->                     0.25
"""
from __future__ import annotations

from typing import Any

from ..schemas import (RedTeamOutput, StrategistOutput, red_team_silent,
                       synthesis_multiplier)
from . import chief_strategist, red_team

agents = (chief_strategist.agent, red_team.agent)


async def run_pair(*, persist: bool = True) -> dict[str, Any]:
    """Form a stance, then challenge it. Never raises.

    Both agents already swallow their own failures, so the only thing left
    to guard here is the shape of what comes back -- a Red Team returning
    something other than a RedTeamOutput would otherwise reach the
    multiplier as an unchallenged full-size approval.
    """
    stance = await chief_strategist.agent.run(persist=persist)
    if not isinstance(stance, StrategistOutput):
        stance = StrategistOutput(stance="insufficient_data",
                                  reasoning="strategist returned an unusable shape")

    challenge = await red_team.agent.challenge(stance).run(persist=persist)
    if not isinstance(challenge, RedTeamOutput):
        challenge = red_team_silent("red team returned an unusable shape")

    multiplier = synthesis_multiplier(stance, challenge)
    return {
        "stance": stance.to_payload(),
        "challenge": challenge.to_payload(),
        "size_multiplier": multiplier,
        "agreed": challenge.agreement and not challenge.objections,
        "analysts_used": stance.analysts_used,
        "tradeable": stance.stance != "insufficient_data" and multiplier > 0,
    }


def current() -> dict[str, Any]:
    """The latest stored stance and challenge, without calling a model.

    Used by the dashboard and by anything deciding right now. A stale pair
    degrades the same way a missing one does, because `current()` on each
    agent returns its own fallback once the view ages out.
    """
    stance = chief_strategist.agent.current()
    challenge = red_team.agent.current()
    if not isinstance(stance, StrategistOutput):
        stance = StrategistOutput(stance="insufficient_data")
    if not isinstance(challenge, RedTeamOutput):
        challenge = red_team_silent("no stored challenge")

    return {
        "stance": stance.to_payload(),
        "challenge": challenge.to_payload(),
        "size_multiplier": synthesis_multiplier(stance, challenge),
        "agreed": challenge.agreement and not challenge.objections,
        "tradeable": stance.stance != "insufficient_data",
    }


__all__ = ["chief_strategist", "red_team", "agents", "run_pair", "current"]
