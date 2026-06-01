"""Live tick hub.

Fans normalized ticks {symbol, ltp, ts} out to connected WebSocket clients.
Uses the Upstox MarketDataStreamerV3 when a token is present, otherwise a mock
random-walk ticker so the chart is always live.
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
        self._streamer_keys: set[str] = set()   # all keys ever requested
        self._streamer_ready: bool = False       # True once WS is open
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        return "upstox" if (credentials_present() and tokens.authenticated) else "mock"

    # ── client lifecycle ────────────────────────────────────────────────
    async def add_client(self, ws: WebSocket) -> None:
        self.loop = asyncio.get_running_loop()
        self.clients[ws] = set()

    async def remove_client(self, ws: WebSocket) -> None:
        self.clients.pop(ws, None)

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
        Subscribes all keys that were requested before the connection was ready."""
        self._streamer_ready = True
        pending = list(self._streamer_keys)
        print(f"[feed] Upstox streamer connected — subscribing {len(pending)} instrument(s): {pending}")
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

    def _on_upstox_message(self, message: Any) -> None:
        """Runs on the streamer thread. Decode ltpc and hand off to the loop."""
        import time as _t
        try:
            feeds = (message or {}).get("feeds", {})
            for key, payload in feeds.items():
                from ..instruments import by_key
                inst = by_key(key)
                if not inst:
                    continue
                ltpc = (payload or {}).get("ltpc") or (payload or {}).get("fullFeed", {}).get("indexFF", {}).get("ltpc")
                if not ltpc:
                    continue
                ltp = ltpc.get("ltp")
                if ltp is None:
                    continue
                ts = int(_t.time())
                if self.loop:
                    asyncio.run_coroutine_threadsafe(
                        self._broadcast(inst.symbol, float(ltp), ts), self.loop
                    )
        except Exception as exc:  # noqa: BLE001
            print(f"[feed] message decode error: {exc}")


hub = FeedHub()
