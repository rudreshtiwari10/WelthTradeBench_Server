"""yfinance-sourced daily backfill — fills the 1D base series to full multi-year
depth when Upstox's own history is too shallow.

Upstox's intraday (1m) history is short and even its daily history may not reach
the full target window.  yfinance serves 10+ years of daily index data for free,
so we use it as the PRIMARY source for the 1D base series.  Live / current data
still comes from the Upstox socket (and the EOD incremental sync); this module
only backfills COMPLETED daily candles.

There is no free source for multi-year 1-minute data (yfinance caps 1m at 7 days,
which Upstox already covers), so intraday depth remains bounded by Upstox's 1m
history — the 1D series is what gives deep history for 1D/1W/1M charts.

Timestamps are normalised to 00:00 IST of each trading date so they match the
anchor Upstox uses for its own stored daily candles (mod 86400 == 66600) — this
prevents duplicate daily rows when yfinance and Upstox cover the same dates.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

from ..instruments import Instrument
from .store import (
    count_candles,
    get_timestamp_range,
    update_sync_state,
    upsert_candles,
)

_IST = timedelta(hours=5, minutes=30)
_IST_OFFSET = 19800  # 5h30m in seconds

# Stored index symbol → Yahoo Finance ticker.  Kept in sync with the indices in
# config.stored_symbols().  Unlisted symbols fall back to the "SYMBOL.NS" convention.
YF_DAILY_TICKERS: dict[str, str] = {
    "NIFTY":      "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "SENSEX":     "^BSESN",
    "FINNIFTY":   "^CNXFIN",
    "MIDCPNIFTY": "NIFTY_MID_SELECT.NS",
    "NIFTY100":   "^CNX100",
    "BANKEX":     "BANKEX.BO",
}


def _normalize_daily_ts(ts) -> int:
    """pandas Timestamp → unix seconds at 00:00 IST of that calendar date.

    Matches the anchor Upstox stores its daily candles at, so yfinance and Upstox
    daily rows for the same date share one timestamp (idempotent upsert).
    """
    d = ts.date()
    midnight_utc = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
    return int(midnight_utc) - _IST_OFFSET


async def backfill_yf_daily(inst: Instrument, years: int = 5) -> dict:
    """Download `years` of daily candles from yfinance and upsert into the 1D base.

    Skips the current (in-progress) IST session — today's bar is built live and
    persisted by the EOD sync.  Returns a summary dict.
    """
    import yfinance as yf

    sym = inst.symbol.upper()
    ticker = YF_DAILY_TICKERS.get(sym) or f"{sym}.NS"
    today_ist = (datetime.now(timezone.utc) + _IST).date()

    def _fetch() -> list[dict]:
        df = yf.download(
            ticker,
            period=f"{years * 365}d",
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
        if df is None or df.empty:
            return []
        # yfinance ≥0.2 returns multi-level columns for a single ticker; flatten.
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        rows: list[dict] = []
        for ts, row in df.iterrows():
            try:
                if ts.date() >= today_ist:
                    continue  # in-progress session handled live
                o = float(row["Open"]); h = float(row["High"])
                l = float(row["Low"]);  c = float(row["Close"])
                if any(math.isnan(x) for x in (o, h, l, c)):
                    continue
                raw_vol = row["Volume"] if "Volume" in row else 0
                vol = int(raw_vol) if not (isinstance(raw_vol, float) and math.isnan(raw_vol)) else 0
                rows.append({
                    "time":   _normalize_daily_ts(ts),
                    "open":   round(o, 2), "high": round(h, 2),
                    "low":    round(l, 2), "close": round(c, 2),
                    "volume": vol,
                })
            except (TypeError, ValueError, KeyError):
                continue
        return rows

    candles = await asyncio.get_event_loop().run_in_executor(None, _fetch)

    upserted = 0
    if candles:
        upserted = await upsert_candles(sym, "1D", candles, source="yfinance")

    earliest, latest = await get_timestamp_range(sym, "1D")
    total = await count_candles(sym, "1D")
    await update_sync_state(
        sym, "1D",
        earliest_ts=earliest, latest_ts=latest, total_candles=total,
        backfill_complete=True, backfill_source="yfinance",
    )

    return {
        "symbol":   sym,
        "timeframe": "1D",
        "ticker":   ticker,
        "upserted": upserted,
        "total":    total,
        "earliest_ts": earliest,
        "latest_ts":   latest,
        "done":     True,
    }
