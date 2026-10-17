"""Helpers the analyst agents share.

Two ideas run through all of it.

**Numbers come from the store, judgements come from the model.** Every
`gather()` here formats data we already hold into a readable block. No agent
is asked what the price of crude is; it is told, and asked what it means. A
model that invents a plausible-looking number is far more dangerous than one
that gives a vague opinion, so it is never given the chance.

**Missing data is stated, not hidden.** When a source is down the block says
so in words the model can read, because "crude: unavailable" leads to a
low-confidence answer while a silently omitted line leads to a confident one
about whatever is left.
"""
from __future__ import annotations

from typing import Any, Iterable

from ...store import agent_outputs as store
from ...upstox import instruments

UNAVAILABLE = "unavailable"


def num(value: Any, digits: int = 2, suffix: str = "") -> str:
    """Format a number for a prompt, or say it is missing."""
    if value is None:
        return UNAVAILABLE
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return UNAVAILABLE
    try:
        return f"{float(value):+.{digits}f}%"
    except (TypeError, ValueError):
        return str(value)


def block(title: str, rows: dict[str, Any] | Iterable[str]) -> str:
    """One labelled section of a prompt."""
    lines = [f"{title}:"]
    if isinstance(rows, dict):
        if not rows:
            lines.append(f"- {UNAVAILABLE}")
        else:
            lines.extend(f"- {k}: {v}" for k, v in rows.items())
    else:
        items = list(rows)
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append(f"- {UNAVAILABLE}")
    return "\n".join(lines)


def join(*sections: str) -> str:
    return "\n\n".join(s for s in sections if s)


def headline_lines(rows: list[dict[str, Any]], limit: int = 15) -> list[str]:
    """Event rows as one line each, newest first."""
    out = []
    for row in rows[:limit]:
        when = (row.get("published_at") or row.get("fetched_at") or "")[:19]
        symbols = row.get("symbols") or ""
        tag = f" [{symbols}]" if symbols else ""
        out.append(f"[{when}] ({row.get('source', '?')}){tag} {row.get('title', '')}")
    return out


def peer_view(agent: str, max_age_seconds: int = 3600) -> str:
    """Another agent's stored view, as a single line.

    Agents read each other through the store rather than by calling each
    other. That keeps the dependency one-way and replayable: a backtest sees
    exactly the view that existed at the time, not one recomputed later.
    """
    row = store.latest(agent, max_age_seconds)
    if not row:
        return f"{agent}: no recent view"
    payload = row.get("payload") or {}
    return (f"{agent}: {payload.get('view', 'neutral')} "
            f"(confidence {payload.get('confidence', 0)}, "
            f"risk_flag {payload.get('risk_flag', False)})")


# --------------------------------------------------------------------------
# Live quotes
# --------------------------------------------------------------------------
def quote_rows(keys: dict[str, str], price_fn) -> dict[str, str]:
    """name -> formatted live quote, for instruments we are subscribed to.

    An unsubscribed instrument says so instead of showing a zero. Zero is a
    price; "not subscribed" is the truth.
    """
    out: dict[str, str] = {}
    for label, key in keys.items():
        if not key:
            out[label] = "no instrument key"
            continue
        tick = price_fn(key)
        if tick is None:
            out[label] = "not subscribed"
            continue
        out[label] = f"{num(tick.ltp)} ({pct(tick.change_pct)})"
    return out


def nearest_future(query: str, segment: str) -> str:
    """Instrument key of the nearest-expiry future matching a name.

    Derivative keys must never be hardcoded -- USDINR futures expire weekly,
    so a key written down today is wrong next Friday. `search()` already
    sorts futures first and nearest expiry first, so the first hit is the
    front month.
    """
    if not instruments.loaded():
        return ""
    hits = instruments.search(query, limit=5, segment=segment)
    for hit in hits:
        if hit.get("type") == "FUT":
            return hit.get("instrument_key", "")
    return hits[0].get("instrument_key", "") if hits else ""


def index_key(name: str) -> str:
    """Instrument key for an NSE index, by exact name.

    Exact match first, and only then a search. Index names collide badly --
    "Nifty Fin Service", "Nifty FinSrv25 50", "Nifty FinSerExBnk" and
    "Nifty MS Fin Serv" are four different indices -- so a fuzzy match would
    quietly hand back the wrong one. Index keys contain no expiry and do not
    rotate, which is what makes the exact name reliable here and not for
    futures.
    """
    if not instruments.loaded():
        return ""
    wanted = name.strip().lower()
    for row in instruments.all_instruments():
        if row["segment"] == "NSE_INDEX" and row["name"].strip().lower() == wanted:
            return row["instrument_key"]
    hits = instruments.search(name, limit=3, segment="NSE_INDEX")
    return hits[0].get("instrument_key", "") if hits else ""


def equity_key(symbol: str) -> str:
    if not instruments.loaded():
        return ""
    hits = instruments.search(symbol, limit=3, segment="NSE_EQ")
    return hits[0].get("instrument_key", "") if hits else ""


# --------------------------------------------------------------------------
# Prompt fragments reused across agents
# --------------------------------------------------------------------------
HONESTY_RULE = """If the data below is missing, stale or thin, say so by
returning a neutral view with low confidence. A neutral answer costs nothing;
a confident answer built on absent data costs money."""

SECTOR_NOTE = """Use only these sector labels: IT, BANK, FINANCIAL, PHARMA,
HEALTHCARE, AUTO, FMCG, METAL, ENERGY, OMC, OIL_GAS, REALTY, INFRA, CEMENT,
TELECOM, MEDIA, CHEMICALS, PAINTS, AVIATION, POWER, CAPITAL_GOODS,
CONSUMER_DURABLES, TEXTILES, DEFENCE, PSU. Anything else is discarded."""

BASE_FIELDS = """  "view": "bullish" | "bearish" | "neutral",
  "confidence": 0.0 to 1.0,
  "horizon": "intraday" | "overnight" | "swing",
  "sectors_positive": [],
  "sectors_negative": [],
  "affected_symbols": [],
  "risk_flag": true | false,
  "reasoning": "one or two sentences\""""


def schema_block(extra: str = "") -> str:
    """Standard schema hint with an agent's extra fields appended."""
    body = BASE_FIELDS + (",\n" + extra if extra else "")
    return "Return exactly this JSON shape:\n{\n" + body + "\n}"
