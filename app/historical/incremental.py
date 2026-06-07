"""Incremental sync: fetches the last SYNC_WINDOW_DAYS from the Upstox REST API
and upserts into the historical store.

Bridges the gap between the bulk backfill dataset (which may lag by 5-7 days)
and today's live data, so the store is always queryable without waiting for
the full backfill to complete.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone

from ..instruments import Instrument
from ..upstox.rest import (
    _fetch_historical,
    _parse_rows,
    _UNIT_MAP,
    _IST,
    _MCX_MOPEN_UTC,
    _MOPEN_UTC,
    _aggregate_bars,
)
from .store import update_sync_state, upsert_candles

SYNC_WINDOW_DAYS: int = 7


async def incremental_sync(inst: Instrument, timeframe: str) -> dict:
    """Fetch the last SYNC_WINDOW_DAYS and upsert into the store.

    Returns {symbol, timeframe, upserted, error?}.
    """
    unit, value = _UNIT_MAP.get(timeframe, ("days", 1))
    key = urllib.parse.quote(inst.instrument_key, safe="")
    mopen = _MCX_MOPEN_UTC if inst.kind == "commodity" else _MOPEN_UTC

    now_ist = datetime.now(timezone.utc) + _IST
    to_str = (now_ist.date() + timedelta(days=1)).isoformat()
    frm_str = (now_ist.date() - timedelta(days=SYNC_WINDOW_DAYS)).isoformat()

    try:
        rows = await _fetch_historical(key, unit, value, to_str, frm_str)

        if not rows and unit == "hours" and value > 1:
            rows_1h = await _fetch_historical(key, "hours", 1, to_str, frm_str)
            if rows_1h:
                bars = _aggregate_bars(_parse_rows(rows_1h), unit, value, mopen)
                candles = list(bars.values())
            else:
                candles = []
        else:
            candles = list(_parse_rows(rows).values())

        upserted = 0
        if candles:
            upserted = await upsert_candles(
                inst.symbol, timeframe, candles, source="incremental"
            )
            await update_sync_state(
                inst.symbol, timeframe,
                last_incremental_sync=datetime.now(timezone.utc),
            )

        return {"symbol": inst.symbol, "timeframe": timeframe, "upserted": upserted}

    except Exception as exc:
        print(f"[incremental] {inst.symbol}/{timeframe}: {exc}")
        return {"symbol": inst.symbol, "timeframe": timeframe, "upserted": 0, "error": str(exc)}
