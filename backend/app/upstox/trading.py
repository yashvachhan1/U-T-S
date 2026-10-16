"""Order placement and portfolio reads.

Everything that can spend real money funnels through `place_order`, which
refuses to call Upstox at all while PAPER_TRADING is on.

The order itself is built and guarded in orders.py; this module adds the parts
that need the outside world -- the live price, the rate limiter, and the broker
call.
"""
from __future__ import annotations

from typing import Any

from .. import config, db, ratelimit
from . import client, instruments, marketdata, orders

# Re-exported so existing callers keep working.
VALID_SIDES = orders.VALID_SIDES
VALID_PRODUCTS = orders.VALID_PRODUCTS
VALID_ORDER_TYPES = orders.VALID_ORDER_TYPES


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
    """Build, guard, rate-limit and send one order.

    `tag` is kept for callers that want to label a source; the tag actually
    sent is the idempotency key, so a retry cannot create a second position.
    """
    req = orders.OrderRequest(
        instrument_key=instrument_key,
        side=side,
        quantity=quantity,
        order_type=order_type,
        product=product,
        price=price,
        trigger_price=trigger_price,
        validity=validity,
        source=source or tag,
    )

    # A live price is needed to convert MARKET to a marketable limit, and to
    # sanity-check any limit price we were handed.
    ltp = await _reference_price(instrument_key, price or trigger_price)

    try:
        built = orders.build(req, ltp)
    except orders.OrderRejected as exc:
        raise client.UpstoxError(str(exc)) from exc

    # Daily cap first: it is a hard stop, not something to queue behind.
    try:
        ratelimit.daily_orders.check()
    except ratelimit.RateLimitExceeded as exc:
        raise client.UpstoxError(str(exc)) from exc

    # Then the per-second bucket. Waiting here is correct -- being a few
    # hundred ms late beats breaching the exchange limit.
    await ratelimit.orders.acquire()

    symbol = _symbol_for(instrument_key)

    # ---------------- paper mode: record locally, touch nothing live ----
    if config.PAPER_TRADING:
        ratelimit.daily_orders.record()
        order_id = db.execute(
            """INSERT INTO paper_orders
               (created_at, instrument_key, symbol, side, quantity, price,
                order_type, product, status, source, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                db.now(), instrument_key, symbol, built.payload["transaction_type"],
                built.payload["quantity"], built.limit_price,
                built.payload["order_type"], built.payload["product"],
                "complete", source, built.tag,
            ),
        )
        return {
            "paper": True,
            "order_id": f"PAPER-{order_id}",
            "status": "complete",
            "price": built.limit_price,
            "tag": built.tag,
            "converted_from": built.converted_from,
            "notes": built.notes,
            "message": f"Paper order: {orders.describe(built)}",
        }

    # ---------------- live mode -----------------------------------------
    # A duplicate tag means this exact intent already went out -- almost always
    # a retry after a timeout. Returning the existing order beats placing a second.
    existing = await _find_order_by_tag(built.tag)
    if existing:
        return {
            "paper": False,
            "order_id": existing.get("order_id"),
            "status": "duplicate",
            "price": built.limit_price,
            "tag": built.tag,
            "message": f"Ye order pehle se bheja ja chuka hai (tag {built.tag}).",
        }

    ratelimit.daily_orders.record()
    data = await client.post(
        "/v2/order/place",
        json_body=built.payload,
        extra_headers=orders.algo_headers(),
    )
    return {
        "paper": False,
        "order_id": (data or {}).get("order_id"),
        "status": "submitted",
        "price": built.limit_price,
        "tag": built.tag,
        "converted_from": built.converted_from,
        "notes": built.notes,
        "message": f"Live order bheja: {orders.describe(built)}",
    }


async def _find_order_by_tag(tag: str) -> dict | None:
    """Look for an order we already placed with this idempotency tag."""
    try:
        book = await client.get("/v2/order/retrieve-all") or []
    except client.UpstoxError:
        # If the book is unreadable we cannot prove it is a duplicate. Let the
        # order through rather than silently dropping a real one.
        return None
    for row in book:
        if row.get("tag") == tag:
            return row
    return None


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
