"""Market sessions, DST, and the Yahoo chart parser.

The parser tests exist because of a real bug: Yahoo's `chartPreviousClose` is
the close *before the requested range*, so with range=5d it produced a six-day
move that we were labelling as the daily change. Live, that turned KOSPI's
+2.05% session into -14.46% and flipped the Asia read from broadly positive to
risk-off. These lock the correct behaviour in.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import markets  # noqa: E402
from app.clock import IST  # noqa: E402
from app.collectors import global_markets as gm  # noqa: E402


def ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


# ===================================================== DST calculations
def test_us_eastern_dst_boundaries():
    assert markets.us_eastern_offset(date(2026, 1, 15)) == -5.0     # EST
    assert markets.us_eastern_offset(date(2026, 7, 15)) == -4.0     # EDT
    # EDT starts 2nd Sunday of March, ends 1st Sunday of November.
    assert markets.us_eastern_offset(date(2026, 3, 7)) == -5.0
    assert markets.us_eastern_offset(date(2026, 3, 9)) == -4.0
    assert markets.us_eastern_offset(date(2026, 11, 2)) == -5.0


def test_australia_dst_is_southern_hemisphere():
    """AEDT runs over the southern summer, so it wraps the year end."""
    assert markets.australia_offset(date(2026, 1, 15)) == 11.0
    assert markets.australia_offset(date(2026, 7, 15)) == 10.0
    assert markets.australia_offset(date(2026, 12, 15)) == 11.0


# ====================================================== session windows
def test_asian_markets_open_before_india():
    """Nikkei and KOSPI open ~3.5h before us -- that is the pre-open read."""
    when = date(2026, 10, 16)
    nikkei_open, _ = markets.MARKETS_BY_KEY["nikkei"].ist_window(when)
    india_open, _ = markets.MARKETS_BY_KEY["india"].ist_window(when)
    assert nikkei_open < india_open
    assert nikkei_open == time(5, 30)


def test_hangseng_overlaps_most_of_our_session():
    _, close = markets.MARKETS_BY_KEY["hangseng"].ist_window(date(2026, 10, 16))
    assert close == time(13, 30)


def test_us_futures_cover_the_whole_indian_session():
    """This is the point of tracking futures at all."""
    fut = markets.MARKETS_BY_KEY["us_futures"]
    for hh, mm in [(9, 15), (12, 0), (15, 30)]:
        assert fut.is_open(ist(2026, 10, 16, hh, mm)), f"futures shut at {hh}:{mm}"


def test_us_cash_does_not_overlap_india():
    """19:00-01:30 IST against 09:15-15:30 -- no overlap, despite wrapping."""
    assert markets.overlaps_india(markets.MARKETS_BY_KEY["us_cash"]) is False
    assert markets.overlaps_india(markets.MARKETS_BY_KEY["us_futures"]) is True
    assert markets.overlaps_india(markets.MARKETS_BY_KEY["nikkei"]) is True


def test_markets_are_shut_at_the_weekend():
    saturday = ist(2026, 10, 17, 12, 0)
    assert saturday.weekday() == 5
    assert markets.open_markets(saturday) == []


def test_india_session_matches_exchange_hours():
    india = markets.MARKETS_BY_KEY["india"]
    assert india.is_open(ist(2026, 10, 16, 10, 0))
    assert not india.is_open(ist(2026, 10, 16, 9, 0))
    assert not india.is_open(ist(2026, 10, 16, 16, 0))


def test_session_overview_covers_every_market():
    rows = markets.session_overview()
    assert len(rows) == len(markets.MARKETS)
    assert {r["key"] for r in rows} == set(markets.MARKETS_BY_KEY)


# ======================================================== chart parsing
def chart(closes: list, price: float, prev_close_meta: float | None = None) -> dict:
    return {
        "chart": {"result": [{
            "meta": {
                "symbol": "^KS11", "regularMarketPrice": price,
                "chartPreviousClose": prev_close_meta, "currency": "KRW",
            },
            "indicators": {"quote": [{"close": closes}]},
        }]}
    }


def test_daily_change_uses_previous_close_not_range_start():
    """The bug that shipped: meta's previous close spans the whole range."""
    closes = [6384.24, 6035.35, 6008.81, 5554.22, 5668.05]
    payload = chart(closes, price=5668.05, prev_close_meta=6625.93)

    q = gm.parse_chart("^KS11", payload)
    # Correct: last vs the close before it.
    assert q["change_pct"] == pytest.approx(2.05, abs=0.01)
    # What the meta field would have given -- nearly 7x wrong, and the wrong sign.
    assert q["change_pct"] != pytest.approx(-14.46, abs=0.1)


