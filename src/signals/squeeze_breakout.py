"""
Squeeze breakout setup — a REAL trade setup, not just a trend state.

Logic: price compresses into a tight range (volatility contraction), then
breaks out with volume. The stop sits at the opposite side of the range —
a logical invalidation point: if price re-enters the range, the idea is dead.

Edge source: volatility cycles. Tight consolidation stores energy; the
breakout releases it. A tight box also means a small, logical stop —
asymmetric risk/reward.
"""
import pandas as pd
from loguru import logger

from src.config import cfg
from src.indicators import atr as _atr
from src.signals.trend_follower import Signal

RANGE_LOOKBACK = 20      # the consolidation box = last 20 bars
SQUEEZE_RATIO = 0.7      # recent range must be < 70% of the prior range
VOL_SPIKE = 1.5          # breakout bar volume vs 20-bar average
MAX_BOX_ATR = 4.0        # skip if the box is wider than 4×ATR (not a real squeeze)


class SqueezeBreakout:
    name = "squeeze_breakout_v1"

    def generate(self, coin: str, df_1h: pd.DataFrame, df_4h: pd.DataFrame, df_1d: pd.DataFrame) -> list[Signal]:
        try:
            return self._run(coin, df_1h)
        except Exception as e:
            logger.warning(f"squeeze | {coin}: {e}")
            return []

    def _run(self, coin: str, df: pd.DataFrame) -> list[Signal]:
        need = RANGE_LOOKBACK * 2 + 5
        if len(df) < need:
            return []

        atr_s = _atr(df, cfg.ATR_PERIOD)
        last_atr = atr_s.iloc[-1]
        if pd.isna(last_atr) or last_atr <= 0:
            return []

        close = df["close"].iloc[-1]
        prev_close = df["close"].iloc[-2]

        # The box = previous RANGE_LOOKBACK bars (exclude current breakout bar)
        box = df.iloc[-(RANGE_LOOKBACK + 1):-1]
        box_high = box["high"].max()
        box_low = box["low"].min()
        recent_range = box_high - box_low
        if recent_range <= 0:
            return []

        # Prior window — to confirm volatility actually contracted
        prior = df.iloc[-(RANGE_LOOKBACK * 2 + 1):-(RANGE_LOOKBACK + 1)]
        prior_range = prior["high"].max() - prior["low"].min()
        if prior_range <= 0:
            return []

        # Squeeze: recent range significantly tighter than prior range
        if recent_range >= prior_range * SQUEEZE_RATIO:
            return []

        # Box must be genuinely tight relative to ATR
        if recent_range > MAX_BOX_ATR * last_atr:
            return []

        # Volume confirmation on the breakout bar
        vol_avg = df["volume"].rolling(RANGE_LOOKBACK).mean().iloc[-1]
        vol_ok = vol_avg > 0 and df["volume"].iloc[-1] > vol_avg * VOL_SPIKE
        if not vol_ok:
            return []

        signals = []

        # LONG — breakout bar closes above the box (prev bar was still inside)
        if close > box_high and prev_close <= box_high:
            stop = box_low
            risk = close - stop
            if risk > 0:
                signals.append(Signal(
                    coin=coin, direction="LONG",
                    entry=close, suggested_stop=stop,
                    suggested_tp1=close + cfg.TP1_R * risk,
                    suggested_tp2=close + cfg.TP2_R * risk,
                    confidence=0.7,
                    reason=f"Squeeze breakout above {box_high:.4f} (box {recent_range/last_atr:.1f}×ATR)",
                    source=self.name, atr=last_atr,
                ))

        # SHORT — breakout bar closes below the box
        if close < box_low and prev_close >= box_low:
            stop = box_high
            risk = stop - close
            if risk > 0:
                signals.append(Signal(
                    coin=coin, direction="SHORT",
                    entry=close, suggested_stop=stop,
                    suggested_tp1=close - cfg.TP1_R * risk,
                    suggested_tp2=close - cfg.TP2_R * risk,
                    confidence=0.7,
                    reason=f"Squeeze breakout below {box_low:.4f} (box {recent_range/last_atr:.1f}×ATR)",
                    source=self.name, atr=last_atr,
                ))

        return signals
