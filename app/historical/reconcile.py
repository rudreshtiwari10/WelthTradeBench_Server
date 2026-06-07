"""EOD reconciliation: after the historical backfill catches up to the incremental
window, re-fetches the overlapping period from Upstox so that the authoritative
backfill version overwrites any API-fetched records.

This is run once per day after market close (scheduled by scheduler.py).
Only executes when the backfill is marked complete for the given pair.
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
from .store import get_sync_state, update_sync_state, upsert_candles

RECONCILE_WINDOW_DAYS: int = 7


async def reconcile_symbol_timeframe(inst: Instrument, timeframe: str) -> dict:
    """Re-fetch RECONCILE_WINDOW_DAYS and upsert as 'reconciled', overwriting
    any incremental records in that window with the authoritative Upstox history.

    Skips pairs where the backfill is not yet complete.
    """
    state = await get_sync_state(inst.symbol, timeframe)
    if not state or not state.get("backfill_complete"):
        return {
            "symbol": inst.symbol, "timeframe": timeframe,
            "skipped": "backfill_not_complete",
        }

    unit, value = _UNIT_MAP.get(timeframe, ("days", 1))
    key = urllib.parse.quote(inst.instrument_key, safe="")
    mopen = _MCX_MOPEN_UTC if inst.kind == "commodity" else _MOPEN_UTC

    now_ist = datetime.now(timezone.utc) + _IST
    to_str = (now_ist.date() + timedelta(days=1)).isoformat()
    frm_str = (now_ist.date() - timedelta(days=RECONCILE_WINDOW_DAYS)).isoformat()

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

        reconciled = 0
        if candles:
            reconciled = await upsert_candles(
                inst.symbol, timeframe, candles, source="reconciled"
            )
            await update_sync_state(
                inst.symbol, timeframe,
                last_reconcile=datetime.now(timezone.utc),
            )

        return {"symbol": inst.symbol, "timeframe": timeframe, "reconciled": reconciled}

    except Exception as exc:
        print(f"[reconcile] {inst.symbol}/{timeframe}: {exc}")
        return {"symbol": inst.symbol, "timeframe": timeframe, "reconciled": 0, "error": str(exc)}
