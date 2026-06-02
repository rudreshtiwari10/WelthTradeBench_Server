"""Upstox REST proxy: historical candles, quotes, and full broker API."""
from __future__ import annotations

import math
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx

from ..config import tokens
from ..instruments import Instrument
from ..mock.generator import interval_seconds

API_BASE = "https://api.upstox.com"


def _upstox_raise(r: httpx.Response) -> None:
    """Parse the Upstox error body and raise with a human-readable message."""
    try:
        body = r.json()
        errors = body.get("errors") or []
        if errors and isinstance(errors, list):
            msg = errors[0].get("message") or str(errors[0])
        else:
            msg = (body.get("message") or body.get("error") or
                   body.get("detail") or f"HTTP {r.status_code}")
    except Exception:
        msg = r.text[:300] if r.text else f"HTTP {r.status_code}: {r.reason_phrase}"
    raise ValueError(msg)


# client interval -> (unit, value) for the V3 historical-candle API
_UNIT_MAP: dict[str, tuple[str, int]] = {
    "1m":  ("minutes", 1),  "3m":  ("minutes", 3),  "5m":  ("minutes", 5),
    "15m": ("minutes", 15), "30m": ("minutes", 30),
    "1H":  ("hours", 1),    "2H":  ("hours", 2),     "4H":  ("hours", 4),
    "1D":  ("days", 1),     "1W":  ("weeks", 1),     "1M":  ("months", 1),
}


def _headers() -> dict:
    return {"accept": "application/json", "Authorization": f"Bearer {tokens.token}"}


# NSE has 375 trading minutes per session (9:15 AM–3:30 PM IST).
_NSE_BARS_PER_DAY: dict[str, float] = {
    "1m": 375, "3m": 125, "5m": 75, "15m": 25, "30m": 12.5,
    "1H": 6.25, "2H": 3.125, "4H": 1.5625,
}

_IST = timedelta(hours=5, minutes=30)

# Upstox V3 historical-candle API: maximum calendar-day span allowed per request.
# Exceeding these triggers HTTP 400 "invalid date range" / UDAPI1XXXXX errors.
#   minutes/hours: 200 days
#   days/weeks/months: 2000 days
# We use slightly smaller values to give a safe margin.
_API_MAX_DAYS: dict[str, int] = {
    "minutes": 190,
    "hours":   190,
    "days":    1900,
    "weeks":   1900,
    "months":  1900,
}


def _from_to(interval: str, count: int) -> tuple[str, str]:
    """Return (from_date, to_date) strings in IST covering ~count bars.

    days_back is capped to the Upstox V3 per-request limit for the interval
    unit so we never send a request that Upstox rejects with 'invalid date range'.
    """
    today_ist = (datetime.now(timezone.utc) + _IST).date()
    unit = _UNIT_MAP.get(interval, ("days", 1))[0]
    max_days = _API_MAX_DAYS.get(unit, 190)

    if interval in _NSE_BARS_PER_DAY:
        bars_per_day = _NSE_BARS_PER_DAY[interval]
        trading_days_needed = math.ceil(count / bars_per_day)
        # ×1.7 for weekends/holidays + 5-day buffer
        days_back = int(trading_days_needed * 1.7) + 5
    else:
        secs = interval_seconds(interval) * count
        days_back = secs // 86400 + 30

    # Clamp so we never exceed the Upstox API limit for this interval unit.
    days_back = max(5, min(int(days_back), max_days))

    frm = today_ist - timedelta(days=days_back)
    return frm.isoformat(), today_ist.isoformat()


