"""All HTTP endpoints for the dashboard."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import config, db
from .. import agents, collectors, markets
from ..collectors import constituents
from .. import decision
from .. import execution
from .. import forward
from .. import preflight
from ..risk import killswitch
from ..risk import state as risk_state
from ..store import audit
from ..engine import agent_feed
from ..engine import backtester, feed_service, runner
from ..store import events as events_store
from ..store import macro as macro_store
from ..strategies import base as strat
from ..upstox import client, instruments, marketdata, trading

router = APIRouter(prefix="/api")


def _wrap(exc: client.UpstoxError) -> HTTPException:
    return HTTPException(status_code=exc.status if exc.status >= 400 else 400, detail=exc.message)


# ===================================================================== auth
@router.get("/status")
async def status() -> dict:
    info = client.session_info()
    info.update(
        paper_trading=config.PAPER_TRADING,
        instruments_loaded=instruments.loaded(),
        instruments_count=len(instruments.all_instruments()),
        redirect_uri=config.REDIRECT_URI,
        active_bots=len([b for b in runner.active() if b["status"] == "running"]),
        market_open=runner.market_open_now(),
    )
    return info


@router.get("/auth/login")
async def auth_login() -> dict:
    if not config.credentials_ok():
        raise HTTPException(
            400,
            "UPSTOX_API_KEY / UPSTOX_API_SECRET .env file me set nahi hain. "
            "README ka 'Upstox app banana' section dekhein.",
        )
    return {"url": client.login_url()}


@router.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(code: str | None = None, error: str | None = None) -> HTMLResponse:
    """Upstox redirects the browser here after the user approves."""
    if error or not code:
        msg = error or "Upstox ne code nahi bheja."
        return HTMLResponse(_result_page(False, msg), status_code=400)
    try:
        data = await client.exchange_code(code)
    except client.UpstoxError as exc:
        return HTMLResponse(_result_page(False, exc.message), status_code=400)
    name = data.get("user_name") or data.get("user_id") or "trader"
    return HTMLResponse(_result_page(True, f"Welcome, {name}! Login ho gaya."))


def _result_page(ok: bool, message: str) -> str:
    colour = "#16a34a" if ok else "#dc2626"
    icon = "&#10004;" if ok else "&#10006;"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Upstox Login</title><style>
body{{font:16px system-ui;background:#0b1220;color:#e5e7eb;display:grid;place-items:center;height:100vh;margin:0}}
.card{{background:#111a2e;border:1px solid #1f2b45;border-radius:14px;padding:32px 40px;text-align:center;max-width:420px}}
.i{{font-size:40px;color:{colour}}} a{{color:#60a5fa}}
</style></head><body><div class="card"><div class="i">{icon}</div>
<h2>{message}</h2><p>Ye tab band karke dashboard par wapas jayein.</p>
<p><a href="/">Dashboard kholein</a></p>
<script>setTimeout(()=>{{try{{window.opener&&window.opener.postMessage('upstox-auth','*')}}catch(e){{}}}},100)</script>
</div></body></html>"""


@router.post("/auth/logout")
async def auth_logout() -> dict:
    await runner.stop_all()
    client.clear_token()
    return {"ok": True}


@router.get("/auth/profile")
async def auth_profile() -> dict:
    try:
        return await trading.profile()
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc


# ============================================================== instruments
@router.post("/instruments/refresh")
async def instruments_refresh() -> dict:
    try:
        n = await instruments.download()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Instrument list download fail: {exc}") from exc
    return {"count": n}


@router.get("/instruments/search")
async def instruments_search(
    q: str = Query(..., min_length=1), limit: int = 20, segment: str | None = None
) -> list[dict]:
    if not instruments.loaded():
        raise HTTPException(409, "Instrument list load nahi hui. Pehle 'Refresh instruments' dabayein.")
    return instruments.search(q, limit, segment)


