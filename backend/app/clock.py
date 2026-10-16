"""IST helpers.

Windows ships without the IANA tz database, so `ZoneInfo("Asia/Kolkata")`
blows up there. IST has no daylight saving, so a fixed +05:30 offset is exactly
right and needs no dependency.
"""
from datetime import datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def now_ist() -> datetime:
    return datetime.now(IST)


# Trading windows, IST. Equity closes first; commodities run into the night,
# which is what makes them useful for overnight (BTST) signals.
WINDOWS = {
    "equity": (time(9, 15), time(15, 30)),
    "currency": (time(9, 0), time(17, 0)),
    "commodity": (time(9, 0), time(23, 30)),
}


def is_open(segment: str = "equity", at: datetime | None = None) -> bool:
    at = at or now_ist()
    if at.weekday() >= 5:          # Sat / Sun
        return False
    start, end = WINDOWS[segment]
    return start <= at.time() <= end
