"""
Price collector — fetches OHLCV candles directly from Binance REST API via httpx.
No ccxt, no aiodns, no DNS issues.
"""
import asyncio
from typing import Optional
import httpx
import pandas as pd
from loguru import logger
from sqlalchemy import delete

from src.config import cfg
from src.database.db import AsyncSessionLocal
from src.database.models import Candle

TIMEFRAMES = ["1h", "4h", "1d"]
CANDLES_LIMIT = 210      # EMA200 needs 200 + buffer — was 500 (too slow)
BINANCE_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"

# Binance Futures uses 1000x-scaled symbols for very low-price coins
FUTURES_SYMBOL_OVERRIDE = {
    "SHIB/USDT":  "1000SHIBUSDT",
    "PEPE/USDT":  "1000PEPEUSDT",
    "FLOKI/USDT": "1000FLOKIUSDT",
    "LUNC/USDT":  "1000LUNCUSDT",
    "BONK/USDT":  "1000BONKUSDT",
}


class PriceCollector:
    def __init__(self):
        self._cache: dict[str, dict[str, pd.DataFrame]] = {}

    async def fetch(self) -> dict[str, dict[str, pd.DataFrame]]:
        # Phase 1: fetch all HTTP in parallel (semaphore = 10 concurrent)
        semaphore = asyncio.Semaphore(10)
        fetched: list[tuple[str, str, pd.DataFrame]] = []

        async with httpx.AsyncClient(timeout=30) as client:
            tasks = [
                self._fetch_one(semaphore, client, coin, tf)
                for coin in cfg.WATCHLIST
                for tf in TIMEFRAMES
            ]
            results = await asyncio.gather(*tasks)

        for item in results:
            if item is not None:
                coin, tf, df = item
                self._cache.setdefault(coin, {})[tf] = df
                fetched.append((coin, tf, df))

        # Phase 2: write to DB sequentially — avoids SQLite lock contention
        for coin, tf, df in fetched:
            await self._persist(coin, tf, df)

        logger.debug(f"🕯  prices updated | {len(cfg.WATCHLIST)} coins × {len(TIMEFRAMES)} TF")
        return self._cache

    async def _fetch_one(
        self,
        sem: asyncio.Semaphore,
        client: httpx.AsyncClient,
        coin: str,
        tf: str,
    ) -> Optional[tuple[str, str, pd.DataFrame]]:
        symbol = FUTURES_SYMBOL_OVERRIDE.get(coin, coin.replace("/", ""))
        async with sem:
            try:
                raw = await self._fetch_klines(client, symbol, tf)
                df = self._to_df(raw)
                return coin, tf, df
            except Exception as e:
                logger.warning(f"⚠️  fetch failed | {coin} {tf}: {e}")
                return None

    async def _fetch_klines(self, client: httpx.AsyncClient, symbol: str, tf: str) -> list:
        resp = await client.get(BINANCE_KLINES_URL, params={
            "symbol": symbol,
            "interval": tf,
            "limit": CANDLES_LIMIT,
        })
        resp.raise_for_status()
        return resp.json()

    def get(self, coin: str, tf: str) -> Optional[pd.DataFrame]:
        return self._cache.get(coin, {}).get(tf)

    @staticmethod
    def _to_df(raw: list) -> pd.DataFrame:
        df = pd.DataFrame(raw, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df[["open", "high", "low", "close", "volume"]] = \
            df[["open", "high", "low", "close", "volume"]].astype(float)
        df.set_index("timestamp", inplace=True)
        return df

    async def _persist(self, coin: str, tf: str, df: pd.DataFrame):
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(Candle).where(Candle.coin == coin, Candle.timeframe == tf)
            )
            session.add_all([
                Candle(
                    coin=coin, timeframe=tf,
                    timestamp=idx.to_pydatetime(),
                    open=row["open"], high=row["high"],
                    low=row["low"], close=row["close"], volume=row["volume"],
                )
                for idx, row in df.iterrows()
            ])
            await session.commit()

    def close(self):
        pass
