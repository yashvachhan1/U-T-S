"""FRED — one free key, most of the global macro picture.

Crude, the dollar index, US yields and India's policy-uncertainty index all
live here, so this one collector replaces half a dozen scrapers.

Two things the API forces us to handle:

  * **Missing values.** FRED writes "." for a day with no observation (market
    holidays, mostly). Parsing that as a number would poison every downstream
    average, so those rows are dropped.
  * **Revisions.** The first CPI or GDP print gets replaced weeks later. We
    store `fetched_at` with every row, and `macro.as_of()` filters on it, so a
    backtest sees the number that was knowable that day rather than today's
    corrected one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import config
from ..ratelimit import TokenBucket
from ..store import macro
from .base import Collector

FRED_API = "https://api.stlouisfed.org/fred/series/observations"


@dataclass(frozen=True)
class Series:
    fred_id: str
    label: str
    why: str


# Chosen to cover the macro drivers in docs/01-research.md, nothing more.
# Each extra series is another thing to keep healthy for little gain.
SERIES = [
    Series("DCOILBRENTEU", "Brent crude",      "India ka #1 macro driver -- 85% oil import"),
    Series("DTWEXBGS",     "Dollar index",     "DXY up -> EM se paisa nikalta hai"),
    Series("DGS10",        "US 10Y yield",     "Yield up -> EM outflow pressure"),
    Series("VIXCLS",       "US VIX",           "Global fear gauge"),
    Series("FEDFUNDS",     "Fed funds rate",   "US policy rate"),
    Series("INDEPUINDXM",  "India EPU",        "Policy uncertainty, 7 Indian newspapers se"),
    Series("WUIIND",       "World Uncertainty India", "IMF-linked uncertainty index"),
    Series("CPIAUCSL",     "US CPI",           "Rate expectations"),
]

SERIES_BY_ID = {s.fred_id: s for s in SERIES}

# FRED writes a lone dot where an observation is missing.
MISSING = "."


def parse_observations(series_id: str, payload: dict) -> list[macro.Point]:
    """Pure: FRED JSON -> Points. Missing observations are dropped, not zeroed."""
    points: list[macro.Point] = []
    for row in (payload or {}).get("observations", []):
        raw_value = (row.get("value") or "").strip()
        if not raw_value or raw_value == MISSING:
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        date = row.get("date")
        if not date:
            continue
        points.append(macro.Point(f"FRED_{series_id}", date, value))
    return points


class FredCollector(Collector):
    """Daily pull of the configured FRED series."""

    def __init__(self, lookback_days: int = 400) -> None:
        super().__init__(
            name="fred",
            source="fred",
            # Daily series publish once a day; hourly polling would just burn
            # quota for the same numbers.
            interval_seconds=6 * 3600,
            rate=TokenBucket(rate=2.0, capacity=5.0, name="fred"),
        )
        self.lookback_days = lookback_days

    @property
    def configured(self) -> bool:
        return bool(config.FRED_API_KEY)

    def is_due(self) -> bool:
        return self.status.enabled and self.configured

    async def fetch(self) -> Any:
        if not self.configured:
            raise RuntimeError(
                "FRED_API_KEY set nahi hai. Free key: "
                "https://fredaccount.stlouisfed.org/apikeys -- phir .env me daalein."
            )

        from datetime import timedelta

        from ..clock import now_ist

        start = (now_ist() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")
        out: dict[str, dict] = {}

        for series in SERIES:
            try:
                r = await self.http_get(
                    FRED_API,
                    params={
                        "series_id": series.fred_id,
                        "api_key": config.FRED_API_KEY,
                        "file_type": "json",
                        "observation_start": start,
                    },
                )
                out[series.fred_id] = r.json()
            except Exception as exc:        # noqa: BLE001
                # One bad series should not cost us the other seven.
                print(f"[fred] {series.fred_id}: {exc}")

        if not out:
            raise RuntimeError("FRED se koi series nahi mili")
        return out

    def parse(self, raw: Any) -> list[macro.Point]:
        points: list[macro.Point] = []
        for series_id, payload in (raw or {}).items():
            points.extend(parse_observations(series_id, payload))
        return points


def macro_snapshot() -> dict[str, Any]:
    """Latest value of every series, for the dashboard and the agents."""
    out: dict[str, Any] = {}
    for series in SERIES:
        row = macro.latest(f"FRED_{series.fred_id}")
        out[series.fred_id] = {
            "label": series.label,
            "why": series.why,
            "value": row["value"] if row else None,
            "as_of": row["ts"] if row else None,
            "fetched_at": row["fetched_at"] if row else None,
        }
    return out


def crude_rupee_stress(usdinr_change_pct: float | None = None) -> dict[str, Any]:
    """Crude and the rupee are one signal, not two.

    India imports ~85% of its oil and pays in dollars, so expensive crude and a
    weak rupee compound: the same barrel costs more in both currencies at once.
    Twin-deficit pressure is what pushes FIIs out, and it shows up here before
    it shows up in the index.
    """
    brent_now = macro.latest("FRED_DCOILBRENTEU")
    brent_hist = macro.series("FRED_DCOILBRENTEU", limit=25)

    if not brent_now or len(brent_hist) < 2:
        return {"available": False}

    older = brent_hist[-1]["value"]
    crude_change = ((brent_now["value"] - older) / older * 100) if older else 0.0

    stress = crude_change
    if usdinr_change_pct is not None:
        # A weakening rupee adds to the same pressure, so the parts add.
        stress += usdinr_change_pct

    return {
        "available": True,
        "brent": brent_now["value"],
        "brent_change_pct": round(crude_change, 2),
        "usdinr_change_pct": usdinr_change_pct,
        "stress_index": round(stress, 2),
        "elevated": stress > 5.0,
        "sectors_hurt": ["OMC", "AVIATION", "PAINTS", "CHEMICALS"] if stress > 5 else [],
        "as_of": brent_now["ts"],
    }


collector = FredCollector()
