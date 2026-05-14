"""
Agent B — Volume & Momentum Analyst.
Votes "take" or "skip" based on volume quality and momentum.
Strict: skips overbought/oversold entries, weak momentum, declining volume.
"""
from loguru import logger
from src.ai.openrouter_client import call_openrouter
from src.indicators import rsi as _rsi


MODEL = "deepseek/deepseek-chat"

_SYSTEM = """You are a strict crypto volume and momentum analyst.
Your job: vote ONLY if volume AND momentum clearly support the trade direction.
Be conservative — when in doubt, vote "skip".

Rules for LONG:
- SKIP if RSI > 70 (overbought)
- SKIP if current volume < average volume (no confirmation)
- SKIP if price dropped last 3h while volume rose (distribution)
- SKIP if momentum is decelerating (last bar smaller than previous)

Rules for SHORT:
- SKIP if RSI < 30 (oversold)
- SKIP if current volume < average volume (no confirmation)
- SKIP if price rose last 3h while volume rose (accumulation)

Respond ONLY with valid JSON: {"vote": "take" or "skip", "reason": "1 short sentence"}"""


def _build_prompt(signal, price_cache: dict) -> str:
    coin = signal.coin
    direction = signal.direction
    df_1h = price_cache.get(coin, {}).get("1h")

    vol_data = "no data"
    momentum_data = "no data"
    rsi_data = "no data"

    if df_1h is not None and len(df_1h) >= 20:
        vol_last = df_1h["volume"].iloc[-1]
        vol_avg = df_1h["volume"].rolling(20).mean().iloc[-1]
        vol_ratio = vol_last / vol_avg if vol_avg else 1.0

        # Volume on up vs down candles (last 10 bars)
        last10 = df_1h.tail(10)
        up_vol   = last10[last10["close"] > last10["open"]]["volume"].sum()
        down_vol = last10[last10["close"] < last10["open"]]["volume"].sum()

        # Volume trend — growing or shrinking last 5 bars
        vol5 = df_1h["volume"].iloc[-5:]
        vol_trend = "growing" if vol5.iloc[-1] > vol5.iloc[0] else "shrinking"

        # Price momentum last 3h and 1h
        price_chg_3h = (df_1h["close"].iloc[-1] - df_1h["close"].iloc[-4]) / df_1h["close"].iloc[-4] * 100
        price_chg_1h = (df_1h["close"].iloc[-1] - df_1h["close"].iloc[-2]) / df_1h["close"].iloc[-2] * 100

        # Last two candle bodies — is momentum accelerating or decelerating?
        body_last = abs(df_1h["close"].iloc[-1] - df_1h["open"].iloc[-1])
        body_prev = abs(df_1h["close"].iloc[-2] - df_1h["open"].iloc[-2])
        momentum_quality = "accelerating" if body_last > body_prev else "decelerating"

        vol_data = (
            f"Current volume: {vol_last:.0f} ({vol_ratio:.1f}x avg) — {vol_trend}\n"
            f"Up-candle volume (10 bars): {up_vol:.0f}\n"
            f"Down-candle volume (10 bars): {down_vol:.0f}"
        )
        momentum_data = (
            f"Price change last 3h: {price_chg_3h:+.2f}%\n"
            f"Price change last 1h: {price_chg_1h:+.2f}%\n"
            f"Momentum: {momentum_quality} (last candle body vs previous)"
        )

    if df_1h is not None and len(df_1h) >= 14:
        rsi_val = _rsi(df_1h, 14).iloc[-1]
        rsi_data = f"RSI(14): {rsi_val:.1f}"

    return f"""Trade signal to evaluate:
Coin: {coin} | Direction: {direction}
Entry: {signal.entry:.4f} | Stop: {signal.suggested_stop:.4f} | TP: {signal.suggested_tp2:.4f}

Volume data (1h):
{vol_data}

Momentum:
{momentum_data}

RSI:
{rsi_data}

Should we take this {direction} trade based on volume/momentum? Apply strict rules. Vote "take" or "skip"."""


async def vote(signal, price_cache: dict) -> tuple[bool, str]:
    """Returns (True=take, reason)."""
    prompt = _build_prompt(signal, price_cache)
    try:
        result = await call_openrouter(
            system=_SYSTEM,
            user=prompt,
            model=MODEL,
            temperature=0.1,
            max_tokens=80,
        )
        decision = result.get("vote", "skip").lower()
        reason = result.get("reason", "")
        logger.debug(f"agent_b | {signal.coin} {signal.direction} → {decision} | {reason}")
        return decision == "take", reason
    except Exception as e:
        logger.warning(f"agent_b | {signal.coin} error: {e} — abstain (skip)")
        return False, "error — abstain"
