"""The eleven analyst agents, and the arithmetic they are not allowed to do.

Two concerns run through this file.

**Degradation.** Every agent must produce something usable when its data
source is empty, closed or broken. A trading system that stops because an ETF
is unsubscribed is worse than one that trades a little more cautiously, so
every `gather()` here is exercised against nothing at all.

**The division of labour.** Counting advancers, taking medians and resolving
contracts are done in Python; the model is only asked what the result means.
Those computations are tested directly, because a model that miscounts does
so silently while still looking healthy.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents import llm  # noqa: E402
from app.agents.base import INPUT_CLOSE, INPUT_OPEN  # noqa: E402
from app.agents.analysts import _shared as sh  # noqa: E402
from app.agents.analysts.industry import industry_performance  # noqa: E402
from app.collectors import constituents  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    from app import db
    from app.store import agent_outputs, events as events_store, macro

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    # Each store caches "schema already created" in a module flag. Pointing
    # at a new database without clearing them leaves every store convinced
    # its tables exist in a file that is empty.
    stores = (agent_outputs, events_store, macro)
    for store in stores:
        store._ready = False
    yield
    for store in stores:
        store._ready = False


@pytest.fixture
def registry():
    from app import agents
    return agents.registry


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
def test_all_twelve_analysts_are_registered(registry):
    """Twelve agents, but thirteen entries -- the index agent runs twice."""
    names = registry.names()
    assert len(names) == 13
    for expected in ("india_news", "asia_markets", "us_markets", "global_macro",
                     "commodity", "currency", "crypto_risk", "sector_rotation",
                     "earnings", "power_policy", "industry",
                     "index_composition_nifty", "index_composition_banknifty"):
        assert expected in names


def test_agent_names_are_unique(registry):
    """A duplicate name would silently overwrite an agent in the registry."""
    assert len(registry.names()) == len(set(registry.names()))


# --------------------------------------------------------------------------
# Degradation: every agent works with no data at all
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", [
    "india_news", "asia_markets", "us_markets", "global_macro", "commodity",
    "currency", "crypto_risk", "sector_rotation", "earnings", "power_policy",
    "industry", "index_composition_nifty", "index_composition_banknifty",
])
def test_gather_survives_an_empty_world(name, registry, clean_db, monkeypatch):
    """No feed, no macro history, no events -- still a prompt, never a crash."""
    from app.engine import feed_service

    monkeypatch.setattr(feed_service, "_latest", {})
    text = run(registry.get(name).gather())
    assert isinstance(text, str) and text.strip()


@pytest.mark.parametrize("name", [
    "asia_markets", "us_markets", "commodity", "currency", "sector_rotation",
])
def test_missing_quotes_say_so_rather_than_showing_zero(
        name, registry, clean_db, monkeypatch):
    """Zero is a price. "not subscribed" is the truth, and the model can act on it."""
    from app.engine import feed_service

    monkeypatch.setattr(feed_service, "_latest", {})
    text = run(registry.get(name).gather())
    assert "not subscribed" in text or "unavailable" in text
    assert "0.00 (+0.00%)" not in text


def test_a_failing_agent_returns_neutral_not_an_error(registry, clean_db):
    """A dead source must cost an opinion, never stop the system."""
    llm.use_client(llm.StubClient([]))             # every call raises
    out = run(registry.run_one("commodity"))
    assert out.view.value == "neutral"
    assert out.size_multiplier() == 1.0


# --------------------------------------------------------------------------
# Each agent's own contract
# --------------------------------------------------------------------------
VALID_EXTRA = {
    "asia_markets": {"gap_bias": "negative"},
    "us_markets": {"gap_bias": "positive"},
    "global_macro": {"regime": "elevated_risk"},
    "commodity": {"driver": "crude"},
    "currency": {"rupee_direction": "weakening", "rbi_intervention_flag": True},
    "crypto_risk": {"risk_appetite": "deteriorating"},
    "sector_rotation": {"factor_leader": "quality"},
    "earnings": {"guidance": "lowered", "tone": "cautious",
                 "drift_expectation": "negative"},
    "power_policy": {"event_type": "policy", "historical_n": 8},
    "index_composition_nifty": {"index": "NIFTY", "breadth_read": "narrow"},
    "index_composition_banknifty": {"index": "BANKNIFTY", "breadth_read": "broad"},
    "industry": {"industries_positive": ["Information Technology"],
                 "industries_negative": ["Power"]},
}

BASE = {"view": "bearish", "confidence": 0.7, "horizon": "intraday",
        "sectors_positive": ["IT"], "sectors_negative": ["OMC"],
        "affected_symbols": ["DLF"], "risk_flag": True, "reasoning": "test"}


@pytest.mark.parametrize("name,extra", sorted(VALID_EXTRA.items()))
def test_each_agent_parses_its_own_shape(name, extra, registry):
    payload = {**BASE, **extra}
    out = registry.get(name).parse(json.dumps(payload))
    for field, value in extra.items():
        got = getattr(out, field)
        if isinstance(value, str):
            assert got == value.lower().replace(" ", "_") or got == value
        else:
            assert got == value or got == [v.upper().replace(" ", "_") for v in value]


@pytest.mark.parametrize("name,extra", sorted(VALID_EXTRA.items()))
def test_each_schema_hint_documents_its_extra_fields(name, extra, registry):
    """A field the prompt never mentions is a field the model will omit."""
    hint = registry.get(name).schema_hint()
    for field in extra:
        assert field in hint


@pytest.mark.parametrize("name", sorted(VALID_EXTRA))
def test_fields_outside_the_contract_are_rejected(name, registry):
    """extra="forbid": a model inventing a field is not following the contract."""
    from pydantic import ValidationError

    payload = {**BASE, **VALID_EXTRA[name], "position_size": 999}
    with pytest.raises(ValidationError):
        registry.get(name).parse(json.dumps(payload))


# --------------------------------------------------------------------------
# The two schema-enforced safety rules
# --------------------------------------------------------------------------
def test_crypto_cannot_return_a_bullish_view(registry):
    """BTC up is not an equity signal, and the prompt is not what enforces that.

    BTC-equity correlation is near zero in a calm market and only rises in a
    sell-off, so the information is one-sided. Leaving that to the prompt
    means trusting a model to stay disciplined on the day the number is
    dramatic.
    """
    out = registry.get("crypto_risk").parse(json.dumps(
        {**BASE, "view": "bullish", "confidence": 0.95,
         "risk_appetite": "improving"}))
    assert out.view.value == "neutral"
    assert out.confidence <= 0.2
    assert "downgraded" in out.reasoning


def test_crypto_bearish_view_passes_through(registry):
    """The downside is the whole point of the agent -- it must survive."""
    out = registry.get("crypto_risk").parse(json.dumps(
        {**BASE, "view": "bearish", "confidence": 0.8,
         "risk_appetite": "deteriorating"}))
    assert out.view.value == "bearish"
    assert out.size_multiplier() == 0.25       # bearish and flagged


def test_thin_evidence_caps_policy_confidence(registry):
    """Two precedents cannot support a confident base rate, whatever it says."""
    agent = registry.get("power_policy")
    thin = agent.parse(json.dumps(
        {**BASE, "confidence": 0.95, "event_type": "state_visit",
         "historical_n": 2}))
    assert thin.confidence == 0.35

    solid = agent.parse(json.dumps(
        {**BASE, "confidence": 0.95, "event_type": "state_visit",
         "historical_n": 12}))
    assert solid.confidence == 0.95


def test_invented_industry_labels_are_dropped(registry):
    """An industry we cannot join back to stocks is not an industry."""
    out = registry.get("industry").parse(json.dumps(
        {**BASE, "industries_positive": ["Information Technology", "Crypto Mining"],
         "industries_negative": []}))
    assert out.industries_positive == ["INFORMATION_TECHNOLOGY"]


def test_unknown_choice_values_fall_back_rather_than_rejecting(registry):
    """A vague word should cost precision, not the whole output."""
    out = registry.get("asia_markets").parse(json.dumps(
        {**BASE, "gap_bias": "sideways-ish"}))
    assert out.gap_bias == "flat"


# --------------------------------------------------------------------------
# Untrusted input is still fenced
# --------------------------------------------------------------------------
def test_every_agent_fences_its_data(registry, clean_db, monkeypatch):
    from app.engine import feed_service

    monkeypatch.setattr(feed_service, "_latest", {})
    for name in registry.names():
        agent = registry.get(name)
        prompt = agent.build_user_prompt(run(agent.gather()))
        assert prompt.count(INPUT_OPEN) == 1, name
        assert prompt.count(INPUT_CLOSE) == 1, name


def test_combined_multiplier_over_all_agents_never_exceeds_one(registry):
    """Thirteen bullish, maximally confident agents still cannot enlarge a trade."""
    from app.agents.schemas import AgentOutput, combined_multiplier

    outs = [AgentOutput(view="bullish", confidence=1.0)
            for _ in registry.names()]
    assert combined_multiplier(outs) == 1.0


# --------------------------------------------------------------------------
# Constituents: parsing
# --------------------------------------------------------------------------
CSV = """Company Name,Industry,Symbol,Series,ISIN Code
Infosys Ltd.,Information Technology,INFY,EQ,INE009A01021
Reliance Industries Ltd.,Oil Gas & Consumable Fuels,RELIANCE,EQ,INE002A01018
"""


def test_parse_csv_reads_symbol_industry_and_isin():
    members = constituents.parse_csv(CSV)
    assert [m.symbol for m in members] == ["INFY", "RELIANCE"]
    assert members[0].industry == "INFORMATION_TECHNOLOGY"
    assert members[0].instrument_key == "NSE_EQ|INE009A01021"


def test_rows_without_a_symbol_or_isin_are_dropped():
    """A member we cannot subscribe to or join is worse than a missing one."""
    text = CSV + "Mystery Ltd.,Services,,EQ,\nGhost Ltd.,Services,GHOST,EQ,\n"
    assert [m.symbol for m in constituents.parse_csv(text)] == ["INFY", "RELIANCE"]


def test_duplicate_symbols_are_kept_once():
    text = CSV + "Infosys Ltd.,Information Technology,INFY,EQ,INE009A01021\n"
    assert len(constituents.parse_csv(text)) == 2


def test_parse_csv_on_rubbish_returns_nothing_rather_than_raising():
    assert constituents.parse_csv("") == []
    assert constituents.parse_csv("not,a,constituent,list\n1,2,3,4") == []


# --------------------------------------------------------------------------
# Breadth: the arithmetic the model is not asked to do
# --------------------------------------------------------------------------
def test_breadth_counts_advancers_and_decliners():
    out = constituents.breadth({"A": 1.0, "B": 2.0, "C": -1.0, "D": 0.0})
    assert out["advancing"] == 2
    assert out["declining"] == 1
    assert out["unchanged"] == 1
    assert out["advance_ratio"] == 0.5


def test_breadth_catches_a_narrow_rally():
    """Index up while most stocks fall: a few heavyweights carrying it."""
    changes = {f"S{i}": -0.3 for i in range(40)}
    changes.update({f"B{i}": 4.0 for i in range(10)})
    out = constituents.breadth(changes, index_change_pct=0.6)
    assert out["declining"] == 40
    assert out["median_change_pct"] < 0
    assert out["narrow"] is True
    assert out["divergent"] is True


def test_breadth_on_a_genuinely_broad_move_is_not_flagged_narrow():
    changes = {f"S{i}": 1.0 for i in range(50)}
    out = constituents.breadth(changes, index_change_pct=1.1)
    assert out["advance_ratio"] == 1.0
    assert out["narrow"] is False
    assert out["divergent"] is False


def test_breadth_with_no_quotes_is_unavailable_not_zero():
    assert constituents.breadth({}) == {"available": False}


def test_live_changes_skips_instruments_with_no_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(constituents, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(constituents, "_cache", {})
    (tmp_path / "ind_nifty50list.csv").write_text(CSV, encoding="utf-8")

    class Tick:
        def __init__(self, pct):
            self.change_pct = pct

    ticks = {"NSE_EQ|INE009A01021": Tick(1.5)}
    out = constituents.live_changes("nifty50", lambda k: ticks.get(k))
    assert out == {"INFY": 1.5}


# --------------------------------------------------------------------------
# Industry performance
# --------------------------------------------------------------------------
def test_industry_performance_uses_the_median_not_the_mean():
    """One limit-up constituent must not crown its whole industry.

    With a mean, IT's single +20% name would beat METAL's four steady
    gainers and the agent would chase a stock story as an industry move.
    """
    groups = {"INFORMATION_TECHNOLOGY": ["A", "B", "C", "D"],
              "METALS_&_MINING": ["E", "F", "G", "H"]}
    changes = {"A": 20.0, "B": -0.2, "C": -0.1, "D": 0.0,
               "E": 1.2, "F": 1.4, "G": 1.1, "H": 1.3}
    rows = industry_performance(changes, groups)
    assert rows[0]["industry"] == "METALS_&_MINING"
    assert rows[0]["median_pct"] == pytest.approx(1.25)


def test_thinly_covered_industries_are_left_out():
    """An average over one stock is that stock, not an industry."""
    groups = {"TEXTILES": ["A", "B", "C"], "REALTY": ["D", "E", "F"]}
    rows = industry_performance({"A": 1.0, "D": 5.0}, groups)
    assert rows == []


def test_industry_performance_reports_leader_and_laggard():
    groups = {"POWER": ["A", "B", "C"]}
    rows = industry_performance({"A": 2.0, "B": 0.5, "C": -1.0}, groups)
    assert rows[0]["leader"] == "A"
    assert rows[0]["laggard"] == "C"
    assert rows[0]["advancing"] == 2


# --------------------------------------------------------------------------
# Instrument resolution
# --------------------------------------------------------------------------
@pytest.fixture
def fake_master(monkeypatch, tmp_path):
    """A small instrument master: two index name-alikes and two futures."""
    from app.clock import now_ist
    from app.upstox import instruments

    now_ms = now_ist().timestamp() * 1000
    rows = [
        {"instrument_key": "NSE_INDEX|Nifty Fin Service", "symbol": "",
         "name": "Nifty Fin Service", "segment": "NSE_INDEX", "type": "",
         "expiry": None},
        {"instrument_key": "NSE_INDEX|Nifty FinSrv25 50", "symbol": "",
         "name": "Nifty FinSrv25 50", "segment": "NSE_INDEX", "type": "",
         "expiry": None},
        {"instrument_key": "NCD_FO|expired", "symbol": "USDINR FUT 01 JAN 20",
         "name": "USDINR", "segment": "NCD_FO", "type": "FUT",
         "expiry": now_ms - 86_400_000},
        {"instrument_key": "NCD_FO|live", "symbol": "USDINR FUT 30 DEC 99",
         "name": "USDINR", "segment": "NCD_FO", "type": "FUT",
         "expiry": now_ms + 86_400_000},
    ]
    monkeypatch.setattr(instruments, "_cache", rows)
    monkeypatch.setattr(instruments, "loaded", lambda: True)
    return instruments


def test_expired_contracts_do_not_win_the_search(fake_master):
    """An expired key quotes nothing, so returning it blinds the agent silently."""
    hits = fake_master.search("USDINR", limit=5, segment="NCD_FO")
    assert hits[0]["instrument_key"] == "NCD_FO|live"
    # Still present: historical candles are looked up by the old key.
    assert any(h["instrument_key"] == "NCD_FO|expired" for h in hits)


def test_nearest_future_returns_the_live_contract(fake_master):
    assert sh.nearest_future("USDINR", "NCD_FO") == "NCD_FO|live"


def test_index_key_matches_the_exact_name(fake_master):
    """Four Nifty financial indices exist; a fuzzy match picks the wrong one."""
    assert sh.index_key("Nifty Fin Service") == "NSE_INDEX|Nifty Fin Service"
    assert sh.index_key("Nifty FinSrv25 50") == "NSE_INDEX|Nifty FinSrv25 50"


def test_instrument_helpers_are_quiet_without_a_master(monkeypatch):
    from app.upstox import instruments

    monkeypatch.setattr(instruments, "loaded", lambda: False)
    assert sh.index_key("Nifty 50") == ""
    assert sh.nearest_future("USDINR", "NCD_FO") == ""
    assert sh.equity_key("INFY") == ""


# --------------------------------------------------------------------------
# What the feed has to subscribe
# --------------------------------------------------------------------------
def test_agents_declare_what_they_need_subscribed(registry):
    """An unsubscribed agent fails silently and reports itself healthy."""
    wanted = registry.required_instruments()
    assert wanted
    for key, who in wanted.items():
        assert key and who


def test_required_instruments_stay_within_the_feed_limit(registry):
    """The V3 feed caps combined subscriptions, and 500 constituents is most of it."""
    from app.upstox.feed import COMBINED_KEY_LIMIT

    assert len(registry.required_instruments()) <= COMBINED_KEY_LIMIT


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------
def test_earnings_reads_results_filings_and_ignores_the_rest(clean_db):
    from app.agents.analysts.earnings import EarningsAgent
    from app.store import events

    events.save([
        events.Event("nse", "filing", "INFY: Financial Results for Q2 FY27",
                     symbols=["INFY"]),
        events.Event("nse", "filing", "TCS: Certificate under SEBI Regulation 74(5)",
                     symbols=["TCS"]),
        events.Event("moneycontrol", "news", "Results season earnings preview"),
    ])

    titles = [r["title"] for r in EarningsAgent().results_filings()]
    assert any("Financial Results" in t for t in titles)
    assert not any("Certificate" in t for t in titles)
    # Wire copy is not a filing, however suggestive the headline.
    assert not any("preview" in t for t in titles)


def test_power_policy_picks_out_disclosed_insider_activity(clean_db):
    from app.agents.analysts.power_policy import PowerPolicyAgent
    from app.store import events

    events.save([
        events.Event("nse", "filing",
                     "ABC: Disclosure under Regulation 29 - acquisition of shares",
                     symbols=["ABC"]),
        events.Event("nse", "filing", "XYZ: Creation of pledge by promoter",
                     symbols=["XYZ"]),
        events.Event("nse", "filing", "PQR: Newspaper Publication", symbols=["PQR"]),
    ])

    titles = [r["title"] for r in PowerPolicyAgent().insider_filings()]
    assert len(titles) == 2
    assert not any("Newspaper" in t for t in titles)


def test_peer_view_reports_absence_rather_than_inventing_one(clean_db):
    assert "no recent view" in sh.peer_view("asia_markets")


def test_peer_view_reads_a_stored_view(clean_db):
    from app.agents.schemas import StoredOutput
    from app.clock import now_ist
    from app.store import agent_outputs

    agent_outputs.save(StoredOutput(
        agent="commodity", ts=now_ist(),
        payload={"view": "bearish", "confidence": 0.7, "risk_flag": True}))
    line = sh.peer_view("commodity")
    assert "bearish" in line and "0.7" in line
