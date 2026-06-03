"""Resolve active MCX commodity futures instrument keys from Upstox's public CDN.

Upstox publishes an updated instrument master daily (no auth required):
  https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz

We download it, filter MCX FUT contracts, and keep only the front-month
(nearest unexpired expiry) for each tracked commodity.  The result is cached
for the calendar day so we hit the CDN at most once per day.

Call `refresh()` once at startup when in Upstox mode.  Call `active_key(sym)`
from any thread to get the instrument key for a given commodity symbol.
"""
from __future__ import annotations

import csv
import datetime
import gzip
import io
import json
import threading
from typing import Optional

import httpx

# ── Thread-safe module-level state ───────────────────────────────────────────

_lock = threading.Lock()
_cache_date: Optional[datetime.date] = None

# Commodity symbol → front-month instrument key  e.g. "GOLD" → "MCX_FO|427013"
active_keys: dict[str, str] = {}

# instrument_key → commodity symbol reverse-map for the feed handler
reverse_map: dict[str, str] = {}

# Commodities this app tracks (must match instruments.py symbol names exactly)
_TRACKED = {
    "GOLD", "GOLDM", "GOLDPETAL",
    "SILVER", "SILVERM", "SILVERMIC",
    "CRUDEOIL", "CRUDEOILM",
    "NATURALGAS",
    "COPPER",
    "ZINC",
    "ALUMINIUM",
    "NICKEL",
    "LEAD",
}

# Upstox CDN — no auth required, updated every trading day before market open.
_CSV_URL  = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
_JSON_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"


# ── Public API ────────────────────────────────────────────────────────────────

def active_key(symbol: str) -> Optional[str]:
    """Return the cached front-month MCX instrument key for a commodity (thread-safe)."""
    with _lock:
        return active_keys.get(symbol.upper())


async def refresh() -> dict[str, str]:
    """Fetch/re-cache MCX active contracts.  Returns the active_keys dict.

    Re-uses the in-memory cache if it was already populated today.
    """
    global _cache_date

    with _lock:
        today = datetime.date.today()
        if active_keys and _cache_date == today:
            return dict(active_keys)

    new_keys, new_reverse = await _fetch_and_parse()

    with _lock:
        active_keys.clear()
        active_keys.update(new_keys)
        reverse_map.clear()
        reverse_map.update(new_reverse)
        _cache_date = datetime.date.today()

    if new_keys:
        print(
            "[mcx] Active front-month contracts: "
            + ", ".join(f"{s}={k}" for s, k in sorted(new_keys.items()))
        )
    else:
        print("[mcx] No active MCX contracts found — live MCX ticks unavailable")

    return dict(active_keys)


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _fetch_and_parse() -> tuple[dict[str, str], dict[str, str]]:
    """Download the Upstox instrument master and extract front-month MCX futures keys.

    Tries the CSV format first (lighter on memory), falls back to JSON.
    """
    for url, parser in [(_CSV_URL, _parse_csv), (_JSON_URL, _parse_json)]:
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            raw = gzip.decompress(resp.content)
            keys, rev = parser(raw)
            if keys:
                return keys, rev
        except Exception as exc:  # noqa: BLE001
            print(f"[mcx] {url} failed: {exc}")

    return {}, {}


def _build_maps(
    contracts: dict[str, list[tuple[datetime.date, str]]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Pick the front-month key for each commodity and build both maps."""
    today = datetime.date.today()
    keys: dict[str, str] = {}
    rev: dict[str, str] = {}
    for sym, lst in contracts.items():
        future = [(exp, k) for exp, k in lst if exp >= today]
        if not future:
            continue
        future.sort(key=lambda x: x[0])
        _, k = future[0]
        keys[sym] = k
        rev[k] = sym
    return keys, rev


def _match_name(raw_name: str) -> Optional[str]:
    """Map a raw instrument name / trading-symbol to one of our tracked symbols.

    Handles exact matches (e.g. "GOLD") and prefix matches on trading symbols
    (e.g. "GOLDOCT24FUT" → "GOLD").
    """
    name = raw_name.strip().upper()
    if name in _TRACKED:
        return name
    for sym in _TRACKED:
        if name.startswith(sym):
            return sym
    return None


def _parse_csv(raw: bytes) -> tuple[dict[str, str], dict[str, str]]:
    """Parse gzip-decompressed Upstox instruments CSV."""
    today = datetime.date.today()
    text = raw.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    contracts: dict[str, list[tuple[datetime.date, str]]] = {}

    for row in reader:
        exchange = (row.get("exchange") or row.get("Exchange") or "").strip().upper()
        if exchange != "MCX":
            continue
        inst_type = (
            row.get("instrument_type") or row.get("InstrumentType") or ""
        ).strip().upper()
        if inst_type != "FUT":
            continue

        raw_name = (
            row.get("name") or row.get("Name") or
            row.get("trading_symbol") or row.get("tradingsymbol") or ""
        )
        sym = _match_name(raw_name)
        if not sym:
            continue

        expiry_str = (
            row.get("expiry") or row.get("Expiry") or row.get("expiry_date") or ""
        ).strip()
        try:
            expiry = datetime.date.fromisoformat(expiry_str[:10])
        except (ValueError, IndexError):
            continue
        if expiry < today:
            continue

        key = (row.get("instrument_key") or row.get("InstrumentKey") or "").strip()
        if key:
            contracts.setdefault(sym, []).append((expiry, key))

    return _build_maps(contracts)


def _parse_json(raw: bytes) -> tuple[dict[str, str], dict[str, str]]:
    """Parse gzip-decompressed Upstox instruments JSON."""
    today = datetime.date.today()
    instruments = json.loads(raw)
    contracts: dict[str, list[tuple[datetime.date, str]]] = {}

    for inst in instruments:
        exchange = (inst.get("exchange") or "").strip().upper()
        if exchange != "MCX":
            continue
        inst_type = (
            inst.get("instrument_type") or inst.get("instrumentType") or ""
        ).strip().upper()
        if inst_type != "FUT":
            continue

        raw_name = (
            inst.get("name") or inst.get("tradingsymbol") or
            inst.get("trading_symbol") or ""
        )
        sym = _match_name(raw_name)
        if not sym:
            continue

        expiry_raw = inst.get("expiry") or inst.get("expiry_date") or ""
        try:
            expiry = datetime.date.fromisoformat(str(expiry_raw)[:10])
        except (ValueError, IndexError):
            continue
        if expiry < today:
            continue

        key = (inst.get("instrument_key") or inst.get("instrumentKey") or "").strip()
        if key:
            contracts.setdefault(sym, []).append((expiry, key))

    return _build_maps(contracts)