def _parse_rows(rows: list) -> dict[int, dict]:
    """Convert Upstox candle rows → {ts: bar_dict}.

    Upstox V3 candle row format: [timestamp, open, high, low, close, volume, oi]

    Robustness notes:
    - timestamp: ISO-8601 string ("2024-01-15T09:15:00+05:30") or Unix int/float
    - OHLC: float or occasionally string-formatted float
    - volume: integer for equities; NULL/None for NSE index instruments on intraday
              bars — default to 0 so index rows are NOT silently skipped.
    - oi: optional 7th element, not used
    """
    bars: dict[int, dict] = {}
    for row in rows:
        try:
            # ── Timestamp ────────────────────────────────────────────────
            ts_raw = row[0]
            if isinstance(ts_raw, (int, float)):
                ts = int(ts_raw)
            else:
                ts = int(datetime.fromisoformat(str(ts_raw)).timestamp())

            # ── OHLC ─────────────────────────────────────────────────────
            open_  = float(row[1])
            high   = float(row[2])
            low    = float(row[3])
            close  = float(row[4])

            # ── Volume ───────────────────────────────────────────────────
            # NSE index intraday candles return null/None for volume.
            # int(None) raises TypeError → silently skips the row → empty
            # bars → mock fallback.  Use 0 when absent/null.
            raw_vol = row[5] if len(row) > 5 else None
            volume  = int(raw_vol) if raw_vol is not None else 0

            bars[ts] = {
                "time": ts,
                "open": open_, "high": high, "low": low, "close": close,
                "volume": volume,
            }
        except (ValueError, IndexError, KeyError, TypeError, AttributeError):
            continue
    return bars


def _is_date_range_error(resp: httpx.Response) -> bool:
    """Return True when the Upstox response indicates an invalid/too-large date range."""
    try:
        body   = resp.json()
        errors = body.get("errors") or []
        msg    = (errors[0].get("message") or "").lower() if errors else ""
        code   = (errors[0].get("errorCode") or "").upper() if errors else ""
        return (
            resp.status_code == 400
            and (
                "date" in msg
                or "range" in msg
                or "invalid" in msg
                or "UDAPI" in code   # Upstox API error code prefix
            )
        )
    except Exception:
        return resp.status_code == 400


async def _fetch_historical(key: str, unit: str, value: int, to: str, frm: str) -> list:
    """GET the Upstox V3 historical-candle endpoint and return raw candle rows.

    Raises ValueError (via _upstox_raise) on non-date-range HTTP errors.
    Returns an empty list if a date-range error occurred (caller should retry
    with a shorter span).
    """
    url = f"{API_BASE}/v3/historical-candle/{key}/{unit}/{value}/{to}/{frm}"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers=_headers())

    if resp.is_success:
        return resp.json().get("data", {}).get("candles", []) or []

    if _is_date_range_error(resp):
        # Signal caller to retry with a shorter range instead of raising.
        try:
            msg = (resp.json().get("errors") or [{}])[0].get("message", "")
        except Exception:
            msg = f"HTTP {resp.status_code}"
        print(f"[history] Date-range rejected by Upstox [{frm} → {to}]: {msg}")
        return []          # empty → caller retries

    _upstox_raise(resp)    # non-date error → propagate immediately
    return []              # unreachable; keeps type checker happy


