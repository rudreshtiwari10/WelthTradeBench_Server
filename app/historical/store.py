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
