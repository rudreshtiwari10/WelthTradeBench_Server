"""FastAPI router for the historical data pipeline.

All endpoints live under /api/historical/* and are isolated from the
live-market (/api/history, /ws) and backtest (/api/backtest/*) paths.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from ..instruments import by_symbol
from ..upstox.feed import hub
from .backfill import BACKFILL_TARGET_DAYS, backfill_full
from .gap_detector import detect_gaps
from .scheduler import DEFAULT_TIMEFRAMES
from .store import (
    get_all_sync_states,
    get_sync_state,
    get_timestamp_range,
    has_sufficient_data,
    query_candles,
    update_sync_state,
)

router = APIRouter(prefix="/api/historical", tags=["historical"])

# Tracks running download tasks: symbol → asyncio.Task
_running: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Download endpoint — the main entry point
# ---------------------------------------------------------------------------

@router.post("/download")
async def download_symbol(
    background_tasks: BackgroundTasks,
    symbol: str,
    timeframe: str | None = None,
    years: int = Query(5, ge=1, le=10),
    force: bool = False,
) -> dict:
    """Download full historical data for a symbol.

    Downloads from the last completed market session backwards for `years` years,
    in chunks, for every timeframe in HIST_TIMEFRAMES env var (default: 1D,1H,15m,5m,1m).

    Pass ?timeframe=1D to download a single timeframe instead of all.
    Pass ?years=N to override the default 5-year window.

    Runs in the background — poll /api/historical/status or
    /api/historical/pair-info to track progress.
    """
    if hub.mode != "upstox":
        raise HTTPException(status_code=403, detail="Upstox not authenticated")

    inst = by_symbol(symbol)
    if not inst:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")

    timeframes = [timeframe] if timeframe else DEFAULT_TIMEFRAMES
    target_days = years * 365

    # Reset backfill_complete so the download runs again from scratch
    if force:
        for tf in timeframes:
            await update_sync_state(symbol.upper(), tf, backfill_complete=False)

    # Duplicate guard is per symbol+timeframe so different timeframes can run in parallel
    already_running = [
        tf for tf in timeframes
        if not _running.get(f"{symbol.upper()}_{tf}", asyncio.Future()).done()
        and f"{symbol.upper()}_{tf}" in _running
    ]
    if already_running:
        return {
            "triggered":      False,
            "reason":         "already_running",
            "symbol":         symbol,
            "timeframes_busy": already_running,
        }

    async def _run_tf(tf: str) -> None:
        key = f"{symbol.upper()}_{tf}"
        print(f"[download] Starting {symbol.upper()}/{tf} — {years} years")
        try:
            result = await backfill_full(inst, tf, target_days=target_days)
            print(
                f"[download] {symbol.upper()}/{tf} complete: "
                f"{result['total_upserted']} candles, {result['chunks']} chunks"
            )
        except Exception as exc:
            print(f"[download] {symbol.upper()}/{tf} error: {exc}")
        finally:
            _running.pop(key, None)

    for tf in timeframes:
        key = f"{symbol.upper()}_{tf}"
        task = asyncio.get_event_loop().create_task(_run_tf(tf), name=f"download_{symbol}_{tf}")
        _running[key] = task

    return {
        "triggered":   True,
        "symbol":      symbol,
        "timeframes":  timeframes,
        "target_years": years,
        "note":        "Running in background. Poll /api/historical/status for progress.",
    }


@router.delete("/download/{symbol}")
async def cancel_download(symbol: str, timeframe: str | None = None) -> dict:
    """Cancel a running download. Cancels all timeframes for the symbol, or just one."""
    sym = symbol.upper()
    keys = (
        [f"{sym}_{timeframe}"]
        if timeframe
        else [k for k in _running if k.startswith(f"{sym}_")]
    )
    cancelled = []
    for key in keys:
        task = _running.get(key)
        if task and not task.done():
            task.cancel()
            _running.pop(key, None)
            cancelled.append(key.split("_", 1)[1])  # extract timeframe part
    if not cancelled:
        return {"cancelled": False, "reason": "not_running", "symbol": symbol}
    return {"cancelled": True, "symbol": symbol, "timeframes": cancelled}


# ---------------------------------------------------------------------------
# Status / query endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
async def historical_status() -> dict:
    """Overall pipeline status: sync state for all tracked symbol/timeframe pairs."""
    states = await get_all_sync_states()
    running_symbols = [k for k, t in _running.items() if not t.done()]
    complete_count = sum(1 for s in states if s.get("backfill_complete"))
    return {
        "mode":                      hub.mode,
        "configured_timeframes":     DEFAULT_TIMEFRAMES,
        "tracked_pairs":             len(states),
        "backfill_complete_pairs":   complete_count,
        "downloads_running":         running_symbols,
        "states":                    states,
    }


@router.get("/candles")
async def get_historical_candles(
    symbol: str,
    timeframe: str = "1D",
    from_ts: int | None = None,
    to_ts: int | None = None,
    limit: int = Query(2000, ge=1, le=10000),
) -> dict:
    """Query stored candles for any symbol/timeframe from the historical store."""
    candles = await query_candles(symbol, timeframe, from_ts, to_ts, limit)
    state = await get_sync_state(symbol, timeframe)
    earliest_ts, latest_ts = await get_timestamp_range(symbol, timeframe)
    return {
        "symbol":            symbol,
        "timeframe":         timeframe,
        "source":            "historical_store",
        "count":             len(candles),
        "backfill_complete": state.get("backfill_complete", False) if state else False,
        "earliest_ts":       earliest_ts,
        "latest_ts":         latest_ts,
        "candles":           candles,
    }


@router.get("/gaps")
async def get_gaps(
    symbol: str,
    timeframe: str = "1D",
    from_ts: int | None = None,
    to_ts: int | None = None,
    max_gaps: int = Query(100, ge=1, le=500),
) -> dict:
    """Detect missing candle ranges in the stored data for a symbol/timeframe."""
    gaps = await detect_gaps(symbol, timeframe, from_ts, to_ts, max_gaps)
    return {
        "symbol":    symbol,
        "timeframe": timeframe,
        "gap_count": len(gaps),
        "gaps":      gaps,
    }


@router.get("/pair-info")
async def pair_info(symbol: str, timeframe: str = "1D") -> dict:
    """Sync state + coverage details for a single symbol/timeframe pair."""
    state = await get_sync_state(symbol, timeframe)
    earliest, latest = await get_timestamp_range(symbol, timeframe)
    sufficient = await has_sufficient_data(symbol, timeframe, min_candles=200)
    key = f"{symbol.upper()}_{timeframe}"
    running = key in _running and not _running[key].done()
    return {
        "symbol":          symbol,
        "timeframe":       timeframe,
        "download_running": running,
        "sufficient_data": sufficient,
        "earliest_ts":     earliest,
        "latest_ts":       latest,
        "state":           state,
    }
