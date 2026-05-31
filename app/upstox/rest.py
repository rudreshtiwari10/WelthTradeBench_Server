"""Upstox REST proxy: historical candles, quotes, and full broker API."""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta

import httpx

from ..config import tokens
from ..instruments import Instrument
from ..mock.generator import interval_seconds

API_BASE = "https://api.upstox.com"

# client interval -> (unit, value) for the V3 historical-candle API
_UNIT_MAP: dict[str, tuple[str, int]] = {
    "1m": ("minutes", 1), "3m": ("minutes", 3), "5m": ("minutes", 5),
    "15m": ("minutes", 15), "30m": ("minutes", 30),
    "1H": ("hours", 1), "2H": ("hours", 2), "4H": ("hours", 4),
    "1D": ("days", 1), "1W": ("weeks", 1), "1M": ("months", 1),
}


def _headers() -> dict:
    return {"accept": "application/json", "Authorization": f"Bearer {tokens.token}"}


def _from_to(interval: str, count: int) -> tuple[str, str]:
    """Compute from/to dates covering ~count bars of the given interval."""
    secs = interval_seconds(interval) * count
    today = datetime.utcnow().date()
    days_back = max(1, secs // 86400 + 5)
    # Intraday intervals need a wider calendar window (weekends/holidays).
    if interval in ("1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H"):
        days_back = max(days_back, count // 6 + 10)
    frm = today - timedelta(days=int(days_back))
    return frm.isoformat(), today.isoformat()


async def historical_candles(inst: Instrument, interval: str, count: int) -> list[dict]:
    unit, value = _UNIT_MAP.get(interval, ("days", 1))
    key = urllib.parse.quote(inst.instrument_key, safe="")
    frm, to = _from_to(interval, count)
    url = f"{API_BASE}/v3/historical-candle/{key}/{unit}/{value}/{to}/{frm}"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        payload = resp.json()
    candles = payload.get("data", {}).get("candles", [])
    # Upstox returns newest-first: [ts, o, h, l, c, vol, oi]. Normalize + sort asc.
    out = []
    for row in candles:
        ts = int(datetime.fromisoformat(row[0]).timestamp())
        out.append({
            "time": ts, "open": row[1], "high": row[2], "low": row[3],
            "close": row[4], "volume": int(row[5]),
        })
    out.sort(key=lambda c: c["time"])
    return out[-count:]


async def ltp(inst: Instrument) -> dict | None:
    key = urllib.parse.quote(inst.instrument_key, safe="")
    url = f"{API_BASE}/v3/market-quote/ltp?instrument_key={key}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        data = resp.json().get("data", {})
    for _, v in data.items():
        return {"ltp": v.get("last_price"), "ts": v.get("ltt")}
    return None


# ── Broker: Account Funds ─────────────────────────────────────────────────

async def get_funds() -> dict:
    """GET /v2/user/fund-margin — available and used margin."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API_BASE}/v2/user/fund-margin", headers=_headers())
        r.raise_for_status()
    return r.json().get("data", {})


# ── Broker: Short-term Positions ──────────────────────────────────────────

async def get_positions() -> list[dict]:
    """GET /v2/portfolio/short-term-positions — today's open positions."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{API_BASE}/v2/portfolio/short-term-positions", headers=_headers()
        )
        r.raise_for_status()
    return r.json().get("data") or []


# ── Broker: Orders ────────────────────────────────────────────────────────

async def get_orders() -> list[dict]:
    """GET /v2/order/retrieve-all — all orders for the day."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API_BASE}/v2/order/retrieve-all", headers=_headers())
        r.raise_for_status()
    return r.json().get("data") or []


# ── Broker: Place Order ───────────────────────────────────────────────────

async def place_order(
    instrument_key: str,
    qty: int,
    transaction_type: str,
    order_type: str = "MARKET",
    price: float = 0.0,
    product: str = "D",
    trigger_price: float = 0.0,
) -> dict:
    """POST /v2/order/place — returns {order_id} on success."""
    body = {
        "quantity": qty,
        "product": product,
        "validity": "DAY",
        "price": price,
        "tag": "welthwest",
        "instrument_token": instrument_key,
        "order_type": order_type.upper(),
        "transaction_type": transaction_type.upper(),
        "disclosed_quantity": 0,
        "trigger_price": trigger_price,
        "is_amo": False,
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{API_BASE}/v2/order/place",
            json=body,
            headers={**_headers(), "Content-Type": "application/json"},
        )
        r.raise_for_status()
    return r.json().get("data", {})


# ── Broker: Cancel Order ──────────────────────────────────────────────────

async def cancel_order(order_id: str) -> dict:
    """DELETE /v2/order/cancel?order_id=… — cancel a pending order."""
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(
            f"{API_BASE}/v2/order/cancel?order_id={order_id}",
            headers=_headers(),
        )
        r.raise_for_status()
    return r.json().get("data", {})


# ── Broker: Option Chain ──────────────────────────────────────────────────

async def get_option_chain(underlying_key: str, expiry_date: str) -> list[dict]:
    """GET /v2/option/chain — returns per-strike call+put data with instrument keys."""
    key = urllib.parse.quote(underlying_key, safe="")
    url = f"{API_BASE}/v2/option/chain?instrument_key={key}&expiry_date={expiry_date}"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, headers=_headers())
        r.raise_for_status()
    return r.json().get("data") or []
