"""Shared machinery for every external data source.

A collector's only job is to turn one external source into a list of Points.
Everything around that -- HTTP, retries, backoff, rate limiting, recording the
run -- lives here, so each collector stays small enough to read in one sitting.

Two rules every collector follows:

  1. **Parsing is a pure function.** `parse()` takes the raw payload and
     returns Points. No network, no clock. That is what makes collectors
     testable against a saved response instead of a live API.
  2. **A failing source never takes the app down.** `run()` catches, records
     and returns. Missing data is a degraded signal; a crashed process is a
     dead one.
"""
from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass, field
from typing import Any

import certifi
import httpx

from ..clock import now_ist
from ..ratelimit import TokenBucket
from ..store import macro

# Some installers (PostgreSQL, here) point CURL_CA_BUNDLE at a file that does
# not exist, which breaks every HTTPS call with a confusing error. Pinning the
# CA bundle to certifi means we never depend on the environment.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

DEFAULT_TIMEOUT = 20.0
MAX_ATTEMPTS = 3


class CollectorError(Exception):
    pass


@dataclass
class CollectorStatus:
    name: str
    enabled: bool = True
    last_run: str | None = None
    last_success: str | None = None
    last_error: str | None = None
    runs: int = 0
    failures: int = 0
    points_total: int = 0
    points_last: int = 0

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["healthy"] = self.last_error is None and self.runs > 0
        return d


@dataclass
class Collector:
    """Base class. Subclasses implement `fetch()` and `parse()`."""

    name: str = "collector"
    source: str = "unknown"
    interval_seconds: float = 300.0
    rate: TokenBucket | None = None
    status: CollectorStatus = field(init=False)

    def __post_init__(self) -> None:
        self.status = CollectorStatus(name=self.name)

    # ---- subclasses override these ------------------------------------
    async def fetch(self) -> Any:
        """Get the raw payload from the source."""
        raise NotImplementedError

    def parse(self, raw: Any) -> list[macro.Point]:
        """Pure: raw payload -> Points. No I/O, no clock."""
        raise NotImplementedError

    def is_due(self) -> bool:
        """Override for collectors that only run in certain hours."""
        return self.status.enabled

    # ---- shared ---------------------------------------------------------
    async def http_get(
        self, url: str, *, params: dict | None = None, headers: dict | None = None
    ) -> httpx.Response:
        """GET with retry and backoff, rate-limited if a bucket was given."""
        if self.rate is not None:
            await self.rate.acquire()

        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                async with httpx.AsyncClient(
                    timeout=DEFAULT_TIMEOUT, verify=SSL_CONTEXT, follow_redirects=True
                ) as c:
                    r = await c.get(url, params=params, headers=headers or {})
                # 429 and 5xx are worth retrying; a 4xx will fail the same way
                # every time, so fail fast and surface the real message.
                if r.status_code == 429 or r.status_code >= 500:
                    raise CollectorError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r
            except Exception as exc:                 # noqa: BLE001
                last = exc
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(2 ** attempt)
        raise CollectorError(f"{self.name}: {url} failed after "
                             f"{MAX_ATTEMPTS} attempts ({last})")

    async def run(self) -> int:
        """One collection cycle. Returns points saved. Never raises."""
        self.status.runs += 1
        self.status.last_run = now_ist().isoformat(timespec="seconds")
        run_id = macro.start_run(self.name)

        try:
            points = self.parse(await self.fetch())
        except Exception as exc:                     # noqa: BLE001
            self.status.failures += 1
            self.status.last_error = str(exc)
            macro.finish_run(run_id, "error", 0, str(exc))
            print(f"[{self.name}] failed: {exc}")
            return 0

        saved = macro.save(points, self.source)
        self.status.points_last = saved
        self.status.points_total += saved
        self.status.last_success = self.status.last_run
        self.status.last_error = None
        macro.finish_run(run_id, "ok", saved)
        return saved


class CollectorRunner:
    """Runs collectors on their own schedules, each isolated from the others."""

    def __init__(self) -> None:
        self.collectors: dict[str, Collector] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()

    def register(self, collector: Collector) -> Collector:
        self.collectors[collector.name] = collector
        return collector

    async def run_once(self, name: str) -> int:
        collector = self.collectors.get(name)
        if not collector:
            raise KeyError(f"Unknown collector: {name}")
        return await collector.run()

    async def _loop(self, collector: Collector) -> None:
        # Collect immediately so a restart does not leave a gap.
        while not self._stop.is_set():
            if collector.is_due():
                await collector.run()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=collector.interval_seconds)
                return
            except asyncio.TimeoutError:
                continue

    async def start(self, names: list[str] | None = None) -> list[str]:
        self._stop.clear()
        started = []
        for name, collector in self.collectors.items():
            if names is not None and name not in names:
                continue
            if name in self._tasks and not self._tasks[name].done():
                continue
            self._tasks[name] = asyncio.create_task(
                self._loop(collector), name=f"collector-{name}")
            started.append(name)
        return started

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    def status(self) -> dict[str, Any]:
        return {
            "running": [n for n, t in self._tasks.items() if not t.done()],
            "collectors": {n: c.status.as_dict() for n, c in self.collectors.items()},
        }


runner = CollectorRunner()
