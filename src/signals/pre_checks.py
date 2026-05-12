"""
Rule-based pre-checks — fast deterministic gate before AI filter.
Returns (passed: bool, reasons: list[str]).
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

from src.config import cfg
from src.indicators import ema, atr, adx
from src.signals.trend_follower import Signal


@dataclass
class TrendContext:
    bullish: bool
    bearish: bool
    adx_val: float
    close: float


def _trend_context(df: pd.DataFrame) -> TrendContext | None:
    if df is None or len(df) < cfg.EMA_SLOW + 5:
        return None
    ema_fast = ema(df, cfg.EMA_FAST)
    ema_slow = ema(df, cfg.EMA_SLOW)
    adx_s, dmp, dmn = adx(df, cfg.ADX_PERIOD)

    close = df["close"].iloc[-1]
    ef = ema_fast.iloc[-1]
    es = ema_slow.iloc[-1]
    adx_v = adx_s.iloc[-1]
    dp = dmp.iloc[-1]
    dm = dmn.iloc[-1]

    bullish = ef > es and close > es and dp > dm
    bearish = ef < es and close < es and dm > dp

    return TrendContext(bullish=bullish, bearish=bearish, adx_val=adx_v, close=close)


def _atr_extreme(df: pd.DataFrame) -> tuple[float, bool]:
    atr_s = atr(df, cfg.ATR_PERIOD)
    current = atr_s.iloc[-1]
    avg = atr_s.rolling(14).mean().iloc[-1]
    extreme = (not pd.isna(avg)) and (not pd.isna(current)) and current > avg * cfg.ATR_EXTREME_MULTIPLIER
    return float(current) if not pd.isna(current) else 0.0, extreme


def _btc_hourly_move(df_btc_1h: pd.DataFrame | None) -> float:
    if df_btc_1h is None or len(df_btc_1h) < 2:
        return 0.0
    last = df_btc_1h["close"].iloc[-1]
    prev = df_btc_1h["close"].iloc[-2]
    return abs(last - prev) / prev


def rule_pre_checks(
    signal: Signal,
    price_cache: dict,
    portfolio_state: dict,
    news_watcher=None,
) -> tuple[bool, list[str]]:
    """Returns (passed, failed_reasons)."""
    fails: list[str] = []
    coin = signal.coin
    direction = signal.direction

    df_4h = price_cache.get(coin, {}).get("4h")
    df_1d = price_cache.get(coin, {}).get("1d")
    df_1h = price_cache.get(coin, {}).get("1h")
    df_btc_1h = price_cache.get("BTC/USDT", {}).get("1h")

    if cfg.TEST_SIGNALS:
        # TEST MODE — skip market condition checks, only enforce portfolio limits
        pass
    else:
        # 1. Multi-TF trend alignment
        ctx_4h = _trend_context(df_4h)
        ctx_1d = _trend_context(df_1d)

        if ctx_4h is None:
            fails.append("insufficient data for trend check")
        else:
            if direction == "LONG":
                # 4h must be bullish; 1d is a bonus but not required
                if not ctx_4h.bullish:
                    fails.append("4h trend not bullish")
                if ctx_4h.adx_val < cfg.ADX_THRESHOLD:
                    fails.append(f"ADX(4h)={ctx_4h.adx_val:.1f} < {cfg.ADX_THRESHOLD}")
            else:
                if not ctx_4h.bearish:
                    fails.append("4h trend not bearish")
                if ctx_4h.adx_val < cfg.ADX_THRESHOLD:
                    fails.append(f"ADX(4h)={ctx_4h.adx_val:.1f} < {cfg.ADX_THRESHOLD}")

        # 2. ATR not extreme
        if df_1h is not None and len(df_1h) > cfg.ATR_PERIOD:
            _, atr_ext = _atr_extreme(df_1h)
            if atr_ext:
                fails.append("ATR extreme (>2x avg) — market panic")

        # 3. BTC not moving sharply
        btc_move = _btc_hourly_move(df_btc_1h)
        if btc_move > cfg.BTC_HOURLY_MOVE_THRESHOLD:
            fails.append(f"BTC moved {btc_move*100:.1f}% last hour")

    # 4. Portfolio kill switches
    drawdown_today = portfolio_state.get("drawdown_today", 0.0)
    loss_streak = portfolio_state.get("loss_streak", 0)
    open_positions = portfolio_state.get("open_positions", 0)
    open_by_dir = portfolio_state.get("open_by_direction", {})
    open_coins = portfolio_state.get("open_coins", set())

    if drawdown_today <= -cfg.MAX_DAILY_DRAWDOWN:
        fails.append(f"daily drawdown {drawdown_today*100:.1f}% at limit")
    if loss_streak >= cfg.MAX_LOSS_STREAK:
        fails.append(f"loss streak {loss_streak}")
    if open_positions >= cfg.MAX_POSITIONS:
        fails.append(f"max {cfg.MAX_POSITIONS} positions reached")
    if open_by_dir.get(direction, 0) >= cfg.MAX_SAME_DIRECTION:
        fails.append(f"max same-direction ({cfg.MAX_SAME_DIRECTION}) reached")
    if coin in open_coins:
        fails.append(f"already have open position in {coin}")

    # 5. TP/SL ratio — use TP2 (2R target), not TP1
    risk = abs(signal.entry - signal.suggested_stop)
    reward = abs(signal.suggested_tp2 - signal.entry)
    if risk > 0 and reward / risk < 1.2:
        fails.append(f"R:R = {reward/risk:.2f} < 1.2")

    # 6. News window — skip in test mode
    if not cfg.TEST_SIGNALS and news_watcher is not None and news_watcher.has_major_event_soon(cfg.NEWS_WINDOW_MINUTES):
        fails.append("major news in last hour")

    return len(fails) == 0, fails
