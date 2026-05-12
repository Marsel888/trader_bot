"""
Binance Futures WebSocket mini-ticker stream — real-time prices for all watchlist coins.
Uses fstream.binance.com (perpetual futures) to match TradingView BTCUSDT.P prices.
Updates every ~1 second per coin. Used by portfolio for stop/TP checks.

Falls back gracefully: if WS is down, portfolio uses REST candle prices.
"""
import asyncio
import json
import time
from loguru import logger

import websockets

from src.config import cfg
from src.data.price_collector import FUTURES_SYMBOL_OVERRIDE


class WSPriceStream:
    WS_URL = "wss://fstream.binance.com/stream?streams={streams}"

    def __init__(self):
        self._prices: dict[str, float] = {}       # "BTC/USDT" → latest price
        self._updated_at: dict[str, float] = {}   # "BTC/USDT" → unix timestamp
        self._running = False
        self._task: asyncio.Task | None = None

        # Reverse lookup: "1000SHIBUSDT" → "SHIB/USDT", "BTCUSDT" → "BTC/USDT"
        self._symbol_map = {
            FUTURES_SYMBOL_OVERRIDE.get(coin, coin.replace("/", "")): coin
            for coin in cfg.WATCHLIST
        }

    # ── Public API ──────────────────────────────────────────────────────────

    def get_price(self, coin: str) -> float | None:
        """Returns latest WS price, or None if not yet received."""
        return self._prices.get(coin)

    def get_price_age(self, coin: str) -> float:
        """Returns seconds since last price update (large if never received)."""
        ts = self._updated_at.get(coin)
        return (time.time() - ts) if ts else 9999.0

    def is_fresh(self, coin: str, max_age: float = 30.0) -> bool:
        return self.get_price_age(coin) <= max_age

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self, loop: asyncio.AbstractEventLoop | None = None):
        """Start WebSocket stream as background task."""
        self._running = True
        self._task = asyncio.ensure_future(self._run())
        logger.info(f"ws_stream | started for {len(cfg.WATCHLIST)} coins")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _run(self):
        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"ws_stream | disconnected: {e} — reconnect in 10s")
                await asyncio.sleep(10)

    async def _connect(self):
        streams = "/".join(
            FUTURES_SYMBOL_OVERRIDE.get(coin, coin.replace("/", "")).lower() + "@miniTicker"
            for coin in cfg.WATCHLIST
        )
        url = self.WS_URL.format(streams=streams)

        async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
            logger.info("ws_stream | connected to Binance")
            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    data = msg.get("data", {})
                    symbol = data.get("s", "")   # "BTCUSDT"
                    price_str = data.get("c")     # current close price as string
                    if symbol and price_str:
                        coin = self._symbol_map.get(symbol)
                        if coin:
                            self._prices[coin] = float(price_str)
                            self._updated_at[coin] = time.time()
                except Exception:
                    pass  # malformed message — skip silently
