"""Tradomate backend — FastAPI.

REST proxy (history/search/quote/broker) + Upstox OAuth + a /ws live-tick relay.
Serves mock data whenever Upstox credentials/token are absent, so the chart is
always live and demoable. When authenticated, /api/broker/* routes call the real
Upstox V2 API for funds, positions, orders, and order placement.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .config import (
    CLIENT_URL, SANDBOX, credentials_present, tokens,
    kite_credentials_present, kite_tokens,
)
from .database import connect_db, close_db
from .instruments import Instrument, by_symbol, search
from .mcx_instruments import refresh as _mcx_refresh, active_key as _mcx_active_key
from .routers import auth as _auth_router, layouts as _layouts_router, drawings as _drawings_router, admin as _admin_router
from .historical.router import router as _historical_router
from .historical.config import is_stored_symbol
from .historical.store import get_chart_candles
from .mock.generator import (
    generate_candles, generate_option_candles,
    generate_futures_candles, option_premium_py,
)
from .upstox import auth, rest
from .upstox.feed import hub
from .kite import auth as kite_auth, rest as kite_rest, instruments as kite_instruments


# ── Pydantic request models ───────────────────────────────────────────────

class PlaceOrderBody(BaseModel):
    instrument_key: str | None = None  # Upstox addressing
    qty: int
    transaction_type: str          # "BUY" | "SELL"
    order_type: str = "MARKET"     # "MARKET" | "LIMIT" | "SL" | "SL-M"
    price: float = 0.0
    product: str = "D"             # "D" = NRML/CNC, "I" = MIS
    trigger_price: float = 0.0
    broker: str = "upstox"         # "upstox" | "kite"
    segment: str = "option"        # "option" | "future" | "equity"
    # Kite contract addressing:
    tradingsymbol: str | None = None
    exchange: str | None = None
    underlying: str | None = None
    expiry: str | None = None      # YYYY-MM-DD
    strike: float | None = None
    option_type: str | None = None  # "CE" | "PE"

app = FastAPI(title="Tradomate API", version="0.3.0")

# CORS: CLIENT_URL may be comma-separated (e.g. "http://localhost:5173,https://app.vercel.app").
# Set CORS_ORIGINS env var to override entirely.
_raw_cors = os.getenv("CORS_ORIGINS", CLIENT_URL)
_cors_origins = [o.strip() for o in _raw_cors.split(",") if o.strip()] or [CLIENT_URL]

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # Allow every Vercel deployment (production + preview URLs) without having to
    # enumerate each one in CORS_ORIGINS.  Explicit origins above still apply.
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(_auth_router.router)
app.include_router(_layouts_router.router)
app.include_router(_drawings_router.router)
app.include_router(_admin_router.router)
app.include_router(_historical_router)


@app.on_event("startup")
async def _startup() -> None:
    """Connect to MongoDB and pre-fetch MCX active contract keys."""
    try:
        await connect_db()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] MongoDB connection failed: {exc}")
    try:
        await _mcx_refresh()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] MCX instrument prefetch failed (will retry on first subscribe): {exc}")
    try:
        from .historical.scheduler import start_scheduler
        start_scheduler()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] Historical EOD scheduler failed to start: {exc}")


@app.on_event("shutdown")
async def _shutdown() -> None:
    try:
        from .historical.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:  # noqa: BLE001
        pass
    await close_db()


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "mode": hub.mode}


@app.get("/api/time")
async def server_time() -> dict:
    """Return the server's current Unix time in milliseconds.

    Clients use this for NTP-style clock-skew correction so candle boundaries
    are computed from accurate market time rather than the user's local clock.
    """
    import time as _time
    return {"serverTime": int(_time.time() * 1000)}


@app.post("/api/mcx/refresh")
async def mcx_refresh() -> dict:
    """Force-refresh the MCX active contract key cache from Upstox CDN.
    Useful after a monthly contract rollover."""
    from .mcx_instruments import refresh as _r, active_keys
    keys = await _r()
    return {"ok": True, "contracts": keys}


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


# ── Kite (Zerodha) OAuth ───────────────────────────────────────────────────

def _oauth_done(qparam: str, value: str) -> HTMLResponse:
    """Popup-aware OAuth responder.

    When login was opened in a popup (the in-app "Authenticate" button) it
    notifies the opener via postMessage and closes itself; when it was a plain
    top-level redirect it navigates back to the SPA with a ?<qparam>=<value>
    marker. The frontend popup flow depends on the {type:'broker-auth', ...}
    message — keep it verbatim.
    """
    redirect = f"{CLIENT_URL}/?{qparam}={value}"
    html = (
        "<!doctype html><meta charset='utf-8'><title>Authentication</title>"
        "<script>(function(){"
        "try{if(window.opener){window.opener.postMessage("
        "{type:'broker-auth',param:'" + qparam + "',status:'" + value + "'},'*');}}catch(e){}"
        "if(window.opener){try{window.close();}catch(e){}}"
        "setTimeout(function(){location.replace('" + redirect + "');},200);"
        "})();</script>"
        "<p style='font:14px sans-serif;padding:24px'>"
        "Authentication complete — you can close this window.</p>"
    )
    return HTMLResponse(html)


@app.get("/auth/kite/login")
async def kite_login() -> RedirectResponse:
    if not kite_credentials_present():
        return RedirectResponse(f"{CLIENT_URL}/?kite_auth=missing_credentials")
    return RedirectResponse(kite_auth.login_url())


@app.get("/auth/kite/callback")
async def kite_callback(request_token: str | None = None, status: str | None = None):
    """Kite redirects here with ?request_token=...&status=success after login."""
    if not request_token:
        return _oauth_done("kite_auth", "error")
    try:
        kite_tokens.set(await kite_auth.exchange_token(request_token))
        return _oauth_done("kite_auth", "success")
    except Exception as exc:  # noqa: BLE001
        print(f"[kite] token exchange failed: {exc}")
        return _oauth_done("kite_auth", "error")


@app.post("/api/auth/kite/logout")
async def kite_logout() -> dict:
    kite_tokens.clear()
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
    before_ts: int | None = None,
) -> dict:
    count = max(50, min(count, 5000))
    candles: list[dict] = []
    info: dict | None = None
    source = "mock"
    source_warning: str | None = None   # surfaced to frontend when Upstox falls back to mock

    # ── Stored-symbol path: serve old candles from the local 1m/1D store ──
    # Old candles come from MongoDB (aggregated to the requested interval); only
    # today's in-progress session is fetched live and merged on top.  before_ts
    # drives lazy scroll-back paging (older bars only).
    if not instrument_key and is_stored_symbol(symbol):
        try:
            stored = await get_chart_candles(symbol, interval, count, before_ts)
        except Exception as exc:  # noqa: BLE001
            print(f"[history] store read failed ({symbol} {interval}): {exc}")
            stored = []
        # Use the store when it has data, OR for any paging request (a paging
        # request with no data simply means "no older history" → return empty).
        if stored or before_ts is not None:
            if stored and before_ts is None and hub.mode == "upstox":
                inst0 = by_symbol(symbol)
                if inst0:
                    today = await rest.intraday_supplement(inst0, interval)
                    if today:
                        merged = {c["time"]: c for c in stored}
                        for c in today:
                            merged[c["time"]] = c
                        stored = sorted(merged.values(), key=lambda c: c["time"])
            inst0 = by_symbol(symbol)
            info0 = (
                {"symbol": inst0.symbol, "name": inst0.name, "exchange": inst0.exchange, "kind": inst0.kind}
                if inst0 else {"symbol": symbol, "name": symbol, "exchange": "NSE", "kind": "index"}
            )
            return {
                "symbol": symbol, "interval": interval,
                "source": "historical_store", "source_warning": None,
                "info": info0, "candles": stored,
            }
        # Store not yet downloaded for this symbol → fall through to live/mock.

    # ── Direct instrument-key path (options / futures) ────────────────
    if instrument_key:
        if instrument_key.startswith("MOCK:option:"):
            try:
                _, _, ul, strike_s, otype, expiry = instrument_key.split(":")
                candles = generate_option_candles(ul, float(strike_s), otype, expiry, interval, count)
                info = {"symbol": symbol, "name": symbol, "exchange": "NSE_FO", "kind": "option"}
            except Exception as exc:  # noqa: BLE001
                print(f"[history] mock-option parse failed: {exc}")
        elif instrument_key.startswith("MOCK:future:"):
            try:
                _, _, ul, expiry = instrument_key.split(":")
                candles = generate_futures_candles(ul, expiry, interval, count)
                info = {"symbol": symbol, "name": symbol, "exchange": "NSE_FO", "kind": "future"}
            except Exception as exc:  # noqa: BLE001
                print(f"[history] mock-future parse failed: {exc}")
        elif hub.mode == "upstox":
            try:
                tmp = Instrument(
                    symbol=symbol, name=symbol,
                    exchange="NSE_FO", instrument_key=instrument_key, kind="option",
                )
                candles = await rest.historical_candles(tmp, interval, count)
                info = {"symbol": symbol, "name": symbol, "exchange": "NSE_FO", "kind": "option"}
                source = "upstox"
            except Exception as exc:  # noqa: BLE001
                reason = str(exc)
                print(f"[history] direct-key Upstox failed ({instrument_key}): {reason}")
                source_warning = f"Upstox unavailable for this instrument ({interval}): {reason}"

    # ── Symbol-based fallback ─────────────────────────────────────────
    if not candles:
        inst = by_symbol(symbol)
        if inst and hub.mode == "upstox":
            # MCX instruments are stored with placeholder keys (e.g. "MCX_FO|GOLD").
            # The real front-month contract key must be resolved before calling Upstox.
            if inst.kind == "commodity":
                real_key = _mcx_active_key(symbol)
                if not real_key:
                    try:
                        await _mcx_refresh()
                        real_key = _mcx_active_key(symbol)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[history] MCX key resolution failed for {symbol}: {exc}")
                if real_key:
                    inst = Instrument(
                        symbol=inst.symbol, name=inst.name, exchange=inst.exchange,
                        instrument_key=real_key, kind=inst.kind,
                    )
                else:
                    print(f"[history] No active MCX contract for {symbol} — falling back to mock")
                    inst = None  # triggers mock fallback below
            if inst:
                try:
                    candles = await rest.historical_candles(inst, interval, count)
                    source = "upstox"
                except Exception as exc:  # noqa: BLE001
                    reason = str(exc)
                    print(f"[history] Upstox fetch failed ({symbol} {interval}): {reason}")
                    source_warning = f"Upstox data unavailable for {symbol} {interval}: {reason}"
        if not candles:
            candles = generate_candles(symbol, interval, count)
        if info is None:
            info = (
                {"symbol": inst.symbol, "name": inst.name, "exchange": inst.exchange, "kind": inst.kind}
                if inst else {"symbol": symbol, "name": symbol, "exchange": "NSE", "kind": "stock"}
            )

    return {
        "symbol": symbol, "interval": interval,
        "source": source, "source_warning": source_warning,
        "info": info or {}, "candles": candles,
    }


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
            elif action == "sub_options":
                keys = [k for k in (data.get("keys") or []) if isinstance(k, str)]
                if keys:
                    await hub.subscribe_option_keys(websocket, keys)
            elif action == "unsub_options":
                keys = [k for k in (data.get("keys") or []) if isinstance(k, str)]
                if keys:
                    await hub.unsubscribe_option_keys(websocket, keys)
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

def _kite_live() -> bool:
    """True when Kite credentials are configured AND a fresh token is present."""
    return kite_credentials_present() and kite_tokens.authenticated


def _broker_live(broker: str) -> bool:
    return _kite_live() if broker == "kite" else hub.mode == "upstox"


def _broker_check(broker: str = "upstox") -> None:
    """Raise 403 if not authenticated with the requested broker."""
    if not _broker_live(broker):
        name = "Kite" if broker == "kite" else "Upstox"
        raise HTTPException(status_code=403, detail=f"Not authenticated with {name}")


@app.get("/api/broker/status")
async def broker_status() -> dict:
    return {
        "mode": hub.mode,
        "authenticated": tokens.authenticated,          # upstox (primary, back-compat)
        "sandbox": SANDBOX,
        "credentialsPresent": credentials_present(),
        "brokers": {
            "upstox": {"authenticated": tokens.authenticated,
                       "credentialsPresent": credentials_present(), "sandbox": SANDBOX},
            "kite":   {"authenticated": kite_tokens.authenticated,
                       "credentialsPresent": kite_credentials_present(), "sandbox": False},
        },
    }


# ── Funds ─────────────────────────────────────────────────────────────────

@app.get("/api/broker/funds")
async def broker_funds(broker: str = Query("upstox")) -> dict:
    if not _broker_live(broker):
        return {"source": "paper", "sandbox": False, "equity": {}, "commodity": {}}
    try:
        if broker == "kite":
            data = await kite_rest.get_funds()
            return {"source": "kite", "sandbox": False, **data}
        data = await rest.get_funds()
        return {"source": "upstox", "sandbox": SANDBOX, **data}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker:{broker}] funds error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Positions ─────────────────────────────────────────────────────────────

@app.get("/api/broker/positions")
async def broker_positions(broker: str = Query("upstox")) -> dict:
    if not _broker_live(broker):
        return {"source": "paper", "sandbox": False, "positions": []}
    try:
        if broker == "kite":
            data = await kite_rest.get_positions()
            return {"source": "kite", "sandbox": False, "positions": data}
        data = await rest.get_positions()
        return {"source": "upstox", "sandbox": SANDBOX, "positions": data}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker:{broker}] positions error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Orders ────────────────────────────────────────────────────────────────

@app.get("/api/broker/orders")
async def broker_orders(broker: str = Query("upstox")) -> dict:
    if not _broker_live(broker):
        return {"source": "paper", "sandbox": False, "orders": []}
    try:
        if broker == "kite":
            data = await kite_rest.get_orders()
            return {"source": "kite", "sandbox": False, "orders": data}
        data = await rest.get_orders()
        return {"source": "upstox", "sandbox": SANDBOX, "orders": data}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker:{broker}] orders error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Place order ───────────────────────────────────────────────────────────

async def _resolve_kite_symbol(body: PlaceOrderBody) -> tuple[str, str]:
    """Prefer an explicit tradingsymbol+exchange; else resolve from the dump."""
    if body.tradingsymbol and body.exchange:
        return body.tradingsymbol, body.exchange
    if not (body.underlying and body.expiry):
        raise ValueError(
            "Kite order needs tradingsymbol+exchange OR underlying+expiry"
            "(+strike+option_type for options)"
        )
    if body.segment == "future":
        resolved = await kite_instruments.resolve_future(body.underlying, body.expiry)
        if not resolved:
            raise ValueError(f"No Kite future found for {body.underlying} {body.expiry}")
        return resolved["tradingsymbol"], resolved["exchange"]
    if not (body.strike and body.option_type):
        raise ValueError("Kite option order needs underlying+expiry+strike+option_type")
    resolved = await kite_instruments.resolve_option(
        body.underlying, body.expiry, body.strike, body.option_type)
    if not resolved:
        raise ValueError(
            f"No Kite contract found for {body.underlying} {body.expiry} "
            f"{body.strike} {body.option_type}")
    return resolved["tradingsymbol"], resolved["exchange"]


@app.post("/api/broker/order")
async def broker_place_order(body: PlaceOrderBody) -> dict:
    _broker_check(body.broker)
    try:
        if body.broker == "kite":
            if body.segment == "equity":
                # Cash/equity: address the stock by tradingsymbol+exchange directly.
                tradingsymbol = body.tradingsymbol or body.underlying
                exchange = body.exchange or "NSE"
                if not tradingsymbol:
                    raise ValueError("Equity order needs a tradingsymbol/underlying")
            else:
                tradingsymbol, exchange = await _resolve_kite_symbol(body)
            data = await kite_rest.place_order(
                tradingsymbol=tradingsymbol, exchange=exchange, qty=body.qty,
                transaction_type=body.transaction_type, order_type=body.order_type,
                price=body.price, product=body.product,
                trigger_price=body.trigger_price, segment=body.segment)
            return {"source": "kite", "sandbox": False, **data}

        # ── Upstox branch ──
        if not body.instrument_key:
            raise ValueError("Upstox order needs an instrument_key")
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
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[broker:{body.broker}] place_order error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Cancel order ──────────────────────────────────────────────────────────

@app.delete("/api/broker/order/{order_id}")
async def broker_cancel_order(order_id: str, broker: str = Query("upstox")) -> dict:
    _broker_check(broker)
    try:
        if broker == "kite":
            data = await kite_rest.cancel_order(order_id)
            return {"source": "kite", "sandbox": False, **data}
        data = await rest.cancel_order(order_id)
        return {"source": "upstox", "sandbox": SANDBOX, **data}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker:{broker}] cancel_order error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ═══════════════════════════════════════════════════════════════════════════
# DERIVATIVES  (/api/derivatives/*)
# ═══════════════════════════════════════════════════════════════════════════

import datetime as _dt, calendar as _cal


def _mock_expiry_dates(underlying: str) -> list[str]:
    """Compute expected option expiry dates for mock/demo mode.

    Python weekday: Mon=0 Tue=1 Wed=2 Thu=3 Fri=4
    NIFTY/SENSEX → next 6 weekly (Thu/Fri).
    Others       → next 4 monthly (last Thu/Tue/Mon of month).
    """
    ul = underlying.upper()
    today = _dt.date.today()

    WEEKLY_WD  = {"NIFTY": 3, "SENSEX": 4}          # Thu, Fri
    MONTHLY_WD = {                                    # last weekday of month
        "BANKNIFTY": 3, "FINNIFTY": 1,
        "MIDCPNIFTY": 0, "BANKEX": 0,
    }

    results: list[str] = []

    if ul in WEEKLY_WD:
        wd, n = WEEKLY_WD[ul], 6
        d = today
        while len(results) < n:
            ahead = (wd - d.weekday() + 7) % 7 or 7
            d = d + _dt.timedelta(days=ahead)
            results.append(d.isoformat())
    else:
        wd = MONTHLY_WD.get(ul, 3)
        year, month, n = today.year, today.month, 4
        while len(results) < n:
            last_day  = _cal.monthrange(year, month)[1]
            last_date = _dt.date(year, month, last_day)
            back      = (last_date.weekday() - wd + 7) % 7
            exp_date  = last_date - _dt.timedelta(days=back)
            if exp_date >= today:
                results.append(exp_date.isoformat())
            month += 1
            if month > 12:
                month, year = 1, year + 1

    return results


def _parse_chain_row(row: dict) -> dict:
    call = row.get("call_options") or {}
    put  = row.get("put_options")  or {}
    cmd  = call.get("market_data") or {}
    pmd  = put.get("market_data")  or {}
    # Use close_price as fallback when ltp is 0 (market closed / pre-open)
    call_ltp = cmd.get("ltp") or cmd.get("close_price") or 0
    put_ltp  = pmd.get("ltp") or pmd.get("close_price") or 0
    return {
        "strike":  row.get("strike_price"),
        "expiry":  row.get("expiry"),
        "callKey": call.get("instrument_key"),
        "callLtp": call_ltp,
        "callBid": cmd.get("bid_price") or 0,
        "callAsk": cmd.get("ask_price") or 0,
        "callOi":  cmd.get("oi") or 0,
        "putKey":  put.get("instrument_key"),
        "putLtp":  put_ltp,
        "putBid":  pmd.get("bid_price") or 0,
        "putAsk":  pmd.get("ask_price") or 0,
        "putOi":   pmd.get("oi") or 0,
    }


@app.get("/api/derivatives/expiries")
async def derivatives_expiries(underlying: str) -> dict:
    """Available option expiry dates for an underlying.

    Upstox mode: fetches the real list of listed contracts from Upstox and
                 returns the unique expiry dates (sorted, future only).
    Mock mode:   returns locally-computed expected dates for demo purposes.
    """
    inst = by_symbol(underlying)

    if hub.mode == "upstox" and inst:
        try:
            dates = await rest.get_option_expiries(inst.instrument_key)
            if dates:
                return {"source": "upstox", "underlying": underlying, "expiries": dates}
            # Empty → Upstox returned nothing (unusual, but handle gracefully)
            raise HTTPException(
                status_code=404,
                detail=f"No option contracts found for {underlying} on Upstox."
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            print(f"[derivatives] expiries upstox error ({underlying}): {exc}")
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Mock mode
    dates = _mock_expiry_dates(underlying)
    return {"source": "mock", "underlying": underlying, "expiries": dates}


@app.get("/api/derivatives/chain")
async def derivatives_chain(underlying: str, expiry: str) -> dict:
    """Option chain for an underlying + expiry.

    Upstox mode: returns ONLY real Upstox data.  Never falls back to synthetic
                 prices — that would give the user fabricated LTPs and MOCK
                 instrument keys that cannot be used for real orders.
    Mock mode:   returns a synthetic Black-Scholes chain with MOCK: keys.
    """
    inst = by_symbol(underlying)

    if hub.mode == "upstox":
        if not inst:
            raise HTTPException(status_code=404, detail=f"Unknown underlying: {underlying}")
        try:
            raw = await rest.get_option_chain(inst.instrument_key, expiry)
        except Exception as exc:  # noqa: BLE001
            print(f"[derivatives] chain upstox error ({underlying} {expiry}): {exc}")
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if not raw:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No option chain data for {underlying} expiry {expiry}. "
                    "Use the expiry selector to pick a valid listed date."
                ),
            )

        chains = [_parse_chain_row(r) for r in raw]
        spot   = raw[0].get("underlying_spot_price") or 0
        return {"source": "upstox", "sandbox": SANDBOX, "spot": spot, "chains": chains}

    # ── Mock / demo mode only below ───────────────────────────────────────
    spot_candles = generate_candles(underlying, "1D", 2)
    spot = spot_candles[-1]["close"]

    try:
        expiry_dt = _dt.datetime.strptime(expiry, "%Y-%m-%d")
        days_left = max(1.0, (expiry_dt.date() - _dt.datetime.utcnow().date()).days)
    except ValueError:
        days_left = 7.0

    step = 100 if underlying.upper() in ("BANKNIFTY", "SENSEX", "BANKEX") else 50
    atm  = round(spot / step) * step
    chains = []
    for i in range(-12, 13):
        k  = atm + i * step
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


def _mcx_expiry_day(underlying: str, yr: int, mon: int) -> "_dt.date":
    """Return the MCX expiry date for a commodity in a given year/month.

    MCX expiry rules (simplified; if the computed day is a holiday it would
    fall back — we use the raw calendar day here for demo/mock purposes):
      GOLD / GOLDM / GOLDPETAL / SILVER / SILVERM / SILVERMIC → 5th of month
      CRUDEOIL / CRUDEOILM                                    → 19th of month
      NATURALGAS                                              → 24th of month
      Others (base metals)                                    → last day of month
    """
    import calendar as _cal
    ul = underlying.upper()
    if ul in {"GOLD", "GOLDM", "GOLDPETAL", "SILVER", "SILVERM", "SILVERMIC"}:
        day = 5
    elif ul in {"CRUDEOIL", "CRUDEOILM"}:
        day = 19
    elif ul == "NATURALGAS":
        day = 24
    else:
        day = _cal.monthrange(yr, mon)[1]  # last day of month
    # Roll back if it's a weekend (Sat→Fri, Sun→Fri)
    d = _dt.date(yr, mon, day)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d


_MCX_COMMODITIES = {
    "GOLD", "GOLDM", "GOLDPETAL",
    "SILVER", "SILVERM", "SILVERMIC",
    "CRUDEOIL", "CRUDEOILM",
    "NATURALGAS",
    "COPPER", "COPPERM",
    "ZINC", "ZINCP",
    "ALUMINIUM", "ALUMINIM",
    "NICKEL", "NICKELM",
    "LEAD", "LEADM",
}


@app.get("/api/derivatives/futures")
async def derivatives_futures(underlying: str) -> dict:
    """List of futures contracts (next 3 monthly expiries) for an underlying.
    Handles both NSE index futures (last-Thursday rule) and MCX commodity futures
    (commodity-specific expiry day). Always returns data in both mock and Upstox mode."""
    import datetime as _dt, calendar as _cal

    inst = by_symbol(underlying)
    spot_candles = generate_candles(underlying, "1D", 2)
    spot = spot_candles[-1]["close"]

    today = _dt.datetime.utcnow().date()
    futures = []
    is_mcx = underlying.upper() in _MCX_COMMODITIES
    carry_rate = 0.06 if is_mcx else 0.065
    exchange   = inst.exchange if inst else ("MCX" if is_mcx else "NSE")

    if is_mcx:
        # Walk forward month by month collecting the next 3 active contracts.
        yr, mon = today.year, today.month
        while len(futures) < 3:
            dt = _mcx_expiry_day(underlying, yr, mon)
            if dt >= today:
                expiry_str   = dt.strftime("%Y-%m-%d")
                expiry_label = dt.strftime("%d %b %Y")
                days_left    = max(1, (dt - today).days)
                futures.append({
                    "symbol":        f"{underlying}FUT",
                    "name":          f"{underlying} Futures {expiry_label}",
                    "exchange":      exchange,
                    "expiry":        expiry_str,
                    "expiryLabel":   expiry_label,
                    "ltp":           round(spot * (1.0 + carry_rate * days_left / 365.0), 2),
                    "instrumentKey": f"MOCK:future:{underlying}:{expiry_str}",
                    "kind":          "future",
                })
            # Advance to next month
            mon += 1
            if mon > 12:
                mon, yr = 1, yr + 1
    else:
        for months_ahead in range(3):
            yr  = today.year + (today.month + months_ahead - 1) // 12
            mon = (today.month + months_ahead - 1) % 12 + 1
            last_day = _cal.monthrange(yr, mon)[1]
            dt = _dt.date(yr, mon, last_day)
            while dt.weekday() != 3:
                dt -= _dt.timedelta(days=1)

            expiry_str   = dt.strftime("%Y-%m-%d")
            expiry_label = dt.strftime("%d %b %Y")
            days_left    = max(1, (dt - today).days)
            futures.append({
                "symbol":        f"{underlying}FUT",
                "name":          f"{underlying} Futures {expiry_label}",
                "exchange":      exchange,
                "expiry":        expiry_str,
                "expiryLabel":   expiry_label,
                "ltp":           round(spot * (1.0 + carry_rate * days_left / 365.0), 2),
                "instrumentKey": f"MOCK:future:{underlying}:{expiry_str}",
                "kind":          "future",
            })

    return {"source": hub.mode, "underlying": underlying, "futures": futures}


# ═══════════════════════════════════════════════════════════════════════════
# BACKTEST  (/api/backtest/*)
# Isolated from all live-mode paths.  Uses Yahoo Finance as the data source
# so users get maximum historical depth (up to 20 years on daily) without
# consuming Upstox API quota.
# ═══════════════════════════════════════════════════════════════════════════

# Symbol → Yahoo Finance ticker.  NSE equities not listed here fall back to
# the SYMBOL.NS convention automatically.
_YF_TICKER_MAP: dict[str, str] = {
    # NSE indices
    "NIFTY":          "^NSEI",
    "BANKNIFTY":      "^NSEBANK",
    "SENSEX":         "^BSESN",
    "FINNIFTY":       "^CNXFIN",
    "MIDCPNIFTY":     "NIFTY_MID_SELECT.NS",
    "NIFTYNXT50":     "^NSMIDCP",
    "NIFTY100":       "^CNX100",
    "NIFTY200":       "^CNX200",
    "NIFTY500":       "^CNX500",
    "NIFTYMIDCAP150": "^CNXMIDCAP",
    "NIFTYIT":        "^CNXIT",
    "NIFTYPHARMA":    "^CNXPHARMA",
    "VIXNSE":         "^INDIAVIX",
    "BANKEX":         "BANKEX.BO",
    # MCX commodity futures — international benchmarks (prices in USD, not INR)
    "GOLD":       "GC=F",
    "GOLDM":      "GC=F",
    "GOLDPETAL":  "GC=F",
    "SILVER":     "SI=F",
    "SILVERM":    "SI=F",
    "SILVERMIC":  "SI=F",
    "CRUDEOIL":   "CL=F",
    "CRUDEOILM":  "CL=F",
    "NATURALGAS": "NG=F",
    "COPPER":     "HG=F",
    "ALUMINIUM":  "ALI=F",
    "ZINC":       "ZINC=F",
    "NICKEL":     "NI=F",
    "LEAD":       "PB=F",
}

# Our timeframe codes → Yahoo Finance interval strings (closest available).
_YF_INTERVAL_MAP: dict[str, str] = {
    "1m":  "1m",
    "3m":  "5m",   # Yahoo has no 3m; 5m is the closest
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1H":  "60m",
    "2H":  "60m",  # fetch 1H bars, aggregate on our side
    "4H":  "60m",  # fetch 1H bars, aggregate on our side
    "1D":  "1d",
    "1W":  "1wk",
    "1M":  "1mo",
}

# In-memory candle cache for the backtest endpoint.
# Key: (symbol_upper, timeframe)  Value: (gzip_bytes, cached_at_epoch)
# TTL: 1 hour.  After the first (slow) MongoDB+gzip build, subsequent calls serve
# pre-gzipped bytes directly with no serialisation or compression overhead.
import gzip as _gzip_mod
import time as _time_mod
_BACKTEST_CACHE: dict[tuple[str, str], tuple[bytes, float]] = {}
_BACKTEST_CACHE_TTL: float = 3600.0

# Maximum historical lookback Yahoo Finance reliably supports per interval.
_YF_MAX_DAYS: dict[str, int] = {
    "1m":  7,      # Yahoo hard limit: 7 days for 1-minute data
    "3m":  60,
    "5m":  60,
    "15m": 60,
    "30m": 60,
    "1H":  730,    # 2 years
    "2H":  730,
    "4H":  730,
    "1D":  3650,   # 10 years
    "1W":  7300,   # 20 years
    "1M":  7300,
}


@app.get("/api/backtest/history")
async def backtest_history(symbol: str, interval: str = "1D") -> dict:
    """Historical data for Backtest Mode.

    Priority 1 — yfinance library: returns maximum historical depth without
    consuming Upstox quota.  Runs in a thread pool so the async event loop
    is not blocked.

    Priority 2 — Upstox / mock fallback: if yfinance returns no data the
    endpoint falls back to the existing /api/history logic with count=2000.
    A source_warning is added so the frontend can toast the user.
    """
    import asyncio as _asyncio
    import yfinance as _yf

    sym_upper   = symbol.upper()
    ticker      = _YF_TICKER_MAP.get(sym_upper) or f"{sym_upper}.NS"
    yf_interval = _YF_INTERVAL_MAP.get(interval, "1d")
    days        = _YF_MAX_DAYS.get(interval, 3650)

    # ── Priority 0: Historical store (populated by the backfill pipeline) ───
    try:
        from fastapi.responses import Response as _Resp
        import orjson as _orjson
        from .historical.store import has_sufficient_data, get_chart_candles as _hist_query, _base_timeframe
        if await has_sufficient_data(sym_upper, _base_timeframe(interval), min_candles=200):
            _cache_key = (sym_upper, interval)
            _cached = _BACKTEST_CACHE.get(_cache_key)
            if _cached and (_time_mod.time() - _cached[1]) < _BACKTEST_CACHE_TTL:
                # Serve pre-gzipped bytes directly — zero serialisation overhead.
                return _Resp(
                    content=_cached[0],
                    media_type="application/json",
                    headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
                )
            # Cache miss: read MongoDB, build payload, gzip once, cache bytes.
            print(f"[backtest] cache miss {sym_upper}/{interval} — loading from MongoDB")
            stored = await _hist_query(sym_upper, interval, count=20000)
            if stored:
                inst = by_symbol(symbol)
                _info = (
                    {"symbol": inst.symbol, "name": inst.name,
                     "exchange": inst.exchange, "kind": inst.kind}
                    if inst else
                    {"symbol": symbol, "name": symbol,
                     "exchange": "Historical Store", "kind": "stock"}
                )
                _payload = _orjson.dumps({
                    "symbol":         symbol,
                    "interval":       interval,
                    "source":         "historical_store",
                    "source_warning": None,
                    "info":           _info,
                    "candles":        stored,
                })
                _gz = _gzip_mod.compress(_payload, compresslevel=1)
                _BACKTEST_CACHE[_cache_key] = (_gz, _time_mod.time())
                print(f"[backtest] cached {len(stored):,} candles for {sym_upper}/{interval} "
                      f"({len(_gz)//1024}KB gzipped)")
                return _Resp(
                    content=_gz,
                    media_type="application/json",
                    headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"},
                )
    except Exception as _exc:  # noqa: BLE001
        print(f"[backtest] historical store check failed: {_exc}")

    # ── Priority 1: yfinance ─────────────────────────────────────────────────
    yahoo_fail_reason: str | None = None
    try:
        def _fetch_yf() -> list[dict]:
            import math as _math
            period = f"{days}d"
            df = _yf.download(
                ticker,
                period=period,
                interval=yf_interval,
                auto_adjust=True,
                progress=False,
            )
            if df is None or df.empty:
                return []
            # yfinance ≥0.2 returns multi-level columns for single ticker; flatten.
            if hasattr(df.columns, "levels"):
                df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
            rows: list[dict] = []
            for ts, row in df.iterrows():
                try:
                    ts_unix = int(ts.timestamp())
                    o = float(row["Open"])
                    h = float(row["High"])
                    l = float(row["Low"])
                    c = float(row["Close"])
                    vol = int(row["Volume"]) if "Volume" in row else 0
                    if any(_math.isnan(v2) for v2 in (o, h, l, c)):
                        continue
                    rows.append({"time": ts_unix, "open": round(o, 2), "high": round(h, 2),
                                 "low": round(l, 2), "close": round(c, 2), "volume": vol})
                except (TypeError, ValueError, KeyError):
                    continue
            return rows

        candles: list[dict] = await _asyncio.get_event_loop().run_in_executor(None, _fetch_yf)

        if not candles:
            yahoo_fail_reason = "0 valid bars returned by yfinance"
        else:
            # 2H / 4H: yfinance only supplies 60m — aggregate on our side.
            if interval in ("2H", "4H"):
                from .upstox.rest import _aggregate_bars, _MCX_MOPEN_UTC, _MOPEN_UTC
                mopen  = _MCX_MOPEN_UTC if sym_upper in _MCX_COMMODITIES else _MOPEN_UTC
                value  = 2 if interval == "2H" else 4
                agg    = _aggregate_bars({c["time"]: c for c in candles}, "hours", value, mopen)
                candles = sorted(agg.values(), key=lambda x: x["time"])

            source_warning: str | None = None
            if sym_upper in _MCX_COMMODITIES:
                source_warning = (
                    "Backtest prices sourced from international USD futures (Yahoo Finance). "
                    "Pattern shapes are accurate; absolute INR values differ from MCX."
                )

            inst = by_symbol(symbol)
            info = (
                {"symbol": inst.symbol, "name": inst.name,
                 "exchange": inst.exchange, "kind": inst.kind}
                if inst else
                {"symbol": symbol, "name": symbol,
                 "exchange": "Yahoo Finance", "kind": "stock"}
            )
            return {
                "symbol":         symbol,
                "interval":       interval,
                "source":         "yahoo",
                "source_warning": source_warning,
                "info":           info,
                "candles":        candles,
            }

    except Exception as exc:  # noqa: BLE001
        yahoo_fail_reason = str(exc)

    # ── Priority 2: Upstox / mock fallback ──────────────────────────────────
    print(f"[backtest] yfinance failed for {symbol} {interval} ({yahoo_fail_reason}) "
          f"— falling back to Upstox/mock")

    fallback = await history(symbol=symbol, interval=interval, count=2000)

    src_label = "Upstox" if fallback.get("source") == "upstox" else "mock data"
    existing_warn = (fallback.get("source_warning") or "").strip()
    fallback["source_warning"] = (
        f"Yahoo Finance unavailable ({yahoo_fail_reason}); using {src_label}. "
        + existing_warn
    ).rstrip()
    return fallback


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
                "callLtp": call_md.get("ltp") or call_md.get("close_price") or 0,
                "callBid": call_md.get("bid_price") or 0,
                "callAsk": call_md.get("ask_price") or 0,
                "callOi":  call_md.get("oi") or 0,
                "callVol": call_md.get("volume") or 0,
                "putKey":  put.get("instrument_key"),
                "putLtp":  put_md.get("ltp") or put_md.get("close_price") or 0,
                "putBid":  put_md.get("bid_price") or 0,
                "putAsk":  put_md.get("ask_price") or 0,
                "putOi":   put_md.get("oi") or 0,
                "putVol":  put_md.get("volume") or 0,
            })
        return {"source": "upstox", "sandbox": SANDBOX, "chains": chains}
    except Exception as exc:  # noqa: BLE001
        print(f"[broker] option-chain error: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
