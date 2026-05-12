"""
Pure pandas/numpy indicator implementations — no pandas-ta dependency.
All functions accept a DataFrame with OHLCV columns and return a Series.
"""
import numpy as np
import pandas as pd


def ema(df: pd.DataFrame, length: int) -> pd.Series:
    return df["close"].ewm(span=length, adjust=False).mean()


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def adx(df: pd.DataFrame, length: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (ADX, +DI, -DI)."""
    high = df["high"]
    low = df["low"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = df["close"].shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm_s = pd.Series(plus_dm, index=df.index)
    minus_dm_s = pd.Series(minus_dm, index=df.index)

    tr_s = atr(df, length) * length  # un-smoothed ATR × period = smoothed TR
    tr_s = tr_s.replace(0, np.nan)

    smooth_plus = plus_dm_s.ewm(span=length, adjust=False).mean()
    smooth_minus = minus_dm_s.ewm(span=length, adjust=False).mean()
    smooth_tr = atr(df, length)
    smooth_tr = smooth_tr.replace(0, np.nan)

    plus_di = 100 * smooth_plus / smooth_tr
    minus_di = 100 * smooth_minus / smooth_tr

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_s = dx.ewm(span=length, adjust=False).mean()

    return adx_s, plus_di, minus_di


def rsi(df: pd.DataFrame, length: int = 14) -> pd.Series:
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(span=length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=length, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))
