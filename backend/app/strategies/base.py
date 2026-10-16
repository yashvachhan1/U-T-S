"""Strategy contract.

A strategy turns a list of candles into a list of signals of the same length.
Signal values: "BUY" (enter long), "SELL" (exit long / enter short), or None.

Keeping it vectorised like this means the SAME code drives both the backtester
and the live bot -- the live bot just re-runs it on the latest candles and looks
at the last element.  No drift between backtest and live behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

Signal = str | None


@dataclass
class Param:
    name: str
    label: str
    default: Any
    type: str = "number"          # number | select
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[str] = field(default_factory=list)
    help: str = ""


@dataclass
class Strategy:
    key: str
    name: str
    description: str
    params: list[Param]
    fn: Callable[[list[dict], dict], list[Signal]]
    # Extra series to draw on the chart: name -> list aligned with candles
    plot: Callable[[list[dict], dict], dict[str, list]] | None = None

    def resolve(self, given: dict | None) -> dict:
        given = given or {}
        out = {}
        for p in self.params:
            v = given.get(p.name, p.default)
            if p.type == "number":
                try:
                    v = float(v)
                    if v == int(v):
                        v = int(v)
                except (TypeError, ValueError):
                    v = p.default
            out[p.name] = v
        return out

    def run(self, candles: list[dict], params: dict | None = None) -> list[Signal]:
        return self.fn(candles, self.resolve(params))

    def overlays(self, candles: list[dict], params: dict | None = None) -> dict[str, list]:
        if not self.plot:
            return {}
        return self.plot(candles, self.resolve(params))

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "params": [p.__dict__ for p in self.params],
        }


REGISTRY: dict[str, Strategy] = {}


def register(s: Strategy) -> Strategy:
    REGISTRY[s.key] = s
    return s


def get(key: str) -> Strategy:
    if key not in REGISTRY:
        raise KeyError(f"Unknown strategy: {key}")
    return REGISTRY[key]


def listing() -> list[dict]:
    return [s.to_json() for s in REGISTRY.values()]
