"""Tradomate backend — FastAPI.

REST proxy (history/search/quote) + Upstox OAuth + a /ws live-tick relay.
Serves mock data whenever Upstox credentials/token are absent, so the chart is
always live and demoable.
"""
from __future__ import annotations

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .config import CLIENT_URL, credentials_present, tokens
from .instruments import by_symbol, search
from .mock.generator import generate_candles
from .upstox import auth, rest
from .upstox.feed import hub

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
async def history(symbol: str, interval: str = "1D", count: int = 600) -> dict:
    count = max(50, min(count, 2000))
    inst = by_symbol(symbol)
    source = "mock"
    candles: list[dict] = []

    if inst and hub.mode == "upstox":
        try:
            candles = await rest.historical_candles(inst, interval, count)
            source = "upstox"
        except Exception as exc:  # noqa: BLE001
            print(f"[history] Upstox fetch failed ({symbol} {interval}); using mock: {exc}")

    if not candles:
        candles = generate_candles(symbol, interval, count)

    info = (
        {"symbol": inst.symbol, "name": inst.name, "exchange": inst.exchange, "kind": inst.kind}
        if inst else {"symbol": symbol, "name": symbol, "exchange": "NSE", "kind": "stock"}
    )
    return {"symbol": symbol, "interval": interval, "source": source, "info": info, "candles": candles}


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