# ============================================================ market data
@router.get("/market/candles")
async def market_candles(
    instrument_key: str, interval: str = "1d", days: int = 365
) -> dict:
    try:
        rows = await marketdata.historical(instrument_key, interval, days)
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc
    meta = instruments.by_key(instrument_key) or {}
    return {"instrument_key": instrument_key, "symbol": meta.get("symbol"), "interval": interval, "candles": rows}


@router.get("/market/quote")
async def market_quote(instrument_key: str) -> dict:
    try:
        rows = await marketdata.snapshot([instrument_key])
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc
    return rows[0] if rows else {}


class KeysBody(BaseModel):
    instrument_keys: list[str] = Field(default_factory=list)


@router.post("/market/snapshot")
async def market_snapshot(body: KeysBody) -> list[dict]:
    try:
        return await marketdata.snapshot(body.instrument_keys[:200])
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc


# ============================================================= collectors
@router.get("/collectors/status")
async def collectors_status() -> dict:
    return collectors.runner.status()


@router.post("/collectors/start")
async def collectors_start(names: str | None = None) -> dict:
    wanted = names.split(",") if names else None
    return {"started": await collectors.runner.start(wanted)}


@router.post("/collectors/stop")
async def collectors_stop() -> dict:
    await collectors.runner.stop()
    return {"ok": True}


@router.post("/collectors/{name}/run")
async def collectors_run_once(name: str) -> dict:
    """Run one collector right now -- useful for testing a source."""
    try:
        points = await collectors.runner.run_once(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "collector": name,
        "points": points,
        "status": collectors.runner.collectors[name].status.as_dict(),
    }


@router.get("/macro/series")
async def macro_known() -> list[dict]:
    return macro_store.known_series()


@router.get("/macro/series/{series_id}")
async def macro_one(series_id: str, limit: int = 200) -> list[dict]:
    return macro_store.series(series_id, limit)


@router.get("/agents/status")
async def agents_status() -> dict:
    return agents.registry.status()


@router.get("/agents/views")
async def agents_views() -> dict:
    """Each agent's current view, plus the combined size multiplier.

    The multiplier is the only number that reaches position sizing, and it
    can never exceed 1.0 -- an agent may shrink a trade, never enlarge it.

    Analyst and synthesis multipliers are reported separately on purpose.
    The synthesis pair is a reading *of* the analysts, so multiplying the
    two together would count the same caution twice.
    """
    from ..agents.synthesis.chief_strategist import SYNTHESIS_AGENTS

    views = agents.registry.current_views()
    analysts = [v for n, v in views.items() if n not in SYNTHESIS_AGENTS]
    pair = agents.synthesis.current()
    return {
        "views": {n: v.to_payload() for n, v in views.items()},
        "analyst_multiplier": agents.combined_multiplier(analysts),
        "synthesis": pair,
        # What the risk engine reads: the synthesis pair's verdict.
        "size_multiplier": pair["size_multiplier"],
    }


@router.get("/agents/synthesis")
async def agents_synthesis() -> dict:
    """The latest stance and the challenge to it, without calling a model."""
    return agents.synthesis.current()


@router.post("/agents/synthesis/run")
async def agents_synthesis_run() -> dict:
    """Form a stance from the analysts, then have the Red Team attack it."""
    return await agents.synthesis.run_pair()


@router.get("/agents/instruments")
async def agents_instruments() -> dict:
    """What the agents need, what is subscribed, and what is actually ticking.

    An unsubscribed agent is the quietest failure in the system: it reads
    "not subscribed" on every line, returns a neutral view, and reports
    itself healthy forever. This endpoint is how that becomes visible.
    """
    return agent_feed.status()


@router.post("/agents/instruments/subscribe")
async def agents_instruments_subscribe(refresh_lists: bool = False) -> dict:
    """Subscribe the agent universe on the running feed.

    With refresh_lists, re-download any constituent list older than a week
    first. Subscriptions are recorded locally and replayed on connect, so
    this is safe to call before the socket is up.
    """
    if refresh_lists:
        return await agent_feed.refresh_universe()
    return {"subscribed": await agent_feed.apply(),
            "lists": constituents.status()}


