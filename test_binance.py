import ccxt

ex = ccxt.binance({"options": {"defaultType": "spot"}})
ticker = ex.fetch_ticker("BTC/USDT")
print(f"BTC price: {ticker['last']}")
ohlcv = ex.fetch_ohlcv("BTC/USDT", "1h", limit=5)
print(f"Got {len(ohlcv)} candles, last close: {ohlcv[-1][4]}")
print("Binance OK")
