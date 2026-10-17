"""Instrument master: download once, then search locally.

Upstox publishes the full contract list as a gzipped JSON file.  We download
NSE (+ BSE equity) once a day, trim it to the fields we care about, and keep it
in memory for fast prefix search.
"""
from __future__ import annotations

import gzip
import io
import json
import time
from typing import Any

import httpx

from .client import SSL_CONTEXT

from ..clock import now_ist
from ..config import INSTRUMENTS_PATH

SOURCES = {
    "NSE": "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
    "BSE": "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz",
}

# Only these segments are kept -- keeps the file small and search relevant.
#   NCD_FO  = NSE currency derivatives (USDINR, EURINR ...)
#   NSE_COM = NSE commodities (CRUDEOIL, GOLD, SILVER ...)
# Macro signals live in those two, so they are worth carrying.
KEEP_SEGMENTS = {
    "NSE_EQ", "NSE_INDEX", "BSE_EQ", "BSE_INDEX", "NSE_FO", "NCD_FO", "NSE_COM",
}

# Currency and commodity segments are ~32k rows, almost all option strikes we
# never quote. We only track the underlying price, so keep futures alone.
FUTURES_ONLY_SEGMENTS = {"NCD_FO", "NSE_COM"}

_cache: list[dict[str, Any]] | None = None
_loaded_at: float = 0.0


def _norm(raw: dict[str, Any]) -> dict[str, Any] | None:
    seg = raw.get("segment") or ""
    if seg not in KEEP_SEGMENTS:
        return None
    if seg in FUTURES_ONLY_SEGMENTS and raw.get("instrument_type") != "FUT":
        return None
    key = raw.get("instrument_key")
    if not key:
        return None
    return {
        "instrument_key": key,
        "symbol": raw.get("trading_symbol") or raw.get("tradingsymbol") or "",
        "name": raw.get("name") or "",
        "exchange": raw.get("exchange") or seg.split("_")[0],
        "segment": seg,
        "type": raw.get("instrument_type") or "",
        "lot_size": raw.get("lot_size") or 1,
        "tick_size": raw.get("tick_size") or 0.05,
        "expiry": raw.get("expiry"),
        "strike": raw.get("strike_price") or raw.get("strike"),
        "option_type": raw.get("option_type"),
    }


async def download() -> int:
    """Fetch the instrument master from Upstox and cache it on disk."""
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=180, follow_redirects=True, verify=SSL_CONTEXT) as c:
        for name, url in SOURCES.items():
            try:
                r = await c.get(url)
                r.raise_for_status()
                data = json.loads(gzip.decompress(r.content).decode("utf-8"))
            except Exception as exc:  # one exchange failing shouldn't kill the rest
                print(f"[instruments] {name} download failed: {exc}")
                continue
            for raw in data:
                row = _norm(raw)
                if row:
                    out.append(row)

    if not out:
        raise RuntimeError("Instrument master download failed -- internet check karein.")

    INSTRUMENTS_PATH.write_text(json.dumps(out), encoding="utf-8")
    global _cache, _loaded_at
    _cache = out
    _loaded_at = time.time()
    return len(out)


def loaded() -> bool:
    return INSTRUMENTS_PATH.exists()


def all_instruments() -> list[dict[str, Any]]:
    global _cache
    if _cache is None:
        if not INSTRUMENTS_PATH.exists():
            return []
        _cache = json.loads(INSTRUMENTS_PATH.read_text(encoding="utf-8"))
    return _cache


# Cash equity and indices are what people mean 95% of the time, so they win
# ties against the tens of thousands of option contracts sharing the same name.
_SEGMENT_RANK = {
    "NSE_EQ": 0, "NSE_INDEX": 1, "BSE_INDEX": 2, "BSE_EQ": 3,
    "NCD_FO": 4, "NSE_COM": 5, "NSE_FO": 6,
}


def search(q: str, limit: int = 25, segment: str | None = None) -> list[dict[str, Any]]:
    q = (q or "").strip().upper()
    if not q:
        return []

    now_ms = now_ist().timestamp() * 1000
    scored: list[tuple[tuple, dict]] = []
    for r in all_instruments():
        if segment and r["segment"] != segment:
            continue
        sym = r["symbol"].upper()
        name = r["name"].upper()

        if sym == q:
            match = 0
        elif name == q:
            match = 1
        elif sym.startswith(q):
            match = 2
        elif name.startswith(q):
            match = 3
        elif q in sym or q in name:
            match = 4
        else:
            continue

        # Match quality first, then segment: an exact hit on the Nifty index
        # should beat a Nifty-named ETF, while "RELIANCE" still surfaces the
        # NSE share before its 400 option strikes. Futures outrank option
        # strikes, and among contracts the nearest expiry wins -- that is the
        # one people actually trade.
        # An expired contract is still in the master and sorts first by
        # expiry, so without this an October future is returned in November:
        # a dead key that quotes nothing and silently blinds whatever reads
        # it. Expired contracts go last, never away -- old keys are still
        # needed to look up historical candles.
        type_rank = 1 if r["type"] in ("CE", "PE") else 0
        expiry = r.get("expiry") or 0
        expired = 1 if (expiry and expiry < now_ms) else 0
        scored.append((
            (match, _SEGMENT_RANK.get(r["segment"], 9), type_rank, expired,
             expiry, len(sym)), r))

    scored.sort(key=lambda x: x[0])
    return [r for _, r in scored[:limit]]


def by_key(instrument_key: str) -> dict[str, Any] | None:
    for r in all_instruments():
        if r["instrument_key"] == instrument_key:
            return r
    return None