@router.post("/agents/constituents/refresh")
async def agents_constituents_refresh() -> dict:
    """Re-download the NSE index constituent lists.

    Constituents change at index review, not during a session, so this is a
    manual or daily job rather than something on the hot path.
    """
    return await constituents.refresh_all()


@router.post("/agents/{name}/run")
async def agents_run(name: str) -> dict:
    try:
        out = await agents.registry.run_one(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "agent": name,
        "output": out.to_payload(),
        "size_multiplier": out.size_multiplier(),
        "status": agents.registry.get(name).status(),
    }


@router.get("/agents/{name}/history")
async def agents_history(name: str, limit: int = 50) -> list[dict]:
    from ..store import agent_outputs
    return agent_outputs.history(name, limit)


# ==========================================================================
# Risk: the layer with the final say
# ==========================================================================
@router.get("/risk/status")
async def risk_status(equity: float | None = None) -> dict:
    """Kill-switch state and the account numbers every limit is checked against."""
    return {
        "switches": killswitch.status(),
        "account": risk_state.snapshot(equity=equity),
        "limits": {
            "max_risk_per_trade_pct": config.MAX_RISK_PER_TRADE_PCT,
            "max_position_pct": config.MAX_POSITION_PCT,
            "max_open_positions": config.MAX_OPEN_POSITIONS,
            "max_sector_exposure_pct": config.MAX_SECTOR_EXPOSURE_PCT,
            "max_correlated_positions": config.MAX_CORRELATED_POSITIONS,
            "max_total_exposure_pct": config.MAX_TOTAL_EXPOSURE_PCT,
            "daily_loss_halt_pct": config.DAILY_LOSS_HALT_PCT,
            "max_drawdown_halt_pct": config.MAX_DRAWDOWN_HALT_PCT,
            "max_trades_per_day": config.MAX_TRADES_PER_DAY,
            "trading_capital": config.TRADING_CAPITAL,
        },
    }


class StopBody(BaseModel):
    by: str = "user"
    detail: str = ""


@router.post("/risk/stop")
async def risk_stop(body: StopBody) -> dict:
    """STOP ALL. No new orders.

    Open positions are deliberately untouched -- an automatic panic-exit
    happens at the worst price of the day. Closing them is a human call.
    """
    await runner.stop_all()
    return killswitch.stop_all(by=body.by, detail=body.detail)


class ResetBody(BaseModel):
    by: str
    trigger: str | None = None


@router.post("/risk/reset")
async def risk_reset(body: ResetBody) -> dict:
    """Clear a halt by hand. The name is recorded and is not optional."""
    try:
        return killswitch.reset(by=body.by, trigger=body.trigger)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/risk/sweep")
async def risk_sweep(equity: float | None = None) -> dict:
    """Re-check the automatic kill switches against current state."""
    return await decision.sweep(equity=equity)


class SignalBody(BaseModel):
    instrument_key: str
    side: str = "BUY"
    source: str = "manual"
    product: str = "I"
    expected_move_pct: float = 0.0
    probability: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    atr: float = 0.0
    note: str = ""


@router.post("/risk/evaluate")
async def risk_evaluate(body: SignalBody, equity: float | None = None) -> dict:
    """Run a signal through the risk engine without sending anything.

    The same path a real decision takes, so a rejection here is the exact
    rejection a live signal would get.
    """
    signal = decision.Signal(**body.model_dump())
    result = await decision.decide(signal, equity=equity)
    return result.as_dict()


# ==========================================================================
# Execution
# ==========================================================================
# ==========================================================================
# Scanner -- where signals come from
# ==========================================================================
@router.get("/scanner/status")
async def scanner_status() -> dict:
    return decision.scanner.status()


class ScannerBody(BaseModel):
    enabled: bool | None = None
    auto_execute: bool | None = None
    strategy: str | None = None
    params: dict | None = None
    interval: str | None = None
    product: str | None = None
    instruments: list[str] | None = None
    expected_move_pct: float | None = None
    probability: float | None = None


