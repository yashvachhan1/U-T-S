"""All HTTP endpoints for the dashboard."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import config, db
from ..engine import backtester, runner
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
