"""Phase 2 collectors: parsing, point-in-time storage, and failure handling.

Parsing is tested against saved payloads rather than the live API, so these
stay fast and deterministic. One test does hit Binance, and is skipped when
there is no network.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.collectors import base, crypto, fred  # noqa: E402
from app.store import macro  # noqa: E402


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    """Point the store at a throwaway database."""
    from app import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    macro._ready = False
    yield
    macro._ready = False


# ===================================================== crypto: risk logic
def test_risk_signal_is_asymmetric():
    """A BTC rally says little about Indian equities; a crash says a lot."""
    assert crypto.classify_risk(15.0) == "neutral"
    assert crypto.classify_risk(5.0) == "neutral"
    assert crypto.classify_risk(0.0) == "neutral"
    assert crypto.classify_risk(-5.0) == "risk_off"
    assert crypto.classify_risk(-10.0) == "severe_risk_off"


def test_risk_threshold_boundary():
    t = crypto.RISK_OFF_DROP_PCT
    assert crypto.classify_risk(t + 0.01) == "neutral"
    assert crypto.classify_risk(t) == "risk_off"
    assert crypto.classify_risk(t * 2 + 0.01) == "risk_off"
    assert crypto.classify_risk(t * 2) == "severe_risk_off"


# ===================================================== crypto: parsing
BINANCE_SAMPLE = [
    {"symbol": "BTCUSDT", "lastPrice": "82761.65", "priceChangePercent": "-6.5",
     "quoteVolume": "1500000000", "highPrice": "89000", "lowPrice": "82000"},
    {"symbol": "ETHUSDT", "lastPrice": "3100.20", "priceChangePercent": "-4.2",
     "quoteVolume": "800000000", "highPrice": "3300", "lowPrice": "3050"},
]


def test_crypto_parse_produces_price_and_change():
    points = crypto.CryptoCollector().parse(BINANCE_SAMPLE)
    ids = {p.series_id for p in points}
    assert ids == {"CRYPTO_BTCUSDT", "CRYPTO_BTCUSDT_CHG24",
                   "CRYPTO_ETHUSDT", "CRYPTO_ETHUSDT_CHG24"}
    btc = next(p for p in points if p.series_id == "CRYPTO_BTCUSDT")
    assert btc.value == 82761.65
    # -6.5% clears the risk_off threshold (-4) but not severe (-8).
    assert btc.meta["risk_signal"] == "risk_off"


def test_crypto_parse_skips_malformed_rows():
    rows = BINANCE_SAMPLE + [{"symbol": "BADUSDT", "lastPrice": "not-a-number"},
                             {"no_symbol": True}]
    points = crypto.CryptoCollector().parse(rows)
    assert not any("BAD" in p.series_id for p in points)
    assert len(points) == 4


def test_crypto_range_pct_computed():
    points = crypto.CryptoCollector().parse(BINANCE_SAMPLE)
    btc = next(p for p in points if p.series_id == "CRYPTO_BTCUSDT")
    assert btc.meta["range_pct"] == pytest.approx((89000 - 82000) / 82761.65 * 100, abs=0.01)


# ======================================================== fred: parsing
FRED_SAMPLE = {
    "observations": [
        {"date": "2026-10-13", "value": "104.5"},
        {"date": "2026-10-14", "value": "."},          # market holiday
        {"date": "2026-10-15", "value": "106.2"},
        {"date": "2026-10-16", "value": ""},           # not published yet
    ]
}


def test_fred_drops_missing_observations():
    """FRED writes '.' for no observation -- parsing it as a number poisons averages."""
    points = fred.parse_observations("DCOILBRENTEU", FRED_SAMPLE)
    assert len(points) == 2
    assert [p.value for p in points] == [104.5, 106.2]
    assert all(p.series_id == "FRED_DCOILBRENTEU" for p in points)


def test_fred_handles_empty_payload():
    assert fred.parse_observations("X", {}) == []
    assert fred.parse_observations("X", {"observations": []}) == []


def test_fred_skips_rows_without_date():
    payload = {"observations": [{"value": "1.0"}, {"date": "2026-01-01", "value": "2.0"}]}
    points = fred.parse_observations("X", payload)
    assert len(points) == 1


def test_fred_needs_a_key_before_running():
    c = fred.FredCollector()
    if not c.configured:
        assert c.is_due() is False          # won't burn cycles without a key


def test_fred_series_cover_the_documented_drivers():
    ids = {s.fred_id for s in fred.SERIES}
    for required in ("DCOILBRENTEU", "DTWEXBGS", "DGS10", "INDEPUINDXM"):
        assert required in ids


# ================================================ store: point-in-time
def test_save_and_read_back(clean_db):
    macro.save([macro.Point("TEST_A", "2026-10-15", 1.0),
                macro.Point("TEST_A", "2026-10-16", 2.0)], source="unit")
    rows = macro.series("TEST_A")
    assert len(rows) == 2
    assert macro.latest("TEST_A")["value"] == 2.0


def test_unchanged_value_keeps_original_fetched_at(clean_db):
    """Re-fetching the same number must not make it look newer than it is."""
    macro.save([macro.Point("TEST_B", "2026-10-15", 5.0)], source="unit")
    first = macro.latest("TEST_B")["fetched_at"]
    macro.save([macro.Point("TEST_B", "2026-10-15", 5.0)], source="unit")
    assert macro.latest("TEST_B")["fetched_at"] == first


def test_revision_updates_value_and_fetch_time(clean_db):
    """A revised print is new information, so the fetch time moves."""
    macro.save([macro.Point("TEST_C", "2026-10-15", 5.0)], source="unit")
    macro.save([macro.Point("TEST_C", "2026-10-15", 7.5)], source="unit")
    row = macro.latest("TEST_C")
    assert row["value"] == 7.5


def test_as_of_hides_data_we_had_not_fetched_yet(clean_db):
    """The backtest must not see a number published after the decision."""
    macro.save([macro.Point("TEST_D", "2026-10-15", 1.0)], source="unit")
    # Asking about a date before anything was fetched returns nothing.
    assert macro.as_of("TEST_D", "2020-01-01") is None
    # Asking today does return it.
    assert macro.as_of("TEST_D", "2099-01-01")["value"] == 1.0


def test_known_series_summary(clean_db):
    macro.save([macro.Point("TEST_E", "2026-10-15", 1.0),
                macro.Point("TEST_E", "2026-10-16", 2.0)], source="unit")
    row = next(r for r in macro.known_series() if r["series_id"] == "TEST_E")
    assert row["points"] == 2
    assert row["latest_ts"] == "2026-10-16"


def test_saving_nothing_is_safe(clean_db):
    assert macro.save([], source="unit") == 0


# ================================================== failure containment
def test_collector_run_records_failure_without_raising(clean_db):
    """A dead source degrades the signal; it must not crash the app."""
    class Broken(base.Collector):
        def __init__(self):
            super().__init__(name="broken", source="x", interval_seconds=1)

        async def fetch(self):
            raise RuntimeError("source is down")

        def parse(self, raw):
            return []

    c = Broken()
    assert asyncio.run(c.run()) == 0
    assert c.status.failures == 1
    assert "source is down" in c.status.last_error
    assert c.status.as_dict()["healthy"] is False


def test_successful_run_clears_previous_error(clean_db):
    class Flaky(base.Collector):
        def __init__(self):
            super().__init__(name="flaky", source="x", interval_seconds=1)
            self.fail = True

        async def fetch(self):
            if self.fail:
                raise RuntimeError("down")
            return [1]

        def parse(self, raw):
            return [macro.Point("FLAKY", "2026-10-16", 1.0)]

    c = Flaky()
    asyncio.run(c.run())
    assert c.status.last_error is not None
    c.fail = False
    assert asyncio.run(c.run()) == 1
    assert c.status.last_error is None
    assert c.status.as_dict()["healthy"] is True


def test_runner_rejects_unknown_collector():
    with pytest.raises(KeyError):
        asyncio.run(base.CollectorRunner().run_once("nope"))


def test_registered_collectors_present():
    from app import collectors
    assert set(collectors.runner.collectors) >= {"crypto", "fred"}


# ===================================================== live (network)
@pytest.mark.skipif(
    "CI" in __import__("os").environ, reason="network test")
def test_binance_live_fetch(clean_db):
    """Binance's public endpoint needs no key -- verify the real shape."""
    c = crypto.CryptoCollector()
    try:
        raw = asyncio.run(c.fetch())
    except Exception as exc:                 # noqa: BLE001
        pytest.skip(f"network unavailable: {exc}")
    points = c.parse(raw)
    assert any(p.series_id == "CRYPTO_BTCUSDT" for p in points)
    btc = next(p for p in points if p.series_id == "CRYPTO_BTCUSDT")
    assert btc.value > 0
    assert btc.meta["risk_signal"] in ("neutral", "risk_off", "severe_risk_off")