def test_week_change_is_reported_separately():
    """A multi-day move is useful, but it must never pose as the daily one."""
    closes = [100.0, 101.0, 102.0, 103.0, 110.0]
    q = gm.parse_chart("X", chart(closes, price=110.0))
    assert q["change_pct"] == pytest.approx(6.796, abs=0.01)    # vs 103
    assert q["week_change_pct"] == pytest.approx(10.0, abs=0.01)  # vs 100


def test_parse_handles_gaps_in_the_close_series():
    """Yahoo writes null for holidays; those must not shift the comparison."""
    q = gm.parse_chart("X", chart([100.0, None, 102.0, None, 105.0], price=105.0))
    assert q["change_pct"] == pytest.approx((105 - 102) / 102 * 100, abs=0.01)


def test_parse_survives_a_single_close():
    q = gm.parse_chart("X", chart([50.0], price=50.0))
    assert q["price"] == 50.0
    assert q["change_pct"] is None          # nothing to compare against


def test_parse_rejects_unusable_payloads():
    assert gm.parse_chart("X", {}) is None
    assert gm.parse_chart("X", {"chart": {"result": []}}) is None
    assert gm.parse_chart("X", {"chart": {"result": [{"meta": {}}]}}) is None


def test_parse_handles_missing_indicators():
    payload = {"chart": {"result": [{"meta": {"regularMarketPrice": 10.0}}]}}
    q = gm.parse_chart("X", payload)
    assert q["price"] == 10.0
    assert q["change_pct"] is None


# ====================================================== collector output
def test_quotes_are_tagged_live_or_last_close():
    """A live futures tick and a finished Nikkei close are different things."""
    raw = {
        "us_futures": chart([7700.0, 7797.0], price=7797.0),
        "nikkei": chart([64000.0, 65324.0], price=65324.0),
    }
    points = gm.GlobalMarketsCollector().parse(raw)
    by_id = {p.series_id: p for p in points if p.meta}
    for pt in by_id.values():
        assert pt.meta["quality"] in ("live", "last_close")
        assert isinstance(pt.meta["was_live"], bool)


def test_parse_emits_price_and_change_series():
    raw = {"nikkei": chart([64000.0, 65324.0], price=65324.0)}
    ids = {p.series_id for p in gm.GlobalMarketsCollector().parse(raw)}
    assert ids == {"MKT_NIKKEI", "MKT_NIKKEI_CHG"}


def test_unknown_market_key_is_ignored():
    raw = {"atlantis": chart([1.0, 2.0], price=2.0)}
    assert gm.GlobalMarketsCollector().parse(raw) == []


# ============================================================ asia read
def test_asia_read_needs_breadth_for_risk_off(monkeypatch):
    """One market falling is a local story; five falling is a signal."""
    def fake(key):
        return {"nikkei": (0, -3.0), "kospi": (0, 0.5), "taiwan": (0, 0.2),
                "hangseng": (0, 0.1), "shanghai": (0, 0.3)}.get(key, (None, None))

    monkeypatch.setattr(gm, "_latest", fake)
    read = gm.asia_read()
    assert read["bias"] == "mixed"
    assert read["risk_off"] is False        # one weak market is not risk-off


def test_asia_read_flags_broad_decline(monkeypatch):
    monkeypatch.setattr(gm, "_latest", lambda k: (0, -2.0))
    read = gm.asia_read()
    assert read["bias"] == "broadly_negative"
    assert read["up_count"] == 0
    assert read["risk_off"] is True


def test_asia_read_ignores_shallow_broad_decline(monkeypatch):
    """Everything down a touch is noise, not a regional risk event."""
    monkeypatch.setattr(gm, "_latest", lambda k: (0, -0.2))
    read = gm.asia_read()
    assert read["bias"] == "broadly_negative"
    assert read["risk_off"] is False        # average above the -1% bar


def test_asia_read_without_data(monkeypatch):
    monkeypatch.setattr(gm, "_latest", lambda k: (None, None))
    assert gm.asia_read()["available"] is False


# ============================================================ etf proxies
def test_etf_proxies_cover_us_and_hong_kong():
    assert "MASPTOP50" in markets.ETF_PROXIES      # S&P 500
    assert "MON100" in markets.ETF_PROXIES         # Nasdaq 100
    assert "HNGSNGBEES" in markets.ETF_PROXIES     # Hang Seng
    for key, _ in markets.ETF_PROXIES.values():
        assert key.startswith("NSE_EQ|")


def test_etf_read_marks_unsubscribed_instruments():
    read = gm.etf_proxy_read(lambda key: 0.0)
    assert all(v["subscribed"] is False for v in read.values())
    read = gm.etf_proxy_read(lambda key: 123.0)
    assert all(v["ltp"] == 123.0 for v in read.values())
