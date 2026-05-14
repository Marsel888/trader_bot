"""
Agent B — Volume & Momentum Analyst.
Independent AI that evaluates signals based purely on volume and price momentum.
Votes "take" or "skip" for each proposed signal.
"""
from loguru import logger
from src.ai.openrouter_client import call_openrouter


MODEL = "deepseek/deepseek-chat"

_SYSTEM = """You are a crypto volume and momentum analyst.
Your job: decide if a trade signal is supported by volume and price momentum.
You ONLY look at volume patterns and momentum — ignore trend/fundamentals.
Respond ONLY with valid JSON: {"vote": "take" or "skip", "reason": "1 short sentence"}"""


def _build_prompt(signal, price_cache: dict) -> str:
    coin = signal.coin
    df_1h = price_cache.get(coin, {}).get("1h")

    vol_data = "no data"
    momentum_data = "no data"

    if df_1h is not None and len(df_1h) >= 20:
        vol_last = df_1h["volume"].iloc[-1]
        vol_avg = df_1h["volume"].rolling(20).mean().iloc[-1]
        vol_ratio = vol_last / vol_avg if vol_avg else 1.0

        # Volume on up vs down candles (last 10 bars)
        last10 = df_1h.tail(10)
        up_vol   = last10[last10["close"] > last10["open"]]["volume"].sum()
        down_vol = last10[last10["close"] < last10["open"]]["volume"].sum()

        # Price change over last 5 bars
        price_chg_5 = (df_1h["close"].iloc[-1] - df_1h["close"].iloc[-6]) / df_1h["close"].iloc[-6] * 100

        vol_data = (
            f"Current volume: {vol_last:.0f} ({vol_ratio:.1f}x avg)\n"
            f"Up-candle volume (10 bars): {up_vol:.0f}\n"
            f"Down-candle volume (10 bars): {down_vol:.0f}"
        )
        momentum_data = f"Price change last 5h: {price_chg_5:+.2f}%"

    return f"""Trade signal to evaluate:
Coin: {signal.coin} | Direction: {signal.direction}
Entry: {signal.entry:.4f} | Stop: {signal.suggested_stop:.4f} | TP: {signal.suggested_tp2:.4f}

Volume data (1h):
{vol_data}

Momentum:
{momentum_data}

Should we take this trade based on volume/momentum? Vote "take" or "skip"."""


async def vote(signal, price_cache: dict) -> tuple[bool, str]:
    """Returns (True=take, reason)."""
    prompt = _build_prompt(signal, price_cache)
    try:
        result = await call_openrouter(
            system=_SYSTEM,
            user=prompt,
            model=MODEL,
            temperature=0.2,
            max_tokens=80,
        )
        decision = result.get("vote", "skip").lower()
        reason = result.get("reason", "")
        logger.debug(f"agent_b | {signal.coin} {signal.direction} → {decision} | {reason}")
        return decision == "take", reason
    except Exception as e:
        logger.warning(f"agent_b | {signal.coin} error: {e} — abstain (take)")
        return True, "abstain"
