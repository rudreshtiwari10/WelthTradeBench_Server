"""Tradomate backend — FastAPI.

REST proxy (history/search/quote/broker) + Upstox OAuth + a /ws live-tick relay.
Serves mock data whenever Upstox credentials/token are absent, so the chart is
always live and demoable. When authenticated, /api/broker/* routes call the real
Upstox V2 API for funds, positions, orders, and order placement.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .config import CLIENT_URL, SANDBOX, credentials_present, tokens
from .instruments import Instrument, by_symbol, search
from .mock.generator import (
    generate_candles, generate_option_candles,
    generate_futures_candles, option_premium_py,
)
from .upstox import auth, rest
from .upstox.feed import hub


# ── Pydantic request models ───────────────────────────────────────────────

class PlaceOrderBody(BaseModel):
    instrument_key: str
    qty: int
    transaction_type: str          # "BUY" | "SELL"
    order_type: str = "MARKET"     # "MARKET" | "LIMIT" | "SL" | "SL-M"
    price: float = 0.0
    product: str = "D"             # "D" = NRML, "I" = MIS
    trigger_price: float = 0.0

app = FastAPI(title="Tradomate API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[CLIENT_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "mode": hub.mode}


@app.get("/api/auth/status")
async def auth_status() -> dict:
    return {
        "credentialsPresent": credentials_present(),
        "authenticated": tokens.authenticated,
        "mode": hub.mode,
    }


# ── OAuth ───────────────────────────────────────────────────────────────
@app.get("/auth/login")
async def login() -> RedirectResponse:
    if not credentials_present():
        return RedirectResponse(f"{CLIENT_URL}/?auth=missing_credentials")
    return RedirectResponse(auth.login_url())


@app.get("/auth/callback")
async def callback(code: str | None = None, state: str | None = None) -> RedirectResponse:
    if not code:
        return RedirectResponse(f"{CLIENT_URL}/?auth=error")
    try:
        token = await auth.exchange_code(code)
        tokens.set(token)
        return RedirectResponse(f"{CLIENT_URL}/?auth=success")
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] token exchange failed: {exc}")
        return RedirectResponse(f"{CLIENT_URL}/?auth=error")


@app.get("/")
async def root(code: str | None = None, state: str | None = None):
    """Fallback OAuth callback for apps registered with the bare origin
    (e.g. redirect URL = http://localhost:8000). If Upstox redirects here with
    a ?code=, exchange it; otherwise just report status."""
    if code:
        try:
            tokens.set(await auth.exchange_code(code))
            return RedirectResponse(f"{CLIENT_URL}/?auth=success")
        except Exception as exc:  # noqa: BLE001
            print(f"[auth] token exchange failed (root): {exc}")
            return RedirectResponse(f"{CLIENT_URL}/?auth=error")
    return {"service": "tradomate", "mode": hub.mode, "authenticated": tokens.authenticated}


@app.post("/api/auth/logout")
async def logout() -> dict:
    tokens.clear()
    return {"ok": True}


# ── Symbol search ─────────────────────────────────────────────────────────
@app.get("/api/search")
async def search_symbols(q: str = Query("", alias="q")) -> dict:
    results = [
        {"symbol": i.symbol, "name": i.name, "exchange": i.exchange, "kind": i.kind}
        for i in search(q)
    ]
    return {"results": results}


# ── Historical candles ──────────────────────────────────────────────────
@app.get("/api/history")
async def history(
    symbol: str,
    interval: str = "1D",
    count: int = 600,
    instrument_key: str | None = None,
) -> dict:
    count = max(50, min(count, 2000))
    candles: list[dict] = []
    info: dict | None = None
    source = "mock"

    # ── Direct instrument-key path (options / futures) ────────────────
    if instrument_key:
        if instrument_key.startswith("MOCK:option:"):
            # MOCK:option:{underlying}:{strike}:{type}:{expiry}
            try:
                _, _, ul, strike_s, otype, expiry = instrument_key.split(":")
                candles = generate_option_candles(ul, float(strike_s), otype, expiry, interval, count)
                info = {"symbol": symbol, "name": symbol, "exchange": "NSE_FO", "kind": "option"}
            except Exception as exc:  # noqa: BLE001
                print(f"[history] mock-option parse failed: {exc}")
        elif instrument_key.startswith("MOCK:future:"):
            # MOCK:future:{underlying}:{expiry}
            try:
                _, _, ul, expiry = instrument_key.split(":")
                candles = generate_futures_candles(ul, expiry, interval, count)
                info = {"symbol": symbol, "name": symbol, "exchange": "NSE_FO", "kind": "future"}
            except Exception as exc:  # noqa: BLE001
                print(f"[history] mock-future parse failed: {exc}")
        elif hub.mode == "upstox":
            # Real Upstox instrument key (e.g. "NSE_FO|141398")
            try:
                tmp = Instrument(
                    symbol=symbol, name=symbol,
                    exchange="NSE_FO", instrument_key=instrument_key, kind="option",
                )
                candles = await rest.historical_candles(tmp, interval, count)
                info = {"symbol": symbol, "name": symbol, "exchange": "NSE_FO", "kind": "option"}
                source = "upstox"
            except Exception as exc:  # noqa: BLE001
                print(f"[history] direct-key Upstox failed ({instrument_key}): {exc}")

    # ── Symbol-based fallback ─────────────────────────────────────────
    if not candles:
        inst = by_symbol(symbol)
        if inst and hub.mode == "upstox":
            try:
                candles = await rest.historical_candles(inst, interval, count)
                source = "upstox"
            except Exception as exc:  # noqa: BLE001
                print(f"[history] Upstox fetch failed ({symbol} {interval}); using mock: {exc}")
        if not candles:
            candles = generate_candles(symbol, interval, count)
        if info is None:
            info = (
                {"symbol": inst.symbol, "name": inst.name, "exchange": inst.exchange, "kind": inst.kind}
                if inst else {"symbol": symbol, "name": symbol, "exchange": "NSE", "kind": "stock"}
            )

    return {"symbol": symbol, "interval": interval, "source": source, "info": info or {}, "candles": candles}


# ── Quote ─────────────────────────────────────────────────────────────────
@app.get("/api/quote")
async def quote(symbol: str) -> dict:
    inst = by_symbol(symbol)
    if inst and hub.mode == "upstox":
        try:
            q = await rest.ltp(inst)
            if q:
                return {"symbol": symbol, "source": "upstox", **q}
        except Exception as exc:  # noqa: BLE001
            print(f"[quote] Upstox fetch failed: {exc}")
    last = generate_candles(symbol, "1D", 2)[-1]
    return {"symbol": symbol, "source": "mock", "ltp": last["close"], "ts": last["time"]}


# ── Live feed WebSocket ────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    await hub.add_client(websocket)
    await websocket.send_json({"type": "hello", "mode": hub.mode})
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("type")
            symbol = data.get("symbol", "")
            if action == "sub" and symbol:
                await hub.subscribe(websocket, symbol)
            elif action == "unsub" and symbol:
                await hub.unsubscribe(websocket, symbol)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.remove_client(websocket)


# ═══════════════════════════════════════════════════════════════════════════
# BROKER API  (/api/broker/*)
# All endpoints return {"source": "upstox"|"paper", "sandbox": bool, ...}
# In paper/mock mode they return empty scaffolds so the frontend can stay
# in paper-trading mode without crashing.
# ═══════════════════════════════════════════════════════════════════════════

def _broker_check() -> None:
    """Raise 403 if not authenticated with Upstox."""
    if hub.mode != "upstox":
        raise HTTPException(status_code=403, detail="Not authenticated with Upstox")


@app.get("/api/broker/status")
async def broker_status() -> dict:
    return {
        "mode": hub.mode,
        "authenticated": tokens.authenticated,
        "sandbox": SANDBOX,
        "credentialsPresent": credentials_present(),
    }


# ── Funds ─────────────────────────────────────────────────────────────────

@app.get("/api/broker/funds")
async def broker_funds() -> dict:
    if hub.mode != "upstox":
        return {"source": "paper", "sandbox": False, "equity": {}, "commodity": {}}
    try:
        data = await rest.get_funds()
        return {"source": "upstox", "sandbox": SANDBOX, **data}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker] funds error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Positions ─────────────────────────────────────────────────────────────

@app.get("/api/broker/positions")
async def broker_positions() -> dict:
    if hub.mode != "upstox":
        return {"source": "paper", "sandbox": False, "positions": []}
    try:
        data = await rest.get_positions()
        return {"source": "upstox", "sandbox": SANDBOX, "positions": data}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker] positions error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Orders ────────────────────────────────────────────────────────────────

@app.get("/api/broker/orders")
async def broker_orders() -> dict:
    if hub.mode != "upstox":
        return {"source": "paper", "sandbox": False, "orders": []}
    try:
        data = await rest.get_orders()
        return {"source": "upstox", "sandbox": SANDBOX, "orders": data}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker] orders error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Place order ───────────────────────────────────────────────────────────

@app.post("/api/broker/order")
async def broker_place_order(body: PlaceOrderBody) -> dict:
    _broker_check()
    try:
        data = await rest.place_order(
            instrument_key=body.instrument_key,
            qty=body.qty,
            transaction_type=body.transaction_type,
            order_type=body.order_type,
            price=body.price,
            product=body.product,
            trigger_price=body.trigger_price,
        )
        return {"source": "upstox", "sandbox": SANDBOX, **data}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker] place_order error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Cancel order ──────────────────────────────────────────────────────────

@app.delete("/api/broker/order/{order_id}")
async def broker_cancel_order(order_id: str) -> dict:
    _broker_check()
    try:
        data = await rest.cancel_order(order_id)
        return {"source": "upstox", "sandbox": SANDBOX, **data}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker] cancel_order error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Option chain ──────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════════════
# DERIVATIVES  (/api/derivatives/*)
# Separate from /api/broker/* — these work in BOTH mock and upstox mode.
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/derivatives/chain")
async def derivatives_chain(underlying: str, expiry: str) -> dict:
    """Option chain for an underlying + expiry.
    Upstox mode: real chain with live LTPs and instrument keys.
    Mock mode: synthetic chain via Black-Scholes with MOCK: instrument keys."""
    inst = by_symbol(underlying)

    if hub.mode == "upstox" and inst:
        try:
            raw = await rest.get_option_chain(inst.instrument_key, expiry)
            chains = []
            for row in raw:
                call = row.get("call_options") or {}
                put  = row.get("put_options")  or {}
                cmd  = call.get("market_data") or {}
                pmd  = put.get("market_data")  or {}
                chains.append({
                    "strike":  row.get("strike_price"),
                    "expiry":  row.get("expiry"),
                    "callKey": call.get("instrument_key"),
                    "callLtp": cmd.get("ltp") or 0,
                    "callBid": cmd.get("bid_price") or 0,
                    "callAsk": cmd.get("ask_price") or 0,
                    "callOi":  cmd.get("oi") or 0,
                    "putKey":  put.get("instrument_key"),
                    "putLtp":  pmd.get("ltp") or 0,
                    "putBid":  pmd.get("bid_price") or 0,
                    "putAsk":  pmd.get("ask_price") or 0,
                    "putOi":   pmd.get("oi") or 0,
                })
            spot = raw[0].get("underlying_spot_price", 0) if raw else 0
            return {"source": "upstox", "sandbox": SANDBOX, "spot": spot, "chains": chains}
        except Exception as exc:  # noqa: BLE001
            print(f"[derivatives] chain upstox error: {exc}")

    # Mock synthetic chain
    spot_candles = generate_candles(underlying, "1D", 2)
    spot = spot_candles[-1]["close"]

    import datetime as _dt
    try:
        expiry_dt = _dt.datetime.strptime(expiry, "%Y-%m-%d")
        days_left = max(1.0, (expiry_dt.date() - _dt.datetime.utcnow().date()).days)
    except ValueError:
        days_left = 7.0

    step = 100 if underlying.upper() in ("BANKNIFTY", "SENSEX", "BANKEX") else 50
    atm = round(spot / step) * step
    chains = []
    for i in range(-12, 13):
        k = atm + i * step
        cl = option_premium_py(spot, k, "CE", days_left)
        pl = option_premium_py(spot, k, "PE", days_left)
        chains.append({
            "strike":  k,
            "expiry":  expiry,
            "callKey": f"MOCK:option:{underlying}:{k}:CE:{expiry}",
            "callLtp": cl,
            "callBid": round(cl * 0.998, 2),
            "callAsk": round(cl * 1.002, 2),
            "callOi":  0,
            "putKey":  f"MOCK:option:{underlying}:{k}:PE:{expiry}",
            "putLtp":  pl,
            "putBid":  round(pl * 0.998, 2),
            "putAsk":  round(pl * 1.002, 2),
            "putOi":   0,
        })
    return {"source": "mock", "sandbox": False, "spot": spot, "chains": chains}


@app.get("/api/derivatives/futures")
async def derivatives_futures(underlying: str) -> dict:
    """List of futures contracts (next 3 monthly expiries) for an underlying.
    Always returns data in both mock and upstox mode."""
    import datetime as _dt, calendar as _cal

    inst = by_symbol(underlying)
    spot_candles = generate_candles(underlying, "1D", 2)
    spot = spot_candles[-1]["close"]

    today = _dt.datetime.utcnow().date()
    futures = []

    for months_ahead in range(3):
        yr  = today.year + (today.month + months_ahead - 1) // 12
        mon = (today.month + months_ahead - 1) % 12 + 1
        last_day = _cal.monthrange(yr, mon)[1]
        # Last Thursday of the month
        dt = _dt.datetime(yr, mon, last_day)
        while dt.weekday() != 3:
            dt -= _dt.timedelta(days=1)

        expiry_str   = dt.strftime("%Y-%m-%d")
        expiry_label = dt.strftime("%d %b %Y")
        days_left    = max(1, (dt.date() - today).days)
        fut_price    = round(spot * (1.0 + 0.065 * days_left / 365.0), 2)

        futures.append({
            "symbol":        f"{underlying}FUT",
            "name":          f"{underlying} Futures {expiry_label}",
            "exchange":      inst.exchange if inst else "NSE",
            "expiry":        expiry_str,
            "expiryLabel":   expiry_label,
            "ltp":           fut_price,
            "instrumentKey": f"MOCK:future:{underlying}:{expiry_str}",
            "kind":          "future",
        })

    return {"source": hub.mode, "underlying": underlying, "futures": futures}


@app.get("/api/broker/option-chain")
async def broker_option_chain(underlying: str, expiry: str) -> dict:
    if hub.mode != "upstox":
        return {"source": "paper", "sandbox": False, "chains": []}
    inst = by_symbol(underlying)
    if not inst:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {underlying}")
    try:
        raw = await rest.get_option_chain(inst.instrument_key, expiry)
        chains = []
        for row in raw:
            call = row.get("call_options") or {}
            put = row.get("put_options") or {}
            call_md = call.get("market_data") or {}
            put_md = put.get("market_data") or {}
            chains.append({
                "strike": row.get("strike_price"),
                "expiry": row.get("expiry"),
                "callKey": call.get("instrument_key"),
                "callLtp": call_md.get("ltp") or 0,
                "callBid": call_md.get("bid_price") or 0,
                "callAsk": call_md.get("ask_price") or 0,
                "callOi":  call_md.get("oi") or 0,
                "callVol": call_md.get("volume") or 0,
                "putKey":  put.get("instrument_key"),
                "putLtp":  put_md.get("ltp") or 0,
                "putBid":  put_md.get("bid_price") or 0,
                "putAsk":  put_md.get("ask_price") or 0,
                "putOi":   put_md.get("oi") or 0,
                "putVol":  put_md.get("volume") or 0,
            })
        return {"source": "upstox", "sandbox": SANDBOX, "chains": chains}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker] option-chain error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
