"""
Trend follower signal generator.
Fires when EMA50 crosses EMA200, ADX > 25, and volume confirms.
"""
from dataclasses import dataclass, field
import pandas as pd
from loguru import logger

from src.config import cfg
from src.indicators import ema, atr, adx


@dataclass
class Signal:
    coin: str
    direction: str          # LONG | SHORT
    entry: float
    suggested_stop: float
    suggested_tp1: float
    suggested_tp2: float
    confidence: float
    reason: str
    source: str
    atr: float = 0.0
    adx: float = 0.0
    extra: dict = field(default_factory=dict)


class TrendFollower:
    name = "trend_follower_v1"

    def generate(
        self,
        coin: str,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
        df_1d: pd.DataFrame,
    ) -> list[Signal]:
        try:
            return self._run(coin, df_4h)
        except Exception as e:
            logger.warning(f"trend_follower | {coin}: {e}")
            return []

    def _run(self, coin: str, df: pd.DataFrame) -> list[Signal]:
        if len(df) < cfg.EMA_SLOW + 10:
            return []

        ema_fast = ema(df, cfg.EMA_FAST)
        ema_slow = ema(df, cfg.EMA_SLOW)
        adx_s, dmp, dmn = adx(df, cfg.ADX_PERIOD)
        atr_s = atr(df, cfg.ATR_PERIOD)

        last_atr = atr_s.iloc[-1]
        if pd.isna(last_atr) or last_atr == 0:
            return []

        close = df["close"].iloc[-1]
        last_adx = adx_s.iloc[-1]
        last_dmp = dmp.iloc[-1]
        last_dmn = dmn.iloc[-1]

        prev_fast = ema_fast.iloc[-2]
        curr_fast = ema_fast.iloc[-1]
        prev_slow = ema_slow.iloc[-2]
        curr_slow = ema_slow.iloc[-1]

        gap_pct = (curr_fast - curr_slow) / curr_slow * 100
        vol_avg = df["volume"].rolling(20).mean().iloc[-1]
        vol_ok = df["volume"].iloc[-1] > vol_avg * 1.1

        golden_cross = (prev_fast <= prev_slow) and (curr_fast > curr_slow)
        death_cross = (prev_fast >= prev_slow) and (curr_fast < curr_slow)

        # TEST MODE: fire whenever EMA alignment holds, not just at crossing moment
        if cfg.TEST_SIGNALS:
            golden_cross = curr_fast > curr_slow
            death_cross = curr_fast < curr_slow

        adx_min = 15 if cfg.TEST_SIGNALS else cfg.ADX_THRESHOLD

        logger.debug(
            f"trend | {coin} | EMA{cfg.EMA_FAST}={curr_fast:.2f} EMA{cfg.EMA_SLOW}={curr_slow:.2f} "
            f"gap={gap_pct:+.2f}% ADX={last_adx:.1f} "
            f"{'🔀SIGNAL!' if golden_cross or death_cross else 'no signal'}"
            f"{' [TEST]' if cfg.TEST_SIGNALS else ''}"
        )

        signals = []

        # Pullback filter — entry must be within N×ATR of EMA_FAST (trigger zone)
        # Avoids buying tops / shorting bottoms when price has already extended.
        distance_to_ema = abs(close - curr_fast)
        max_distance = cfg.PULLBACK_MAX_DISTANCE_ATR * last_atr
        is_pullback_zone = distance_to_ema <= max_distance

        if not is_pullback_zone:
            logger.debug(
                f"trend | {coin} | distance to EMA{cfg.EMA_FAST}={distance_to_ema:.4f} "
                f"> {max_distance:.4f} ({cfg.PULLBACK_MAX_DISTANCE_ATR}×ATR) — extended, skip"
            )
            return []

        if golden_cross and last_adx >= adx_min and last_dmp > last_dmn:
            stop = close - cfg.ATR_STOP_MULTIPLIER * last_atr
            tp1 = close + cfg.TP1_R * (close - stop)
            tp2 = close + cfg.TP2_R * (close - stop)
            conf = min(0.9, 0.6 + (last_adx - 25) / 100 + (0.1 if vol_ok else 0))
            signals.append(Signal(
                coin=coin, direction="LONG",
                entry=close, suggested_stop=stop, suggested_tp1=tp1, suggested_tp2=tp2,
                confidence=conf,
                reason=f"Pullback to EMA{cfg.EMA_FAST}, golden cross, ADX={last_adx:.1f}",
                source=self.name, atr=last_atr, adx=last_adx, extra={"vol_ok": vol_ok},
            ))

        if death_cross and last_adx >= adx_min and last_dmn > last_dmp:
            stop = close + cfg.ATR_STOP_MULTIPLIER * last_atr
            tp1 = close - cfg.TP1_R * (stop - close)
            tp2 = close - cfg.TP2_R * (stop - close)
            conf = min(0.9, 0.6 + (last_adx - 25) / 100 + (0.1 if vol_ok else 0))
            signals.append(Signal(
                coin=coin, direction="SHORT",
                entry=close, suggested_stop=stop, suggested_tp1=tp1, suggested_tp2=tp2,
                confidence=conf,
                reason=f"Pullback to EMA{cfg.EMA_FAST}, death cross, ADX={last_adx:.1f}",
                source=self.name, atr=last_atr, adx=last_adx, extra={"vol_ok": vol_ok},
            ))

        return signals