async def historical_candles(inst: Instrument, interval: str, count: int) -> list[dict]:
    unit, value = _UNIT_MAP.get(interval, ("days", 1))
    key = urllib.parse.quote(inst.instrument_key, safe="")
    frm, to = _from_to(interval, count)

    # ── Historical (completed prior sessions) ─────────────────────────────
    # Attempt 1: full computed range (already capped by _from_to/_API_MAX_DAYS).
    # Attempt 2: half the range — safety net if the cap is still too wide for
    #            a specific instrument type or Upstox account tier.
    hist_rows: list = []
    frm_attempt = frm

    for attempt in range(2):
        hist_rows = await _fetch_historical(key, unit, value, to, frm_attempt)
        if hist_rows:
            break
        if attempt == 0:
            # No data returned (or date-range rejected) — cut the window in half.
            from_dt   = datetime.fromisoformat(frm_attempt)
            to_dt     = datetime.fromisoformat(to)
            half_days = max(5, (to_dt.date() - from_dt.date()).days // 2)
            frm_attempt = (to_dt.date() - timedelta(days=half_days)).isoformat()
            print(
                f"[history] Retrying {inst.symbol} {interval} with shorter range "
                f"[{frm_attempt} → {to}] (was [{frm} → {to}])"
            )

    if not hist_rows:
        print(
            f"[history] Upstox returned 0 rows for {inst.symbol} {interval} "
            f"after both attempts — check token / instrument key"
        )

    bars = _parse_rows(hist_rows)

    if hist_rows and not bars:
        sample = hist_rows[0] if hist_rows else []
        print(
            f"[history] All {len(hist_rows)} rows failed to parse for "
            f"{inst.symbol} {interval}.  Sample row: {sample!r}"
        )

    # ── Intraday supplement (today's live session) ────────────────────────
    # The historical endpoint only returns COMPLETED prior sessions for intraday
    # intervals.  Today's in-progress bars need the separate intraday endpoint.
    if interval in _NSE_BARS_PER_DAY:
        intra_url = f"{API_BASE}/v3/historical-candle/intraday/{key}/{unit}/{value}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                intra_resp = await client.get(intra_url, headers=_headers())
            if intra_resp.is_success:
                intra_rows = intra_resp.json().get("data", {}).get("candles", []) or []
                bars.update(_parse_rows(intra_rows))
            else:
                print(
                    f"[history] Intraday endpoint returned {intra_resp.status_code} "
                    f"for {inst.symbol} {interval}"
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[history] Intraday supplement failed ({inst.symbol} {interval}): {exc}")

    if not bars:
        raise ValueError(
            f"No candle data from Upstox for {inst.symbol} {interval} "
            f"(hist_rows={len(hist_rows)}, intraday also empty)"
        )

    out = sorted(bars.values(), key=lambda c: c["time"])
    return out[-count:]


async def ltp(inst: Instrument) -> dict | None:
    key = urllib.parse.quote(inst.instrument_key, safe="")
    url = f"{API_BASE}/v3/market-quote/ltp?instrument_key={key}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
        if not resp.is_success:
            _upstox_raise(resp)
        data = resp.json().get("data", {})
    for _, v in data.items():
        return {"ltp": v.get("last_price"), "ts": v.get("ltt")}
    return None


# ── Broker: Account Funds ─────────────────────────────────────────────────

async def get_funds() -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API_BASE}/v2/user/fund-margin", headers=_headers())
        if not r.is_success:
            _upstox_raise(r)
    return r.json().get("data", {})


# ── Broker: Short-term Positions ──────────────────────────────────────────

async def get_positions() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{API_BASE}/v2/portfolio/short-term-positions", headers=_headers()
        )
        if not r.is_success:
            _upstox_raise(r)
    return r.json().get("data") or []


# ── Broker: Orders ────────────────────────────────────────────────────────

async def get_orders() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API_BASE}/v2/order/retrieve-all", headers=_headers())
        if not r.is_success:
            _upstox_raise(r)
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
        if not r.is_success:
            _upstox_raise(r)
    return r.json().get("data", {})


# ── Broker: Cancel Order ──────────────────────────────────────────────────

async def cancel_order(order_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(
            f"{API_BASE}/v2/order/cancel?order_id={order_id}",
            headers=_headers(),
        )
        if not r.is_success:
            _upstox_raise(r)
    return r.json().get("data", {})


# ── Broker: Option Chain ──────────────────────────────────────────────────

async def get_option_chain(underlying_key: str, expiry_date: str) -> list[dict]:
    key = urllib.parse.quote(underlying_key, safe="")
    url = f"{API_BASE}/v2/option/chain?instrument_key={key}&expiry_date={expiry_date}"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, headers=_headers())
        if not r.is_success:
            _upstox_raise(r)
    return r.json().get("data") or []


async def get_option_expiries(underlying_key: str) -> list[str]:
    """Return a sorted list of available option expiry dates (YYYY-MM-DD) from Upstox.

    Calls /v2/option/contract without an expiry_date filter to discover every
    listed contract for the underlying, then extracts and deduplicates the expiry
    dates, filtering out dates in the past.
    """
    key = urllib.parse.quote(underlying_key, safe="")
    url = f"{API_BASE}/v2/option/contract?instrument_key={key}"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, headers=_headers())
        if not r.is_success:
            _upstox_raise(r)
    data = r.json().get("data") or []
    today = (datetime.now(timezone.utc) + _IST).date().isoformat()
    seen: set[str] = set()
    for contract in data:
        raw = contract.get("expiry")
        if not raw:
            continue
        # Upstox may return "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS+05:30"
        date_str = str(raw)[:10]
        if len(date_str) == 10 and date_str >= today:
            seen.add(date_str)
    return sorted(seen)
