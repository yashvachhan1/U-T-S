"""Configuration loaded from .env (project root)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]   # .../trading-app
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = ROOT / "frontend"

API_KEY = os.getenv("UPSTOX_API_KEY", "").strip()
API_SECRET = os.getenv("UPSTOX_API_SECRET", "").strip()
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/callback").strip()

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() in ("1", "true", "yes")

# --------------------------------------------------------------------------
# Order safety. These are the outer walls -- a config file or a strategy can
# make them tighter, never looser.
# --------------------------------------------------------------------------

# Upstox stopped accepting MARKET orders over the API on 1 April 2026, so a
# "market" order becomes a LIMIT priced this far through the touch. Wide enough
# to fill like a market order, tight enough to cap the damage on a bad tick.
MARKET_ORDER_BAND_PCT = float(os.getenv("MARKET_ORDER_BAND_PCT", "0.30"))

# SEBI/NSE treat <10 orders/sec as low-frequency (no exchange registration).
# We sit well under it: the limit is a cliff, not a target.
MAX_ORDERS_PER_SECOND = float(os.getenv("MAX_ORDERS_PER_SECOND", "5"))
# Burst allowance. Worst case in any one-second window is burst + rate, so this
# has to stay small enough that the sum never reaches the exchange's 10/sec.
MAX_ORDER_BURST = float(os.getenv("MAX_ORDER_BURST", "3"))
MAX_ORDERS_PER_DAY = int(os.getenv("MAX_ORDERS_PER_DAY", "200"))

# Fat-finger guard: no single order may be worth more than this.
MAX_ORDER_VALUE = float(os.getenv("MAX_ORDER_VALUE", "100000"))

# Reject any limit price this far from the last trade -- catches stale quotes
# and bad inputs before they reach the exchange.
MAX_PRICE_DEVIATION_PCT = float(os.getenv("MAX_PRICE_DEVIATION_PCT", "10.0"))

# Exchange-mandated algo identifier, sent with every order.
ALGO_NAME = os.getenv("ALGO_NAME", "trading-desk")[:20]


# Absolute ceilings. Config above cannot exceed these -- if it does, the app
# refuses to start rather than quietly trading with the looser value.
ABSOLUTE_MAX_ORDERS_PER_SECOND = 9.0
ABSOLUTE_MAX_ORDER_VALUE = 1_000_000.0
ABSOLUTE_MAX_ORDERS_PER_DAY = 1000


def validate() -> list[str]:
    """Config sanity. Returns a list of problems -- empty means safe to run."""
    problems = []
    if MAX_ORDERS_PER_SECOND > ABSOLUTE_MAX_ORDERS_PER_SECOND:
        problems.append(
            f"MAX_ORDERS_PER_SECOND={MAX_ORDERS_PER_SECOND} is above the hard "
            f"ceiling {ABSOLUTE_MAX_ORDERS_PER_SECOND} (SEBI low-frequency limit is 10/sec)"
        )
    # A token bucket can emit its full burst and then keep refilling, so the
    # real worst case inside one second is burst + rate.
    peak = MAX_ORDER_BURST + MAX_ORDERS_PER_SECOND
    if peak > ABSOLUTE_MAX_ORDERS_PER_SECOND:
        problems.append(
            f"burst({MAX_ORDER_BURST}) + rate({MAX_ORDERS_PER_SECOND}) = {peak} "
            f"orders in one second, above the hard ceiling "
            f"{ABSOLUTE_MAX_ORDERS_PER_SECOND}. Lower MAX_ORDER_BURST."
        )
    if MAX_ORDER_VALUE > ABSOLUTE_MAX_ORDER_VALUE:
        problems.append(
            f"MAX_ORDER_VALUE={MAX_ORDER_VALUE} is above the hard ceiling "
            f"{ABSOLUTE_MAX_ORDER_VALUE}"
        )
    if MAX_ORDERS_PER_DAY > ABSOLUTE_MAX_ORDERS_PER_DAY:
        problems.append(
            f"MAX_ORDERS_PER_DAY={MAX_ORDERS_PER_DAY} is above the hard ceiling "
            f"{ABSOLUTE_MAX_ORDERS_PER_DAY}"
        )
    if not 0 < MARKET_ORDER_BAND_PCT <= 5:
        problems.append(
            f"MARKET_ORDER_BAND_PCT={MARKET_ORDER_BAND_PCT} must be between 0 and 5"
        )
    return problems

DB_PATH = DATA_DIR / "trading.db"
TOKEN_PATH = DATA_DIR / "token.json"
INSTRUMENTS_PATH = DATA_DIR / "instruments.json"

UPSTOX_BASE = "https://api.upstox.com"


def credentials_ok() -> bool:
    return bool(API_KEY and API_SECRET and not API_KEY.startswith("your_"))