@router.post("/scanner/config")
async def scanner_config(body: ScannerBody) -> dict:
    """Configure the scanner.

    `enabled` and `auto_execute` are separate on purpose: looking for
    signals and acting on them are different decisions, and the gap
    between them is where a person gets to look.
    """
    return decision.scanner.configure(**body.model_dump(exclude_none=True))


@router.post("/scanner/run")
async def scanner_run(execute: bool = False) -> dict:
    """Run one scan by hand. Sends nothing unless execute=true."""
    return await decision.scanner.run(execute=execute)


@router.get("/execution/status")
async def execution_status() -> dict:
    return {"loop": execution.loop.status(),
            "executor": execution.executor.status()}


@router.get("/execution/orders")
async def execution_orders(limit: int = 100) -> list[dict]:
    return execution.tracker.recent(limit)


@router.post("/execution/start")
async def execution_start() -> dict:
    """Start the background loop: poll, square off, reconcile, sweep."""
    return await execution.loop.start()


@router.post("/execution/stop")
async def execution_stop() -> dict:
    """Stop the loop. Orders and positions are left exactly as they are."""
    return await execution.loop.stop()


@router.post("/execution/cycle")
async def execution_cycle() -> dict:
    """Run one pass by hand, for testing and for a nudge after a fix."""
    return await execution.loop.cycle()


@router.post("/execution/reconcile")
async def execution_reconcile(halt_on_mismatch: bool = True) -> dict:
    """Compare our book with the broker's.

    `halt_on_mismatch=false` reports without tripping the switch -- for
    looking at a known discrepancy, not for routine use.
    """
    return await execution.reconcile.run(halt_on_mismatch=halt_on_mismatch)


@router.post("/execution/square-off")
async def execution_square_off() -> dict:
    """Close intraday positions. Only acts once the square-off time passes."""
    return await execution.executor.square_off_intraday()


class ExecuteBody(SignalBody):
    confirm: bool = False


@router.post("/trade/execute")
async def trade_execute(body: ExecuteBody, equity: float | None = None) -> dict:
    """The whole path: signal -> risk decision -> order, with the stop.

    `confirm` must be true to actually send. The default is a dry run,
    because the difference between "show me what this would do" and "do it"
    should never be a typo.
    """
    signal = decision.Signal(**body.model_dump(exclude={"confirm"}))
    result = await decision.decide(signal, equity=equity)

    out: dict = {"decision": result.as_dict(), "sent": False}
    if not result.approved:
        return out
    if not body.confirm:
        out["note"] = "dry run -- confirm=true bhejein order ke liye"
        return out

    stop = float(result.proposal.get("stop") or 0)
    try:
        out["order"] = await execution.executor.place_entry(
            result, stop_price=stop, product=body.product,
            source=signal.source)
        out["sent"] = True
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return out


@router.get("/audit")
async def audit_recent(limit: int = 100, kind: str | None = None) -> list[dict]:
    return audit.recent(limit=limit, kind=kind)


@router.get("/audit/verify")
async def audit_verify() -> dict:
    """Walk the hash chain and report the first row that does not fit."""
    return {**audit.verify(), "stats": audit.stats()}


# ==========================================================================
# Phase 7: the paper forward test
# ==========================================================================
@router.get("/preflight")
async def preflight_check(network: bool = False) -> dict:
    """Is this install ready to start the paper run?

    Shares its checks with `tools/preflight.py`, so the terminal and the
    dashboard cannot disagree about what is outstanding.
    """
    sections = preflight.sections()
    if network:
        sections.append(await preflight.check_network())
    return preflight.as_dict(sections)


@router.get("/forward/report")
async def forward_report(limit: int = 1000) -> dict:
    """Everything the forward test has established so far."""
    return forward.report.collect(limit)


@router.get("/forward/report.txt", response_class=PlainTextResponse)
async def forward_report_text() -> str:
    """The same report as plain text, for a terminal or a daily mail."""
    return forward.report.text()


