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


def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (MACD line, Signal line, Histogram)."""
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger(df: pd.DataFrame, length: int = 20, std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper band, middle band, lower band)."""
    middle = df["close"].rolling(length).mean()
    sigma = df["close"].rolling(length).std()
    return middle + std * sigma, middle, middle - std * sigma


def vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP from start of DataFrame (use last 24 bars for daily VWAP)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_tp_vol = (typical * df["volume"]).cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def candle_patterns(df: pd.DataFrame) -> list[str]:
    """Detect last-bar candlestick patterns. Returns list of pattern names."""
    if len(df) < 2:
        return []
    patterns = []
    o, h, l, c = df["open"].iloc[-1], df["high"].iloc[-1], df["low"].iloc[-1], df["close"].iloc[-1]
    po, ph, pl, pc = df["open"].iloc[-2], df["high"].iloc[-2], df["low"].iloc[-2], df["close"].iloc[-2]
    body = abs(c - o)
    full_range = h - l if h != l else 0.0001
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l

    if body / full_range < 0.1:
        patterns.append("doji")
    if lower_shadow > 2 * body and upper_shadow < body and c > o:
        patterns.append("hammer")
    if upper_shadow > 2 * body and lower_shadow < body and c < o:
        patterns.append("shooting_star")
    if c > po and o < pc and c > o:
        patterns.append("bullish_engulfing")
    if c < po and o > pc and c < o:
        patterns.append("bearish_engulfing")
    if c > o and c > ph:
        patterns.append("breakout_candle_up")
    if c < o and c < pl:
        patterns.append("breakout_candle_down")
    return patterns
