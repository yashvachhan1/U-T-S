"""Tick persistence: buffer in memory, write Parquet in the background.

Measured on this machine (1M rows): Parquet is 4.6x smaller than SQLite and
123x faster on a full scan, which is the shape every backtest query takes.
Benchmark lives in tools/bench_storage.py.

The hard rule is that the feed loop must never wait on disk. Ticks go into a
list, and a background task flushes them. If the writer falls behind, ticks are
dropped rather than letting the buffer grow without bound -- stale prices are
worse than missing ones.
"""
from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..clock import now_ist
from ..config import DATA_DIR

MARKET_DIR = DATA_DIR / "market"
FLUSH_ROWS = 10_000          # write once the buffer reaches this
FLUSH_SECONDS = 30.0         # ...or this long since the last write
MAX_BUFFER = 200_000         # beyond this we are losing; drop oldest

_SCHEMA_COLUMNS = [
    "instrument_key", "ts", "ltp", "ltq", "close_price", "volume", "atp",
    "oi", "iv", "bid_qty", "bid_price", "ask_qty", "ask_price",
    "total_buy_qty", "total_sell_qty", "book_imbalance", "mode",
]


@dataclass
class WriterStats:
    buffered: int = 0
    written: int = 0
    dropped: int = 0
    flushes: int = 0
    last_flush: str | None = None
    last_error: str | None = None
    files: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class TickWriter:
    """Batches ticks and writes date-partitioned Parquet."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (MARKET_DIR / "ticks")
        self.stats = WriterStats()
        self._buffer: deque[dict] = deque(maxlen=MAX_BUFFER)
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._seq = 0

    # ------------------------------------------------------------ ingest
    def add(self, row: dict) -> None:
        """Called from the feed handler. Must stay cheap and non-blocking."""
        with self._lock:
            if len(self._buffer) == MAX_BUFFER:
                self.stats.dropped += 1       # deque evicts the oldest for us
            self._buffer.append(row)
            self.stats.buffered = len(self._buffer)

    def add_many(self, rows: list[dict]) -> None:
        for row in rows:
            self.add(row)

    # ------------------------------------------------------------- flush
    def _take(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            n = len(self._buffer) if limit is None else min(limit, len(self._buffer))
            rows = [self._buffer.popleft() for _ in range(n)]
            self.stats.buffered = len(self._buffer)
        return rows

    def flush_sync(self) -> int:
        """Write everything buffered. Returns rows written.

        Runs off the event loop (via to_thread) so pyarrow's compression never
        stalls the feed.
        """
        rows = self._take()
        if not rows:
            return 0
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            self.stats.last_error = f"pyarrow missing: {exc}"
            return 0

        try:
            day = now_ist().strftime("%Y-%m-%d")
            out_dir = self.root / f"date={day}"
            out_dir.mkdir(parents=True, exist_ok=True)

            columns = {c: [r.get(c) for r in rows] for c in _SCHEMA_COLUMNS}
            table = pa.table(columns)

            self._seq += 1
            path = out_dir / f"part-{now_ist():%H%M%S}-{self._seq:04d}.parquet"
            pq.write_table(table, path, compression="zstd")

            self.stats.written += len(rows)
            self.stats.flushes += 1
            self.stats.files += 1
            self.stats.last_flush = now_ist().isoformat(timespec="seconds")
            self.stats.last_error = None
            return len(rows)
        except Exception as exc:               # noqa: BLE001
            # Never re-raise into the feed path. Losing a batch beats losing
            # the connection.
            self.stats.last_error = str(exc)
            return 0

    async def flush(self) -> int:
        return await asyncio.to_thread(self.flush_sync)

    # ---------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="tick-writer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.flush()                      # don't lose the tail

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=FLUSH_SECONDS)
                return
            except asyncio.TimeoutError:
                pass
            if self.stats.buffered:
                await self.flush()

    def should_flush_now(self) -> bool:
        return self.stats.buffered >= FLUSH_ROWS


# --------------------------------------------------------------- reading
def read_day(day: str, root: Path | None = None) -> Any:
    """Read one day's ticks with DuckDB. Returns a pandas DataFrame."""
    import duckdb

    root = root or (MARKET_DIR / "ticks")
    pattern = str(root / f"date={day}" / "*.parquet")
    return duckdb.connect().execute(f"SELECT * FROM '{pattern}'").fetchdf()


def available_days(root: Path | None = None) -> list[str]:
    root = root or (MARKET_DIR / "ticks")
    if not root.exists():
        return []
    return sorted(
        p.name.removeprefix("date=")
        for p in root.iterdir()
        if p.is_dir() and p.name.startswith("date=")
    )


writer = TickWriter()
