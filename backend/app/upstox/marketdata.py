"""Market data helpers: candles, quotes, LTP.

Upstox returns candles newest-first as bare arrays; everything downstream in
this app wants oldest-first dicts, so the conversion happens here once.
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

from . import client

# label -> (unit, interval) for the v3 historical-candle API
INTERVALS: dict[str, tuple[str, int]] = {
    "1m": ("minutes", 1),
    "3m": ("minutes", 3),
    "5m": ("minutes", 5),
    "15m": ("minutes", 15),
    "30m": ("minutes", 30),
    "1h": ("hours", 1),
    "1d": ("days", 1),
    "1w": ("weeks", 1),
    "1M": ("months", 1),
}

# How far back the API will serve for each unit, roughly.
MAX_LOOKBACK_DAYS = {"minutes": 365, "hours": 365, "days": 3650, "weeks": 3650, "months": 3650}


def _enc(instrument_key: str) -> str:
    return quote(instrument_key, safe="")


def _rows(candles: list[list]) -> list[dict]:
    out = []
    for c in candles or []:
        try:
            out.append(
                {
                    "ts": c[0],
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]) if len(c) > 5 and c[5] is not None else 0.0,
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda r: r["ts"])
    return out


async def historical(
    instrument_key: str, interval: str = "1d", days: int = 365
) -> list[dict]:
    """Candles for the last `days` days, oldest first."""
    if interval not in INTERVALS:
        raise client.UpstoxError(f"Unsupported interval: {interval}")
    unit, step = INTERVALS[interval]
    days = max(1, min(days, MAX_LOOKBACK_DAYS.get(unit, 365)))

    to_d = date.today()
    from_d = to_d - timedelta(days=days)
    path = (
        f"/v3/historical-candle/{_enc(instrument_key)}/{unit}/{step}"
        f"/{to_d.isoformat()}/{from_d.isoformat()}"
    )
    data = await client.get(path)
    rows = _rows((data or {}).get("candles", []))

    # The historical endpoint excludes today, so top it up with intraday.
    if unit in ("minutes", "hours", "days"):
        try:
            today = await intraday(instrument_key, interval)
            if today:
                seen = {r["ts"] for r in rows}
                rows += [r for r in today if r["ts"] not in seen]
                rows.sort(key=lambda r: r["ts"])
        except client.UpstoxError:
            pass
    return rows


async def intraday(instrument_key: str, interval: str = "5m") -> list[dict]:
    """Today's candles so far."""
    unit, step = INTERVALS.get(interval, ("minutes", 5))
    if unit in ("weeks", "months"):
        return []
    path = f"/v3/historical-candle/intraday/{_enc(instrument_key)}/{unit}/{step}"
    data = await client.get(path)
    return _rows((data or {}).get("candles", []))


async def quotes(instrument_keys: list[str]) -> dict:
    """Full quote (depth, OHLC, volume) for up to ~500 instruments."""
    if not instrument_keys:
        return {}
    data = await client.get(
        "/v2/market-quote/quotes", params={"instrument_key": ",".join(instrument_keys)}
    )
    return data or {}


async def ltp(instrument_keys: list[str]) -> dict[str, dict]:
    """Last traded price keyed by instrument_key (not by the odd Upstox key)."""
    if not instrument_keys:
        return {}
    data = await client.get(
        "/v2/market-quote/ltp", params={"instrument_key": ",".join(instrument_keys)}
    )
    out: dict[str, dict] = {}
    for _, v in (data or {}).items():
        key = v.get("instrument_token") or v.get("instrument_key")
        if key:
            out[key] = {"ltp": v.get("last_price"), "instrument_key": key}
    # Fall back to positional mapping if Upstox omitted the token.
    if not out and data:
        for k, v in zip(instrument_keys, (data or {}).values()):
            out[k] = {"ltp": v.get("last_price"), "instrument_key": k}
    return out


async def snapshot(instrument_keys: list[str]) -> list[dict]:
    """One row per instrument with price + day change, for the watchlist."""
    data = await quotes(instrument_keys)
    rows = []
    for _, v in (data or {}).items():
        key = v.get("instrument_token") or v.get("instrument_key")
        ohlc = v.get("ohlc") or {}
        last = v.get("last_price")
        prev_close = ohlc.get("close") if ohlc.get("close") else v.get("close_price")
        change = pct = None
        if last is not None and prev_close:
            change = round(last - prev_close, 2)
            pct = round(change / prev_close * 100, 2)
        rows.append(
            {
                "instrument_key": key,
                "symbol": v.get("symbol") or v.get("trading_symbol"),
                "ltp": last,
                "open": ohlc.get("open"),
                "high": ohlc.get("high"),
                "low": ohlc.get("low"),
                "prev_close": prev_close,
                "change": change,
                "change_pct": pct,
                "volume": v.get("volume"),
            }
        )
    return rows
