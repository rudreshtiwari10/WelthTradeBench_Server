"""FastAPI router for the historical data pipeline.

All endpoints live under /api/historical/* and are isolated from the
live-market (/api/history, /ws) and backtest (/api/backtest/*) paths.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from ..database import get_db
from ..instruments import by_symbol
from ..upstox.feed import hub
from .backfill import BACKFILL_TARGET_DAYS, backfill_full
from .config import stored_symbols
from .yf_backfill import backfill_yf_daily
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

# Tracks running download tasks: "SYMBOL_tf" → asyncio.Task
_running: dict[str, asyncio.Task] = {}


def _launch_download(symbol: str, timeframes: list[str], target_days: int) -> list[str]:
    """Spawn one background download task per (symbol, timeframe) not already running.

    Returns the list of timeframes that were actually launched.
    """
    sym = symbol.upper()
    inst = by_symbol(sym)
    if not inst:
        return []
    launched: list[str] = []
    for tf in timeframes:
        key = f"{sym}_{tf}"
        existing = _running.get(key)
        if existing and not existing.done():
            continue  # already running

        async def _run_tf(tf: str = tf) -> None:
            years = max(1, target_days // 365)
            print(f"[download] Starting {sym}/{tf} — {years} years")
            try:
                if tf == "1D":
                    # Daily base: yfinance gives the deepest history (10+ years).
                    # Fall back to Upstox daily only if yfinance returns nothing.
                    result = await backfill_yf_daily(inst, years=years)
                    print(f"[download] {sym}/1D via yfinance ({result['ticker']}): "
                          f"+{result['upserted']} candles, total={result['total']}")
                    if result["total"] == 0:
                        print(f"[download] {sym}/1D yfinance empty — falling back to Upstox daily")
                        result = await backfill_full(inst, "1D", target_days=target_days)
                        print(f"[download] {sym}/1D via Upstox: {result.get('total_upserted', 0)} candles")
                else:
                    # Intraday base (1m): Upstox only — no free deep intraday source.
                    result = await backfill_full(inst, tf, target_days=target_days)
                    print(f"[download] {sym}/{tf} complete: "
                          f"{result.get('total_upserted', 0)} candles, {result.get('chunks', 0)} chunks")
            except Exception as exc:  # noqa: BLE001
                print(f"[download] {sym}/{tf} error: {exc}")
            finally:
                _running.pop(f"{sym}_{tf}", None)

        _running[key] = asyncio.get_event_loop().create_task(_run_tf(), name=f"download_{sym}_{tf}")
        launched.append(tf)
    return launched


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

    launched = _launch_download(symbol, timeframes, target_days)
    if not launched:
        return {
            "triggered":      False,
            "reason":         "already_running",
            "symbol":         symbol,
            "timeframes_busy": timeframes,
        }

    return {
        "triggered":   True,
        "symbol":      symbol,
        "timeframes":  launched,
        "target_years": years,
        "note":        "Running in background. Poll /api/historical/status for progress.",
    }


@router.post("/download-all")
async def download_all(
    years: int = Query(5, ge=1, le=10),
    force: bool = False,
) -> dict:
    """Download the configured base series (1m + 1D) for EVERY configured symbol.

    Convenience entry point for a fresh start — equivalent to calling /download
    for each symbol in HIST_SYMBOLS.  Runs in the background.
    """
    if hub.mode != "upstox":
        raise HTTPException(status_code=403, detail="Upstox not authenticated")

    target_days = years * 365
    triggered: dict[str, list[str]] = {}
    for sym in stored_symbols():
        if force:
            for tf in DEFAULT_TIMEFRAMES:
                await update_sync_state(sym, tf, backfill_complete=False)
        launched = _launch_download(sym, DEFAULT_TIMEFRAMES, target_days)
        if launched:
            triggered[sym] = launched

    return {
        "triggered":    bool(triggered),
        "symbols":      list(triggered.keys()),
        "timeframes":   DEFAULT_TIMEFRAMES,
        "target_years": years,
        "note":         "Running in background. Poll /api/historical/status for progress.",
    }


@router.post("/reset")
async def reset_store() -> dict:
    """Wipe ALL stored historical data and sync state — start completely fresh.

    Drops every document from historical_candles + historical_sync_state and
    evicts the backtest gzip cache.  Re-run /download-all afterwards.
    """
    db = get_db()
    candles = await db.historical_candles.delete_many({})
    states = await db.historical_sync_state.delete_many({})
    try:
        from ..main import _BACKTEST_CACHE
        _BACKTEST_CACHE.clear()
    except Exception:  # noqa: BLE001
        pass
    return {
        "ok":               True,
        "candles_deleted":  candles.deleted_count,
        "states_deleted":   states.deleted_count,
    }


@router.post("/reset-symbols")
async def reset_symbols(
    symbols: str,
    timeframes: str | None = None,
    years: int = Query(4, ge=1, le=10),
) -> dict:
    """Delete stored candles + sync state for specific symbols and re-download.

    symbols   : comma-separated, e.g. NIFTY,BANKNIFTY
    timeframes: comma-separated (default = all stored: 1m,1D)
    years     : depth to re-download (default 4)

    Uses the hybrid approach automatically — yfinance for 1D, Upstox for 1m.
    Running downloads for the affected pairs are cancelled first.
    """
    if hub.mode != "upstox":
        raise HTTPException(status_code=403, detail="Upstox not authenticated")

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not sym_list:
        raise HTTPException(status_code=400, detail="No symbols provided")

    tf_list = (
        [t.strip() for t in timeframes.split(",") if t.strip()]
        if timeframes
        else DEFAULT_TIMEFRAMES
    )

    db = get_db()
    total_candles_deleted = 0
    total_states_deleted = 0
    all_launched: dict[str, list[str]] = {}

    for sym in sym_list:
        inst = by_symbol(sym)
        if not inst:
            continue

        # Cancel any in-progress downloads first
        for tf in tf_list:
            key = f"{sym}_{tf}"
            task = _running.get(key)
            if task and not task.done():
                task.cancel()
                _running.pop(key, None)

        # Delete candles and sync state for this symbol/timeframe set
        for tf in tf_list:
            res = await db.historical_candles.delete_many({"symbol": sym, "timeframe": tf})
            total_candles_deleted += res.deleted_count
            res2 = await db.historical_sync_state.delete_many({"symbol": sym, "timeframe": tf})
            total_states_deleted += res2.deleted_count

        # Evict backtest cache entries for this symbol
        try:
            from ..main import _BACKTEST_CACHE
            for tf in tf_list:
                _BACKTEST_CACHE.pop((sym, tf), None)
        except Exception:  # noqa: BLE001
            pass

        # Launch fresh hybrid download
        launched = _launch_download(sym, tf_list, years * 365)
        if launched:
            all_launched[sym] = launched

    return {
        "ok":                  True,
        "symbols_reset":       sym_list,
        "timeframes_reset":    tf_list,
        "candles_deleted":     total_candles_deleted,
        "states_deleted":      total_states_deleted,
        "downloads_launched":  all_launched,
        "target_years":        years,
        "note": (
            "Data deleted. Re-downloading in background — "
            "yfinance for 1D (deep history), Upstox for 1m. "
            "Poll /api/historical/status for progress."
        ),
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
