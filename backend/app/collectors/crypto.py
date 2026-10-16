"""Crypto as a global risk-appetite proxy.

BTC trades 24/7, which makes it the one live risk signal available while Indian
markets are shut -- exactly the window where tomorrow's gap is forming.

How it gets used matters more than the number itself. BTC's correlation with
equities averages around 0.30 and sits near zero in calm markets, but runs
toward 1.0 in a liquidity crisis when every risk asset sells off together. So
it is read **asymmetrically**: a sharp BTC drop is a risk-off warning worth
acting on, a sharp rise is not a buy signal. `risk_off` only ever fires on the
downside, and the risk engine can only use it to size down.

Binance's public endpoints need no key and no auth.
"""
from __future__ import annotations

from typing import Any

from ..clock import now_ist
from ..ratelimit import TokenBucket
from ..store import macro
from .base import Collector

BINANCE = "https://api.binance.com/api/v3"
COINGECKO = "https://api.coingecko.com/api/v3"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

# A drop this big over 24h is treated as a genuine risk-off signal rather than
# ordinary crypto noise.
RISK_OFF_DROP_PCT = -4.0


def classify_risk(change_24h_pct: float) -> str:
    """Asymmetric by design -- upside gets no signal.

    Rising BTC tells us little about Indian equities; falling BTC during a
    liquidity event tells us a lot.
    """
    if change_24h_pct <= RISK_OFF_DROP_PCT * 2:
        return "severe_risk_off"
    if change_24h_pct <= RISK_OFF_DROP_PCT:
        return "risk_off"
    return "neutral"


class CryptoCollector(Collector):
    """24h ticker for BTC and ETH, every minute."""

    def __init__(self) -> None:
        super().__init__(
            name="crypto",
            source="binance",
            interval_seconds=60.0,
            # Binance allows far more; this is politeness, not a constraint.
            rate=TokenBucket(rate=2.0, capacity=4.0, name="binance"),
        )

    async def fetch(self) -> Any:
        out = []
        for symbol in SYMBOLS:
            try:
                r = await self.http_get(f"{BINANCE}/ticker/24hr", params={"symbol": symbol})
                out.append(r.json())
            except Exception as exc:        # noqa: BLE001
                # One symbol failing should not lose the other.
                print(f"[crypto] {symbol}: {exc}")
        if not out:
            raise RuntimeError("Binance se koi symbol nahi mila")
        return out

    def parse(self, raw: Any) -> list[macro.Point]:
        points: list[macro.Point] = []
        ts = now_ist().isoformat(timespec="minutes")

        for row in raw:
            symbol = row.get("symbol", "")
            if not symbol:
                continue
            try:
                price = float(row["lastPrice"])
                change_pct = float(row["priceChangePercent"])
                volume = float(row.get("quoteVolume", 0))
                high = float(row.get("highPrice", 0))
                low = float(row.get("lowPrice", 0))
            except (KeyError, TypeError, ValueError):
                continue

            meta = {
                "change_24h_pct": change_pct,
                "quote_volume": volume,
                "high_24h": high,
                "low_24h": low,
            }
            if symbol == "BTCUSDT":
                meta["risk_signal"] = classify_risk(change_pct)
                # Intraday range as a share of price -- a crude volatility read
                # that needs no history.
                meta["range_pct"] = round((high - low) / price * 100, 3) if price else 0.0

            points.append(macro.Point(f"CRYPTO_{symbol}", ts, price, meta))
            points.append(macro.Point(f"CRYPTO_{symbol}_CHG24", ts, change_pct))

        return points


def risk_state() -> dict[str, Any]:
    """Current crypto-derived risk read, for the decision layer."""
    latest = macro.latest("CRYPTO_BTCUSDT")
    change = macro.latest("CRYPTO_BTCUSDT_CHG24")
    if not latest or not change:
        return {"available": False, "signal": "neutral", "risk_off": False}

    change_pct = change["value"]
    signal = classify_risk(change_pct)
    return {
        "available": True,
        "btc_price": latest["value"],
        "change_24h_pct": change_pct,
        "signal": signal,
        # The only thing the risk engine reads -- and it can only reduce size.
        "risk_off": signal != "neutral",
        "as_of": latest["ts"],
    }


collector = CryptoCollector()
