"""Preflight: does it say what is actually missing?

The value of this check is entirely in it being right about an install it
has never seen. A preflight that passes a broken setup is worse than none,
because it converts "I should check" into "it said it was fine".

So the tests here mostly break something on purpose and assert that the
specific thing is named, with a fix attached.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, preflight  # noqa: E402


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    from app import db
    from app.forward import compare, journal
    from app.risk import killswitch

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "_local", type("L", (), {})())
    stores = (journal, compare, killswitch)
    for store in stores:
        store._ready = False
    yield
    for store in stores:
        store._ready = False


def find(section, name):
    return next(c for c in section.checks if c.name == name)


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------
def test_live_mode_is_called_out_during_a_paper_run(monkeypatch):
    """The whole point of the phase is that this stays off."""
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    check = find(preflight.check_safety(), "PAPER_TRADING on")
    assert check.ok is False
    assert "LIVE" in check.detail


def test_unset_capital_is_blocking(monkeypatch):
    """Without it the risk engine refuses to size, so nothing would trade."""
    monkeypatch.setattr(config, "TRADING_CAPITAL", 0.0)
    check = find(preflight.check_safety(), "TRADING_CAPITAL set")
    assert check.ok is False
    assert check.blocking is True
    assert "TRADING_CAPITAL=" in check.fix


def test_capital_set_passes(monkeypatch):
    monkeypatch.setattr(config, "TRADING_CAPITAL", 500_000.0)
    assert find(preflight.check_safety(), "TRADING_CAPITAL set").ok is True


def test_binding_to_the_world_is_called_out(monkeypatch):
    """There is no authentication in this app, so the bind address is the
    only thing standing between a stranger and the order path."""
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    check = find(preflight.check_safety(), "binds to localhost only")
    assert check.ok is False
    assert "SSH tunnel" in check.fix


# --------------------------------------------------------------------------
# Optional versus blocking
# --------------------------------------------------------------------------
def test_a_missing_groq_key_warns_rather_than_blocks(monkeypatch):
    """The system still trades without it -- it just learns nothing about
    its agents, which is a warning and not a stop."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)
    check = find(preflight.check_data(), "Groq API key")
    assert check.ok is False
    assert check.blocking is False
    assert "neutral" in check.optional_note


def test_the_groq_warning_explains_what_stops_working(monkeypatch):
    """A warning with no consequence attached gets ignored."""
    monkeypatch.setattr(config, "FRED_API_KEY", "")
    check = find(preflight.check_data(), "FRED API key")
    assert check.optional_note
    assert "agents" in check.optional_note


def test_a_key_set_through_the_pool_variable_counts(monkeypatch):
    """The keys are read from the environment, not from config -- checking
    a config attribute would report "no key" even with one set."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEYS", "a,b,c")
    check = find(preflight.check_data(), "Groq API key")
    assert check.ok is True
    assert "3 keys" in check.detail


# --------------------------------------------------------------------------
# Forward test prerequisites
# --------------------------------------------------------------------------
def test_a_missing_baseline_is_blocking(clean_db):
    """Without one there is nothing for the paper run to be compared to."""
    check = find(preflight.check_forward(), "backtest baseline registered")
    assert check.ok is False
    assert check.blocking is True


def test_a_registered_baseline_passes(clean_db):
    from app.forward import compare

    compare.register({"total_return_pct": 12.0, "max_drawdown_pct": 5.0,
                      "win_rate_pct": 55.0, "trades": 90}, label="v1")
    check = find(preflight.check_forward(), "backtest baseline registered")
    assert check.ok is True
    assert "v1" in check.detail


def test_a_halt_blocks_the_start(clean_db):
    from app.risk import killswitch

    killswitch.stop_all(by="test")
    check = find(preflight.check_forward(), "not halted")
    assert check.ok is False
    assert "Reset" in check.fix


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------
def test_every_failing_check_carries_a_fix(clean_db, monkeypatch):
    """A check that says "this is wrong" without saying what to do is a
    check that gets ignored."""
    monkeypatch.setattr(config, "TRADING_CAPITAL", 0.0)
    for section in preflight.sections():
        for check in section.checks:
            if not check.ok and check.blocking:
                assert check.fix, f"{check.name} has no fix"


def test_the_api_shape_separates_blocking_from_warnings(clean_db, monkeypatch):
    monkeypatch.setattr(config, "TRADING_CAPITAL", 0.0)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEYS", raising=False)

    out = preflight.as_dict(preflight.sections())
    assert "TRADING_CAPITAL set" in out["blocking"]
    assert "Groq API key" in out["warnings"]
    assert out["ready"] is False


def test_ready_is_true_only_when_nothing_blocking_remains(clean_db, monkeypatch):
    from app.forward import compare

    monkeypatch.setattr(config, "TRADING_CAPITAL", 1_000_000.0)
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(config, "HOST", "127.0.0.1")
    monkeypatch.setattr(config, "credentials_ok", lambda: True)
    monkeypatch.setattr("app.upstox.client.session_info",
                        lambda: {"logged_in": True, "expires_at": "2026-10-21T06:00:00"})
    compare.register({"total_return_pct": 12.0, "max_drawdown_pct": 5.0,
                      "win_rate_pct": 55.0, "trades": 90}, label="v1")

    # The scanner needs something to look at, or the run would record no
    # trades at all -- which is why an empty watchlist blocks.
    from app.decision import scanner

    monkeypatch.setattr(scanner, "watchlist", lambda: ["NSE_EQ|INE009A01021"])

    out = preflight.as_dict(preflight.sections())
    assert out["blocking"] == []
    assert out["ready"] is True


def test_an_empty_watchlist_blocks(clean_db, monkeypatch):
    """Without instruments the scan runs and finds nothing, for two
    months -- the most expensive way to waste the phase."""
    from app.decision import scanner

    monkeypatch.setattr(scanner, "watchlist", lambda: [])
    check = find(preflight.check_forward(), "scanner configured")
    assert check.ok is False
    assert check.blocking is True


def test_a_disabled_scanner_warns_loudly_but_does_not_block(clean_db):
    """It is a legitimate state for the first day or two, while you watch
    what the strategy produces before letting it act."""
    check = find(preflight.check_forward(), "scanner enabled")
    assert check.ok is False
    assert check.blocking is False
    assert "ZERO trade" in check.optional_note


def test_the_checks_write_nothing(clean_db, tmp_path):
    """It has to be safe to run at any moment, including mid-session."""
    from app import db

    preflight.sections()
    before = db.query("SELECT name FROM sqlite_master WHERE type='table'")
    preflight.sections()
    after = db.query("SELECT name FROM sqlite_master WHERE type='table'")
    assert before == after
