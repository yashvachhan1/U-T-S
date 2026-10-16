"""Alternative data: real-economy activity, ahead of official statistics.

**Vidyut Pravah** gives all-India electricity demand for the current 15-minute
block, updated live. Power demand tracks industrial activity, and it arrives
*now* -- the Index of Industrial Production covering the same period shows up
about six weeks later. That lag is the whole point: by the time IIP prints,
the market has already moved on it.

The site renders empty placeholders and fills them over AJAX, so scraping the
HTML returns dashes. The real numbers come from two POST endpoints found in
the page's own JavaScript. Both need a request body -- without one the server
answers 411 Length Required.

**VAHAN** (daily vehicle registrations) is not here, and the reason is worth
recording. Its dashboard is a PrimeFaces app that loads data through a chain
of about fifteen JSF AJAX posts, each carrying a ViewState that rotates on
every request, against component ids (j_idt666 and friends) that are generated
and change between deployments. Replicating that with an HTTP client is
possible but would break silently and often. It needs real browser automation,
which is a different kind of dependency than the rest of this file. Documented
in docs/08-roadmap.md rather than shipped as code that quietly returns nothing.
"""
from __future__ import annotations

import re
from typing import Any

from ..clock import now_ist
from ..ratelimit import TokenBucket
from ..store import macro
from .base import Collector

VP_BASE = "https://vidyutpravah.in/PXDashboard"
VP_STATS = f"{VP_BASE}/BindTopStatisticsFromJS"
VP_TIME = f"{VP_BASE}/BindCurrentDateTimeForJson"

VP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/javascript, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://vidyutpravah.in/",
    "Content-Type": "application/x-www-form-urlencoded",
}

_TAGS = re.compile(r"<[^>]+>")
_NUMBER = re.compile(r"-?[\d,]*\.?\d+")


def extract_number(value: Any) -> float | None:
    """Pull a number out of a field that may be wrapped in HTML.

    The API returns things like "<span class='counter'>227</span>&nbsp;GW",
    and a literal "-" when a figure is unavailable.
    """
    if value is None:
        return None
    text = _TAGS.sub(" ", str(value))
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    match = _NUMBER.search(text)
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def parse_power(stats: Any, timing: Any = None) -> dict[str, Any]:
    """Pure: Vidyut Pravah payloads -> a flat reading."""
    row = stats[0] if isinstance(stats, list) and stats else stats
    if not isinstance(row, dict):
        return {}

    out: dict[str, Any] = {
        "demand_gw": extract_number(row.get("demand")),
        "demand_yesterday_gw": extract_number(row.get("demandYest")),
        "peak_gw": extract_number(row.get("Peak")),
        "umcp": extract_number(row.get("UMCP")),
    }

    if isinstance(timing, list) and timing:
        t = timing[0]
        if isinstance(t, dict):
            out["block_from"] = t.get("FromTime")
            out["block_to"] = t.get("ToTime")
            out["reading_date"] = t.get("CurrentDate")

    today, yesterday = out["demand_gw"], out["demand_yesterday_gw"]
    if today and yesterday:
        out["change_pct"] = round((today - yesterday) / yesterday * 100, 2)
    return out


class AltDataCollector(Collector):
    """All-India power demand, every fifteen minutes."""

    def __init__(self) -> None:
        super().__init__(
            name="altdata",
            source="vidyutpravah",
            # The site publishes one figure per 15-minute block, so polling
            # faster would re-read the same number.
            interval_seconds=900.0,
            rate=TokenBucket(rate=0.5, capacity=2.0, name="vidyutpravah"),
        )

    async def fetch(self) -> Any:
        # Both endpoints are POST and both need a body; an empty GET gets 411.
        stats = await self.http_post(VP_STATS, headers=VP_HEADERS)
        try:
            timing = await self.http_post(VP_TIME, headers=VP_HEADERS)
        except Exception:                        # noqa: BLE001
            timing = None                        # the reading still stands
        return {"stats": stats, "timing": timing}

    def parse(self, raw: Any) -> list[macro.Point]:
        reading = parse_power((raw or {}).get("stats"), (raw or {}).get("timing"))
        if not reading.get("demand_gw"):
            return []

        ts = now_ist().isoformat(timespec="minutes")
        meta = {k: v for k, v in reading.items() if k != "demand_gw"}

        points = [macro.Point("POWER_DEMAND_GW", ts, reading["demand_gw"], meta)]
        if reading.get("change_pct") is not None:
            points.append(
                macro.Point("POWER_DEMAND_CHG", ts, reading["change_pct"]))
        if reading.get("peak_gw"):
            points.append(macro.Point("POWER_PEAK_GW", ts, reading["peak_gw"]))
        return points


def power_read() -> dict[str, Any]:
    """Latest power reading, for the decision layer.

    Treated as a slow-loop input: it shapes the day's context rather than
    triggering a trade. A single 15-minute block is noisy; the day-over-day
    comparison is what carries signal.
    """
    latest = macro.latest("POWER_DEMAND_GW")
    change = macro.latest("POWER_DEMAND_CHG")
    if not latest:
        return {"available": False}

    change_pct = change["value"] if change else None
    return {
        "available": True,
        "demand_gw": latest["value"],
        "change_vs_yesterday_pct": change_pct,
        # Industrial activity moves slowly; a few percent is a real shift.
        "activity": (
            "expanding" if (change_pct or 0) > 2
            else "contracting" if (change_pct or 0) < -2
            else "steady"
        ),
        "as_of": latest["ts"],
        "note": "IIP se ~6 hafte pehle milta hai",
    }


collector = AltDataCollector()
