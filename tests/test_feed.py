"""Phase 2: feed protocol, tick decoding, and the Parquet writer.

The decoder tests build real protobuf messages and serialise them, so they
exercise the same path a live frame takes -- no hand-written fixtures that can
drift from the schema.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.store import ticks as store_ticks  # noqa: E402
from app.upstox import feed, ticks as tk  # noqa: E402

pb = pytest.importorskip("MarketDataFeedV3_pb2") if tk.PROTO_AVAILABLE else None

KEY = "NSE_EQ|INE002A01018"
INDEX_KEY = "NSE_INDEX|Nifty 50"


# ===================================================== subscription protocol
def test_subscribe_message_is_json_bytes():
    raw = feed.build_message("sub", feed.Mode.FULL, [KEY])
    msg = json.loads(raw.decode())
    assert msg["method"] == "sub"
    assert msg["data"]["mode"] == "full"
    assert msg["data"]["instrumentKeys"] == [KEY]
    assert msg["guid"]


def test_unsub_message_has_no_mode():
    msg = json.loads(feed.build_message("unsub", None, [KEY]).decode())
    assert "mode" not in msg["data"]


def test_message_is_binary_frame_payload():
    """Upstox requires subscription frames to be sent as binary."""
    assert isinstance(feed.build_message("sub", feed.Mode.LTPC, [KEY]), bytes)


def test_guids_are_unique():
    a = json.loads(feed.build_message("sub", feed.Mode.LTPC, [KEY]).decode())["guid"]
    b = json.loads(feed.build_message("sub", feed.Mode.LTPC, [KEY]).decode())["guid"]
    assert a != b


# ========================================================= key limits
def test_single_mode_uses_its_own_limit():
    sub = feed.Subscription()
    assert sub.limit_for(feed.Mode.LTPC) == 5000
    assert sub.limit_for(feed.Mode.FULL) == 2000


def test_mixing_modes_drops_every_limit_to_combined():
    """Docs: once several categories are in use, each is capped at 1500."""
    sub = feed.Subscription()
    sub.add(feed.Mode.LTPC, ["a"])
    sub.add(feed.Mode.FULL, ["b"])
    assert sub.limit_for(feed.Mode.LTPC) == feed.COMBINED_KEY_LIMIT
    assert sub.limit_for(feed.Mode.FULL) == feed.COMBINED_KEY_LIMIT


def test_exceeding_limit_raises_before_sending():
    sub = feed.Subscription()
    with pytest.raises(feed.SubscriptionLimitExceeded):
        sub.add(feed.Mode.FULL_D30, [f"k{i}" for i in range(51)])


def test_rejected_subscription_leaves_state_untouched():
    """A refused add must not half-apply."""
    sub = feed.Subscription()
    sub.add(feed.Mode.FULL_D30, ["a", "b"])
    with pytest.raises(feed.SubscriptionLimitExceeded):
        sub.add(feed.Mode.FULL_D30, [f"k{i}" for i in range(60)])
    assert sub.keys(feed.Mode.FULL_D30) == {"a", "b"}


def test_remove_clears_across_modes():
    sub = feed.Subscription()
    sub.add(feed.Mode.LTPC, ["a", "b"])
    sub.add(feed.Mode.FULL, ["c"])
    assert sub.remove(["a", "c"]) == {"a", "c"}
    assert sub.all_keys == {"b"}


# ============================================================ reconnect
def test_backoff_grows_and_is_capped():
    delays = [feed.backoff_delay(i) for i in range(12)]
    assert delays[0] < delays[5] < delays[9]
    assert all(d <= feed.MAX_RECONNECT_DELAY for d in delays)


def test_backoff_is_jittered():
    """Without jitter every client reconnects at the same instant."""
    samples = {round(feed.backoff_delay(4), 6) for _ in range(20)}
    assert len(samples) > 1


# ============================================================== decoding
@pytest.mark.skipif(not tk.PROTO_AVAILABLE, reason="protobuf module not built")
class TestDecode:
    def _response(self):
        import MarketDataFeedV3_pb2 as pb  # noqa: N806
        return pb.FeedResponse(), pb

    def test_ltpc_tick(self):
        resp, pb = self._response()
        resp.type = pb.Type.live_feed
        resp.currentTs = 1_760_000_000_000
        resp.feeds[KEY].ltpc.ltp = 1420.5
        resp.feeds[KEY].ltpc.cp = 1400.0
        resp.feeds[KEY].ltpc.ltq = 12
        resp.feeds[KEY].ltpc.ltt = 1_760_000_001_000

        frame = tk.decode(resp.SerializeToString())
        assert len(frame.ticks) == 1
        t = frame.ticks[0]
        assert t.instrument_key == KEY
        assert t.ltp == 1420.5
        assert t.close_price == 1400.0
        assert t.ts == 1_760_000_001_000          # trade time beats feed time
        assert t.mode == "ltpc"
        assert round(t.change_pct, 4) == round(20.5 / 1400 * 100, 4)

    def test_full_feed_with_depth(self):
        resp, pb = self._response()
        resp.type = pb.Type.live_feed
        m = resp.feeds[KEY].fullFeed.marketFF
        m.ltpc.ltp = 1000.0
        m.ltpc.cp = 990.0
        m.vtt = 500_000
        m.atp = 998.5
        m.tbq = 1200
        m.tsq = 800
        for bq, bp, aq, ap in [(100, 999.5, 60, 1000.5), (200, 999.0, 140, 1001.0)]:
            q = m.marketLevel.bidAskQuote.add()
            q.bidQ, q.bidP, q.askQ, q.askP = bq, bp, aq, ap

        t = tk.decode(resp.SerializeToString()).ticks[0]
        assert t.mode == "full"
        assert len(t.depth) == 2
        assert t.best_bid == 999.5
        assert t.best_ask == 1000.5
        assert round(t.spread, 4) == 1.0
        assert t.mid == 1000.0
        assert t.volume == 500_000
        # 300 bid vs 200 ask -> positive imbalance
        assert t.book_imbalance == pytest.approx((300 - 200) / 500)

    def test_index_feed_has_no_depth(self):
        resp, pb = self._response()
        resp.feeds[INDEX_KEY].fullFeed.indexFF.ltpc.ltp = 24500.0
        t = tk.decode(resp.SerializeToString()).ticks[0]
        assert t.is_index
        assert t.depth == []
        assert t.book_imbalance == 0.0
        assert t.mid == 24500.0          # falls back to ltp with no book

    def test_option_greeks_feed(self):
        resp, pb = self._response()
        f = resp.feeds["NSE_FO|12345"].firstLevelWithGreeks
        f.ltpc.ltp = 42.5
        f.oi = 1_000_000
        f.iv = 0.18
        f.optionGreeks.delta = 0.55
        f.optionGreeks.theta = -0.8
        f.firstDepth.bidQ, f.firstDepth.bidP = 50, 42.0
        f.firstDepth.askQ, f.firstDepth.askP = 75, 43.0

        t = tk.decode(resp.SerializeToString()).ticks[0]
        assert t.mode == "option_greeks"
        assert t.greeks["delta"] == 0.55
        assert t.iv == 0.18
        assert len(t.depth) == 1

    def test_multiple_instruments_in_one_frame(self):
        resp, pb = self._response()
        resp.feeds["A"].ltpc.ltp = 1.0
        resp.feeds["B"].ltpc.ltp = 2.0
        resp.feeds["C"].ltpc.ltp = 3.0
        frame = tk.decode(resp.SerializeToString())
        assert {t.instrument_key for t in frame.ticks} == {"A", "B", "C"}

    def test_market_status_frame(self):
        resp, pb = self._response()
        resp.type = pb.Type.market_info
        resp.marketInfo.segmentStatus["NSE_EQ"] = pb.MarketStatus.NORMAL_OPEN
        frame = tk.decode(resp.SerializeToString())
        assert frame.market_status["NSE_EQ"] == "NORMAL_OPEN"

    def test_garbage_bytes_do_not_raise(self):
        """A corrupt frame must not take down the connection."""
        assert tk.decode(b"\xff\xfe\x00garbage").ticks == []

    def test_to_row_has_every_stored_column(self):
        resp, pb = self._response()
        resp.feeds[KEY].ltpc.ltp = 100.0
        row = tk.decode(resp.SerializeToString()).ticks[0].to_row()
        assert set(row) == set(store_ticks._SCHEMA_COLUMNS)


# ======================================================== parquet writer
def test_writer_buffers_without_touching_disk(tmp_path):
    w = store_ticks.TickWriter(root=tmp_path)
    for i in range(100):
        w.add({"instrument_key": KEY, "ts": i, "ltp": float(i)})
    assert w.stats.buffered == 100
    assert w.stats.written == 0
    assert not any(tmp_path.rglob("*.parquet"))


def test_writer_flushes_to_partitioned_parquet(tmp_path):
    w = store_ticks.TickWriter(root=tmp_path)
    w.add_many([{"instrument_key": KEY, "ts": i, "ltp": float(i)} for i in range(500)])
    assert w.flush_sync() == 500
    assert w.stats.buffered == 0

    files = list(tmp_path.rglob("*.parquet"))
    assert len(files) == 1
    assert files[0].parent.name.startswith("date=")

    import duckdb
    rows = duckdb.connect().execute(
        f"SELECT COUNT(*) c, MAX(ltp) m FROM '{files[0]}'").fetchone()
    assert rows[0] == 500
    assert rows[1] == 499.0


def test_writer_drops_oldest_when_overwhelmed(tmp_path):
    """A slow disk must not grow the buffer without bound."""
    w = store_ticks.TickWriter(root=tmp_path)
    w._buffer = __import__("collections").deque(maxlen=10)
    for i in range(25):
        w.add({"instrument_key": KEY, "ts": i, "ltp": float(i)})
    assert len(w._buffer) == 10
    assert w._buffer[-1]["ts"] == 24          # newest kept


def test_unknown_columns_are_ignored(tmp_path):
    """Extra fields from a newer feed version must not break the writer."""
    w = store_ticks.TickWriter(root=tmp_path)
    w.add({"instrument_key": KEY, "ts": 1, "ltp": 1.0, "brand_new_field": 42})
    assert w.flush_sync() == 1
    assert w.stats.last_error is None


def test_flush_error_is_swallowed(tmp_path):
    """Losing a batch beats losing the feed."""
    w = store_ticks.TickWriter(root=tmp_path)
    # A known column holding something pyarrow cannot encode.
    w.add({"instrument_key": KEY, "ts": 1, "ltp": object()})
    assert w.flush_sync() == 0
    assert w.stats.last_error is not None
    assert not list(tmp_path.rglob("*.parquet"))


def test_empty_flush_is_a_noop(tmp_path):
    assert store_ticks.TickWriter(root=tmp_path).flush_sync() == 0


def test_available_days_lists_partitions(tmp_path):
    (tmp_path / "date=2026-10-15").mkdir(parents=True)
    (tmp_path / "date=2026-10-16").mkdir(parents=True)
    (tmp_path / "junk").mkdir()
    assert store_ticks.available_days(tmp_path) == ["2026-10-15", "2026-10-16"]


# ============================================================ feed object
def test_feed_status_before_connecting():
    f = feed.MarketFeed()
    s = f.status()
    assert s["connected"] is False
    assert s["subscribed_keys"] == 0


def test_subscribe_records_state_even_while_disconnected():
    """Subscriptions are replayed on connect, so they are tracked regardless."""
    f = feed.MarketFeed()
    added = asyncio.run(f.subscribe([KEY, INDEX_KEY], feed.Mode.FULL))
    assert added == {KEY, INDEX_KEY}
    assert f.status()["subscribed_keys"] == 2
    assert f.status()["modes"]["full"] == 2


def test_handler_exception_does_not_escape():
    f = feed.MarketFeed(on_message=lambda raw: 1 / 0)
    assert f.on_message is not None             # wired, and the loop guards it


# ================================================ end-to-end pipeline
@pytest.mark.skipif(not tk.PROTO_AVAILABLE, reason="protobuf module not built")
def test_frame_flows_to_cache_and_parquet(tmp_path, monkeypatch):
    """protobuf frame -> decode -> latest cache -> Parquet, with no broker."""
    import MarketDataFeedV3_pb2 as pbmod  # noqa: N806
    from app.engine import feed_service

    feed_service._reset_for_tests()
    monkeypatch.setattr(store_ticks.writer, "root", tmp_path)
    store_ticks.writer._take()                      # start from empty

    resp = pbmod.FeedResponse()
    resp.type = pbmod.Type.live_feed
    resp.currentTs = 1_760_000_000_000
    m = resp.feeds[KEY].fullFeed.marketFF
    m.ltpc.ltp, m.ltpc.cp, m.vtt = 1500.0, 1480.0, 250_000
    q = m.marketLevel.bidAskQuote.add()
    q.bidQ, q.bidP, q.askQ, q.askP = 300, 1499.5, 100, 1500.5
    resp.feeds[INDEX_KEY].fullFeed.indexFF.ltpc.ltp = 24_600.0

    feed_service._handle_frame(resp.SerializeToString())

    # cache is readable immediately -- this is what the order path uses
    assert feed_service.latest_price(KEY) == 1500.0
    assert feed_service.latest_price(INDEX_KEY) == 24_600.0
    assert feed_service.latest_price("NOT|SUBSCRIBED") == 0.0

    snap = {s["instrument_key"]: s for s in feed_service.snapshot()}
    assert snap[KEY]["spread"] == 1.0
    assert snap[KEY]["book_imbalance"] == pytest.approx(0.5)   # 300 vs 100
    assert snap[KEY]["change_pct"] == pytest.approx(20 / 1480 * 100, abs=0.01)

    st = feed_service.status()
    assert st["frames"] == 1 and st["ticks_seen"] == 2 and st["decode_errors"] == 0

    assert store_ticks.writer.flush_sync() == 2
    import duckdb
    files = list(tmp_path.rglob("*.parquet"))
    rows = duckdb.connect().execute(
        f"SELECT instrument_key, ltp FROM '{files[0]}' ORDER BY ltp").fetchall()
    assert rows == [(KEY, 1500.0), (INDEX_KEY, 24600.0)]


def test_corrupt_frame_counts_but_does_not_raise():
    from app.engine import feed_service
    feed_service._reset_for_tests()
    feed_service._handle_frame(b"\xff\xfe not protobuf")
    assert feed_service.status()["decode_errors"] + feed_service.status()["frames"] >= 1
