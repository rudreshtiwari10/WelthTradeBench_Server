"""Gap detector: scans the historical_candles collection for missing timestamp ranges.

A "gap" is a pair of consecutive stored candles whose time difference is more than
2× the expected candle step — which indicates at least one missing bar in between.
The factor of 2 accounts for weekends and exchange holidays on daily+ timeframes,
and for the overnight break between sessions on intraday timeframes.
"""
from __future__ import annotations

from ..database import get_db
from ..mock.generator import interval_seconds


async def detect_gaps(
    symbol: str,
    timeframe: str,
    from_ts: int | None = None,
    to_ts: int | None = None,
    max_gaps: int = 100,
) -> list[dict]:
    """Return a list of gap descriptors found in the stored historical data.

    Each gap is:
      {from_ts, to_ts, gap_seconds, expected_missing_candles}
    """
    step = interval_seconds(timeframe)
    if step <= 0:
        return []

    db = get_db()
    filt: dict = {"symbol": symbol, "timeframe": timeframe}
    ts_filt: dict = {}
    if from_ts is not None:
        ts_filt["$gte"] = from_ts
    if to_ts is not None:
        ts_filt["$lte"] = to_ts
    if ts_filt:
        filt["timestamp"] = ts_filt

    cursor = db.historical_candles.find(
        filt, {"timestamp": 1, "_id": 0}, sort=[("timestamp", 1)]
    )
    timestamps = [doc["timestamp"] async for doc in cursor]

    if len(timestamps) < 2:
        return []

    # Allow up to 2× the step before flagging a gap.
    # For daily+, weekends produce a natural 3-day gap (Fri→Mon), so allow 4× instead.
    threshold_multiplier = 4 if timeframe in ("1D", "1W", "1M") else 2
    threshold = step * threshold_multiplier

    gaps: list[dict] = []
    for i in range(1, len(timestamps)):
        diff = timestamps[i] - timestamps[i - 1]
        if diff > threshold:
            expected_missing = max(1, diff // step - 1)
            gaps.append({
                "from_ts":               timestamps[i - 1],
                "to_ts":                 timestamps[i],
                "gap_seconds":           diff,
                "expected_missing_candles": expected_missing,
            })
            if len(gaps) >= max_gaps:
                break

    return gaps
