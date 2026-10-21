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

# --------------------------------------------------------------------------
# External data sources
# --------------------------------------------------------------------------
# Free key: https://fredaccount.stlouisfed.org/apikeys
# One key covers crude, DXY, US yields, VIX and India's policy-uncertainty
# index, so it replaces half a dozen separate scrapers.
FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()


# --------------------------------------------------------------------------
# Risk limits
#
# SEBI's FY26 figures: 87.7% of individual F&O traders lost money, Rs 91,685
# crore between them. Those traders did not mostly lose on strategy. They
# lost on size, on the day they decided one more trade would fix it, and on
# the position that was never going to be cut.
#
# So the structure here matters more than the numbers. Every value below is
# a config knob that can make the system *tighter*, and every one is bounded
# by a constant further down that lives in code and cannot be set from a
# file. If the two ever conflict the app refuses to start, because a system
# that silently relaxes its own limits is worse than one that will not run.
# --------------------------------------------------------------------------

# Capital the risk engine sizes against. Zero means "not configured", and
# the engine refuses to size a trade rather than guessing.
TRADING_CAPITAL = float(os.getenv("TRADING_CAPITAL", "0"))

# ---- per position --------------------------------------------------------
MAX_RISK_PER_TRADE_PCT = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "1.0"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "10.0"))
# Every position carries a stop. Without one the engine has no risk figure
# to size against, so there is nothing to compute and the trade is refused.
REQUIRE_STOP_LOSS = os.getenv("REQUIRE_STOP_LOSS", "true").lower() in ("1", "true", "yes")

# ---- portfolio -----------------------------------------------------------
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
MAX_SECTOR_EXPOSURE_PCT = float(os.getenv("MAX_SECTOR_EXPOSURE_PCT", "30.0"))
# Three IT stocks are one bet wearing three hats.
MAX_CORRELATED_POSITIONS = int(os.getenv("MAX_CORRELATED_POSITIONS", "2"))
MAX_TOTAL_EXPOSURE_PCT = float(os.getenv("MAX_TOTAL_EXPOSURE_PCT", "80.0"))

# ---- daily ---------------------------------------------------------------
DAILY_LOSS_HALT_PCT = float(os.getenv("DAILY_LOSS_HALT_PCT", "3.0"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "10"))

# ---- account -------------------------------------------------------------
MAX_DRAWDOWN_HALT_PCT = float(os.getenv("MAX_DRAWDOWN_HALT_PCT", "10.0"))
WEEKLY_LOSS_HALF_SIZE_PCT = float(os.getenv("WEEKLY_LOSS_HALF_SIZE_PCT", "6.0"))
CONSECUTIVE_LOSSES_HALF_SIZE = int(os.getenv("CONSECUTIVE_LOSSES_HALF_SIZE", "5"))

# ---- pre-trade filters ---------------------------------------------------
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "0.5"))
MIN_DAILY_VOLUME = int(os.getenv("MIN_DAILY_VOLUME", "100000"))
# A quote older than this is not a quote. Trading on one means trading on a
# price that no longer exists.
MAX_TICK_AGE_SECONDS = float(os.getenv("MAX_TICK_AGE_SECONDS", "5"))
# Feed silence long enough to stop opening anything new. Exits stay allowed:
# being unable to leave is worse than being unable to enter.
STALE_FEED_PAUSE_SECONDS = float(os.getenv("STALE_FEED_PAUSE_SECONDS", "30"))
ORDER_REJECT_RATE_PAUSE_PCT = float(os.getenv("ORDER_REJECT_RATE_PAUSE_PCT", "20.0"))
# Fresh filing on a name means no new position in it.
EVENT_BLACKOUT_MINUTES = int(os.getenv("EVENT_BLACKOUT_MINUTES", "120"))
# The opening auction leaves wide spreads and prices that mean little.
NO_ENTRY_FIRST_MINUTES = int(os.getenv("NO_ENTRY_FIRST_MINUTES", "15"))
# Intraday positions are squared off at this time; no new entries after it.
SQUARE_OFF_HOUR = int(os.getenv("SQUARE_OFF_HOUR", "15"))
SQUARE_OFF_MINUTE = int(os.getenv("SQUARE_OFF_MINUTE", "15"))

# ---- execution -----------------------------------------------------------
# A working limit order past this is cancelled: the price it was sent for no
# longer exists. Protective stops are exempt -- a stop is meant to sit there
# all day, and cancelling it for being old removes the protection precisely
# because nothing has gone wrong yet.
STALE_ORDER_SECONDS = float(os.getenv("STALE_ORDER_SECONDS", "120"))

