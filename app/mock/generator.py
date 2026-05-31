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