@router.get("/forward/sessions")
async def forward_sessions(limit: int = 90) -> dict:
    rows = forward.journal.sessions(limit)
    return {"sessions": rows, "summary": forward.journal.summary(rows)}


@router.get("/forward/agents")
async def forward_agents(limit: int = 1000) -> dict:
    """Per-agent value-add, replayed from the views stored at entry time."""
    return forward.attribution.report(limit)


@router.get("/forward/checklist")
async def forward_checklist() -> dict:
    return forward.report.checklist()


class BaselineBody(BaseModel):
    label: str = ""
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate_pct: float = 0.0
    trades: int = 0
    sessions: int = 0


@router.post("/forward/baseline")
async def forward_baseline(body: BaselineBody) -> dict:
    """Pin the backtest result the paper run is measured against.

    Only one is kept, on purpose: keeping several invites picking the
    flattering one afterwards, which is the failure this comparison exists
    to catch.
    """
    return forward.compare.register(body.model_dump(exclude={"label"}),
                                    label=body.label)


@router.get("/forward/baseline")
async def forward_baseline_get() -> dict:
    return forward.compare.baseline() or {"registered": False}


@router.get("/features/status")
async def features_status() -> dict:
    from ..features.engine import engine as fe
    return fe.status()


@router.get("/features/catalogue")
async def features_catalogue() -> dict:
    """Every registered feature, with version and why it is here."""
    from ..features.registry import Horizon, registry
    return {
        "total": len(registry.names()),
        "set_hash": registry.set_hash(registry.names()),
        "budget_warnings": registry.check_budget(registry.names()),
        "by_horizon": {
            h.value: [s.to_json() for s in registry.by_horizon(h)] for h in Horizon
        },
    }


@router.get("/features/{instrument_key:path}")
async def features_for(instrument_key: str) -> dict:
    from ..features.engine import engine as fe
    snap = fe.snapshot(instrument_key)
    if snap is None:
        raise HTTPException(404, "Is instrument par abhi koi tick nahi aaya.")
    state = fe.states[instrument_key]
    return {"instrument_key": instrument_key,
            "features": snap, "diagnostics": state.diagnostics()}


@router.get("/news/recent")
async def news_recent(limit: int = 50, source: str | None = None) -> list[dict]:
    return events_store.recent(limit=limit, source=source)


@router.get("/news/symbol/{symbol}")
async def news_for_symbol(symbol: str, limit: int = 20) -> list[dict]:
    return events_store.for_symbol(symbol, limit)


@router.get("/news/blackout")
async def news_blackout(minutes: int = 120) -> dict:
    """Instruments with fresh filings -- no new positions in these."""
    symbols = collectors.news.blackout_symbols(minutes)
    return {"minutes": minutes, "count": len(symbols), "symbols": symbols}


@router.get("/news/stats")
async def news_stats() -> dict:
    return events_store.stats()


@router.get("/markets/sessions")
async def markets_sessions() -> dict:
    """Which world markets are trading right now, in IST."""
    return {
        "now_ist": markets.now_ist().isoformat(timespec="seconds"),
        "sessions": markets.session_overview(),
        "open_now": markets.open_markets(),
    }


@router.get("/markets/global")
async def markets_global() -> dict:
    return {
        **collectors.global_markets.overview(),
        "etf_proxies": collectors.global_markets.etf_proxy_read(
            feed_service.latest_price),
    }


@router.get("/macro/snapshot")
async def macro_snapshot() -> dict:
    """Everything the decision layer reads from macro, in one call."""
    return {
        "fred": collectors.fred.macro_snapshot(),
        "crypto": collectors.crypto.risk_state(),
        "crude_rupee_stress": collectors.fred.crude_rupee_stress(),
        "power": collectors.altdata.power_read(),
        "runs": macro_store.recent_runs(limit=10),
    }


# =================================================================== feed
@router.get("/feed/status")
async def feed_status() -> dict:
    return feed_service.status()


