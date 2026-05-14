"""
Market Regime Agent — rule-based vote on trade signals.

Votes "take" when overall market regime supports the trade direction.
Checks BTC trend, market breadth, and volatility regime.
No AI calls — fast and deterministic.
"""
from loguru import logger
from src.indicators import ema as _ema, atr as _atr
from src.config import cfg


def vote(signal, price_cache: dict) -> bool:
    """Returns True if market regime supports the signal direction."""
    direction = signal.direction

    btc_1h = price_cache.get("BTC/USDT", {}).get("1h")
    btc_4h = price_cache.get("BTC/USDT", {}).get("4h")

    if btc_4h is None or len(btc_4h) < 50:
        logger.debug(f"regime_agent | {signal.coin}: no BTC 4h data — abstain")
        return True

    # ── 1. BTC 4h trend ─────────────────────────────────────────────
    ema50 = _ema(btc_4h, 50).iloc[-1]
    close_btc = btc_4h["close"].iloc[-1]
    btc_bullish = close_btc > ema50

    if direction == "LONG":
        btc_ok = btc_bullish
    else:
        btc_ok = not btc_bullish

    # ── 2. Market breadth — how many watchlist coins trend with signal ─
    trending_with = 0
    trending_against = 0
    for coin in cfg.WATCHLIST[:10]:  # check top 10 for speed
        df = price_cache.get(coin, {}).get("1h")
        if df is None or len(df) < 50:
            continue
        e50 = _ema(df, 50).iloc[-1]
        price = df["close"].iloc[-1]
        if price > e50:
            trending_with += (1 if direction == "LONG" else 0)
            trending_against += (1 if direction == "SHORT" else 0)
        else:
            trending_with += (1 if direction == "SHORT" else 0)
            trending_against += (1 if direction == "LONG" else 0)

    breadth_ok = trending_with >= trending_against

    # ── 3. Volatility regime — avoid extremely high ATR ─────────────
    volatility_ok = True
    if btc_1h is not None and len(btc_1h) >= 20:
        atr_series = _atr(btc_1h, 14)
        atr_val = atr_series.iloc[-1]
        atr_avg = atr_series.rolling(20).mean().iloc[-1]
        if atr_val > atr_avg * cfg.ATR_EXTREME_MULTIPLIER:
            volatility_ok = False  # market too volatile

    result = btc_ok and (breadth_ok or volatility_ok)
    logger.debug(
        f"regime_agent | {signal.coin} {direction} | "
        f"btc={'✓' if btc_ok else '✗'} breadth={'✓' if breadth_ok else '✗'} "
        f"vol={'✓' if volatility_ok else '✗'} → {'TAKE' if result else 'SKIP'}"
    )
    return result
