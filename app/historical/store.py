"""MongoDB operations for the historical OHLCV data store.

Separate from the live-trading collections (users / layouts / drawings).
All writes use upsert so re-ingesting the same candle is idempotent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pymongo import UpdateOne

from ..database import get_db


async def ensure_indexes() -> None:
    db = get_db()
    await db.historical_candles.create_index(
        [("symbol", 1), ("timeframe", 1), ("timestamp", 1)],
        unique=True,
        name="idx_hist_symbol_tf_ts",
        background=True,
    )
    await db.historical_sync_state.create_index(
        [("symbol", 1), ("timeframe", 1)],
        unique=True,
        name="idx_hist_sync_symbol_tf",
        background=True,
    )


async def upsert_candles(
    symbol: str,
    timeframe: str,
    candles: list[dict],
    source: str = "backfill",
) -> int:
    """Bulk-upsert candles. Returns count of inserted + modified docs."""
    if not candles:
        return 0
    db = get_db()
    now = datetime.now(timezone.utc)
    ops = [
        UpdateOne(
            {"symbol": symbol, "timeframe": timeframe, "timestamp": c["time"]},
            {
                "$set": {
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c.get("volume", 0),
                    "source": source,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        for c in candles
    ]
    result = await db.historical_candles.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


async def query_candles(
    symbol: str,
    timeframe: str,
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    limit: int = 2000,
    newest_first: bool = False,
) -> list[dict]:
    """Return candles sorted ascending by timestamp.

    limit=0  → no limit (returns all).
    newest_first=True + limit=N → returns the N most-recent candles (sorted ascending on return).
    """
    db = get_db()
    filt: dict = {"symbol": symbol, "timeframe": timeframe}
    ts_filt: dict = {}
    if from_ts is not None:
        ts_filt["$gte"] = from_ts
    if to_ts is not None:
        ts_filt["$lte"] = to_ts
    if ts_filt:
        filt["timestamp"] = ts_filt

    sort_dir = -1 if (newest_first and limit and limit > 0) else 1
    find_kwargs: dict = {
        "filter": filt,
        "projection": {"_id": 0, "symbol": 0, "timeframe": 0, "source": 0, "created_at": 0, "updated_at": 0},
        "sort": [("timestamp", sort_dir)],
    }
    if limit and limit > 0:
        find_kwargs["limit"] = limit
    cursor = db.historical_candles.find(**find_kwargs)
    rows: list[dict] = []
    async for doc in cursor:
        rows.append({
            "time":   doc["timestamp"],
            "open":   doc["open"],
            "high":   doc["high"],
            "low":    doc["low"],
            "close":  doc["close"],
            "volume": doc.get("volume", 0),
        })
    if newest_first and limit and limit > 0:
        rows.reverse()
    return rows


async def get_sync_state(symbol: str, timeframe: str) -> dict | None:
    db = get_db()
    return await db.historical_sync_state.find_one(
        {"symbol": symbol, "timeframe": timeframe}, {"_id": 0}
    )


async def update_sync_state(symbol: str, timeframe: str, **kwargs) -> None:
    db = get_db()
    kwargs["updated_at"] = datetime.now(timezone.utc)
    await db.historical_sync_state.update_one(
        {"symbol": symbol, "timeframe": timeframe},
        {
            "$set": kwargs,
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )


async def get_all_sync_states() -> list[dict]:
    db = get_db()
    cursor = db.historical_sync_state.find({}, {"_id": 0})
    return [doc async for doc in cursor]


async def count_candles(symbol: str, timeframe: str) -> int:
    db = get_db()
    return await db.historical_candles.count_documents(
        {"symbol": symbol, "timeframe": timeframe}
    )


async def get_timestamp_range(
    symbol: str, timeframe: str
) -> tuple[int | None, int | None]:
    """Return (earliest_ts, latest_ts) for a symbol/timeframe pair."""
    db = get_db()
    oldest = await db.historical_candles.find_one(
        {"symbol": symbol, "timeframe": timeframe},
        {"timestamp": 1, "_id": 0},
        sort=[("timestamp", 1)],
    )
    newest = await db.historical_candles.find_one(
        {"symbol": symbol, "timeframe": timeframe},
        {"timestamp": 1, "_id": 0},
        sort=[("timestamp", -1)],
    )
    return (
        oldest["timestamp"] if oldest else None,
        newest["timestamp"] if newest else None,
    )


async def has_sufficient_data(
    symbol: str, timeframe: str, min_candles: int = 100
) -> bool:
    count = await count_candles(symbol, timeframe)
    return count >= min_candles


# ---------------------------------------------------------------------------
# Chart serving — aggregate the 1m / 1D base series into any requested interval
# ---------------------------------------------------------------------------

# Intraday intervals are aggregated on demand from the stored 1m base series.
_INTRADAY_FROM_1M = {"3m", "5m", "15m", "30m", "1H", "2H", "4H"}
# Weekly / monthly are aggregated from the stored 1D base series.
_DERIVED_FROM_1D = {"1W", "1M"}


def _base_timeframe(interval: str) -> str:
    """Which stored base series an interval is served from."""
    if interval == "1m" or interval in _INTRADAY_FROM_1M:
        return "1m"
    return "1D"  # 1D itself, plus 1W / 1M


def _mopen_for(symbol: str) -> int:
    """Bar-boundary anchor (seconds from UTC midnight) for a symbol.

    Matches the frontend live barTs() and rest._aggregate_bars: 09:00 IST (12600)
    for MCX commodities, 09:15 IST (13500) for everything else.
    """
    # Imported lazily to avoid a circular import at module load.
    from ..instruments import by_symbol
    from ..upstox.rest import _MCX_MOPEN_UTC, _MOPEN_UTC

    inst = by_symbol(symbol)
    return _MCX_MOPEN_UTC if (inst and inst.kind == "commodity") else _MOPEN_UTC


async def _aggregate_1m_pipeline(
    symbol: str,
    step_sec: int,
    mopen: int,
    count: int,
    before_ts: int | None,
) -> list[dict]:
    """Aggregate stored 1m candles into `step_sec` buckets, server-side.

    Returns the newest `count` aggregated bars (ascending by time) whose bucket
    start is < before_ts (or the latest available when before_ts is None).

    A bounded lower-bound match keeps the pipeline from scanning the full ~470k
    1m history on every call.  NSE trades ~6.25h / 24h on 5 of 7 days (≈19% of
    wall-clock), so a 6× window comfortably covers nights, weekends and holidays.
    """
    db = get_db()
    # Anchor the bounded window to the page's upper edge.  For the newest page
    # (before_ts is None) anchor to the LATEST stored 1m timestamp rather than
    # wall-clock now, so a small request still finds data when the store is stale
    # (e.g. over a weekend or before the day's EOD sync has run).
    if before_ts is not None:
        anchor = before_ts
    else:
        newest = await db.historical_candles.find_one(
            {"symbol": symbol, "timeframe": "1m"},
            {"timestamp": 1, "_id": 0},
            sort=[("timestamp", -1)],
        )
        if not newest:
            return []
        anchor = newest["timestamp"] + 1
    window_start = anchor - count * step_sec * 6

    ts_match: dict = {"$gte": window_start}
    if before_ts is not None:
        ts_match["$lt"] = before_ts
    match: dict = {"symbol": symbol, "timeframe": "1m", "timestamp": ts_match}

    # bucket = floor((ts - mopen) / step) * step + mopen
    shifted = {"$subtract": ["$timestamp", mopen]}
    bucket = {
        "$add": [
            {"$subtract": [shifted, {"$mod": [shifted, step_sec]}]},
            mopen,
        ]
    }

    pipeline = [
        {"$match": match},
        {"$sort": {"timestamp": 1}},
        {
            "$group": {
                "_id": bucket,
                "open": {"$first": "$open"},
                "high": {"$max": "$high"},
                "low": {"$min": "$low"},
                "close": {"$last": "$close"},
                "volume": {"$sum": "$volume"},
            }
        },
        {"$sort": {"_id": -1}},
        {"$limit": count},
        {"$sort": {"_id": 1}},
    ]

    rows: list[dict] = []
    async for doc in db.historical_candles.aggregate(pipeline):
        rows.append({
            "time":   doc["_id"],
            "open":   doc["open"],
            "high":   doc["high"],
            "low":    doc["low"],
            "close":  doc["close"],
            "volume": doc.get("volume", 0),
        })
    return rows


async def get_chart_candles(
    symbol: str,
    interval: str,
    count: int,
    before_ts: int | None = None,
) -> list[dict]:
    """Serve `count` candles for any interval from the 1m / 1D base series.

    Used by /api/history (live chart) and /api/backtest/history.  Returns bars
    ascending by time.  When `before_ts` is given, only bars strictly older than
    it are returned (lazy scroll-back paging).

      • 1m                       → direct query of the 1m base
      • 3m/5m/15m/30m/1H/2H/4H   → aggregated from 1m server-side
      • 1D                       → direct query of the 1D base
      • 1W / 1M                  → aggregated from 1D (tiny series, done in Python)
    """
    symbol = symbol.upper()
    # to_ts is inclusive ($lte); paging wants strictly-older, so subtract 1s.
    to_ts = (before_ts - 1) if before_ts is not None else None

    if interval == "1m":
        return await query_candles(symbol, "1m", to_ts=to_ts, limit=count, newest_first=True)

    if interval == "1D":
        return await query_candles(symbol, "1D", to_ts=to_ts, limit=count, newest_first=True)

    if interval in _INTRADAY_FROM_1M:
        from ..mock.generator import interval_seconds
        step = interval_seconds(interval)
        return await _aggregate_1m_pipeline(symbol, step, _mopen_for(symbol), count, before_ts)

    if interval in _DERIVED_FROM_1D:
        from ..upstox.rest import _UNIT_MAP, _aggregate_bars
        unit, value = _UNIT_MAP.get(interval, ("weeks", 1))
        daily = await query_candles(symbol, "1D", to_ts=to_ts, limit=0)
        if not daily:
            return []
        buckets = _aggregate_bars({c["time"]: c for c in daily}, unit, value, _mopen_for(symbol))
        out = sorted(buckets.values(), key=lambda c: c["time"])
        return out[-count:]

    # Unknown interval → treat as daily.
    return await query_candles(symbol, "1D", to_ts=to_ts, limit=count, newest_first=True)
