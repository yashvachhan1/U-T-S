"""NSE index constituents — reference data, not a time series.

Agents 11 (Index Composition) and 12 (Industry) both need the same thing:
which stocks sit inside an index, and what industry each one belongs to.

This is deliberately **not** a `Collector`. A Collector turns a source into
numeric Points on the macro timeline; this produces a list of names that
changes a handful of times a year. Forcing it into that shape would mean
inventing a time series that nobody reads.

Two things make it worth having at all:

  * NSE's own CSV carries an **Industry** column, so the industry mapping is
    the exchange's, not ours. There is nothing to maintain and nothing to get
    wrong.
  * It carries the **ISIN**, and Upstox equity instrument keys are
    `NSE_EQ|<isin>`. So every constituent resolves to a live feed key without
    a name search -- and name searches are exactly where RELIANCE once
    matched RELCHEMQ.

The file is cached on disk. A stale list is a usable list: constituents
change at review, not during a session, so a week-old file is fine and the
age is reported rather than hidden.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable

from .. import config
from ..clock import now_ist
from .base import Collector, CollectorError

ARCHIVE = "https://nsearchives.nseindia.com/content/indices"

# nsearchives serves the file to a plain client, but only with a browser-ish
# User-Agent and an nseindia Referer -- without them it answers 403.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/122.0.0.0 Safari/537.36"),
    "Referer": "https://www.nseindia.com/",
    "Accept": "text/csv,*/*",
}

INDEX_FILES = {
    "nifty50": "ind_nifty50list.csv",
    "banknifty": "ind_niftybanklist.csv",
    "nifty500": "ind_nifty500list.csv",
    "niftyit": "ind_niftyitlist.csv",
    "niftyauto": "ind_niftyautolist.csv",
    "niftypharma": "ind_niftypharmalist.csv",
    "niftyfmcg": "ind_niftyfmcglist.csv",
    "niftymetal": "ind_niftymetallist.csv",
}

# What the two index agents need; the rest are fetched only if asked for.
CORE_INDEXES = ("nifty50", "banknifty", "nifty500")

CACHE_DIR = config.DATA_DIR / "reference"

# Past this the list is still used, but reported as old.
STALE_AFTER_DAYS = 14


@dataclass(frozen=True)
class Member:
    symbol: str
    company: str
    industry: str
    isin: str

    @property
    def instrument_key(self) -> str:
        """Upstox key for the equity leg. Empty when the ISIN is missing."""
        return f"NSE_EQ|{self.isin}" if self.isin else ""


# --------------------------------------------------------------------------
# Parsing — pure
# --------------------------------------------------------------------------
def parse_csv(text: str) -> list[Member]:
    """CSV text to members. Malformed rows are skipped, not guessed at.

    A row without a symbol or an ISIN is useless downstream -- it cannot be
    subscribed to and cannot be joined to anything -- so it is dropped here
    rather than becoming a member with empty fields.
    """
    out: list[Member] = []
    seen: set[str] = set()

    for row in csv.DictReader(io.StringIO(text or "")):
        clean = {(k or "").strip().lower(): (v or "").strip()
                 for k, v in row.items()}
        symbol = clean.get("symbol", "").upper()
        isin = clean.get("isin code", "").upper()
        if not symbol or not isin or symbol in seen:
            continue
        seen.add(symbol)
        out.append(Member(
            symbol=symbol,
            company=clean.get("company name", ""),
            industry=clean.get("industry", "").upper().replace(" ", "_"),
            isin=isin,
        ))
    return out


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
def cache_path(index: str) -> Path:
    name = INDEX_FILES.get(index)
    if not name:
        raise KeyError(f"Unknown index: {index}")
    return CACHE_DIR / name


def age_days(index: str) -> float | None:
    """How old the cached file is, or None if there is none."""
    path = cache_path(index)
    if not path.exists():
        return None
    age = now_ist().timestamp() - path.stat().st_mtime
    return round(age / 86_400, 2)


_cache: dict[str, tuple[float, list[Member]]] = {}


def load(index: str) -> list[Member]:
    """Members from the cached file. Empty list if it was never fetched.

    Re-parsed only when the file changes on disk, so an agent running every
    five minutes is not re-reading 500 rows each time.
    """
    path = cache_path(index)
    if not path.exists():
        return []

    stamp = path.stat().st_mtime
    hit = _cache.get(index)
    if hit and hit[0] == stamp:
        return hit[1]

    try:
        members = parse_csv(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []

    _cache[index] = (stamp, members)
    return members


def _write_cache(index: str, text: str) -> None:
    """Write atomically -- a half-written list is worse than yesterday's."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(index)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    _cache.pop(index, None)


