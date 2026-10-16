"""World market sessions, expressed in IST.

The reason this exists: a quote means different things depending on whether
that market is trading right now. US futures run through our entire session,
so their move is a live intraday signal. The Nikkei close at 11:30 IST is a
finished fact. Treating both as "the latest number" loses that distinction,
and it is the distinction that matters -- a live market can still react to
news, a closed one cannot.

Timezone offsets are fixed numbers rather than zoneinfo lookups, because
Windows ships without the IANA database. US and Australian DST are computed
from their actual rules; Asia has no DST.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .clock import IST, now_ist  # noqa: F401  (re-exported for routers)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """nth occurrence of a weekday in a month (weekday: Monday=0)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of a weekday in a month."""
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def us_eastern_offset(when: date) -> float:
    """-4 during EDT, -5 during EST.

    EDT runs from the second Sunday in March to the first Sunday in November.
    """
    start = _nth_weekday(when.year, 3, 6, 2)      # 2nd Sunday of March
    end = _nth_weekday(when.year, 11, 6, 1)       # 1st Sunday of November
    return -4.0 if start <= when < end else -5.0


def australia_offset(when: date) -> float:
    """+11 during AEDT, +10 during AEST (southern-hemisphere DST)."""
    start = _nth_weekday(when.year, 10, 6, 1)     # 1st Sunday of October
    end = _nth_weekday(when.year, 4, 6, 1)        # 1st Sunday of April
    return 11.0 if (when >= start or when < end) else 10.0


@dataclass(frozen=True)
class Market:
    key: str
    name: str
    symbol: str                   # Yahoo Finance ticker
    open_local: time
    close_local: time
    offset_hours: float | None = None     # None -> use `offset_fn`
    offset_fn: str | None = None          # "us" | "au"
    weekdays_only: bool = True
    note: str = ""

    def offset(self, when: date) -> float:
        if self.offset_fn == "us":
            return us_eastern_offset(when)
        if self.offset_fn == "au":
            return australia_offset(when)
        return self.offset_hours or 0.0

    def ist_window(self, when: date | None = None) -> tuple[time, time]:
        """Local open/close translated into IST."""
        when = when or now_ist().date()
        shift = 5.5 - self.offset(when)           # IST is UTC+5:30
        return (_shift_time(self.open_local, shift), _shift_time(self.close_local, shift))

    def is_open(self, at: datetime | None = None) -> bool:
        at = at or now_ist()
        if self.weekdays_only and at.weekday() >= 5:
            return False
        start, end = self.ist_window(at.date())
        now = at.time()
        # A window that wraps past midnight IST (US cash, futures) needs the
        # other comparison, or every evening would read as closed.
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end


def _shift_time(t: time, hours: float) -> time:
    total = (t.hour * 60 + t.minute + round(hours * 60)) % (24 * 60)
    return time(total // 60, total % 60)


# --------------------------------------------------------------------------
# The markets we actually watch.
#
# Asia opens before India and overlaps our session, which makes it a live
# intraday read. US cash is shut while we trade, but US *futures* are not --
# they run ~23 hours and keep reacting all through our day.
# --------------------------------------------------------------------------
MARKETS: list[Market] = [
    Market("asx", "ASX (Australia)", "^AXJO", time(10, 0), time(16, 0),
           offset_fn="au", note="India se pehle khulta hai"),
    Market("nikkei", "Nikkei (Japan)", "^N225", time(9, 0), time(15, 0),
           offset_hours=9, note="India ke open se 3.5 ghante pehle"),
    Market("kospi", "KOSPI (Korea)", "^KS11", time(9, 0), time(15, 30),
           offset_hours=9, note="Semis -> hamara IT sector ka lead"),
    Market("taiwan", "Taiwan", "^TWII", time(9, 0), time(13, 30),
           offset_hours=8, note="Semiconductors"),
    Market("hangseng", "Hang Seng (HK)", "^HSI", time(9, 30), time(16, 0),
           offset_hours=8, note="India ke saath 13:30 IST tak overlap"),
    Market("shanghai", "Shanghai", "000001.SS", time(9, 30), time(15, 0),
           offset_hours=8, note="Metals, global growth"),
    Market("india", "India (Nifty)", "^NSEI", time(9, 15), time(15, 30),
           offset_hours=5.5),
    Market("us_cash", "US cash (S&P 500)", "^GSPC", time(9, 30), time(16, 0),
           offset_fn="us", note="India band hone ke baad"),
    Market("us_futures", "S&P futures", "ES=F", time(18, 0), time(17, 0),
           offset_fn="us", note="23 ghante -- India ke poore session me LIVE"),
    Market("nasdaq_futures", "Nasdaq futures", "NQ=F", time(18, 0), time(17, 0),
           offset_fn="us", note="Tech sentiment, 23 ghante"),
]

MARKETS_BY_KEY = {m.key: m for m in MARKETS}

# Indian-listed ETFs that track foreign indices. These quote through Upstox on
# our own session, so they need no external source -- and their premium or
# discount to fair value is itself a sentiment read.
ETF_PROXIES = {
    "MASPTOP50": ("NSE_EQ|INF769K01HP3", "S&P 500 Top 50"),
    "MON100": ("NSE_EQ|INF247L01AP3", "Nasdaq 100"),
    "MAFANG": ("NSE_EQ|INF769K01HF4", "US big tech (FANG+)"),
    "HNGSNGBEES": ("NSE_EQ|INF204KB19I1", "Hang Seng"),
    "MAHKTECH": ("NSE_EQ|INF769K01HS7", "Hang Seng Tech"),
}


def open_markets(at: datetime | None = None) -> list[str]:
    return [m.key for m in MARKETS if m.is_open(at)]


def session_overview(at: datetime | None = None) -> list[dict]:
    at = at or now_ist()
    out = []
    for m in MARKETS:
        start, end = m.ist_window(at.date())
        out.append({
            "key": m.key,
            "name": m.name,
            "symbol": m.symbol,
            "ist_open": start.strftime("%H:%M"),
            "ist_close": end.strftime("%H:%M"),
            "is_open": m.is_open(at),
            "note": m.note,
        })
    return out


def overlaps_india(market: Market, when: date | None = None) -> bool:
    """Does this market trade while Indian equities are open?

    An overlapping market is a live intraday signal. A non-overlapping one can
    only tell us about the gap.
    """
    when = when or now_ist().date()
    india = MARKETS_BY_KEY["india"]
    i_start, i_end = india.ist_window(when)
    m_start, m_end = market.ist_window(when)

    def covers(a: time, b: time) -> bool:
        return a <= i_end and b >= i_start

    if m_start > m_end:
        # Wraps midnight, so it is really two windows. US futures cover our
        # whole session this way; US cash (19:00-01:30 IST) covers none of it.
        return covers(m_start, time(23, 59)) or covers(time(0, 0), m_end)
    return covers(m_start, m_end)
