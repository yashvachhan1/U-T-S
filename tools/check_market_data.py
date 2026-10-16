"""Kaun sa data sach me milta hai -- aur kab.

Upstox ke docs ye saaf nahi batate ki commodity (NSE_COM) aur currency (NCD_FO)
par quote / candle endpoints chalte hain ya nahi. Ye script har segment par
teeno endpoint try karta hai aur ek pass/fail table deta hai.

Chalane ka tareeka (login hone ke baad):

    .venv\\Scripts\\python.exe tools\\check_market_data.py

Market band hone ke baad dobara chalayein -- tab pata chalega ki commodity ka
data equity band hone par bhi aa raha hai ya nahi.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.clock import IST  # noqa: E402
from app.upstox import client, instruments, marketdata  # noqa: E402

# (label, search term, segment) -- the nearest contract is resolved at runtime,
# because currency futures expire weekly and any hardcoded key goes stale.
TARGETS = [
    ("Nifty index", "NIFTY", "NSE_INDEX"),
    ("Equity", "RELIANCE", "NSE_EQ"),
    ("Gold ETF", "GOLDBEES", "NSE_EQ"),
    ("Silver ETF", "SILVERBEES", "NSE_EQ"),
    ("USDINR fut", "USDINR", "NCD_FO"),
    ("Crude fut", "CRUDEOIL", "NSE_COM"),
    ("Gold fut", "GOLD", "NSE_COM"),
    ("Silver fut", "SILVER", "NSE_COM"),
]

WINDOWS = [
    ("Equity", "09:15", "15:30"),
    ("Currency", "09:00", "17:00"),
    ("Commodity", "09:00", "23:30"),
]


def resolve(term: str, segment: str) -> dict | None:
    """Nearest matching contract for this segment."""
    for row in instruments.search(term, limit=40, segment=segment):
        return row
    return None


def open_now(start: str, end: str, now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    hhmm = now.strftime("%H:%M")
    return start <= hhmm <= end


async def probe(row: dict) -> dict[str, str]:
    """Try each endpoint; report what came back."""
    out: dict[str, str] = {}
    key = row["instrument_key"]

    try:
        data = await marketdata.ltp([key])
        hit = data.get(key) or (list(data.values())[0] if data else None)
        out["ltp"] = f"OK {hit['ltp']}" if hit and hit.get("ltp") is not None else "empty"
    except client.UpstoxError as exc:
        out["ltp"] = f"FAIL {exc.message[:40]}"

    try:
        rows = await marketdata.snapshot([key])
        out["quote"] = f"OK {rows[0]['ltp']}" if rows and rows[0].get("ltp") is not None else "empty"
    except client.UpstoxError as exc:
        out["quote"] = f"FAIL {exc.message[:40]}"

    try:
        rows = await marketdata.intraday(key, "5m")
        out["intraday"] = f"OK {len(rows)} candles" if rows else "empty"
    except client.UpstoxError as exc:
        out["intraday"] = f"FAIL {exc.message[:40]}"

    try:
        rows = await marketdata.historical(key, "1d", days=30)
        out["daily"] = f"OK {len(rows)} candles" if rows else "empty"
    except client.UpstoxError as exc:
        out["daily"] = f"FAIL {exc.message[:40]}"

    return out


async def main() -> int:
    now = datetime.now(IST)
    print(f"\n  IST: {now:%Y-%m-%d %H:%M:%S} ({now:%A})\n")
    for name, start, end in WINDOWS:
        state = "OPEN " if open_now(start, end, now) else "CLOSED"
        print(f"  {name:<10} {start}-{end}  {state}")

    if not client.access_token():
        print("\n  Upstox me login nahi hai. Dashboard se login karke dobara chalayein.\n")
        return 1
    if not instruments.loaded():
        print("\n  Instrument list nahi mili. Dashboard ka refresh icon dabayein.\n")
        return 1

    print(f"\n  {'Instrument':<22} {'ltp':<16} {'quote':<16} {'intraday':<18} daily")
    print("  " + "-" * 86)

    for label, term, segment in TARGETS:
        row = resolve(term, segment)
        if not row:
            print(f"  {label:<22} NOT FOUND in instrument master")
            continue
        res = await probe(row)
        print(f"  {label:<22} {res['ltp']:<16} {res['quote']:<16} "
              f"{res['intraday']:<18} {res['daily']}")
        print(f"  {'':<22} {row['symbol']}  [{row['instrument_key']}]")

    print("\n  Ise market band hone ke baad (jaise 16:30 aur 21:00 par) dobara")
    print("  chalayein -- tab pata chalega commodity/currency ka data raat ko bhi")
    print("  aata hai ya nahi.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
