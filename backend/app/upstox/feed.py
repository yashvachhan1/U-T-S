"""Upstox V3 market data WebSocket feed.

Polling gave us a price every 5 seconds. That is too stale to manage a stop,
and it cannot produce order-flow features at all -- those need the book, not a
single number. This is the connection that replaces it.

Protocol, from the V3 docs:
  * GET /v3/feed/market-data-feed/authorize returns the wss URL
  * subscription requests are JSON, sent as BINARY frames
  * responses are protobuf FeedResponse messages

The decoder lives in ticks.py; this module owns the connection, the
subscription state, and staying alive.
"""
from __future__ import annotations

import asyncio
import json
import random
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import websockets

from .. import ratelimit
from ..clock import now_ist
from . import client

AUTHORIZE_PATH = "/v3/feed/market-data-feed/authorize"


class Mode(str, Enum):
    """Subscription modes, with the per-mode key limits from the docs."""

    LTPC = "ltpc"
    OPTION_GREEKS = "option_greeks"
    FULL = "full"
    FULL_D30 = "full_d30"

    @property
    def max_keys(self) -> int:
        return {
            Mode.LTPC: 5000,
            Mode.OPTION_GREEKS: 3000,
            Mode.FULL: 2000,
            Mode.FULL_D30: 50,
        }[self]


# When more than one mode is in use, every mode is capped at this instead.
COMBINED_KEY_LIMIT = 1500

# Upstox allows 2 concurrent connections per user; one is plenty and leaves
# the spare for debugging without tripping the limit.
MAX_RECONNECT_DELAY = 60.0
INITIAL_RECONNECT_DELAY = 1.0
IDLE_TIMEOUT = 45.0


class FeedError(Exception):
    pass


class SubscriptionLimitExceeded(FeedError):
    pass


@dataclass
class Subscription:
    """What we want the server to be sending us."""

    by_mode: dict[Mode, set[str]] = field(default_factory=dict)

    def keys(self, mode: Mode) -> set[str]:
        return self.by_mode.setdefault(mode, set())

    @property
    def all_keys(self) -> set[str]:
        return {k for keys in self.by_mode.values() for k in keys}

    @property
    def active_modes(self) -> list[Mode]:
        return [m for m, keys in self.by_mode.items() if keys]

    def limit_for(self, mode: Mode) -> int:
        """A single mode gets its own cap; mixing modes caps every one."""
        others = [m for m in self.active_modes if m is not mode]
        return COMBINED_KEY_LIMIT if others else mode.max_keys

    def check_can_add(self, mode: Mode, new_keys: Iterable[str]) -> None:
        resulting = self.keys(mode) | set(new_keys)
        limit = self.limit_for(mode)
        if len(resulting) > limit:
            raise SubscriptionLimitExceeded(
                f"{mode.value} mode me {len(resulting)} keys ho jayengi, "
                f"limit {limit} hai."
            )

    def add(self, mode: Mode, new_keys: Iterable[str]) -> set[str]:
        new_keys = {k for k in new_keys if k}
        self.check_can_add(mode, new_keys)
        added = new_keys - self.keys(mode)
        self.keys(mode).update(added)
        return added

    def remove(self, keys: Iterable[str]) -> set[str]:
        keys = set(keys)
        removed: set[str] = set()
        for mode_keys in self.by_mode.values():
            hit = mode_keys & keys
            mode_keys -= hit
            removed |= hit
        return removed


