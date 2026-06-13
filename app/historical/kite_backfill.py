"""Kite-sourced intraday backfill — downloads 1-minute OHLCV from Zerodha Kite.

Kite provides up to 60 days of 1-minute historical data per request.
This is the deepest freely available 1m source for NSE/BSE index instruments.

Usage:
    result = await backfill_kite_1m(inst, days=60)

Requires Kite to be authenticated (KITE_API_KEY + access token).
The instrument-token lookup (for unknown symbols) uses Kite's public CSV
endpoint which does NOT need auth.
"""
from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timedelta, timezone

import httpx

from ..config import KITE_API_KEY, kite_tokens
from ..instruments import Instrument
from .store import count_candles, get_timestamp_range, update_sync_state, upsert_candles

API_BASE = "https://api.kite.trade"
_IST = timedelta(hours=5, minutes=30)

# Stable NSE index instrument tokens in Kite — these very rarely change.
# If a symbol isn't here, _lookup_token() will fetch the CSV at runtime.
_KNOWN_TOKENS: dict[str, str] = {
    "NIFTY":      "256265",
    "BANKNIFTY":  "260105",
    "FINNIFTY":   "257801",
    "MIDCPNIFTY": "288009",
    "NIFTY100":   "259849",
}

# One-run cache: exchange → {symbol_upper → token_str}
_TOKEN_CACHE: dict[str, dict[str, str]] = {}


def _kite_headers() -> dict:
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {KITE_API_KEY}:{kite_tokens.token}",
    }


async def _lookup_token(symbol: str, exchange: str) -> str | None:
    """Resolve Kite instrument_token by fetching the public instrument CSV.

    Falls back to NSE if exchange is unknown. No auth required.
    """
    exch = "BSE" if (exchange or "").upper() == "BSE" else "NSE"
    sym_up = symbol.upper()

    if sym_up in _KNOWN_TOKENS:
        return _KNOWN_TOKENS[sym_up]

    global _TOKEN_CACHE
    if exch in _TOKEN_CACHE:
        return _TOKEN_CACHE[exch].get(sym_up)

    def _fetch_csv() -> dict[str, str]:
        with httpx.Client(timeout=30) as c:
            r = c.get(f"{API_BASE}/instruments/{exch}")
            if not r.is_success:
                return {}
        mapping: dict[str, str] = {}
        reader = csv.DictReader(io.StringIO(r.text))
        for row in reader:
            ts = (row.get("tradingsymbol") or "").upper().replace(" ", "")
            name = (row.get("name") or "").upper().replace(" ", "")
            token = (row.get("instrument_token") or "").strip()
            if not token:
                continue
            # Index rows typically have empty instrument_type or type "EQ"/"INDEX"
            for key in (ts, name):
                if key and key not in mapping:
                    mapping[key] = token
        return mapping

    try:
        mapping = await asyncio.get_event_loop().run_in_executor(None, _fetch_csv)
        _TOKEN_CACHE[exch] = mapping
        return mapping.get(sym_up)
    except Exception as exc:
        print(f"[kite_backfill] token lookup for {symbol}/{exch} failed: {exc}")
        return None


async def backfill_kite_1m(inst: Instrument, days: int = 60) -> dict:
    """Download up to `days` (capped at 60) of 1m OHLCV candles from Kite.

    Requires kite_tokens.authenticated == True.
    Returns a summary dict with upserted, total, earliest_ts, latest_ts, done.
    """
    sym = inst.symbol.upper()
    exchange = (inst.exchange or "NSE").upper()
    days = min(max(days, 1), 60)

    if not kite_tokens.authenticated:
        return {"error": "Kite not authenticated", "upserted": 0, "symbol": sym}

    token = await _lookup_token(sym, exchange)
    if not token:
        return {"error": f"No Kite instrument token for {sym} ({exchange})", "upserted": 0, "symbol": sym}

    now_ist = datetime.now(timezone.utc) + _IST
    today = now_ist.date()
    start = today - timedelta(days=days)

    frm = start.strftime("%Y-%m-%d 00:00:00")
    to = now_ist.strftime("%Y-%m-%d %H:%M:%S")

    def _fetch_rows() -> list:
        with httpx.Client(timeout=60) as c:
            r = c.get(
                f"{API_BASE}/instruments/historical/{token}/minute",
                params={"from": frm, "to": to, "continuous": "0", "oi": "0"},
                headers=_kite_headers(),
            )
            if not r.is_success:
                raise ValueError(f"Kite HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            if body.get("status") != "success":
                raise ValueError(f"Kite error: {body.get('message', 'unknown')}")
            return body.get("data", {}).get("candles", [])

    try:
        rows = await asyncio.get_event_loop().run_in_executor(None, _fetch_rows)
    except Exception as exc:
        return {"error": str(exc), "upserted": 0, "symbol": sym}

    candles: list[dict] = []
    for row in rows:
        try:
            ts_str = row[0]
            o, h, l, c, vol = float(row[1]), float(row[2]), float(row[3]), float(row[4]), int(row[5] or 0)
            dt = datetime.fromisoformat(str(ts_str))
            unix = int(dt.timestamp())
            candles.append({
                "time": unix,
                "open": round(o, 2), "high": round(h, 2),
                "low": round(l, 2),  "close": round(c, 2),
                "volume": vol,
            })
        except (IndexError, TypeError, ValueError):
            continue

    upserted = 0
    if candles:
        upserted = await upsert_candles(sym, "1m", candles, source="kite")

    total = await count_candles(sym, "1m")
    earliest, latest = await get_timestamp_range(sym, "1m")
    await update_sync_state(
        sym, "1m",
        earliest_ts=earliest, latest_ts=latest,
        total_candles=total, backfill_complete=True,
        backfill_source="kite",
    )

    print(f"[kite_backfill] {sym}/1m: fetched {len(rows)} rows, "
          f"upserted {upserted}, total={total}, token={token}")

    return {
        "symbol": sym, "timeframe": "1m", "token": token,
        "upserted": upserted, "total": total,
        "earliest_ts": earliest, "latest_ts": latest,
        "done": True,
    }
