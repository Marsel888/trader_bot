"""
Agent B — Volume & Order Flow Analyst.
Unique data: VWAP, CVD, volume divergence, VSA patterns, candlestick patterns,
             4h volume confirmation, volume profile zones.
"""
from loguru import logger
from src.ai.openrouter_client import call_openrouter
from src.indicators import rsi as _rsi, vwap as _vwap, macd as _macd, candle_patterns


MODEL = "deepseek/deepseek-chat"

_SYSTEM = """You are an expert crypto volume and order flow analyst.
You specialize in reading hidden buying/selling pressure through volume data.
Other analysts handle technical indicators and macro — your job is ONLY volume and order flow.

Patterns you must detect:

BULLISH signals:
- Price falling on LOW volume = no real selling (weak bears)
- Price rising on HIGH volume = strong buying pressure
- Volume climax on lows = capitulation (buyers absorb sellers)
- Up-candles have MORE volume than down-candles
- Price above VWAP = institutions buying above fair value
- CVD rising while price consolidates = accumulation
- Hammer/bullish engulfing on above-average volume

BEARISH signals:
- Price rising on LOW volume = weak rally (no buyers)
- Price falling on HIGH volume = strong selling pressure
- Volume climax on highs = distribution (sellers absorb buyers)
- Down-candles have MORE volume than up-candles
- Price below VWAP = institutions selling below fair value
- CVD falling while price consolidates = distribution
- Shooting star/bearish engulfing on above-average volume

DIVERGENCES (very important):
- Bullish divergence: price makes lower low but volume on down moves decreasing → reversal likely
- Bearish divergence: price makes higher high but volume on up moves decreasing → reversal likely

RULES:
- For LONG: SKIP if bearish volume signals present or volume doesn't confirm upside
- For SHORT: SKIP if bullish volume signals present or volume doesn't confirm downside
- SKIP if RSI > 72 (overbought, late entry) or RSI < 28 (oversold, dangerous short)
- SKIP if MACD histogram is against trade direction

Respond ONLY with valid JSON: {"vote": "take" or "skip", "reason": "1-2 sentences explaining volume pattern"}"""


