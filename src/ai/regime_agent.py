"""
Agent C — Macro Market Regime Analyst.
Votes "take" or "skip" based on overall market conditions.
Strict: skips overextended markets, weak breadth, unfavorable BTC regime.
"""
from loguru import logger
from src.ai.openrouter_client import call_openrouter
from src.indicators import ema as _ema, rsi as _rsi
from src.config import cfg


MODEL = "qwen/qwen-2.5-72b-instruct"

_SYSTEM = """You are a strict crypto macro market analyst.
Your job: vote ONLY if the overall market environment clearly supports the trade.
Be conservative — protect capital, only approve high-quality setups.

Rules for LONG:
- SKIP if BTC RSI > 72 (market overbought, high reversal risk)
- SKIP if BTC is below EMA50 on 4h (bearish macro)
- SKIP if fewer than 40% of coins are above their EMA50 (weak breadth)
- SKIP if BTC dropped more than 1.5% in last 4h (momentum against longs)

Rules for SHORT:
- SKIP if BTC RSI < 28 (market oversold, high reversal risk)
- SKIP if BTC is above EMA50 on 4h (bullish macro, shorting into strength)
- SKIP if more than 65% of coins are above their EMA50 (breadth too strong)
- SKIP if BTC rose more than 1.5% in last 4h (momentum against shorts)

Respond ONLY with valid JSON: {"vote": "take" or "skip", "reason": "1 short sentence"}"""


def _build_prompt(signal, price_cache: dict) -> str:
    direction = signal.direction

    # BTC analysis
    btc_4h = price_cache.get("BTC/USDT", {}).get("4h")
    btc_1h = price_cache.get("BTC/USDT", {}).get("1h")
    btc_info = "no BTC data"

    if btc_4h is not None and len(btc_4h) >= 50:
        e50 = _ema(btc_4h, 50).iloc[-1]
        btc_close = btc_4h["close"].iloc[-1]
        btc_trend = "bullish" if btc_close > e50 else "bearish"
        btc_chg_4h = (btc_4h["close"].iloc[-1] - btc_4h["close"].iloc[-4]) / btc_4h["close"].iloc[-4] * 100
        pct_vs_ema = ((btc_close / e50) - 1) * 100
        btc_info = (
            f"BTC 4h trend: {btc_trend} (price vs EMA50: {pct_vs_ema:+.1f}%)\n"
            f"BTC change last 4h: {btc_chg_4h:+.2f}%"
        )

    if btc_1h is not None and len(btc_1h) >= 24:
        btc_chg_24h = (btc_1h["close"].iloc[-1] - btc_1h["close"].iloc[-24]) / btc_1h["close"].iloc[-24] * 100
        btc_info += f"\nBTC change last 24h: {btc_chg_24h:+.2f}%"

    if btc_1h is not None and len(btc_1h) >= 14:
        btc_rsi = _rsi(btc_1h, 14).iloc[-1]
        btc_info += f"\nBTC RSI(14) on 1h: {btc_rsi:.1f}"

    # Market breadth — how many coins trending up
    coins_up, coins_down = 0, 0
    for coin in cfg.WATCHLIST[:20]:
        df = price_cache.get(coin, {}).get("1h")
        if df is not None and len(df) >= 50:
            e50c = _ema(df, 50).iloc[-1]
            if df["close"].iloc[-1] > e50c:
                coins_up += 1
            else:
                coins_down += 1

    total_checked = coins_up + coins_down
    pct_up = coins_up / total_checked * 100 if total_checked else 0
    breadth_info = (
        f"{coins_up}/{total_checked} coins above EMA50 ({pct_up:.0f}%)"
        if total_checked else "no breadth data"
    )

    return f"""Trade signal to evaluate:
Coin: {signal.coin} | Direction: {direction}
Entry: {signal.entry:.4f} | Stop: {signal.suggested_stop:.4f} | TP: {signal.suggested_tp2:.4f}

BTC macro conditions:
{btc_info}

Market breadth:
{breadth_info}

Apply strict rules. Is the overall market environment favorable for a {direction} trade? Vote "take" or "skip"."""


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
        logger.debug(f"agent_c | {signal.coin} {signal.direction} → {decision} | {reason}")
        return decision == "take", reason
    except Exception as e:
        logger.warning(f"agent_c | {signal.coin} error: {e} — abstain (skip)")
        return False, "error — abstain"
