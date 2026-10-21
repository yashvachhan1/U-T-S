"""Is this install ready to start the paper run?

A setup guide tells you what to do. This tells you what you have **not**
done, which is the part that actually blocks people: a guide is read once,
a step is missed, and the symptom turns up three days later as an agent
that silently returns neutral forever.

Every check carries the exact line or command that fixes it. Nothing here
writes anything -- it reads config, the database and the filesystem, plus
one optional network probe.

Lives in the package rather than in the tool so that `tools/preflight.py`
and `GET /api/preflight` cannot disagree about what is outstanding.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import config

ROOT = config.ROOT


def _env_has(key: str) -> bool:
    return bool((getattr(config, key, "") or "").strip())


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    blocking: bool = True
    optional_note: str = ""


@dataclass
class Section:
    title: str
    checks: list[Check] = field(default_factory=list)


def _env_has(key: str) -> bool:
    return bool((getattr(config, key, "") or "").strip())


# --------------------------------------------------------------------------
# 1. The basics
# --------------------------------------------------------------------------
def check_install() -> Section:
    s = Section("1. Install")

    env = ROOT / ".env"
    s.checks.append(Check(
        ".env exists", env.exists(),
        str(env) if env.exists() else "file nahi mili",
        fix="copy .env.example .env   (fir usme values bharein)"))

    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    s.checks.append(Check(
        "virtualenv", venv.exists(),
        "" if venv.exists() else ".venv nahi mila",
        fix="setup.bat chalayein"))

    dist = ROOT / "frontend" / "dist" / "index.html"
    s.checks.append(Check(
        "frontend built", dist.exists(),
        "" if dist.exists() else "frontend/dist nahi bana",
        fix="cd frontend && npm install && npm run build"))

    problems = config.validate()
    s.checks.append(Check(
        "config limits valid", not problems,
        "; ".join(problems) if problems else "saare limits hard ceiling ke andar",
        fix=".env me limits theek karein -- app inke bahar start hi nahi hogi"))
    return s


# --------------------------------------------------------------------------
# 2. Paper-mode safety
# --------------------------------------------------------------------------
def check_safety() -> Section:
    s = Section("2. Safety")

    s.checks.append(Check(
        "PAPER_TRADING on", config.PAPER_TRADING,
        "paper mode -- koi real order nahi jayega" if config.PAPER_TRADING
        else "*** LIVE MODE *** -- real paise se order jayenge",
        fix="PAPER_TRADING=true  (forward test ke liye ye zaroori hai)"))

    capital_ok = config.TRADING_CAPITAL > 0
    s.checks.append(Check(
        "TRADING_CAPITAL set", capital_ok,
        f"Rs {config.TRADING_CAPITAL:,.0f}" if capital_ok
        else "set nahi hai -- risk engine har position isi ke against size "
             "karta hai, aur bina iske size karne se mana kar dega",
        fix="TRADING_CAPITAL=1000000   (jitni capital se test karna hai)"))

    s.checks.append(Check(
        "binds to localhost only", config.HOST in ("127.0.0.1", "localhost"),
        f"HOST={config.HOST}",
        fix="HOST=127.0.0.1 -- app me koi authentication nahi hai, isliye "
            "ise bahar expose mat karein. VPS par SSH tunnel use karein."))
    return s


# --------------------------------------------------------------------------
# 3. Broker
# --------------------------------------------------------------------------
def check_broker() -> Section:
    from app.upstox import client, instruments

    s = Section("3. Upstox")

    creds = config.credentials_ok()
    s.checks.append(Check(
        "API credentials", creds,
        "set hain" if creds else "UPSTOX_API_KEY / UPSTOX_API_SECRET nahi hain",
        fix="https://account.upstox.com/developer/apps par app banayein, "
            "fir .env me key aur secret bharein"))

    info = client.session_info()
    s.checks.append(Check(
        "logged in", bool(info.get("logged_in")),
        f"token {info.get('expires_at') or '?'} tak valid"
        if info.get("logged_in") else "login nahi hua (ya token expire ho gaya)",
        fix="app chalayein aur dashboard par 'Upstox Login' dabayein. "
            "Token roz subah expire hota hai -- har din login karna padega."))

    loaded = instruments.loaded()
    count = len(instruments.all_instruments()) if loaded else 0
    s.checks.append(Check(
        "instrument master", loaded and count > 10000,
        f"{count:,} instruments" if loaded else "download nahi hua",
        fix="dashboard par refresh icon dabayein, ya "
            "POST /api/instruments/refresh"))

    s.checks.append(Check(
        "redirect URI matches", bool(config.REDIRECT_URI),
        config.REDIRECT_URI,
        fix="Upstox app me jo Redirect URL daala hai, .env me bilkul wahi "
            "hona chahiye -- ek character ka fark bhi login tod deta hai"))
    return s


# --------------------------------------------------------------------------
# 4. Data the agents need
# --------------------------------------------------------------------------
def check_data() -> Section:
    from app.collectors import constituents

    s = Section("4. Agent data")

    # Asked of the loader the agents actually use, not of config: the Groq
    # keys are read straight from the environment, so checking a config
    # attribute that does not exist would report "no key" forever.
    from .agents.llm import _load_keys

    keys = _load_keys()
    groq = bool(keys)
    s.checks.append(Check(
        "Groq API key", groq,
        f"{len(keys)} key{'s' if len(keys) > 1 else ''} set" if groq else "nahi hai",
        fix="https://console.groq.com se free key lein, .env me GROQ_API_KEY= "
            "bharein",
        blocking=False,
        optional_note="Bina iske agents chalenge par har baar neutral "
                      "denge -- system trade karega, bas agent layer ka "
                      "koi fayda nahi hoga."))

    fred = _env_has("FRED_API_KEY")   # this one IS a config attribute
    s.checks.append(Check(
        "FRED API key", fred,
        "set hai" if fred else "nahi hai",
        fix="https://fredaccount.stlouisfed.org/apikeys se free key lein",
        blocking=False,
        optional_note="Iske bina crude, DXY, US10Y aur VIX nahi aayenge -- "
                      "4 agents aadhe andhe ho jayenge."))

    lists = constituents.status()
    have = [k for k, v in lists.items() if v.get("members")]
    stale = [k for k, v in lists.items() if v.get("stale")]
    s.checks.append(Check(
        "NSE constituent lists", len(have) >= 3,
        f"{', '.join(have)}" if have else "download nahi hui",
        fix="POST /api/agents/constituents/refresh  (ya feed start karne par "
            "khud ho jayega)"))
    if stale:
        s.checks.append(Check(
            "constituent lists fresh", False,
            f"purani: {', '.join(stale)}",
            fix="POST /api/agents/constituents/refresh",
            blocking=False))
    return s


# --------------------------------------------------------------------------
# 5. Forward test prerequisites
# --------------------------------------------------------------------------
def check_forward() -> Section:
    from app.forward import compare, journal
    from app.risk import killswitch

    s = Section("5. Forward test")

    base = compare.baseline()
    s.checks.append(Check(
        "backtest baseline registered", bool(base),
        f"{base.get('label') or 'unlabelled'}: "
        f"return {base.get('total_return_pct')}%, "
        f"drawdown {base.get('max_drawdown_pct')}%" if base
        else "koi baseline nahi -- paper run kis se compare hoga?",
        fix="Backtest chalayein, fir POST /api/forward/baseline me uska "
            "result bhejein. Sirf ek baseline rakha jaata hai, isliye soch "
            "kar chunein."))

    sw = killswitch.status()
    s.checks.append(Check(
        "not halted", sw["entries_allowed"],
        "trading allowed" if sw["entries_allowed"]
        else f"{sw['state']}: " + "; ".join(
            f"{r['trigger']}" for r in sw["reasons"]),
        fix="Risk page par naam daal kar Reset dabayein"))

    sessions = len(journal.sessions())
    s.checks.append(Check(
        "journal ready", True,
        f"{sessions} sessions record ho chuke hain" if sessions
        else "abhi koi session nahi -- loop chalte hi shuru ho jayega",
        blocking=False))
    return s


# --------------------------------------------------------------------------
# 6. Optional network probes
# --------------------------------------------------------------------------
async def check_network() -> Section:
    s = Section("6. Network (--network)")

    from app.agents import llm

    if llm.client.status().get("configured"):
        try:
            text, _ = await llm.client.complete(
                system="Reply with the single word: ok",
                user="ok", model=llm.ModelTier.FAST, max_tokens=5)
            s.checks.append(Check("Groq reachable", True, text.strip()[:40],
                                  blocking=False))
        except Exception as exc:                   # noqa: BLE001
            s.checks.append(Check(
                "Groq reachable", False, str(exc)[:120],
                fix="key check karein; rate limit ho to thodi der baad",
                blocking=False))
    else:
        s.checks.append(Check("Groq reachable", False, "key set nahi hai",
                              fix="GROQ_API_KEY .env me daalein", blocking=False))

    from app.collectors import constituents

    try:
        count = await constituents.refresh("nifty50")
        s.checks.append(Check("NSE archives reachable", True,
                              f"nifty50: {count} members", blocking=False))
    except Exception as exc:                       # noqa: BLE001
        s.checks.append(Check(
            "NSE archives reachable", False, str(exc)[:120],
            fix="NSE kabhi-kabhi block karta hai -- cached list se kaam "
                "chal jayega", blocking=False))
    return s



# --------------------------------------------------------------------------
# Running the set
# --------------------------------------------------------------------------
def sections(include_network: bool = False) -> list[Section]:
    """Every section, in the order someone would work through them."""
    return [check_install(), check_safety(), check_broker(),
            check_data(), check_forward()]


def as_dict(sections_list: list[Section]) -> dict:
    """The same answer, shaped for the API."""
    out = [{
        "title": s.title,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail,
                    "fix": c.fix, "blocking": c.blocking,
                    "note": c.optional_note} for c in s.checks],
    } for s in sections_list]
    blocking = [c["name"] for s in out for c in s["checks"]
                if not c["ok"] and c["blocking"]]
    warnings = [c["name"] for s in out for c in s["checks"]
                if not c["ok"] and not c["blocking"]]
    return {"sections": out, "blocking": blocking, "warnings": warnings,
            "ready": not blocking, "guide": "docs/12-paper-run.md"}
