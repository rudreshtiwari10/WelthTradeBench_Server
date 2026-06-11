"""Kite REST integration: funds, positions, orders, place, cancel.

Every Kite response is normalised into the exact same dict shape the Upstox
broker returns, so the frontend's typed models work unchanged.
"""
from __future__ import annotations

import httpx

from ..config import KITE_API_KEY, kite_tokens

API_BASE = "https://api.kite.trade"


def _headers() -> dict:
    return {
        "X-Kite-Version": "3",
        # Auth header format is `token <api_key>:<access_token>` — both values,
        # colon-joined, with a literal "token " prefix. Not a bearer token.
        "Authorization": f"token {KITE_API_KEY}:{kite_tokens.token}",
    }


def _kite_raise(r: httpx.Response) -> None:
    """Parse Kite's JSON error body into a human-readable message and raise."""
    try:
        body = r.json()
        msg = body.get("message") or body.get("error_type") or f"HTTP {r.status_code}"
    except Exception:
        msg = r.text[:300] if r.text else f"HTTP {r.status_code}: {r.reason_phrase}"
    raise ValueError(msg)


# ── Funds / margins ────────────────────────────────────────────────────────

def _norm_segment(seg: dict | None) -> dict:
    seg = seg or {}
    avail = seg.get("available") or {}
    used = seg.get("utilised") or {}
    return {
        "available_margin": avail.get("live_balance", seg.get("net", 0)),
        "used_margin": used.get("debits", 0),
        "payin": avail.get("intraday_payin", 0),
        "span": used.get("span", 0),
        "exposure": used.get("exposure", 0),
        "option_premium": used.get("option_premium", 0),
        "collateral": avail.get("collateral", 0),
        "pnl": (used.get("m2m_realised", 0) or 0) + (used.get("m2m_unrealised", 0) or 0),
    }


async def get_funds() -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API_BASE}/user/margins", headers=_headers())
        if not r.is_success:
            _kite_raise(r)
    data = r.json().get("data", {})
    return {
        "equity": _norm_segment(data.get("equity")),
        "commodity": _norm_segment(data.get("commodity")),
    }


# ── Positions ──────────────────────────────────────────────────────────────

def _norm_position(p: dict) -> dict:
    return {
        "exchange": p.get("exchange", ""),
        "trading_symbol": p.get("tradingsymbol", ""),
        "instrument_token": str(p.get("instrument_token", "")),
        "product": p.get("product", ""),
        "quantity": p.get("quantity", 0),
        "buy_quantity": p.get("buy_quantity", 0),
        "sell_quantity": p.get("sell_quantity", 0),
        "average_price": p.get("average_price", 0),
        "buy_price": p.get("buy_price", 0),
        "sell_price": p.get("sell_price", 0),
        "last_price": p.get("last_price", 0),
        "pnl": p.get("pnl", 0),
        "unrealised_profit": p.get("unrealised", 0),
        "realised_profit": p.get("realised", 0),
        "close_price": p.get("close_price", 0),
        "multiplier": p.get("multiplier", 1),
        "value": p.get("value", 0),
    }


async def get_positions() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API_BASE}/portfolio/positions", headers=_headers())
        if not r.is_success:
            _kite_raise(r)
    net = (r.json().get("data") or {}).get("net") or []
    return [_norm_position(p) for p in net]


# ── Orders ─────────────────────────────────────────────────────────────────

def _norm_order(o: dict) -> dict:
    return {
        "order_id": str(o.get("order_id", "")),
        "trading_symbol": o.get("tradingsymbol", ""),
        "exchange": o.get("exchange", ""),
        "instrument_token": str(o.get("instrument_token", "")),
        "transaction_type": o.get("transaction_type", ""),
        "quantity": o.get("quantity", 0),
        "price": o.get("price", 0),
        "average_price": o.get("average_price", 0),
        "filled_quantity": o.get("filled_quantity", 0),
        "pending_quantity": o.get("pending_quantity", 0),
        "order_type": o.get("order_type", ""),
        "product": o.get("product", ""),
        # lowercased to match the other broker's convention
        "status": str(o.get("status", "")).lower(),
        "order_timestamp": str(o.get("order_timestamp", "")),
        "tag": o.get("tag"),
    }


async def get_orders() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API_BASE}/orders", headers=_headers())
        if not r.is_success:
            _kite_raise(r)
    return [_norm_order(o) for o in (r.json().get("data") or [])]


# ── Place order ────────────────────────────────────────────────────────────
# F&O (options + futures) delivery is NRML; cash/equity delivery is CNC.
# Intraday is MIS for both.
_PRODUCT_MAP = {"D": "NRML", "I": "MIS"}          # options & futures
_PRODUCT_MAP_EQUITY = {"D": "CNC", "I": "MIS"}    # cash / equity


async def place_order(
    tradingsymbol: str,
    exchange: str,
    qty: int,
    transaction_type: str,           # "BUY" | "SELL"
    order_type: str = "MARKET",      # "MARKET" | "LIMIT" | "SL" | "SL-M"
    price: float = 0.0,
    product: str = "D",
    trigger_price: float = 0.0,
    segment: str = "option",         # "option" | "future" | "equity"
) -> dict:
    product_map = _PRODUCT_MAP_EQUITY if segment == "equity" else _PRODUCT_MAP
    body = {
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,                       # NFO | BFO | MCX | NSE | BSE
        "transaction_type": transaction_type.upper(),
        "order_type": order_type.upper(),
        "quantity": qty,
        "product": product_map.get(product, product),
        "validity": "DAY",
        "price": price,
        "trigger_price": trigger_price,
        "disclosed_quantity": 0,
        "tag": "welthwest",                         # ≤20 chars, for reconciliation
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{API_BASE}/orders/regular",
            data=body,                              # form-encoded, NOT json
            headers={**_headers(), "Content-Type": "application/x-www-form-urlencoded"},
        )
        if not r.is_success:
            _kite_raise(r)
    return r.json().get("data", {})                 # {"order_id": "..."}


# ── Cancel order ───────────────────────────────────────────────────────────

async def cancel_order(order_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(f"{API_BASE}/orders/regular/{order_id}", headers=_headers())
        if not r.is_success:
            _kite_raise(r)
    return r.json().get("data", {})
