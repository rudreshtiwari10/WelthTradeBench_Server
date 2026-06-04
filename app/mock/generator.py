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

# Intraday intervals need NSE-market-aligned timestamps (9:15 AM – 3:30 PM IST).
_INTRADAY = {"1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H"}
_IST_OFFSET = 19800   # 5 h 30 min in seconds


_MCX_SYMBOLS = {
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


def _market_bar_times(step: int, count: int, symbol: str = "") -> list[int]:
    """Return `count` UTC Unix timestamps for intraday bars, oldest first.

    NSE  (default): Mon–Fri 9:15 AM – 3:30 PM IST
    MCX commodities: Mon–Fri 9:00 AM – 11:30 PM IST (extended evening session)
    """
    import datetime as _dt

    IST = _dt.timezone(_dt.timedelta(seconds=_IST_OFFSET))
    is_mcx = symbol.upper() in _MCX_SYMBOLS
    OPEN  = _dt.time(9,  0) if is_mcx else _dt.time(9, 15)
    CLOSE = _dt.time(23, 30) if is_mcx else _dt.time(15, 30)
    step_min = max(1, step // 60)
    step_td  = _dt.timedelta(seconds=step)

    now = _dt.datetime.now(IST)
    mins = now.hour * 60 + now.minute
    bar_min = (mins // step_min) * step_min
    bar = now.replace(hour=bar_min // 60, minute=bar_min % 60, second=0, microsecond=0)

    result: list[int] = []
    while len(result) < count:
        if bar.weekday() < 5 and OPEN <= bar.time() < CLOSE:
            result.append(int(bar.timestamp()))
        bar -= step_td
    result.reverse()
    return result


# Rough starting prices so different symbols look distinct.
_BASE_PRICE = {
    "NIFTY": 23900, "BANKNIFTY": 51000, "SENSEX": 78000, "FINNIFTY": 23000,
    "RELIANCE": 2900, "TCS": 3900, "HDFCBANK": 1700, "INFY": 1800,
    "ICICIBANK": 1200, "SBIN": 820, "TITAN": 3400, "APOLLOHOSP": 6200,
    "NESTLEIND": 2500, "TATAMOTORS": 980, "WIPRO": 540, "ITC": 470,
    # MCX commodities — prices in INR per standard MCX unit (2025 levels)
    # Gold: Rs/10g, Silver: Rs/kg, CrudeOil: Rs/bbl, NatGas: Rs/MMBtu, metals: Rs/kg
    "GOLD": 92000, "GOLDM": 92000, "GOLDPETAL": 9200,
    "SILVER": 97000, "SILVERM": 97000, "SILVERMIC": 97000,
    "CRUDEOIL": 6200, "CRUDEOILM": 6200,
    "NATURALGAS": 310,
    "COPPER": 830, "COPPERM": 830,
    "ZINC": 265, "ZINCP": 265,
    "ALUMINIUM": 235, "ALUMINIM": 235,
    "NICKEL": 1580,
    "LEAD": 190,
}


def interval_seconds(interval: str) -> int:
    return INTERVAL_SECONDS.get(interval, 86400)


def generate_candles(symbol: str, interval: str, count: int = 600) -> list[dict]:
    seed = sum(ord(c) for c in symbol) * 7919 + count
    rnd = random.Random(seed)
    step = interval_seconds(interval)

    if interval in _INTRADAY:
        timestamps = _market_bar_times(step, count, symbol)
    else:
        # Align to IST midnight (same formula as the frontend barTs / CandleTimer).
        # UTC midnight alignment (now % step) is 5:30 h off from IST midnight,
        # causing live-tick bars and historical bars to have different timestamps
        # for 1D and 1W intervals.
        now = int(time.time())
        last_time = (now + _IST_OFFSET) // step * step - _IST_OFFSET
        timestamps = [last_time - (count - 1 - i) * step for i in range(count)]

    price = _BASE_PRICE.get(symbol.upper(), 1000) * (0.85 + rnd.random() * 0.1)
    vol = 0.012
    out: list[dict] = []
    for t in timestamps:
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
