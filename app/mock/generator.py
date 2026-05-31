"""Deterministic mock OHLCV + live tick simulator (fallback when no Upstox token)."""
from __future__ import annotations

import math
import random
import time

INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400,
    "1D": 86400, "1W": 604800, "1M": 2592000,
}

# Rough starting prices so different symbols look distinct.
_BASE_PRICE = {
    "NIFTY": 23900, "BANKNIFTY": 51000, "SENSEX": 78000, "FINNIFTY": 23000,
    "RELIANCE": 2900, "TCS": 3900, "HDFCBANK": 1700, "INFY": 1800,
    "ICICIBANK": 1200, "SBIN": 820, "TITAN": 3400, "APOLLOHOSP": 6200,
    "NESTLEIND": 2500, "TATAMOTORS": 980, "WIPRO": 540, "ITC": 470,
}


def interval_seconds(interval: str) -> int:
    return INTERVAL_SECONDS.get(interval, 86400)


def generate_candles(symbol: str, interval: str, count: int = 600) -> list[dict]:
    seed = sum(ord(c) for c in symbol) * 7919 + count
    rnd = random.Random(seed)
    step = interval_seconds(interval)
    now = int(time.time())
    last_time = now - (now % step)

    price = _BASE_PRICE.get(symbol.upper(), 1000) * (0.85 + rnd.random() * 0.1)
    vol = 0.012
    out: list[dict] = []
    for i in range(count - 1, -1, -1):
        t = last_time - i * step
        vol = max(0.004, min(0.035, vol + (rnd.random() - 0.5) * 0.004))
        drift = (rnd.random() - 0.49) * vol
        o = price
        c = max(1.0, o * (1 + drift))
        wick = o * vol * (0.4 + rnd.random())
        h = max(o, c) + wick * rnd.random()
        l = min(o, c) - wick * rnd.random()
        v = int((0.6 + rnd.random() * 1.8) * 1_000_000 * (1 + abs(drift) * 30))
        out.append({
            "time": t,
            "open": round(o, 2), "high": round(h, 2), "low": round(l, 2), "close": round(c, 2),
            "volume": v,
        })
        price = c
    return out


def next_tick(last_price: float, symbol: str) -> float:
    """Random-walk one tick from the last price."""
    drift = (random.random() - 0.5) * last_price * 0.0008
    return round(max(1.0, last_price + drift), 2)


# ── Derivatives helpers ────────────────────────────────────────────────────

def option_premium_py(spot: float, strike: float, opt_type: str, days: float) -> float:
    """Black-Scholes-lite premium (mirrors the frontend optionPremium function)."""
    intrinsic = max(0.0, spot - strike) if opt_type == "CE" else max(0.0, strike - spot)
    t = max(0.5, days) / 365.0
    vol = 0.18
    time_value = (
        spot * vol * math.sqrt(t)
        * math.exp(-((spot - strike) / (spot * 0.12)) ** 2 / 2.0)
    )
    return max(0.05, round(intrinsic + time_value, 2))


def generate_option_candles(
    underlying: str, strike: float, opt_type: str, expiry: str,
    interval: str, count: int
) -> list[dict]:
    """Synthetic option OHLCV built from underlying spot candles via Black-Scholes."""
    import datetime as _dt
    spot_candles = generate_candles(underlying, interval, count + 30)
    try:
        expiry_dt = _dt.datetime.strptime(expiry, "%Y-%m-%d")
    except ValueError:
        expiry_dt = _dt.datetime.utcnow() + _dt.timedelta(days=30)

    result = []
    for c in spot_candles[-count:]:
        candle_dt = _dt.datetime.utcfromtimestamp(c["time"])
        days_left = max(0.5, (expiry_dt - candle_dt).total_seconds() / 86400.0)

        # CE: high spot → high option.  PE: low spot → high option.
        o  = option_premium_py(c["open"],  strike, opt_type, days_left)
        cl = option_premium_py(c["close"], strike, opt_type, days_left)
        if opt_type == "CE":
            h = option_premium_py(c["high"], strike, opt_type, days_left)
            l = option_premium_py(c["low"],  strike, opt_type, days_left)
        else:
            h = option_premium_py(c["low"],  strike, opt_type, days_left)
            l = option_premium_py(c["high"], strike, opt_type, days_left)

        result.append({
            "time":   c["time"],
            "open":   o,
            "high":   max(o, h, l, cl),
            "low":    min(o, h, l, cl),
            "close":  cl,
            "volume": max(1, int(c["volume"] * 0.05)),
        })
    return result


def generate_futures_candles(
    underlying: str, expiry: str, interval: str, count: int
) -> list[dict]:
    """Synthetic futures OHLCV = spot × cost-of-carry (6.5% p.a.)."""
    import datetime as _dt
    spot_candles = generate_candles(underlying, interval, count)
    try:
        expiry_dt = _dt.datetime.strptime(expiry, "%Y-%m-%d")
    except ValueError:
        expiry_dt = _dt.datetime.utcnow() + _dt.timedelta(days=30)

    result = []
    for c in spot_candles:
        candle_dt = _dt.datetime.utcfromtimestamp(c["time"])
        days_left = max(0.0, (expiry_dt - candle_dt).total_seconds() / 86400.0)
        factor = 1.0 + 0.065 * days_left / 365.0
        result.append({
            "time":   c["time"],
            "open":   round(c["open"]  * factor, 2),
            "high":   round(c["high"]  * factor, 2),
            "low":    round(c["low"]   * factor, 2),
            "close":  round(c["close"] * factor, 2),
            "volume": c["volume"],
        })
    return result
