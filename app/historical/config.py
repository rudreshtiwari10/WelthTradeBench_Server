"""Configuration for the historical data pipeline.

Single source of truth for WHICH symbols we persist and WHICH base timeframes
we store.  The whole pipeline now stores only two base series per symbol:

  • "1m"  — the source of truth for every intraday interval (3m/5m/15m/30m/1H/2H/4H
            are aggregated from it on demand).
  • "1D"  — a tiny daily series for deep history (Upstox's 1-minute lookback is
            short, often ~1-2 years), from which 1W/1M are aggregated.

Everything else (5m, 15m, 1H, …) is derived at read-time in store.get_chart_candles,
so we never duplicate the same information across collections.
"""
from __future__ import annotations

import os

# Base series we persist.  Order matters for downloads (1m first, then 1D).
STORED_TIMEFRAMES: list[str] = ["1m", "1D"]

# Default symbol universe — major indices only.  All are NSE/BSE indices, so the
# bar-boundary anchor is always 09:15 IST (mopen = 13500).  Override with the
# HIST_SYMBOLS env var (comma-separated).
_DEFAULT_HIST_SYMBOLS = "NIFTY,BANKNIFTY,SENSEX,NIFTY100,FINNIFTY,MIDCPNIFTY,BANKEX"


def stored_symbols() -> list[str]:
    """Return the configured list of symbols to maintain historical data for."""
    raw = os.getenv("HIST_SYMBOLS", _DEFAULT_HIST_SYMBOLS)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def is_stored_symbol(symbol: str) -> bool:
    """True when `symbol` is served from the local historical store rather than
    fetched live from Upstox on every chart load."""
    return symbol.upper() in set(stored_symbols())