def build_message(method: str, mode: Mode | None, keys: Iterable[str]) -> bytes:
    """Subscription frames are JSON, but must be sent as binary."""
    data: dict[str, Any] = {"instrumentKeys": sorted(keys)}
    if mode is not None:
        data["mode"] = mode.value
    payload = {
        "guid": uuid.uuid4().hex[:20],
        "method": method,
        "data": data,
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter.

    Jitter matters: without it every reconnect after a broker-side blip lands
    at the same instant, which is how a hiccup turns into a thundering herd.
    """
    base = min(INITIAL_RECONNECT_DELAY * (2 ** attempt), MAX_RECONNECT_DELAY)
    return base * (0.5 + random.random() * 0.5)


class MarketFeed:
    """One WebSocket connection, kept alive and resubscribed across drops."""

    def __init__(self, on_message: Callable[[bytes], None] | None = None) -> None:
        self.subscription = Subscription()
        self.on_message = on_message
        self.connected = False
        self.last_message_at: float | None = None
        self.messages_received = 0
        self.reconnects = 0
        self.last_error: str | None = None

        self._ws: Any = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._send_lock = asyncio.Lock()

    # ------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "subscribed_keys": len(self.subscription.all_keys),
            "modes": {m.value: len(k) for m, k in self.subscription.by_mode.items() if k},
            "messages_received": self.messages_received,
            "reconnects": self.reconnects,
            "last_message_at": self.last_message_at,
            "last_error": self.last_error,
        }

    # -------------------------------------------------------- connection
    async def _authorized_url(self) -> str:
        """Ask Upstox where to connect. Returns a short-lived wss URL."""
        await ratelimit.market_data.acquire()
        data = await client.get(AUTHORIZE_PATH)
        url = (data or {}).get("authorized_redirect_uri") or (data or {}).get("authorizedRedirectUri")
        if not url:
            raise FeedError(f"Authorize response me wss URL nahi mila: {data}")
        return url

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_forever(), name="market-feed")

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.connected = False

    async def _run_forever(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                attempt = 0                       # a clean session resets backoff
            except asyncio.CancelledError:
                raise
            except Exception as exc:              # noqa: BLE001 -- feed must survive
                self.connected = False
                self.last_error = str(exc)
                if self._stop.is_set():
                    return
                delay = backoff_delay(attempt)
                attempt += 1
                self.reconnects += 1
                print(f"[feed] disconnected ({exc}); retry #{attempt} in {delay:.1f}s")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    return                        # stop() arrived while waiting
                except asyncio.TimeoutError:
                    continue

    async def _connect_once(self) -> None:
        url = await self._authorized_url()
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=10 * 1024 * 1024,
        ) as ws:
            self._ws = ws
            self.connected = True
            self.last_error = None
            print(f"[feed] connected at {now_ist():%H:%M:%S} IST")

            # The server starts with nothing subscribed, so replay our state.
            await self._replay_subscriptions()

            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    # Silence past the ping interval means a half-open socket.
                    # Dropping it is better than sitting on stale prices.
                    raise FeedError(f"{IDLE_TIMEOUT}s se koi message nahi aaya")

                self.messages_received += 1
                self.last_message_at = asyncio.get_running_loop().time()
                if self.on_message is not None:
                    try:
                        self.on_message(raw)
                    except Exception as exc:      # noqa: BLE001
                        # A bad handler must never kill the connection.
                        print(f"[feed] handler error: {exc}")

        self.connected = False
        self._ws = None

    # ------------------------------------------------------ subscriptions
    async def _send(self, payload: bytes) -> None:
        if self._ws is None or not self.connected:
            return
        async with self._send_lock:
            await self._ws.send(payload)

    async def _replay_subscriptions(self) -> None:
        for mode, keys in self.subscription.by_mode.items():
            if keys:
                await self._send(build_message("sub", mode, keys))
                print(f"[feed] resubscribed {len(keys)} keys in {mode.value}")

    async def subscribe(self, keys: Iterable[str], mode: Mode = Mode.LTPC) -> set[str]:
        """Add keys. Raises before sending anything if it would breach a limit."""
        added = self.subscription.add(mode, keys)
        if added:
            await self._send(build_message("sub", mode, added))
        return added

    async def unsubscribe(self, keys: Iterable[str]) -> set[str]:
        removed = self.subscription.remove(keys)
        if removed:
            await self._send(build_message("unsub", None, removed))
        return removed

    async def change_mode(self, keys: Iterable[str], mode: Mode) -> None:
        keys = set(keys)
        self.subscription.remove(keys)
        self.subscription.add(mode, keys)
        await self._send(build_message("change_mode", mode, keys))


# Module-level instance; the app wires its handler in at startup.
feed = MarketFeed()
