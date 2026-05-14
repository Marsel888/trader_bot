"""
Volume & Momentum Agent — rule-based vote on trade signals.

Votes "take" when volume and momentum confirm the signal direction.
No AI calls — fast and deterministic.
"""
from loguru import logger
from src.indicators import ema as _ema


def vote(signal, price_cache: dict) -> bool:
    """Returns True if volume/momentum confirms the signal."""
    coin = signal.coin
    direction = signal.direction

    df_1h = price_cache.get(coin, {}).get("1h")
    df_4h = price_cache.get(coin, {}).get("4h")

    if df_1h is None or len(df_1h) < 20:
        logger.debug(f"volume_agent | {coin}: no 1h data — abstain")
        return True  # abstain = don't block

    # ── 1. Volume confirmation ───────────────────────────────────────
    vol_sma = df_1h["volume"].rolling(20).mean().iloc[-1]
    vol_last = df_1h["volume"].iloc[-1]
    volume_ok = vol_last > vol_sma * 1.1  # at least 10% above average

    # ── 2. Momentum (rate of change over 5 bars) ────────────────────
    if len(df_1h) >= 6:
        roc = (df_1h["close"].iloc[-1] - df_1h["close"].iloc[-6]) / df_1h["close"].iloc[-6]
        momentum_ok = (roc > 0 and direction == "LONG") or (roc < 0 and direction == "SHORT")
    else:
        momentum_ok = True  # abstain

    # ── 3. 4h EMA trend alignment ───────────────────────────────────
    trend_ok = True
    if df_4h is not None and len(df_4h) >= 50:
        ema50 = _ema(df_4h, 50).iloc[-1]
        close_4h = df_4h["close"].iloc[-1]
        if direction == "LONG":
            trend_ok = close_4h > ema50
        else:
            trend_ok = close_4h < ema50

    result = volume_ok and (momentum_ok or trend_ok)
    logger.debug(
        f"volume_agent | {coin} {direction} | "
        f"vol={'✓' if volume_ok else '✗'} mom={'✓' if momentum_ok else '✗'} "
        f"trend={'✓' if trend_ok else '✗'} → {'TAKE' if result else 'SKIP'}"
    )
    return result
