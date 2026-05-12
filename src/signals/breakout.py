"""
Breakout signal generator.
Fires when price breaks 20-period high/low with 2x average volume.
"""
import pandas as pd
from loguru import logger

from src.config import cfg
from src.indicators import atr
from src.signals.trend_follower import Signal

BREAKOUT_PERIOD = 20
VOLUME_MULTIPLIER = 1.5


class BreakoutGenerator:
    name = "breakout_v1"

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
            logger.warning(f"breakout | {coin}: {e}")
            return []

    def _run(self, coin: str, df: pd.DataFrame) -> list[Signal]:
        if len(df) < BREAKOUT_PERIOD + 2:
            return []

        atr_s = atr(df, cfg.ATR_PERIOD)
        last_atr = atr_s.iloc[-1]
        if pd.isna(last_atr) or last_atr == 0:
            return []

        close = df["close"].iloc[-1]
        prev_high = df["high"].iloc[-(BREAKOUT_PERIOD + 1):-1].max()
        prev_low = df["low"].iloc[-(BREAKOUT_PERIOD + 1):-1].min()

        vol_avg = df["volume"].rolling(BREAKOUT_PERIOD).mean().iloc[-1]
        vol_confirmed = df["volume"].iloc[-1] > vol_avg * VOLUME_MULTIPLIER

        vol_ratio = df["volume"].iloc[-1] / vol_avg if vol_avg else 0
        # TEST MODE: skip volume filter
        if cfg.TEST_SIGNALS:
            vol_confirmed = True

        logger.debug(
            f"breakout | {coin} | close={close:.4f} "
            f"high20={prev_high:.4f} low20={prev_low:.4f} "
            f"vol={vol_ratio:.2f}x {'✓' if vol_confirmed else '✗'}"
            f"{' [TEST]' if cfg.TEST_SIGNALS else ''}"
        )

        signals = []

        if close > prev_high and vol_confirmed:
            stop = prev_high - cfg.ATR_STOP_MULTIPLIER * last_atr
            tp1 = close + cfg.TP1_R * (close - stop)
            tp2 = close + cfg.TP2_R * (close - stop)
            signals.append(Signal(
                coin=coin, direction="LONG",
                entry=close, suggested_stop=stop, suggested_tp1=tp1, suggested_tp2=tp2,
                confidence=0.65,
                reason=f"Breakout above {BREAKOUT_PERIOD}-bar high {prev_high:.4f} with {VOLUME_MULTIPLIER}x volume",
                source=self.name, atr=last_atr,
            ))

        if close < prev_low and vol_confirmed:
            stop = prev_low + cfg.ATR_STOP_MULTIPLIER * last_atr
            tp1 = close - cfg.TP1_R * (stop - close)
            tp2 = close - cfg.TP2_R * (stop - close)
            signals.append(Signal(
                coin=coin, direction="SHORT",
                entry=close, suggested_stop=stop, suggested_tp1=tp1, suggested_tp2=tp2,
                confidence=0.65,
                reason=f"Breakout below {BREAKOUT_PERIOD}-bar low {prev_low:.4f} with {VOLUME_MULTIPLIER}x volume",
                source=self.name, atr=last_atr,
            ))

        return signals
