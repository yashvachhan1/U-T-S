"""Vidyut Pravah parsing.

The API returns numbers wrapped in HTML spans, and a literal "-" when a figure
is unavailable, so the extractor carries most of the risk here.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.collectors import altdata  # noqa: E402
from app.store import macro  # noqa: E402


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    macro._ready = False
    yield
    macro._ready = False


# Exactly the shape the live endpoint returns.
STATS = [{
    "frequency": 0,
    "ui_rate": 0,
    "demand": "<span class='counter'>            227</span>"
              "<span class='box_white_title'>&nbsp;GW</span>",
    "demandYest": "<span class='counter'>            230</span>"
                  "<span class='box_white_title'>&nbsp;GW</span>",
    "UMCP": "-",
    "Peak": "<span class='counter'>            943</span>",
}]

TIMING = [{"FromTime": "17:30", "ToTime": "17:45", "CurrentDate": "16 OCT 2026"}]


# ======================================================= number extraction
def test_number_is_pulled_out_of_html():
    assert altdata.extract_number(STATS[0]["demand"]) == 227.0


def test_dash_means_unavailable_not_zero():
    """A missing figure must not become a real reading of zero."""
    assert altdata.extract_number("-") is None
    assert altdata.extract_number("") is None
    assert altdata.extract_number(None) is None


def test_entities_and_commas_handled():
    assert altdata.extract_number("<span>1,234</span>&nbsp;MW") == 1234.0
    assert altdata.extract_number("45.67 GW") == 45.67


def test_negative_numbers_survive():
    assert altdata.extract_number("<span>-12.5</span>") == -12.5


def test_plain_numbers_pass_through():
    assert altdata.extract_number(227) == 227.0
    assert altdata.extract_number("227") == 227.0


# ============================================================ parse_power
def test_parse_reads_demand_and_computes_change():
    out = altdata.parse_power(STATS, TIMING)
    assert out["demand_gw"] == 227.0
    assert out["demand_yesterday_gw"] == 230.0
    assert out["peak_gw"] == 943.0
    assert out["change_pct"] == pytest.approx(-1.3, abs=0.01)


def test_parse_includes_the_time_block():
    out = altdata.parse_power(STATS, TIMING)
    assert out["block_from"] == "17:30"
    assert out["reading_date"] == "16 OCT 2026"


def test_parse_without_timing_still_works():
    """The reading stands even if the clock endpoint fails."""
    out = altdata.parse_power(STATS, None)
    assert out["demand_gw"] == 227.0
    assert "block_from" not in out


def test_unavailable_umcp_is_none_not_zero():
    assert altdata.parse_power(STATS, TIMING)["umcp"] is None


def test_parse_handles_junk():
    assert altdata.parse_power(None) == {}
    assert altdata.parse_power([]) == {}
    assert altdata.parse_power("not a list") == {}


def test_no_change_when_yesterday_missing():
    stats = [{"demand": "<span>227</span>", "demandYest": "-"}]
    out = altdata.parse_power(stats)
    assert out["demand_gw"] == 227.0
    assert "change_pct" not in out


# ============================================================== collector
def test_collector_emits_series():
    points = altdata.AltDataCollector().parse({"stats": STATS, "timing": TIMING})
    ids = {p.series_id for p in points}
    assert ids == {"POWER_DEMAND_GW", "POWER_DEMAND_CHG", "POWER_PEAK_GW"}
    demand = next(p for p in points if p.series_id == "POWER_DEMAND_GW")
    assert demand.value == 227.0
    assert demand.meta["block_from"] == "17:30"


def test_collector_emits_nothing_without_demand():
    """No demand figure means no reading -- not a zero."""
    assert altdata.AltDataCollector().parse({"stats": [{"demand": "-"}]}) == []
    assert altdata.AltDataCollector().parse({}) == []


def test_collector_registered():
    from app import collectors
    assert "altdata" in collectors.runner.collectors


# =========================================================== power_read
@pytest.mark.parametrize("change,expected", [
    (5.0, "expanding"),
    (2.01, "expanding"),
    (0.5, "steady"),
    (-2.0, "steady"),        # exactly at the bar is still steady
    (-5.0, "contracting"),
])
def test_activity_classification(clean_db, change, expected):
    """Industrial load moves slowly, so a few percent is a real shift."""
    ts = "2026-10-16T17:45"
    macro.save([
        macro.Point("POWER_DEMAND_GW", ts, 220.0),
        macro.Point("POWER_DEMAND_CHG", ts, change),
    ], source="test")
    assert altdata.power_read()["activity"] == expected


def test_power_read_without_data(clean_db):
    assert altdata.power_read()["available"] is False


# ====================================================== POST plumbing
def test_post_sends_a_body(clean_db, monkeypatch):
    """Vidyut Pravah answers 411 Length Required for a body-less POST."""
    import httpx
    from app.collectors import base

    captured = {}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, content=None, headers=None):
            captured["content"] = content
            return httpx.Response(200, json={"ok": True},
                                  request=httpx.Request("POST", url))

    monkeypatch.setattr(base.httpx, "AsyncClient", lambda **kw: FakeClient())
    c = base.Collector(name="t", source="t")
    assert asyncio.run(c.http_post("https://example.com")) == {"ok": True}
    assert captured["content"] == b""        # never None