@router.post("/feed/start")
async def feed_start(subscribe_agents: bool = True) -> dict:
    """Start the feed and, by default, subscribe what the agents need.

    Subscribing here rather than leaving it to the caller is deliberate: a
    running feed with no agent instruments on it looks healthy from every
    endpoint while every agent quietly returns neutral.
    """
    if not client.access_token():
        raise HTTPException(401, "Pehle Upstox me login karein.")
    await feed_service.start()

    out = feed_service.status()
    if subscribe_agents:
        # Downloading constituent lists takes a few seconds on a cold
        # start. Worth waiting for -- the answer then says what is actually
        # subscribed instead of what will be subscribed eventually.
        out["agents"] = await agent_feed.refresh_universe()
    return out


@router.post("/feed/stop")
async def feed_stop() -> dict:
    await feed_service.stop()
    return {"ok": True}


class FeedSubBody(BaseModel):
    instrument_keys: list[str] = Field(default_factory=list)
    mode: str = "full"


@router.post("/feed/subscribe")
async def feed_subscribe(body: FeedSubBody) -> dict:
    try:
        added = await feed_service.subscribe(body.instrument_keys, body.mode)
    except ValueError as exc:
        raise HTTPException(400, f"Mode galat hai: {exc}") from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"added": sorted(added), "status": feed_service.status()["feed"]}


@router.delete("/feed/subscribe")
async def feed_unsubscribe(instrument_key: str) -> dict:
    return {"removed": sorted(await feed_service.unsubscribe([instrument_key]))}


@router.post("/feed/snapshot")
async def feed_snapshot(body: KeysBody) -> list[dict]:
    """Live prices straight from the feed cache -- no Upstox round trip."""
    return feed_service.snapshot(body.instrument_keys or None)


# ============================================================== watchlist
@router.get("/watchlist")
async def watchlist_get() -> list[dict]:
    rows = db.query("SELECT * FROM watchlist ORDER BY added_at")
    if not rows:
        return []
    try:
        live = {r["instrument_key"]: r for r in await marketdata.snapshot([r["instrument_key"] for r in rows])}
    except client.UpstoxError:
        live = {}
    for r in rows:
        r.update({k: v for k, v in (live.get(r["instrument_key"]) or {}).items() if k != "symbol"})
    return rows


class WatchBody(BaseModel):
    instrument_key: str


@router.post("/watchlist")
async def watchlist_add(body: WatchBody) -> dict:
    meta = instruments.by_key(body.instrument_key)
    if not meta:
        raise HTTPException(404, "Instrument nahi mila.")
    db.execute(
        "INSERT OR REPLACE INTO watchlist (instrument_key, symbol, name, exchange, added_at) VALUES (?,?,?,?,?)",
        (meta["instrument_key"], meta["symbol"], meta["name"], meta["exchange"], db.now()),
    )
    return {"ok": True}


@router.delete("/watchlist")
async def watchlist_remove(instrument_key: str) -> dict:
    db.execute("DELETE FROM watchlist WHERE instrument_key = ?", (instrument_key,))
    return {"ok": True}


# ================================================================== orders
class OrderBody(BaseModel):
    instrument_key: str
    side: str
    quantity: int
    order_type: str = "MARKET"
    product: str = "D"
    price: float = 0.0
    trigger_price: float = 0.0
    validity: str = "DAY"


@router.post("/orders")
async def orders_place(body: OrderBody) -> dict:
    try:
        return await trading.place_order(**body.model_dump())
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc


@router.get("/orders")
async def orders_list() -> list[dict]:
    try:
        return await trading.order_book()
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc


@router.delete("/orders/{order_id}")
async def orders_cancel(order_id: str) -> dict:
    try:
        return await trading.cancel_order(order_id)
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc


# =============================================================== portfolio
@router.get("/portfolio/positions")
async def portfolio_positions() -> list[dict]:
    try:
        return await trading.positions()
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc


@router.get("/portfolio/holdings")
async def portfolio_holdings() -> list[dict]:
    try:
        return await trading.holdings()
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc


