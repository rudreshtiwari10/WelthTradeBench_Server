"""Historical data EOD scheduler.

One job only: every trading day at 15:45 IST (10:15 UTC), fetch the latest
candles for every symbol/timeframe pair already stored in the database.

The initial bulk download is done manually via POST /api/historical/download.
This job only keeps the existing store up to date day-by-day.
"""
from __future__ import annotations

import asyncio
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..instruments import by_symbol
from ..upstox.feed import hub
from .incremental import incremental_sync
from .store import get_all_sync_states

# Base series we persist.  Everything else is aggregated on demand at read-time
# (see store.get_chart_candles), so we store only "1m" (source of truth for all
# intraday intervals) and "1D" (deep history for 1D/1W/1M).
from .config import STORED_TIMEFRAMES

DEFAULT_TIMEFRAMES: list[str] = [
    tf.strip()
    for tf in os.getenv("HIST_TIMEFRAMES", ",".join(STORED_TIMEFRAMES)).split(",")
    if tf.strip()
]

_scheduler: AsyncIOScheduler | None = None


async def _eod_fill() -> None:
    """Fetch today's candles for every symbol/timeframe pair in the store."""
    if hub.mode != "upstox":
        print("[eod_fill] Skipping — Upstox not authenticated")
        return

    # Discover which symbols/timeframes have been downloaded
    states = await get_all_sync_states()
    if not states:
        print("[eod_fill] No tracked pairs found — run /api/historical/download first")
        return

    print(f"[eod_fill] Updating {len(states)} symbol/timeframe pairs")
    updated_pairs: list[tuple[str, str]] = []
    for state in states:
        sym = state.get("symbol")
        tf = state.get("timeframe")
        if not sym or not tf:
            continue
        inst = by_symbol(sym)
        if not inst:
            continue
        try:
            result = await incremental_sync(inst, tf)
            if result.get("upserted", 0):
                print(f"[eod_fill] {sym}/{tf}: +{result['upserted']} candles")
                updated_pairs.append((sym, tf))
        except Exception as exc:
            print(f"[eod_fill] {sym}/{tf}: {exc}")
        await asyncio.sleep(0.5)

    # Evict gzip cache for pairs that got new candles, so next chart open re-builds.
    if updated_pairs:
        try:
            from ..main import _BACKTEST_CACHE
            for pair in updated_pairs:
                _BACKTEST_CACHE.pop(pair, None)
            print(f"[eod_fill] Evicted backtest cache for {len(updated_pairs)} pairs")
        except Exception:
            pass

    print("[eod_fill] Done")


def start_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        return

    _scheduler = AsyncIOScheduler()

    # 15:45 IST = 10:15 UTC — 15 minutes after NSE closes
    _scheduler.add_job(
        _eod_fill,
        trigger=CronTrigger(hour=10, minute=15, timezone="UTC"),
        id="hist_eod_fill",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=600,
    )

    _scheduler.start()
    print("[historical_scheduler] EOD fill job scheduled at 15:45 IST daily")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    print("[historical_scheduler] Stopped")