def _build_prompt(signal, price_cache: dict) -> str:
    coin = signal.coin
    direction = signal.direction
    df_1h = price_cache.get(coin, {}).get("1h")
    df_4h = price_cache.get(coin, {}).get("4h")

    sections = []

    if df_1h is not None and len(df_1h) >= 26:
        # --- Volume analysis ---
        vol_last = df_1h["volume"].iloc[-1]
        vol_avg_20 = df_1h["volume"].rolling(20).mean().iloc[-1]
        vol_ratio = vol_last / vol_avg_20 if vol_avg_20 else 1.0

        # Up vs down candle volume (last 10 and last 20 bars)
        last10 = df_1h.tail(10)
        last20 = df_1h.tail(20)
        up_vol_10   = last10[last10["close"] > last10["open"]]["volume"].sum()
        down_vol_10 = last10[last10["close"] < last10["open"]]["volume"].sum()
        up_vol_20   = last20[last20["close"] > last20["open"]]["volume"].sum()
        down_vol_20 = last20[last20["close"] < last20["open"]]["volume"].sum()
        vol_bias = "bullish" if up_vol_10 > down_vol_10 * 1.2 else ("bearish" if down_vol_10 > up_vol_10 * 1.2 else "neutral")

        # Volume trend (growing or shrinking)
        vol5 = df_1h["volume"].iloc[-5:]
        vol_trend = "growing" if vol5.iloc[-1] > vol5.mean() * 1.1 else ("shrinking" if vol5.iloc[-1] < vol5.mean() * 0.9 else "stable")

        # CVD approximation (cumulative buy - sell volume proxy)
        df_1h = df_1h.copy()
        df_1h["buy_vol"] = df_1h["volume"] * (df_1h["close"] - df_1h["low"]) / (df_1h["high"] - df_1h["low"] + 1e-9)
        df_1h["sell_vol"] = df_1h["volume"] * (df_1h["high"] - df_1h["close"]) / (df_1h["high"] - df_1h["low"] + 1e-9)
        cvd_last5 = (df_1h["buy_vol"].iloc[-5:].sum() - df_1h["sell_vol"].iloc[-5:].sum())
        cvd_trend = "positive (buyers dominate)" if cvd_last5 > 0 else "negative (sellers dominate)"

        # Volume divergence detection
        price_chg_5 = df_1h["close"].iloc[-1] - df_1h["close"].iloc[-6]
        vol_chg_down = df_1h[df_1h["close"] < df_1h["open"]]["volume"].iloc[-5:].mean() if len(df_1h[df_1h["close"] < df_1h["open"]].iloc[-5:]) > 0 else 0
        vol_chg_up   = df_1h[df_1h["close"] > df_1h["open"]]["volume"].iloc[-5:].mean() if len(df_1h[df_1h["close"] > df_1h["open"]].iloc[-5:]) > 0 else 0

        divergence = "none"
        if price_chg_5 < 0 and vol_chg_down < vol_avg_20 * 0.7:
            divergence = "BULLISH DIVERGENCE: price dropping on weak volume (sellers exhausted)"
        elif price_chg_5 > 0 and vol_chg_up < vol_avg_20 * 0.7:
            divergence = "BEARISH DIVERGENCE: price rising on weak volume (buyers exhausted)"

        # VWAP
        vwap_24 = _vwap(df_1h.tail(24)).iloc[-1]
        price_now = df_1h["close"].iloc[-1]
        vwap_pct = (price_now - vwap_24) / vwap_24 * 100
        vwap_pos = f"{'above' if vwap_pct > 0 else 'below'} VWAP by {abs(vwap_pct):.2f}%"

        # MACD
        macd_line, sig_line, hist = _macd(df_1h)
        hist_last = hist.iloc[-1]
        hist_prev = hist.iloc[-2]
        macd_dir = "rising" if hist_last > hist_prev else "falling"
        macd_sign = "positive" if hist_last > 0 else "negative"

        # RSI
        rsi_val = _rsi(df_1h, 14).iloc[-1]

        # Candlestick patterns
        patterns = candle_patterns(df_1h)
        pattern_str = ", ".join(patterns) if patterns else "none"

        # Price changes
        price_chg_1h = (df_1h["close"].iloc[-1] - df_1h["close"].iloc[-2]) / df_1h["close"].iloc[-2] * 100
        price_chg_3h = (df_1h["close"].iloc[-1] - df_1h["close"].iloc[-4]) / df_1h["close"].iloc[-4] * 100

        # Volume at price levels (simple high-volume zones in last 20 bars)
        high_vol_bar = df_1h.iloc[-20:].nlargest(3, "volume")
        hvn_prices = [f"{p:.4f}" for p in high_vol_bar["close"].values]

        sections.append(f"""=== 1H VOLUME & ORDER FLOW ===
Current volume: {vol_last:.0f} ({vol_ratio:.1f}x 20-bar avg) — {vol_trend}
Volume bias (last 10 bars): {vol_bias} | Up: {up_vol_10:.0f} vs Down: {down_vol_10:.0f}
Volume bias (last 20 bars): Up: {up_vol_20:.0f} vs Down: {down_vol_20:.0f}
CVD last 5 bars: {cvd_trend}
Volume divergence: {divergence}

VWAP (24h): price is {vwap_pos}
High-volume price zones (last 20 bars): {', '.join(hvn_prices)}

MACD histogram: {macd_sign}, {macd_dir}
RSI(14): {rsi_val:.1f}
Last candle patterns: {pattern_str}

Price change 1h: {price_chg_1h:+.2f}%
Price change 3h: {price_chg_3h:+.2f}%""")

    if df_4h is not None and len(df_4h) >= 10:
        vol_4h_last = df_4h["volume"].iloc[-1]
        vol_4h_avg  = df_4h["volume"].rolling(10).mean().iloc[-1]
        vol_4h_ratio = vol_4h_last / vol_4h_avg if vol_4h_avg else 1.0
        up_vol_4h = df_4h.tail(5)[df_4h.tail(5)["close"] > df_4h.tail(5)["open"]]["volume"].sum()
        dn_vol_4h = df_4h.tail(5)[df_4h.tail(5)["close"] < df_4h.tail(5)["open"]]["volume"].sum()
        patterns_4h = candle_patterns(df_4h)
        sections.append(f"""=== 4H VOLUME CONFIRMATION ===
4H volume: {vol_4h_last:.0f} ({vol_4h_ratio:.1f}x avg)
4H bias (last 5 bars): Up {up_vol_4h:.0f} vs Down {dn_vol_4h:.0f}
4H last candle patterns: {', '.join(patterns_4h) if patterns_4h else 'none'}""")

    data_block = "\n\n".join(sections) if sections else "No data available"

    return f"""Trade signal to evaluate:
Coin: {coin} | Direction: {direction}
Entry: {signal.entry:.4f} | Stop: {signal.suggested_stop:.4f} | TP: {signal.suggested_tp2:.4f}

{data_block}

Based on volume and order flow analysis ONLY, should we take this {direction} trade?
Look for volume confirmation, divergences, CVD, VWAP position.
Vote "take" or "skip"."""


async def vote(signal, price_cache: dict) -> tuple[bool, str]:
    """Returns (True=take, reason)."""
    prompt = _build_prompt(signal, price_cache)
    try:
        result = await call_openrouter(
            system=_SYSTEM,
            user=prompt,
            model=MODEL,
            temperature=0.1,
            max_tokens=120,
        )
        decision = result.get("vote", "skip").lower()
        reason = result.get("reason", "")
        logger.debug(f"agent_b | {signal.coin} {signal.direction} → {decision} | {reason}")
        return decision == "take", reason
    except Exception as e:
        logger.warning(f"agent_b | {signal.coin} error: {e} — abstain (skip)")
        return False, "error — abstain"
