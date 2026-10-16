"""News, filings and other dated events.

Unlike macro series, these are text, and text from the open internet is
**untrusted input**. An attacker can publish a filing or article containing
"ignore previous instructions". Nothing here parses that text for meaning --
it only stores it, and agents must return structured output that the decision
engine reads instead of prose. See docs/05-security.md.

What this layer does provide:
  * **deduplication** -- the same story reaches us from several feeds, and
    counting it five times would look like five independent signals
  * **sanitising** -- control characters stripped, length capped, so a
    malformed payload cannot blow up a prompt or a log line
  * **provenance** -- every row records where it came from and when we saw it
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .. import db
from ..clock import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash  TEXT NOT NULL UNIQUE,
    source        TEXT NOT NULL,
    category      TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT,
    url           TEXT,
    symbols       TEXT,                 -- comma separated, uppercase
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    meta          TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_fetched ON events(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_published ON events(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source, published_at DESC);
"""

# Caps so a hostile or broken payload cannot bloat the database or a prompt.
MAX_TITLE = 500
MAX_BODY = 20_000

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")

_ready = False


def ensure_schema() -> None:
    global _ready
    if not _ready:
        conn = db.conn()
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True


def clean(text: str | None, limit: int = MAX_BODY) -> str:
    """Strip control characters, collapse whitespace, cap length.

    Not a security boundary on its own -- the real defence is that agents
    return structured fields and nothing downstream reads free text. This just
    keeps the data sane.
    """
    if not text:
        return ""
    text = _CONTROL.sub(" ", str(text))
    text = _WS.sub(" ", text).strip()
    return text[:limit]


@dataclass
class Event:
    source: str
    category: str
    title: str
    body: str = ""
    url: str = ""
    symbols: list[str] = field(default_factory=list)
    published_at: str | None = None
    meta: dict | None = None

    def normalised(self) -> Event:
        return Event(
            source=self.source,
            category=self.category,
            title=clean(self.title, MAX_TITLE),
            body=clean(self.body, MAX_BODY),
            url=clean(self.url, 1000),
            symbols=sorted({s.strip().upper() for s in self.symbols if s and s.strip()}),
            published_at=self.published_at,
            meta=self.meta,
        )

    @property
    def content_hash(self) -> str:
        """Identity for dedup.

        Title plus URL, not the body: feeds reword summaries and re-publish,
        and the same story arriving twice should still count once.
        """
        basis = f"{self.source}|{clean(self.title, MAX_TITLE).lower()}|{self.url}"
        return hashlib.sha256(basis.encode()).hexdigest()[:32]


def save(events: Iterable[Event]) -> dict[str, int]:
    """Insert new events, ignore ones we already have."""
    ensure_schema()
    fetched = now_ist().isoformat(timespec="seconds")

    rows = []
    seen: set[str] = set()
    total = 0
    for raw in events:
        total += 1
        ev = raw.normalised()
        if not ev.title:
            continue
        h = ev.content_hash
        if h in seen:                 # duplicate inside this same batch
            continue
        seen.add(h)
        rows.append((
            h, ev.source, ev.category, ev.title, ev.body, ev.url,
            ",".join(ev.symbols), ev.published_at, fetched,
            db.dumps(ev.meta) if ev.meta else None,
        ))

    if not rows:
        return {"seen": total, "new": 0, "duplicates": total}

    conn = db.conn()
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.executemany(
        """INSERT OR IGNORE INTO events
           (content_hash, source, category, title, body, url, symbols,
            published_at, fetched_at, meta)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    new = after - before
    return {"seen": total, "new": new, "duplicates": total - new}


def recent(limit: int = 50, source: str | None = None,
           category: str | None = None) -> list[dict[str, Any]]:
    ensure_schema()
    where, params = [], []
    if source:
        where.append("source = ?")
        params.append(source)
    if category:
        where.append("category = ?")
        params.append(category)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    return db.query(
        f"SELECT id, source, category, title, url, symbols, published_at, fetched_at "
        f"FROM events {clause} ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
        tuple(params),
    )


def for_symbol(symbol: str, limit: int = 20) -> list[dict[str, Any]]:
    """Everything filed about one instrument.

    This is what an event blackout reads: 'is there fresh news on this stock?'
    """
    ensure_schema()
    pattern = f"%{symbol.upper()}%"
    return db.query(
        "SELECT id, source, category, title, url, symbols, published_at, fetched_at "
        "FROM events WHERE symbols LIKE ? "
        "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
        (pattern, limit),
    )


def symbols_with_news(since_minutes: int = 120) -> list[str]:
    """Instruments with any filing in the recent window.

    News is a poor entry signal -- by the time we read it, the move has
    happened. It is a good *blackout* signal, and this is the list that feeds
    it.
    """
    ensure_schema()
    from datetime import timedelta

    cutoff = (now_ist() - timedelta(minutes=since_minutes)).isoformat(timespec="seconds")
    rows = db.query(
        "SELECT symbols FROM events WHERE fetched_at >= ? AND symbols != ''",
        (cutoff,),
    )
    out: set[str] = set()
    for row in rows:
        out.update(s for s in row["symbols"].split(",") if s)
    return sorted(out)


def stats() -> dict[str, Any]:
    ensure_schema()
    by_source = db.query(
        "SELECT source, COUNT(*) AS n, MAX(fetched_at) AS last_seen "
        "FROM events GROUP BY source ORDER BY n DESC")
    total = db.query("SELECT COUNT(*) AS n FROM events")[0]["n"]
    return {"total": total, "by_source": by_source}
