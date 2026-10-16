"""Order placement and portfolio reads.

Everything that can spend real money funnels through `place_order`, which
refuses to call Upstox at all while PAPER_TRADING is on.
"""
from __future__ import annotations

from typing import Any

from .. import config, db
from . import client, instruments, marketdata

VALID_SIDES = {"BUY", "SELL"}
VALID_PRODUCTS = {"I", "D", "MTF"}           # Intraday / Delivery / MTF
VALID_ORDER_TYPES = {"MARKET", "LIMIT", "SL", "SL-M"}


def _symbol_for(instrument_key: str) -> str:
    row = instruments.by_key(instrument_key)
    return row["symbol"] if row else instrument_key


async def _reference_price(instrument_key: str, given: float) -> float:
    if given:
        return float(given)
    try:
        data = await marketdata.ltp([instrument_key])
        hit = data.get(instrument_key)
        if hit and hit.get("ltp"):
            return float(hit["ltp"])
    except client.UpstoxError:
        pass
    return 0.0


async def place_order(
    *,
    instrument_key: str,
    side: str,
    quantity: int,
    order_type: str = "MARKET",
    product: str = "D",
    price: float = 0.0,
    trigger_price: float = 0.0,
    validity: str = "DAY",
    tag: str = "manual",
    source: str = "manual",
) -> dict[str, Any]:
    side = side.upper()
    order_type = order_type.upper()
    product = product.upper()

    if side not in VALID_SIDES:
        raise client.UpstoxError(f"Side BUY ya SELL hona chahiye, mila: {side}")
    if product not in VALID_PRODUCTS:
        raise client.UpstoxError(f"Product I, D ya MTF hona chahiye, mila: {product}")
    if order_type not in VALID_ORDER_TYPES:
        raise client.UpstoxError(f"Order type galat hai: {order_type}")
    quantity = int(quantity)
    if quantity <= 0:
        raise client.UpstoxError("Quantity 0 se zyada honi chahiye.")
    if order_type in ("LIMIT", "SL") and not price:
        raise client.UpstoxError(f"{order_type} order ke liye price dena zaroori hai.")
    if order_type in ("SL", "SL-M") and not trigger_price:
        raise client.UpstoxError(f"{order_type} order ke liye trigger price zaroori hai.")

    # ---------------- paper mode: record locally, touch nothing live -----
    if config.PAPER_TRADING:
        fill_price = await _reference_price(instrument_key, price or trigger_price)
        order_id = db.execute(
            """INSERT INTO paper_orders
               (created_at, instrument_key, symbol, side, quantity, price,
                order_type, product, status, source, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                db.now(),
                instrument_key,
                _symbol_for(instrument_key),
                side,
                quantity,
                fill_price,
                order_type,
                product,
                "complete",
                source,
                tag,
            ),
        )
        return {
            "paper": True,
            "order_id": f"PAPER-{order_id}",
            "status": "complete",
            "price": fill_price,
            "message": f"Paper order: {side} {quantity} x {_symbol_for(instrument_key)} @ {fill_price}",
        }

    # ---------------- live mode -------------------------------------------
    body = {
        "quantity": quantity,
        "product": product,
        "validity": validity,
        "price": float(price or 0),
        "tag": tag[:20],
        "instrument_token": instrument_key,
        "order_type": order_type,
        "transaction_type": side,
        "disclosed_quantity": 0,
        "trigger_price": float(trigger_price or 0),
        "is_amo": False,
    }
    data = await client.post("/v2/order/place", json_body=body)
    return {
        "paper": False,
        "order_id": (data or {}).get("order_id"),
        "status": "submitted",
        "message": f"Live order bheja: {side} {quantity} x {_symbol_for(instrument_key)}",
    }


async def cancel_order(order_id: str) -> dict:
    if config.PAPER_TRADING or str(order_id).startswith("PAPER-"):
        raise client.UpstoxError("Paper orders turant fill ho jaate hain, cancel nahi hote.")
    return await client.delete("/v2/order/cancel", params={"order_id": order_id})


async def modify_order(
    order_id: str,
    *,
    quantity: int | None = None,
    price: float | None = None,
    order_type: str | None = None,
    trigger_price: float | None = None,
    validity: str = "DAY",
) -> dict:
    if config.PAPER_TRADING:
        raise client.UpstoxError("Paper mode me order modify nahi hota.")
    body: dict[str, Any] = {"order_id": order_id, "validity": validity}
    if quantity is not None:
        body["quantity"] = int(quantity)
    if price is not None:
        body["price"] = float(price)
    if order_type is not None:
        body["order_type"] = order_type.upper()
    if trigger_price is not None:
        body["trigger_price"] = float(trigger_price)
    return await client.put("/v2/order/modify", json_body=body)


async def order_book() -> list[dict]:
    if config.PAPER_TRADING:
        rows = db.query("SELECT * FROM paper_orders ORDER BY id DESC LIMIT 200")
        return [
            {
                "order_id": f"PAPER-{r['id']}",
                "order_timestamp": r["created_at"],
                "tradingsymbol": r["symbol"],
                "instrument_token": r["instrument_key"],
                "transaction_type": r["side"],
                "quantity": r["quantity"],
                "filled_quantity": r["quantity"],
                "price": r["price"],
                "average_price": r["price"],
                "order_type": r["order_type"],
                "product": r["product"],
                "status": r["status"],
                "tag": r["source"],
            }
            for r in rows
        ]
    return await client.get("/v2/order/retrieve-all") or []


async def trades_today() -> list[dict]:
    if config.PAPER_TRADING:
        return await order_book()
    return await client.get("/v2/order/trades/get-trades-for-day") or []


async def positions() -> list[dict]:
    """Live positions from Upstox, or positions derived from paper fills."""
    if not config.PAPER_TRADING:
        return await client.get("/v2/portfolio/short-term-positions") or []

    rows = db.query("SELECT * FROM paper_orders WHERE status = 'complete'")
    agg: dict[str, dict] = {}
    for r in rows:
        k = r["instrument_key"]
        a = agg.setdefault(
            k,
            {
                "instrument_token": k,
                "tradingsymbol": r["symbol"],
                "product": r["product"],
                "quantity": 0,
                "_buy_qty": 0,
                "_buy_val": 0.0,
                "_sell_qty": 0,
                "_sell_val": 0.0,
            },
        )
        signed = r["quantity"] if r["side"] == "BUY" else -r["quantity"]
        a["quantity"] += signed
        if r["side"] == "BUY":
            a["_buy_qty"] += r["quantity"]
            a["_buy_val"] += r["quantity"] * r["price"]
        else:
            a["_sell_qty"] += r["quantity"]
            a["_sell_val"] += r["quantity"] * r["price"]

    open_keys = [k for k, a in agg.items() if a["quantity"] != 0]
    prices = await marketdata.ltp(open_keys) if open_keys else {}

    out = []
    for k, a in agg.items():
        qty = a["quantity"]
        avg = a["_buy_val"] / a["_buy_qty"] if a["_buy_qty"] else 0.0
        last = (prices.get(k) or {}).get("ltp") or avg
        realised = a["_sell_val"] - avg * a["_sell_qty"] if a["_sell_qty"] else 0.0
        unrealised = (last - avg) * qty if qty else 0.0
        if qty == 0 and realised == 0:
            continue
        out.append(
            {
                "instrument_token": k,
                "tradingsymbol": a["tradingsymbol"],
                "product": a["product"],
                "quantity": qty,
                "average_price": round(avg, 2),
                "last_price": round(last, 2),
                "pnl": round(realised + unrealised, 2),
                "unrealised": round(unrealised, 2),
                "realised": round(realised, 2),
                "paper": True,
            }
        )
    return out


async def holdings() -> list[dict]:
    if config.PAPER_TRADING:
        return []
    return await client.get("/v2/portfolio/long-term-holdings") or []


async def funds() -> dict:
    if config.PAPER_TRADING:
        return {"paper": True, "note": "Paper mode -- real funds nahi dikhte."}
    return await client.get("/v2/user/get-funds-and-margin") or {}


async def profile() -> dict:
    return await client.get("/v2/user/profile") or {}
