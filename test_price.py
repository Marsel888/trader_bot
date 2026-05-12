import asyncio
from src.data.price_collector import PriceCollector

async def main():
    pc = PriceCollector()
    print("Fetching BTC/USDT 1h...")
    cache = await pc.fetch()
    for coin, tfs in cache.items():
        for tf, df in tfs.items():
            print(f"{coin} {tf}: {len(df)} candles, last close={df['close'].iloc[-1]:.2f}")

asyncio.run(main())
