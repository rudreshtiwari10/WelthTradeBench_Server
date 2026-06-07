"""Bulk historical backfill: downloads OHLCV data in chunks from the Upstox REST API,
working backwards in time from the last completed market session.

Two public entry points:
  backfill_next_chunk()  — ONE API call; resumable via cursor_override param.
  backfill_full()        — loops all chunks until target_days window is covered.

Cursor design: backfill_full tracks the scan position locally (cursor_override) and
passes it to each backfill_next_chunk call.  This ensures the scan advances even
when a chunk returns 0 candles (e.g. API limit, holiday period, unsupported lookback).
Without this, earliest_ts would never be set and the same date range would be fetched
in an infinite loop.
"""
from __future__ import annotations

import asyncio
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from ..instruments import Instrument
from ..upstox.rest import (
    _IST,
    _MCX_MOPEN_UTC,
    _MOPEN_UTC,
    _UNIT_MAP,
    _aggregate_bars,
    _fetch_historical,
    _parse_rows,
)
from .store import (
    count_candles,
    get_timestamp_range,
    update_sync_state,
    upsert_candles,
)

# Default historical depth
BACKFILL_TARGET_DAYS: int = 5 * 365

# Max calendar-day span per API chunk.
# NSE index intraday data has much shorter lookback than equity/futures:
#   1m  → ~7  calendar days available
#   5m  → ~25 calendar days available
#   1H  → ~400 days available
# Using 30 days for minutes prevents HTTP 400 "Invalid date range" rejections.
_CHUNK_DAYS: dict[str, int] = {
    "minutes": 30,
    "hours":   180,
    "days":    1800,
    "weeks":   1800,
    "months":  1800,
}

# Stop backfill after this many consecutive empty chunks — we've hit the
# platform's historical data boundary for this instrument/timeframe.
_MAX_EMPTY_CHUNKS = 3

# Rate-limit pause between consecutive API calls
_CHUNK_SLEEP: float = 0.6


def _last_completed_session_ist() -> date:
    """Return the date of the last fully completed NSE trading session (IST).

    Rolls back if the market hasn't closed yet today (NSE closes 15:30 IST)
    and over weekends.
    """
    now_ist = datetime.now(timezone.utc) + _IST
    market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    candidate = now_ist.date() if now_ist >= market_close else now_ist.date() - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


async def backfill_next_chunk(
    inst: Instrument,
    timeframe: str,
    target_days: int = BACKFILL_TARGET_DAYS,
    cursor_override: date | None = None,
) -> dict:
    """Fetch and store ONE historical chunk.

    cursor_override: the `fetch_to` date to use for this chunk.  backfill_full()
    passes chunk_start from the previous result so the scan always advances,
    even when the API returned 0 candles.

    Returns: {done, candles_upserted, fetch_range: (frm_str, to_str), symbol, timeframe}
    """
    unit, value = _UNIT_MAP.get(timeframe, ("days", 1))
    max_chunk = _CHUNK_DAYS.get(unit, 180)
    key = urllib.parse.quote(inst.instrument_key, safe="")
    mopen = _MCX_MOPEN_UTC if inst.kind == "commodity" else _MOPEN_UTC

    last_session = _last_completed_session_ist()
    target_start = last_session - timedelta(days=target_days)

    # Determine fetch_to:
    # Priority 1 — explicit cursor from the caller (handles zero-candle chunks correctly)
    # Priority 2 — earliest stored timestamp (resume after a restart)
    # Priority 3 — first run: start from day after last completed session
    if cursor_override is not None:
        fetch_to = cursor_override
    else:
        earliest_ts, _ = await get_timestamp_range(inst.symbol, timeframe)
        if earliest_ts:
            fetch_to = datetime.fromtimestamp(earliest_ts, tz=timezone.utc).date()
        else:
            # Always use tomorrow in IST — identical to rest.py _from_to().
            # last_session+1 fails on weekends (lands on today = non-trading day)
            # which Upstox treats as an open boundary and returns empty.
            now_ist = datetime.now(timezone.utc) + _IST
            fetch_to = now_ist.date() + timedelta(days=1)

    if fetch_to <= target_start:
        await update_sync_state(inst.symbol, timeframe, backfill_complete=True)
        return {
            "done": True, "candles_upserted": 0,
            "symbol": inst.symbol, "timeframe": timeframe,
        }

    chunk_start = max(fetch_to - timedelta(days=max_chunk), target_start)
    frm_str = chunk_start.isoformat()
    to_str = fetch_to.isoformat()

    candles_upserted = 0
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

        if candles:
            candles_upserted = await upsert_candles(
                inst.symbol, timeframe, candles, source="backfill"
            )

    except Exception as exc:
        print(f"[backfill] {inst.symbol}/{timeframe} {frm_str}→{to_str}: {exc}")

    await _refresh_sync_state(inst.symbol, timeframe)

    done = chunk_start <= target_start
    if done:
        await update_sync_state(inst.symbol, timeframe, backfill_complete=True)

    return {
        "done": done,
        "candles_upserted": candles_upserted,
        "fetch_range": (frm_str, to_str),
        "symbol": inst.symbol,
        "timeframe": timeframe,
    }


