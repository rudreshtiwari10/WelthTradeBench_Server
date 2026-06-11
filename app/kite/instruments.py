"""Kite instrument dump → tradingsymbol resolver.

Kite addresses contracts by `tradingsymbol` + `exchange` (e.g. NIFTY2451524000CE
/ NFO), not by an opaque instrument key. The frontend only knows
(underlying, expiry, strike, CE/PE), so we download Kite's daily instrument dump
(a CSV per exchange), cache it for the IST day, and look the row up.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone, timedelta

import httpx

from ..config import KITE_API_KEY, kite_tokens

API_BASE = "https://api.kite.trade"
_EXCHANGES = ("NFO", "BFO", "MCX")            # derivative segments we may trade
_IST = timedelta(hours=5, minutes=30)
_cache: list[dict] = []
_cache_date: str | None = None


def _headers() -> dict:
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {KITE_API_KEY}:{kite_tokens.token}",
    }


def _today_ist() -> str:
    return (datetime.now(timezone.utc) + _IST).date().isoformat()


async def _load_dump() -> list[dict]:
    """Fetch + parse option/future rows for the derivative exchanges; cache for the IST day."""
    global _cache, _cache_date
    today = _today_ist()
    if _cache and _cache_date == today:
        return _cache
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for exch in _EXCHANGES:
            try:
                resp = await client.get(f"{API_BASE}/instruments/{exch}", headers=_headers())
                if not resp.is_success:
                    print(f"[kite] instruments/{exch} HTTP {resp.status_code}")
                    continue
                reader = csv.DictReader(io.StringIO(resp.text))
                for r in reader:
                    itype = (r.get("instrument_type") or "").upper()
                    if itype not in ("CE", "PE", "FUT"):
                        continue
                    try:
                        strike = float(r.get("strike") or 0)
                    except ValueError:
                        strike = 0.0
                    rows.append({
                        "tradingsymbol": r.get("tradingsymbol", ""),
                        "name": (r.get("name") or "").upper(),
                        "expiry": (r.get("expiry") or "")[:10],
                        "strike": strike,
                        "instrument_type": itype,
                        "exchange": r.get("exchange") or exch,
                        "lot_size": int(float(r.get("lot_size") or 0)) or 0,
                        "instrument_token": r.get("instrument_token", ""),
                    })
            except Exception as exc:  # noqa: BLE001
                print(f"[kite] instruments/{exch} fetch failed: {exc}")
    if rows:
        _cache, _cache_date = rows, today
    return rows


def _nearest_by_expiry(candidates: list[dict], expiry: str) -> dict:
    try:
        target_exp = datetime.fromisoformat(expiry[:10]).date()
    except (ValueError, TypeError):
        target_exp = None
    if target_exp is not None:
        def _exp_dist(r: dict) -> int:
            try:
                d = datetime.fromisoformat(r["expiry"]).date()
            except (ValueError, TypeError):
                return 10_000
            return abs((d - target_exp).days)
        candidates.sort(key=_exp_dist)            # pick the closest listed expiry
    return candidates[0]


async def resolve_option(underlying: str, expiry: str, strike: float, opt_type: str) -> dict | None:
    """Return {tradingsymbol, exchange, lot_size, instrument_token} for an option, or None."""
    rows = await _load_dump()
    if not rows:
        return None
    name = underlying.upper()
    opt = opt_type.upper()
    target_strike = float(strike)
    candidates = [
        r for r in rows
        if r["name"] == name
        and r["instrument_type"] == opt
        and abs(r["strike"] - target_strike) < 0.5
    ]
    if not candidates:
        return None
    best = _nearest_by_expiry(candidates, expiry)
    return {
        "tradingsymbol": best["tradingsymbol"], "exchange": best["exchange"],
        "lot_size": best["lot_size"], "instrument_token": best["instrument_token"],
    }


async def resolve_future(underlying: str, expiry: str) -> dict | None:
    """Return {tradingsymbol, exchange, lot_size, instrument_token} for a future, or None."""
    rows = await _load_dump()
    if not rows:
        return None
    name = underlying.upper()
    candidates = [r for r in rows if r["name"] == name and r["instrument_type"] == "FUT"]
    if not candidates:
        return None
    best = _nearest_by_expiry(candidates, expiry)
    return {
        "tradingsymbol": best["tradingsymbol"], "exchange": best["exchange"],
        "lot_size": best["lot_size"], "instrument_token": best["instrument_token"],
    }
