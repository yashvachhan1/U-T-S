"""World markets: US futures, Asian indices, and Indian-listed ETF proxies.

Two sources, because they answer different questions.

**Yahoo Finance** gives the actual index levels. It is free and needs no key,
but quotes can lag by around 15 minutes, which is fine for everything here --
none of these drive an entry on their own.

**Upstox ETF proxies** (MASPTOP50, MON100, HNGSNGBEES ...) quote on our own
session with no delay. They track the same foreign indices, and the gap
between the ETF's move and the index's move is itself a signal: it is Indian
money repricing a foreign market while that market is shut.

Every quote is tagged with whether its market was trading when we read it.
A live US futures tick and a finished Nikkei close are different kinds of
information, and losing that distinction is how a stale number ends up being
treated as news.
"""
from __future__ import annotations

from typing import Any

from .. import markets
from ..clock import now_ist
from ..ratelimit import TokenBucket
from ..store import macro
from .base import Collector

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"

# Yahoo blocks requests without a browser-ish User-Agent.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; trading-desk/1.0)",
    "Accept": "application/json",
}


def parse_chart(symbol: str, payload: dict) -> dict[str, Any] | None:
    """Pure: one Yahoo chart response -> a flat quote. None if unusable.

    The daily change comes from the close series, not from the meta block.
    `chartPreviousClose` is the close *before the requested range*, so with
    range=5d it yields a six-day move -- which, labelled as a daily change,
    turns a +2% session into a -14% one.
    """
    try:
        result = (payload or {}).get("chart", {}).get("result")
        if not result:
            return None
        node = result[0]
        meta = node.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            return None
        price = float(price)

        closes = [
            c for c in
            (((node.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            if c is not None
        ]

        prev_close = float(closes[-2]) if len(closes) >= 2 else None
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else None

        # Multi-day move, useful as a trend read -- but kept as its own field
        # so it can never be mistaken for the daily change.
        week_pct = None
        if len(closes) >= 2 and closes[0]:
            week_pct = (price - closes[0]) / closes[0] * 100

        return {
            "symbol": meta.get("symbol", symbol),
            "price": price,
            "previous_close": prev_close,
            "change_pct": round(change_pct, 3) if change_pct is not None else None,
            "week_change_pct": round(week_pct, 3) if week_pct is not None else None,
            "currency": meta.get("currency"),
            "exchange_ts": meta.get("regularMarketTime"),
        }
    except (KeyError, TypeError, ValueError, IndexError):
        return None


class GlobalMarketsCollector(Collector):
    """Index levels for every market in markets.MARKETS."""

    def __init__(self) -> None:
        super().__init__(
            name="global_markets",
            source="yahoo",
            # Most of these only matter at session boundaries; polling harder
            # would just re-read the same delayed number.
            interval_seconds=300.0,
            rate=TokenBucket(rate=2.0, capacity=4.0, name="yahoo"),
        )

    async def fetch(self) -> Any:
        out: dict[str, dict] = {}
        for market in markets.MARKETS:
            try:
                r = await self.http_get(
                    f"{YAHOO_CHART}/{market.symbol}",
                    params={"interval": "1d", "range": "5d"},
                    headers=HEADERS,
                )
                out[market.key] = r.json()
            except Exception as exc:          # noqa: BLE001
                # One market failing must not cost us the other nine.
                print(f"[global_markets] {market.key}: {exc}")
        if not out:
            raise RuntimeError("Yahoo se koi market data nahi mila")
        return out

    def parse(self, raw: Any) -> list[macro.Point]:
        points: list[macro.Point] = []
        ts = now_ist().isoformat(timespec="minutes")
        at = now_ist()

        for key, payload in (raw or {}).items():
            market = markets.MARKETS_BY_KEY.get(key)
            if market is None:
                continue
            quote = parse_chart(market.symbol, payload)
            if quote is None:
                continue

            live = market.is_open(at)
            meta = {
                "market": market.name,
                "change_pct": quote["change_pct"],
                "week_change_pct": quote["week_change_pct"],
                "currency": quote["currency"],
                # The distinction that matters: is this still moving?
                "was_live": live,
                "overlaps_india": markets.overlaps_india(market),
                "quality": "live" if live else "last_close",
            }
            points.append(macro.Point(f"MKT_{key.upper()}", ts, quote["price"], meta))
            if quote["change_pct"] is not None:
                points.append(
                    macro.Point(f"MKT_{key.upper()}_CHG", ts, quote["change_pct"]))

        return points


# --------------------------------------------------------------------------
# Reads for the decision layer
# --------------------------------------------------------------------------
def _latest(key: str) -> tuple[float | None, float | None]:
    price = macro.latest(f"MKT_{key.upper()}")
    change = macro.latest(f"MKT_{key.upper()}_CHG")
    return (price["value"] if price else None, change["value"] if change else None)


def asia_read() -> dict[str, Any]:
    """Asian markets open before India and trade alongside it.

    Breadth across them is steadier than any single index: one market can move
    on a local story, but five moving together is a regional risk signal.
    """
    at = now_ist()
    asia = ["nikkei", "kospi", "taiwan", "hangseng", "shanghai"]
    moves: dict[str, float] = {}
    live_now = []

    for key in asia:
        _, change = _latest(key)
        if change is not None:
            moves[key] = change
        if markets.MARKETS_BY_KEY[key].is_open(at):
            live_now.append(key)

    if not moves:
        return {"available": False}

    values = list(moves.values())
    up = sum(1 for v in values if v > 0)
    avg = sum(values) / len(values)

    if up == len(values):
        bias = "broadly_positive"
    elif up == 0:
        bias = "broadly_negative"
    else:
        bias = "mixed"

    return {
        "available": True,
        "moves_pct": {k: round(v, 2) for k, v in moves.items()},
        "average_pct": round(avg, 2),
        "up_count": up,
        "total": len(values),
        "bias": bias,
        "markets_live_now": live_now,
        # Only a broad move is worth reading as a signal.
        "risk_off": bias == "broadly_negative" and avg < -1.0,
    }


def us_futures_read() -> dict[str, Any]:
    """US futures run ~23 hours, so this is live during our whole session."""
    at = now_ist()
    sp, sp_chg = _latest("us_futures")
    nq, nq_chg = _latest("nasdaq_futures")
    market = markets.MARKETS_BY_KEY["us_futures"]

    if sp is None:
        return {"available": False}

    return {
        "available": True,
        "sp_futures": sp,
        "sp_change_pct": sp_chg,
        "nasdaq_futures": nq,
        "nasdaq_change_pct": nq_chg,
        "live_now": market.is_open(at),
        # Futures are the best read we have on tomorrow's open.
        "gap_bias": (
            "negative" if (sp_chg or 0) < -0.5
            else "positive" if (sp_chg or 0) > 0.5
            else "flat"
        ),
    }


def etf_proxy_read(latest_price_fn) -> dict[str, Any]:
    """Indian-listed ETFs tracking foreign indices.

    `latest_price_fn` comes from the live feed, so these are real-time while
    Indian markets are open -- no Yahoo delay, no extra source.
    """
    out: dict[str, Any] = {}
    for symbol, (instrument_key, tracks) in markets.ETF_PROXIES.items():
        price = latest_price_fn(instrument_key)
        out[symbol] = {
            "tracks": tracks,
            "instrument_key": instrument_key,
            "ltp": price or None,
            "subscribed": bool(price),
        }
    return out


def overview() -> dict[str, Any]:
    return {
        "sessions": markets.session_overview(),
        "open_now": markets.open_markets(),
        "asia": asia_read(),
        "us_futures": us_futures_read(),
    }


collector = GlobalMarketsCollector()