async def backfill_full(
    inst: Instrument,
    timeframe: str,
    target_days: int = BACKFILL_TARGET_DAYS,
) -> dict:
    """Run ALL chunks until target_days is covered.

    Tracks the scan cursor locally so the window always advances even when
    a chunk returns 0 candles (API limit, holiday gap, unsupported lookback).
    Prints rich progress to the server terminal after every chunk.
    """
    import math
    import time as _time

    unit = _UNIT_MAP.get(timeframe, ("days", 1))[0]
    max_chunk = _CHUNK_DAYS.get(unit, 180)
    estimated_chunks = math.ceil(target_days / max_chunk)

    total_upserted = 0
    chunks = 0
    cursor: date | None = None
    t_start = _time.monotonic()

    tag = f"[{inst.symbol}/{timeframe}]"
    print(f"\n{tag} ── Starting download ──────────────────────────────────────")
    print(f"{tag} Target : {target_days // 365} years  (~{estimated_chunks} chunks of {max_chunk} days each)")
    print(f"{tag} ────────────────────────────────────────────────────────────")

    consecutive_empty = 0

    while True:
        result = await backfill_next_chunk(inst, timeframe, target_days, cursor_override=cursor)
        total_upserted += result.get("candles_upserted", 0)
        chunks += 1

        frm, to = result.get("fetch_range", ("?", "?"))
        if frm and frm != "?":
            cursor = date.fromisoformat(frm)

        # Track consecutive empty chunks to detect the platform data boundary
        if result["candles_upserted"] == 0:
            consecutive_empty += 1
        else:
            consecutive_empty = 0

        pct = min(100.0, chunks / estimated_chunks * 100)
        elapsed = _time.monotonic() - t_start
        bar_filled = int(pct / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        eta_s = (elapsed / chunks) * (estimated_chunks - chunks) if chunks < estimated_chunks else 0

        status = "empty" if result["candles_upserted"] == 0 else f"+{result['candles_upserted']:,}"
        print(
            f"{tag} [{bar}] {pct:5.1f}%  "
            f"chunk {chunks}/{estimated_chunks}  "
            f"{frm} → {to}  "
            f"{status} candles  "
            f"total={total_upserted:,}  "
            f"elapsed={elapsed:.0f}s  "
            f"eta≈{eta_s:.0f}s"
        )

        if result["done"]:
            break

        # Stop early when Upstox has no data for older periods
        if consecutive_empty >= _MAX_EMPTY_CHUNKS:
            print(
                f"{tag} ⚠ Data boundary reached after {chunks} chunks "
                f"({consecutive_empty} consecutive empty) — "
                f"Upstox has no older {timeframe} data for this instrument"
            )
            await update_sync_state(inst.symbol, timeframe, backfill_complete=True)
            break

        await asyncio.sleep(_CHUNK_SLEEP)

    elapsed_total = _time.monotonic() - t_start
    print(f"{tag} ────────────────────────────────────────────────────────────")
    print(f"{tag} ✓ DONE  {chunks} chunks  {total_upserted:,} candles  {elapsed_total:.0f}s total\n")

    return {
        "symbol":         inst.symbol,
        "timeframe":      timeframe,
        "chunks":         chunks,
        "total_upserted": total_upserted,
        "done":           True,
    }


async def _refresh_sync_state(symbol: str, timeframe: str) -> None:
    earliest, latest = await get_timestamp_range(symbol, timeframe)
    total = await count_candles(symbol, timeframe)
    backfill_days = 0
    if earliest:
        now_ts = datetime.now(timezone.utc).timestamp()
        backfill_days = int((now_ts - earliest) / 86400)
    await update_sync_state(
        symbol, timeframe,
        earliest_ts=earliest,
        latest_ts=latest,
        total_candles=total,
        backfill_current_days=backfill_days,
    )
