"""Live tick hub.

Fans normalized ticks {symbol, ltp, ts} out to connected WebSocket clients.
Uses the Upstox MarketDataStreamerV3 when a token is present, otherwise a mock
random-walk ticker so the chart is always live.

Option contract ticks: clients send {type:'sub_options', keys:[...]} with
instrument keys (e.g. NSE_FO|123456). The hub subscribes them to the Upstox
streamer and fans out {type:'option_tick', key:..., ltp:..., ts:...} messages.

MCX commodities: generic keys like MCX_FO|GOLD are invalid on Upstox.
We resolve the real front-month contract key (e.g. MCX_FO|427013) at subscribe
time via mcx_instruments.refresh() and keep a reverse map so incoming ticks can
be attributed back to the commodity symbol name.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import WebSocket

from ..config import credentials_present, tokens
from ..instruments import by_symbol, by_key
from ..mock.generator import generate_candles, next_tick


class FeedHub:
    def __init__(self) -> None:
        self.clients: dict[WebSocket, set[str]] = {}
        self.last_price: dict[str, float] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._mock_task: asyncio.Task | None = None
        self._streamer: Any = None
        self._streamer_keys: set[str] = set()   # all instrument keys subscribed to Upstox
        self._streamer_ready: bool = False       # True once WS handshake is complete
        self._lock = threading.Lock()
        # Option contract subscriptions (keyed by Upstox instrument key)
        self._option_keys: set[str] = set()
        self.option_subs: dict[str, set[WebSocket]] = {}
        # MCX commodity real-key → symbol reverse-map
        # e.g. "MCX_FO|427013" → "GOLD"
        self._mcx_key_to_symbol: dict[str, str] = {}

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
            await self._subscribe_upstox(symbol)
        else:
            if symbol not in self.last_price:
                # Only known instruments get mock tick prices.
                # Derivative symbols are not in the instruments map; broadcasting
                # ticks at the default ~1000 price would corrupt their candles.
                inst = by_symbol(symbol)
                if inst:
                    candles = generate_candles(symbol, "1D", 2)
                    self.last_price[symbol] = candles[-1]["close"]
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
                    continue  # derivative or unknown — no mock tick
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
                streamer.on("open", self._on_upstox_open)
                threading.Thread(target=streamer.connect, daemon=True).start()
                self._streamer = streamer
            except Exception as exc:  # noqa: BLE001
                print(f"[feed] Upstox streamer init failed, staying on mock: {exc}")
                self._streamer = None

    def _on_upstox_open(self) -> None:
        """Fires on the streamer thread when the Upstox WS connection opens.

        Subscribes all keys that accumulated before the connection was ready.
        _streamer_keys already contains real MCX contract keys (added by
        _subscribe_upstox), so no special MCX handling is needed here.
        """
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

    async def _subscribe_upstox(self, symbol: str) -> None:
        """Resolve instrument key for symbol and subscribe it to the Upstox streamer.

        For MCX commodity symbols the static instrument_key in instruments.py is a
        placeholder (e.g. MCX_FO|GOLD).  We resolve the real front-month contract
        key from mcx_instruments and register it in _mcx_key_to_symbol so incoming
        ticks can be mapped back to the symbol name.
        """
        inst = by_symbol(symbol)
        if not inst:
            return

        key = inst.instrument_key

        if inst.kind == "commodity":
            from .. import mcx_instruments as _mcx  # lazy import to avoid circular

            real_key = _mcx.active_key(symbol)
            if not real_key:
                # Cache empty or stale — try a fresh download.
                try:
                    await _mcx.refresh()
                    real_key = _mcx.active_key(symbol)
                except Exception as exc:  # noqa: BLE001
                    print(f"[feed] MCX key resolution failed for {symbol}: {exc}")

            if real_key:
                key = real_key
                with self._lock:
                    self._mcx_key_to_symbol[key] = symbol
                print(f"[feed] MCX {symbol} → {key}")
            else:
                # Could not resolve a real key — Upstox won't send data for the
                # placeholder key, so skip subscription rather than subscribing
                # to a key that silently produces no ticks.
                print(f"[feed] No active MCX contract for {symbol} (offline or CDN unavailable)")
                return

        if key in self._streamer_keys:
            return
        self._streamer_keys.add(key)

        if self._streamer is not None and self._streamer_ready:
            try:
                self._streamer.subscribe([key], "ltpc")
            except Exception as exc:  # noqa: BLE001
                print(f"[feed] subscribe failed for {key}: {exc}")
        # else: _on_upstox_open will bulk-subscribe all pending keys.

    @staticmethod
    def _extract_ltp(payload: dict) -> float | None:
        """Pull ltp from a Upstox MarketDataStreamerV3 (V3 proto) message payload.

        V3 proto FeedUnion has three variants:
          1. ltpc         – used when subscribed with mode="ltpc" (our default)
                           payload["ltpc"]["ltp"]
          2. fullFeed     – used in full_d5 / full_d30 mode
                           payload["fullFeed"]["marketFF"]["ltpc"]["ltp"]  (equity/FO/MCX)
                           payload["fullFeed"]["indexFF"]["ltpc"]["ltp"]   (indices)
          3. firstLevelWithGreeks – option chain mode, not used here

        There is NO "eFO" or "cFO" field in V3.  Both equity F&O and MCX
        commodity futures arrive under "marketFF" in full feed mode.
        In ltpc mode all instruments (NSE, BSE, MCX) use payload["ltpc"]["ltp"].
        """
        # ── ltpc mode (our subscription mode) ────────────────────────────
        try:
            v = (payload.get("ltpc") or {}).get("ltp")
            if v is not None:
                return float(v)
        except Exception:  # noqa: BLE001
            pass

        # ── full feed mode (fallback — triggered if mode was changed) ─────
        try:
            ff = payload.get("fullFeed") or {}
            # Equity, F&O, MCX FO — all arrive under marketFF
            v = (ff.get("marketFF") or {}).get("ltpc", {}).get("ltp")
            if v is not None:
                return float(v)
            # NSE/BSE indices
            v = (ff.get("indexFF") or {}).get("ltpc", {}).get("ltp")
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

                # ── Static instrument lookup (NSE/BSE indices + equities) ──
                inst = by_key(key)
                if inst:
                    if self.loop:
                        asyncio.run_coroutine_threadsafe(
                            self._broadcast(inst.symbol, ltp, ts), self.loop
                        )
                    continue

                # ── Dynamic MCX commodity key → symbol lookup ─────────────
                with self._lock:
                    mcx_sym = self._mcx_key_to_symbol.get(key)
                if mcx_sym and self.loop:
                    asyncio.run_coroutine_threadsafe(
                        self._broadcast(mcx_sym, ltp, ts), self.loop
                    )
                    continue

                # ── Option contract (directly subscribed by key) ───────────
                if key in self._option_keys and self.loop:
                    asyncio.run_coroutine_threadsafe(
                        self._broadcast_option_tick(key, ltp, ts), self.loop
                    )

        except Exception as exc:  # noqa: BLE001
            print(f"[feed] message decode error: {exc}")


hub = FeedHub()