# ---- cost model, for the edge test --------------------------------------
# Same numbers the backtester charges, so a trade the backtest counted as
# profitable is not one live trading quietly loses on.
BROKERAGE_PER_ORDER = float(os.getenv("BROKERAGE_PER_ORDER", "20.0"))
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.03"))
TAXES_PCT = float(os.getenv("TAXES_PCT", "0.12"))


# --------------------------------------------------------------------------
# Absolute ceilings. Not configurable, by design.
#
# These exist for the case where the config is wrong -- a typo, a bad merge,
# an environment variable set for a different account. Config may set any
# limit tighter than these and never looser, and `validate()` refuses to
# start the app rather than running with the looser value.
# --------------------------------------------------------------------------
ABSOLUTE_MAX_RISK_PER_TRADE_PCT = 2.0
ABSOLUTE_MAX_POSITION_PCT = 25.0
ABSOLUTE_MAX_POSITIONS = 10
ABSOLUTE_MAX_TOTAL_EXPOSURE_PCT = 100.0
ABSOLUTE_MAX_SECTOR_EXPOSURE_PCT = 50.0
ABSOLUTE_DAILY_LOSS_HALT_PCT = 5.0
ABSOLUTE_MAX_DRAWDOWN_HALT_PCT = 20.0
ABSOLUTE_MAX_TRADES_PER_DAY = 50


def _risk_problems() -> list[str]:
    """Every way the configured limits could be looser than the hard ceilings."""
    checks = [
        ("MAX_RISK_PER_TRADE_PCT", MAX_RISK_PER_TRADE_PCT,
         ABSOLUTE_MAX_RISK_PER_TRADE_PCT),
        ("MAX_POSITION_PCT", MAX_POSITION_PCT, ABSOLUTE_MAX_POSITION_PCT),
        ("MAX_OPEN_POSITIONS", MAX_OPEN_POSITIONS, ABSOLUTE_MAX_POSITIONS),
        ("MAX_TOTAL_EXPOSURE_PCT", MAX_TOTAL_EXPOSURE_PCT,
         ABSOLUTE_MAX_TOTAL_EXPOSURE_PCT),
        ("MAX_SECTOR_EXPOSURE_PCT", MAX_SECTOR_EXPOSURE_PCT,
         ABSOLUTE_MAX_SECTOR_EXPOSURE_PCT),
        ("DAILY_LOSS_HALT_PCT", DAILY_LOSS_HALT_PCT, ABSOLUTE_DAILY_LOSS_HALT_PCT),
        ("MAX_DRAWDOWN_HALT_PCT", MAX_DRAWDOWN_HALT_PCT,
         ABSOLUTE_MAX_DRAWDOWN_HALT_PCT),
        ("MAX_TRADES_PER_DAY", MAX_TRADES_PER_DAY, ABSOLUTE_MAX_TRADES_PER_DAY),
    ]
    problems = [
        f"{name}={value} is above the hard ceiling {ceiling}"
        for name, value, ceiling in checks if value > ceiling
    ]

    # A limit set to zero or below is not a tight limit, it is a disabled
    # one, and a disabled stop-loss limit is how an account ends.
    for name, value, _ in checks:
        if value <= 0:
            problems.append(f"{name}={value} must be greater than zero")

    if MAX_POSITION_PCT > MAX_TOTAL_EXPOSURE_PCT:
        problems.append(
            f"MAX_POSITION_PCT={MAX_POSITION_PCT} exceeds "
            f"MAX_TOTAL_EXPOSURE_PCT={MAX_TOTAL_EXPOSURE_PCT} -- one position "
            f"cannot be larger than the whole book")
    if DAILY_LOSS_HALT_PCT >= MAX_DRAWDOWN_HALT_PCT:
        problems.append(
            f"DAILY_LOSS_HALT_PCT={DAILY_LOSS_HALT_PCT} is not below "
            f"MAX_DRAWDOWN_HALT_PCT={MAX_DRAWDOWN_HALT_PCT} -- the daily halt "
            f"would never fire before the account halt")
    if not PAPER_TRADING and TRADING_CAPITAL <= 0:
        problems.append(
            "TRADING_CAPITAL must be set for live trading -- the risk engine "
            "sizes every position against it")
    return problems


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
    problems.extend(_risk_problems())
    return problems

DB_PATH = DATA_DIR / "trading.db"
TOKEN_PATH = DATA_DIR / "token.json"
INSTRUMENTS_PATH = DATA_DIR / "instruments.json"

UPSTOX_BASE = "https://api.upstox.com"


def credentials_ok() -> bool:
    return bool(API_KEY and API_SECRET and not API_KEY.startswith("your_"))