@router.get("/portfolio/funds")
async def portfolio_funds() -> dict:
    try:
        return await trading.funds()
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc


@router.get("/portfolio/summary")
async def portfolio_summary() -> dict:
    try:
        pos = await trading.positions()
        hold = await trading.holdings()
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc

    def num(x: Any) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0

    day_pnl = sum(num(p.get("pnl")) for p in pos)
    invested = sum(num(h.get("average_price")) * num(h.get("quantity")) for h in hold)
    current = sum(num(h.get("last_price")) * num(h.get("quantity")) for h in hold)
    return {
        "day_pnl": round(day_pnl, 2),
        "open_positions": len([p for p in pos if num(p.get("quantity"))]),
        "holdings_count": len(hold),
        "invested": round(invested, 2),
        "current_value": round(current, 2),
        "holdings_pnl": round(current - invested, 2),
        "holdings_pnl_pct": round((current - invested) / invested * 100, 2) if invested else 0.0,
        "paper_trading": config.PAPER_TRADING,
    }


# ============================================================== strategies
@router.get("/strategies")
async def strategies_list() -> list[dict]:
    return strat.listing()


# ================================================================ backtest
class BacktestBody(BaseModel):
    instrument_key: str
    strategy: str
    interval: str = "1d"
    days: int = 365
    params: dict = Field(default_factory=dict)
    capital: float = 100_000
    position_pct: float = 100
    stop_loss_pct: float = 0
    target_pct: float = 0
    allow_short: bool = False
    brokerage_per_order: float = 20
    slippage_pct: float = 0.03


@router.post("/backtest")
async def backtest_run(body: BacktestBody) -> dict:
    try:
        candles = await marketdata.historical(body.instrument_key, body.interval, body.days)
    except client.UpstoxError as exc:
        raise _wrap(exc) from exc
    if len(candles) < 30:
        raise HTTPException(400, "Backtest ke liye itna data nahi mila. Zyada din ya bada interval chunein.")
    try:
        res = backtester.run(
            candles,
            body.strategy,
            body.params,
            capital=body.capital,
            position_pct=body.position_pct,
            stop_loss_pct=body.stop_loss_pct,
            target_pct=body.target_pct,
            allow_short=body.allow_short,
            costs=backtester.Costs(
                brokerage_per_order=body.brokerage_per_order,
                slippage_pct=body.slippage_pct,
            ),
        )
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc

    meta = instruments.by_key(body.instrument_key) or {}
    return {
        "symbol": meta.get("symbol") or body.instrument_key,
        "interval": body.interval,
        "candles": candles,
        "stats": res.stats,
        "trades": [t.to_json() for t in res.trades],
        "equity": res.equity,
        "signals": res.signals,
        "overlays": res.overlays,
    }


# ===================================================================== bot
class BotBody(BaseModel):
    strategy: str
    instrument_key: str
    interval: str = "5m"
    quantity: int = 1
    product: str = "I"
    params: dict = Field(default_factory=dict)
    stop_loss_pct: float = 0
    target_pct: float = 0
    max_trades_per_day: int = 10
    square_off_at_close: bool = True
    only_market_hours: bool = True


@router.get("/bots")
async def bots_list() -> dict:
    return {"active": runner.active(), "history": runner.history(20)}


@router.post("/bots")
async def bots_start(body: BotBody) -> dict:
    if not client.access_token():
        raise HTTPException(401, "Pehle Upstox me login karein.")
    try:
        state = await runner.start(runner.BotConfig(**body.model_dump()))
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return state.to_json()


@router.delete("/bots/{run_id}")
async def bots_stop(run_id: int) -> dict:
    ok = await runner.stop(run_id)
    if not ok:
        raise HTTPException(404, "Bot nahi mila.")
    return {"ok": True}


@router.get("/bots/{run_id}/logs")
async def bots_logs(run_id: int, limit: int = 200) -> list[dict]:
    return runner.logs(run_id, limit)
