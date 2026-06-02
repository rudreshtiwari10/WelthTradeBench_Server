"""Live tick hub.

Fans normalized ticks {symbol, ltp, ts} out to connected WebSocket clients.
Uses the Upstox MarketDataStreamerV3 when a token is present, otherwise a mock
random-walk ticker so the chart is always live.

Option contract ticks: clients send {type:'sub_options', keys:[...]} with
instrument keys (e.g. NSE_FO|123456). The hub subscribes them to the Upstox
streamer and fans out {type:'option_tick', key:..., ltp:..., ts:...} messages.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import WebSocket

from ..config import credentials_present, tokens
from ..instruments import by_symbol
from ..mock.generator import generate_candles, next_tick


class FeedHub:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, set[str]] = {}
        self.last_price: dict[str, float] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._mock_task: asyncio.Task | None = None
        self._streamer: Any = None
        self._streamer_keys: set[str] = set()   # all instrument keys ever subscribed
        self._streamer_ready: bool = False       # True once WS is open
        self._lock = threading.Lock()
        # Option contract subscriptions (keyed by Upstox instrument key)
        self._option_keys: set[str] = set()
        self.option_subs: dict[str, set[WebSocket]] = {}

    @property
    def mode(self) -> str:
        return "upstox" if (credentials_present() and tokens.authenticated) else "mock"

    # ── client lifecycle ────────────────────────────────────────────────
    async def add_client(self, ws: WebSocket) -> None:
        self.loop = asyncio.get_running_loop()
        self.clients[ws] = set()

    async def remove_client(self, ws: WebSocket) -> None:
        self.clients.pop(ws, None)
        for subs in self.option_subs.values():
            subs.discard(ws)

    async def subscribe(self, ws: WebSocket, symbol: str) -> None:
        symbol = symbol.upper()
        self.clients.setdefault(ws, set()).add(symbol)
        if self.mode == "upstox":
            self._ensure_streamer()
            self._subscribe_upstox(symbol)
        else:
            if symbol not in self.last_price:
                # Only base instruments (indices/equities) get mock tick prices.
                # Option/future symbols are not in the instruments map; generating
                # ticks for them at the default ~1000 price would corrupt charts.
                inst = by_symbol(symbol)
                if inst:
                    candles = generate_candles(symbol, "1D", 2)
                    self.last_price[symbol] = candles[-1]["close"]
                # else: derivative/unknown — last_price intentionally left absent
            self._ensure_mock_loop()

    async def unsubscribe(self, ws: WebSocket, symbol: str) -> None:
        symbol = symbol.upper()
        self.clients.get(ws, set()).discard(symbol)

    async def subscribe_option_keys(self, ws: WebSocket, keys: list[str]) -> None:
        """Subscribe a client to real-time ticks for specific option instrument keys."""
        if self.mode != "upstox":
            return
        self._ensure_streamer()
        new_keys: list[str] = []
        for key in keys:
            self.option_subs.setdefault(key, set()).add(ws)
            if key not in self._option_keys:
                self._option_keys.add(key)
                self._streamer_keys.add(key)
                new_keys.append(key)
        if new_keys and self._streamer_ready and self._streamer is not None:
            try:
                self._streamer.subscribe(new_keys, "ltpc")
            except Exception as exc:  # noqa: BLE001
                print(f"[feed] option key subscribe failed: {exc}")

    async def unsubscribe_option_keys(self, ws: WebSocket, keys: list[str]) -> None:
        """Remove a client's interest in specific option key ticks."""
        for key in keys:
            if key in self.option_subs:
                self.option_subs[key].discard(ws)

    def _active_symbols(self) -> set[str]:
        out: set[str] = set()
        for syms in self.clients.values():
            out |= syms
        return out

    # ── broadcast ───────────────────────────────────────────────────────
    async def _broadcast(self, symbol: str, ltp: float, ts: int) -> None:
        self.last_price[symbol] = ltp
        msg = {"type": "tick", "symbol": symbol, "ltp": ltp, "ts": ts}
        dead = []
        for ws, syms in list(self.clients.items()):
            if symbol in syms:
                try:
                    await ws.send_json(msg)
                except Exception:
                    dead.append(ws)
        for ws in dead:
            self.clients.pop(ws, None)

    async def _broadcast_option_tick(self, key: str, ltp: float, ts: int) -> None:
        """Broadcast an option contract LTP tick to all subscribed clients."""
        msg = {"type": "option_tick", "key": key, "ltp": ltp, "ts": ts}
        interested = list(self.option_subs.get(key, set()))
        dead = []
        for ws in interested:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if key in self.option_subs:
                self.option_subs[key].discard(ws)

    # ── mock ticker ─────────────────────────────────────────────────────
    def _ensure_mock_loop(self) -> None:
        if self._mock_task and not self._mock_task.done():
            return
        self._mock_task = asyncio.create_task(self._mock_loop())

    async def _mock_loop(self) -> None:
        import time as _t
        while True:
            await asyncio.sleep(1.0)
            if self.mode != "mock":
                continue
            active = self._active_symbols()
            if not active:
                continue
            now = int(_t.time())
            for sym in active:
                price = self.last_price.get(sym)
                if price is None:
                    continue  # derivative or unknown symbol — no mock tick
                self.last_price[sym] = next_tick(price, sym)
                await self._broadcast(sym, self.last_price[sym], now)

    # ── Upstox streamer ─────────────────────────────────────────────────
    def _ensure_streamer(self) -> None:
        with self._lock:
            if self._streamer is not None:
                return
            try:
                import upstox_client
                cfg = upstox_client.Configuration()
                cfg.access_token = tokens.token
                api_client = upstox_client.ApiClient(cfg)
                streamer = upstox_client.MarketDataStreamerV3(api_client, [], "ltpc")
                streamer.on("message", self._on_upstox_message)
                streamer.on("open", self._on_upstox_open)      # subscribe on connect
                threading.Thread(target=streamer.connect, daemon=True).start()
                self._streamer = streamer
            except Exception as exc:  # noqa: BLE001
                print(f"[feed] Upstox streamer init failed, staying on mock: {exc}")
                self._streamer = None

    def _on_upstox_open(self) -> None:
        """Fires on the streamer thread when the Upstox WS connection is established.
        Subscribes all keys that were requested before the connection was ready,
        including both base instrument keys and option contract keys."""
        self._streamer_ready = True
        pending = list(self._streamer_keys)
        n_opt = len(self._option_keys)
        print(
            f"[feed] Upstox streamer connected — subscribing {len(pending)} "
            f"instrument(s) ({n_opt} option contract(s))"
        )
        if pending and self._streamer is not None:
            try:
                self._streamer.subscribe(pending, "ltpc")
            except Exception as exc:  # noqa: BLE001
                print(f"[feed] bulk subscribe on open failed: {exc}")

    def _subscribe_upstox(self, symbol: str) -> None:
        inst = by_symbol(symbol)
        if not inst or self._streamer is None:
            return
        key = inst.instrument_key
        if key in self._streamer_keys:
            return
        self._streamer_keys.add(key)
        if self._streamer_ready:
            # Connection already open — subscribe immediately.
            try:
                self._streamer.subscribe([key], "ltpc")
            except Exception as exc:  # noqa: BLE001
                print(f"[feed] subscribe failed for {key}: {exc}")
        # else: connection still opening — _on_upstox_open will subscribe all pending keys.

    @staticmethod
    def _extract_ltp(payload: dict) -> float | None:
        """Pull ltp out of whatever Upstox MarketDataStreamerV3 sends.

        The "ltpc" stream can arrive in three shapes depending on instrument type:
          1. payload["ltpc"]["ltp"]                          – equity / FO contracts
          2. payload["fullFeed"]["indexFF"]["ltpc"]["ltp"]   – NSE index (full feed)
          3. payload["fullFeed"]["eFO"]["ltpc"]["ltp"]       – equity F&O (full feed)
        We try all three and return the first truthy value.
        """
        try:
            v = (payload.get("ltpc") or {}).get("ltp")
            if v is not None:
                return float(v)
        except Exception:  # noqa: BLE001
            pass
        try:
            ff = payload.get("fullFeed") or {}
            # Index instruments
            v = (ff.get("indexFF") or {}).get("ltpc", {}).get("ltp")
            if v is not None:
                return float(v)
            # Equity F&O instruments
            v = (ff.get("eFO") or {}).get("ltpc", {}).get("ltp")
            if v is not None:
                return float(v)
        except Exception:  # noqa: BLE001
            pass
        return None

    def _on_upstox_message(self, message: Any) -> None:
        """Runs on the streamer thread. Decode tick and hand off to the event loop."""
        import time as _t
        try:
            feeds = (message or {}).get("feeds", {})
            ts = int(_t.time())
            for key, payload in feeds.items():
                ltp = self._extract_ltp(payload or {})
                if ltp is None:
                    continue

                from ..instruments import by_key
                inst = by_key(key)
                if inst:
                    # Known base instrument (index / stock) — broadcast by symbol name
                    if self.loop:
                        asyncio.run_coroutine_threadsafe(
                            self._broadcast(inst.symbol, ltp, ts), self.loop
                        )
                elif key in self._option_keys:
                    # Directly subscribed option contract — broadcast by instrument key
                    if self.loop:
                        asyncio.run_coroutine_threadsafe(
                            self._broadcast_option_tick(key, ltp, ts), self.loop
                        )
        except Exception as exc:  # noqa: BLE001
            print(f"[feed] message decode error: {exc}")


hub = FeedHub()
