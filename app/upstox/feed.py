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
        self._streamer_keys: set[str] = set()
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
            # Real feed only. No mock seeding — the Upstox stream provides ticks
            # (and during closed-market hours there are simply none, which is
            # correct: the last real close stays put).
            self._ensure_streamer()
            self._subscribe_upstox(symbol)
        else:
            if symbol not in self.last_price:
                candles = generate_candles(symbol, "1D", 2)
                self.last_price[symbol] = candles[-1]["close"]
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
            # Go silent once authenticated — only the real Upstox feed may emit
            # ticks. Prevents a loop started pre-login from overwriting real data.
            if self.mode != "mock":
                continue
            active = self._active_symbols()
            if not active:
                continue
            now = int(_t.time())
            for sym in active:
                price = self.last_price.get(sym, 1000.0)
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
                threading.Thread(target=streamer.connect, daemon=True).start()
                self._streamer = streamer
            except Exception as exc:  # noqa: BLE001
                print(f"[feed] Upstox streamer init failed, staying on mock: {exc}")
                self._streamer = None

    def _subscribe_upstox(self, symbol: str) -> None:
        inst = by_symbol(symbol)
        if not inst or self._streamer is None:
            return
        key = inst.instrument_key
        if key in self._streamer_keys:
            return
        self._streamer_keys.add(key)
        try:
            self._streamer.subscribe([key], "ltpc")
        except Exception as exc:  # noqa: BLE001
            print(f"[feed] subscribe failed for {key}: {exc}")

    def _on_upstox_message(self, message: Any) -> None:
        """Runs on the streamer thread. Decode ltpc and hand off to the loop."""
        import time as _t
        try:
            feeds = (message or {}).get("feeds", {})
            for key, payload in feeds.items():
                inst = None
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
