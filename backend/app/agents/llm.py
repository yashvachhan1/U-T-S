"""Groq client, key pool and model routing.

Groq speaks the OpenAI chat-completions protocol, so this needs httpx and
nothing else -- one less dependency to keep current.

Key pooling exists for isolation rather than throughput. The whole system runs
at roughly 0.6 requests a minute, so a single paid key is ample. What several
keys buy is a bulkhead: one agent hitting a limit cannot starve the others.

The stub client matters as much as the real one. Tests must not depend on a
network, a key, or a model's mood, and a deterministic stub is also how the
prompt-injection tests stay meaningful.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import certifi
import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# PostgreSQL's installer leaves CURL_CA_BUNDLE pointing at a file that does
# not exist on this machine, which breaks every HTTPS call with a confusing
# error. Pinning to certifi keeps us off the environment.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class ModelTier:
    """Which model does what.

    Analysts turn messy text into fixed fields -- high volume, little
    reasoning, so a small fast model is right. Synthesis weighs contradictory
    inputs, which is where a larger model earns its cost.

    Llama 3.3 70B and 3.1 8B were retired in August 2026; these are the
    current replacements. Confirm against the Groq console before relying on
    a name.
    """

    FAST = "openai/gpt-oss-20b"
    STRONG = "openai/gpt-oss-120b"
    FALLBACK = "qwen/qwen3-32b"


class LLMError(Exception):
    pass


class RateLimited(LLMError):
    pass


class LLMClient(Protocol):
    async def complete(self, *, system: str, user: str, model: str,
                       max_tokens: int = 800) -> tuple[str, dict[str, int]]:
        ...


@dataclass
class KeyPool:
    """Round-robin over available keys, with failover on rate limits.

    A key that returns 429 is parked briefly rather than dropped -- limits are
    per-minute, so it will be usable again shortly.
    """

    keys: list[str] = field(default_factory=list)
    _cooldown: dict[str, float] = field(default_factory=dict, repr=False)
    _cycle: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.keys = [k.strip() for k in self.keys if k and k.strip()]
        self._cycle = itertools.cycle(range(len(self.keys))) if self.keys else None

    @property
    def configured(self) -> bool:
        return bool(self.keys)

    def available(self) -> list[str]:
        now = time.monotonic()
        return [k for k in self.keys if self._cooldown.get(k, 0) <= now]

    def next_key(self) -> str:
        usable = self.available()
        if not usable:
            raise RateLimited("saari Groq keys rate-limited hain")
        idx = next(self._cycle) % len(self.keys)
        key = self.keys[idx]
        return key if key in usable else usable[0]

    def park(self, key: str, seconds: float = 30.0) -> None:
        self._cooldown[key] = time.monotonic() + seconds

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "total": len(self.keys),
            "available": len(self.available()),
        }


def _load_keys() -> list[str]:
    """GROQ_API_KEYS takes a comma-separated list; GROQ_API_KEY a single one."""
    multi = os.getenv("GROQ_API_KEYS", "")
    if multi.strip():
        return multi.split(",")
    single = os.getenv("GROQ_API_KEY", "")
    return [single] if single.strip() else []


class GroqClient:
    """Minimal Groq client over httpx."""

    def __init__(self, pool: KeyPool | None = None, timeout: float = 30.0):
        self.pool = pool or KeyPool(_load_keys())
        self.timeout = timeout
        self.calls = 0
        self.failures = 0
        self.total_tokens = 0

    @property
    def configured(self) -> bool:
        return self.pool.configured

    async def complete(self, *, system: str, user: str, model: str,
                       max_tokens: int = 800) -> tuple[str, dict[str, int]]:
        if not self.configured:
            raise LLMError(
                "GROQ_API_KEY set nahi hai. Free key: https://console.groq.com "
                "-- phir .env me daalein."
            )

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Deterministic as the API allows. Agent outputs are stored and
            # replayed in backtests, so run-to-run drift is a real cost.
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        last: Exception | None = None
        for _ in range(max(1, len(self.pool.keys))):
            key = self.pool.next_key()
            self.calls += 1
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout, verify=SSL_CONTEXT
                ) as c:
                    r = await c.post(
                        GROQ_URL, json=body,
                        headers={"Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json"},
                    )

                if r.status_code == 429:
                    self.pool.park(key)
                    last = RateLimited(f"key rate-limited: {r.text[:120]}")
                    continue
                if r.status_code >= 400:
                    raise LLMError(f"Groq HTTP {r.status_code}: {r.text[:200]}")

                data = r.json()
                text = data["choices"][0]["message"]["content"]
                usage = data.get("usage") or {}
                tokens = {
                    "in": int(usage.get("prompt_tokens", 0)),
                    "out": int(usage.get("completion_tokens", 0)),
                }
                self.total_tokens += tokens["in"] + tokens["out"]
                return text, tokens

            except RateLimited as exc:
                last = exc
                continue
            except Exception as exc:              # noqa: BLE001
                self.failures += 1
                raise LLMError(f"Groq call failed: {exc}") from exc

        self.failures += 1
        raise LLMError(f"saari keys exhaust ho gayin: {last}")

    def status(self) -> dict[str, Any]:
        return {
            "provider": "groq",
            "configured": self.configured,
            "keys": self.pool.status(),
            "calls": self.calls,
            "failures": self.failures,
            "total_tokens": self.total_tokens,
        }


class StubClient:
    """Deterministic client for tests and offline runs.

    Returns queued responses in order. Without it, every agent test would need
    a key and a network, and the injection tests would depend on how a
    particular model happened to behave that day.
    """

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict[str, str]] = []
        self.configured = True

    def queue(self, response: str | dict) -> StubClient:
        self.responses.append(
            response if isinstance(response, str) else json.dumps(response))
        return self

    async def complete(self, *, system: str, user: str, model: str,
                       max_tokens: int = 800) -> tuple[str, dict[str, int]]:
        self.calls.append({"system": system, "user": user, "model": model})
        if not self.responses:
            raise LLMError("stub ke paas aur response nahi hai")
        text = self.responses.pop(0)
        return text, {"in": len(user) // 4, "out": len(text) // 4}

    def status(self) -> dict[str, Any]:
        return {"provider": "stub", "configured": True, "calls": len(self.calls)}


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Even with response_format set to JSON, models sometimes wrap output in a
    fence or add a sentence before it. This recovers the object rather than
    throwing away an otherwise good answer -- but it only ever returns parsed
    JSON, never a guess at what the text meant.
    """
    if not text or not text.strip():
        raise LLMError("khali response")

    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = _FENCE.search(stripped)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(stripped[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise LLMError(f"response me valid JSON nahi mila: {stripped[:160]}")


client: LLMClient = GroqClient()


def use_client(new_client: LLMClient) -> None:
    """Swap the client -- used by tests, and to run offline."""
    global client
    client = new_client