# --------------------------------------------------------------------------
# Refresh
# --------------------------------------------------------------------------
class _Fetcher(Collector):
    """Borrows the retry/backoff/rate-limit machinery, nothing else."""

    def parse(self, raw: Any) -> list:            # not a Point source
        return []


_fetcher = _Fetcher(name="constituents", source="nse", interval_seconds=86_400)


async def refresh(index: str) -> int:
    """Fetch one list and cache it. Returns the member count.

    Raises CollectorError on failure rather than leaving a half-truth: the
    caller decides whether an old cached list is good enough, and it usually
    is.
    """
    name = INDEX_FILES.get(index)
    if not name:
        raise KeyError(f"Unknown index: {index}")

    response = await _fetcher.http_get(f"{ARCHIVE}/{name}", headers=HEADERS)
    text = response.text
    members = parse_csv(text)
    if not members:
        raise CollectorError(f"{index}: downloaded file had no usable rows")

    _write_cache(index, text)
    return len(members)


async def refresh_all(indexes: Iterable[str] = CORE_INDEXES) -> dict[str, Any]:
    """Refresh several lists. One failure does not stop the others."""
    results: dict[str, Any] = {}
    for index in indexes:
        try:
            results[index] = await refresh(index)
        except Exception as exc:                   # noqa: BLE001
            results[index] = f"failed: {exc}"
        await asyncio.sleep(0.5)                   # stay polite with NSE
    return results


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------
def symbols(index: str) -> list[str]:
    return [m.symbol for m in load(index)]


def instrument_keys(index: str) -> dict[str, str]:
    """symbol -> Upstox key, for subscribing the whole index at once."""
    return {m.symbol: m.instrument_key for m in load(index) if m.instrument_key}


def by_industry(index: str = "nifty500") -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for m in load(index):
        if m.industry:
            groups.setdefault(m.industry, []).append(m.symbol)
    return groups


def industry_of(symbol: str, index: str = "nifty500") -> str:
    symbol = symbol.upper()
    for m in load(index):
        if m.symbol == symbol:
            return m.industry
    return ""


def status() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for index in INDEX_FILES:
        age = age_days(index)
        if age is None:
            continue
        out[index] = {
            "members": len(load(index)),
            "age_days": age,
            "stale": age > STALE_AFTER_DAYS,
        }
    return out


# --------------------------------------------------------------------------
# Breadth — the part the index agent actually reads
# --------------------------------------------------------------------------
def breadth(changes: dict[str, float], index_change_pct: float | None = None
            ) -> dict[str, Any]:
    """Advance/decline and narrowness, from constituent moves.

    Pure: takes symbol -> change%, so it is testable without a feed.

    Breadth is the one index signal that needs no weights, which matters
    because NSE publishes constituents but not weightings. "Index +0.5% with
    40 of 50 stocks down" says a handful of heavyweights are carrying the
    move, and a move carried by a handful is the kind that reverses.
    """
    values = [v for v in changes.values() if v is not None]
    if not values:
        return {"available": False}

    advancing = sum(1 for v in values if v > 0)
    declining = sum(1 for v in values if v < 0)
    med = median(values)

    out: dict[str, Any] = {
        "available": True,
        "covered": len(values),
        "advancing": advancing,
        "declining": declining,
        "unchanged": len(values) - advancing - declining,
        "advance_ratio": round(advancing / len(values), 3),
        "median_change_pct": round(med, 2),
        "average_change_pct": round(sum(values) / len(values), 2),
    }

    if index_change_pct is not None:
        # Cap-weighted index versus the median stock. A wide gap is the
        # definition of a narrow move.
        gap = index_change_pct - med
        out["index_change_pct"] = round(index_change_pct, 2)
        out["breadth_gap_pct"] = round(gap, 2)
        out["narrow"] = abs(gap) > 0.4
        out["divergent"] = (index_change_pct > 0) != (med > 0) and abs(gap) > 0.3
    return out


def live_changes(index: str, price_fn: Callable[[str], Any]) -> dict[str, float]:
    """Constituent moves from the live feed.

    `price_fn` takes an instrument key and returns a Tick (or None), so this
    stays independent of feed_service for testing.
    """
    out: dict[str, float] = {}
    for m in load(index):
        if not m.instrument_key:
            continue
        tick = price_fn(m.instrument_key)
        if tick is None:
            continue
        change = getattr(tick, "change_pct", None)
        if change is not None:
            out[m.symbol] = float(change)
    return out
