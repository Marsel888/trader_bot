"""
Agent C — Macro & Market Structure Analyst.
Unique data: multi-timeframe BTC analysis, sector breadth, correlation,
             volatility regime, altcoin season indicator, market overextension.
"""
from loguru import logger
from src.ai.openrouter_client import call_openrouter
from src.indicators import ema as _ema, rsi as _rsi, atr as _atr
from src.config import cfg


MODEL = "qwen/qwen-2.5-72b-instruct"

_SYSTEM = """You are an expert crypto macro and market structure analyst.
You specialize in understanding the overall market regime and timing.
Other analysts handle volume and individual technicals — your job is the BIG PICTURE.

Market regimes you identify:
- STRONG_BULL: BTC trending up on all TFs, breadth >60%, alts outperforming → best for longs
- BULL: BTC above EMA50 on 4h, breadth 45-60%, moderate conditions → longs ok with caution
- CHOPPY: BTC sideways, breadth 35-50%, mixed signals → avoid new positions
- BEAR: BTC below EMA50 on 4h, breadth <35%, alts weak → only high-conviction shorts
- STRONG_BEAR: BTC crashing, breadth <20%, panic → emergency exits only

Patterns to detect:

FAVORABLE FOR LONGS:
- BTC aligned bullish on 1h + 4h + 1d simultaneously
- Majors (BTC/ETH/BNB) all above their EMA50
- Alts outperforming BTC (altcoin season)
- Low volatility regime transitioning to higher = breakout
- Breadth improving (more coins above EMA50 than yesterday)

UNFAVORABLE FOR LONGS (SKIP):
- BTC RSI > 72 on any timeframe (overbought = high risk of pullback)
- BTC below EMA50 on 4h (bearish macro regardless of 1h)
- Breadth < 40% (majority of market is weak)
- BTC dropped > 1.5% in last 4h (momentum against longs)
- High volatility regime + BTC at highs (exhaustion)
- Coin is highly correlated with BTC and BTC is bearish

FAVORABLE FOR SHORTS:
- BTC below EMA50 on all timeframes
- Breadth < 35% and falling
- Majors leading down
- High volatility + BTC at lows approaching = distribution

UNFAVORABLE FOR SHORTS (SKIP):
- BTC RSI < 28 (oversold, reversal risk)
- BTC above EMA50 on 4h (bullish macro)
- Breadth > 60% (market too strong to short)
- BTC rose > 1.5% in last 4h (momentum against shorts)

Respond ONLY with valid JSON: {"vote": "take" or "skip", "reason": "1-2 sentences on market regime"}"""


def _calc_trend(df, label: str) -> str:
    if df is None or len(df) < 50:
        return f"{label}: no data"
    e50 = _ema(df, 50).iloc[-1]
    e200 = _ema(df, 200).iloc[-1] if len(df) >= 200 else None
    close = df["close"].iloc[-1]
    rsi_v = _rsi(df, 14).iloc[-1]
    trend = "bullish" if close > e50 else "bearish"
    pct = (close / e50 - 1) * 100
    e200_str = f", vs EMA200: {(close/e200-1)*100:+.1f}%" if e200 else ""
    return f"{label}: {trend} (vs EMA50: {pct:+.1f}%{e200_str}, RSI={rsi_v:.0f})"


def _build_prompt(signal, price_cache: dict) -> str:
    direction = signal.direction
    coin = signal.coin

    # ── BTC multi-timeframe analysis ────────────────────────────────
    btc_1h  = price_cache.get("BTC/USDT", {}).get("1h")
    btc_4h  = price_cache.get("BTC/USDT", {}).get("4h")
    btc_1d  = price_cache.get("BTC/USDT", {}).get("1d")

    btc_lines = [
        _calc_trend(btc_1h, "BTC 1h"),
        _calc_trend(btc_4h, "BTC 4h"),
        _calc_trend(btc_1d, "BTC 1d"),
    ]

    # BTC recent moves
    btc_moves = []
    if btc_1h is not None and len(btc_1h) >= 24:
        chg_1h  = (btc_1h["close"].iloc[-1] - btc_1h["close"].iloc[-2])  / btc_1h["close"].iloc[-2]  * 100
        chg_4h  = (btc_1h["close"].iloc[-1] - btc_1h["close"].iloc[-5])  / btc_1h["close"].iloc[-5]  * 100
        chg_24h = (btc_1h["close"].iloc[-1] - btc_1h["close"].iloc[-25]) / btc_1h["close"].iloc[-25] * 100
        btc_moves.append(f"BTC changes: 1h={chg_1h:+.2f}% | 4h={chg_4h:+.2f}% | 24h={chg_24h:+.2f}%")

    # BTC volatility (ATR%)
    if btc_4h is not None and len(btc_4h) >= 14:
        atr_v = _atr(btc_4h, 14).iloc[-1]
        atr_pct = atr_v / btc_4h["close"].iloc[-1] * 100
        vol_regime = "high" if atr_pct > 2.5 else ("low" if atr_pct < 1.0 else "normal")
        btc_moves.append(f"BTC volatility (ATR%): {atr_pct:.2f}% → {vol_regime} volatility regime")

    # ── Market breadth: majors vs alts ──────────────────────────────
    MAJORS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]
    majors_up, majors_total = 0, 0
    alts_up, alts_total = 0, 0

    for c in cfg.WATCHLIST[:25]:
        df = price_cache.get(c, {}).get("1h")
        if df is None or len(df) < 50:
            continue
        above = df["close"].iloc[-1] > _ema(df, 50).iloc[-1]
        if c in MAJORS:
            majors_total += 1
            majors_up += int(above)
        else:
            alts_total += 1
            alts_up += int(above)

    maj_pct = majors_up / majors_total * 100 if majors_total else 0
    alt_pct = alts_up / alts_total * 100 if alts_total else 0
    total_up = majors_up + alts_up
    total = majors_total + alts_total
    total_pct = total_up / total * 100 if total else 0

    breadth_str = (
        f"Overall: {total_up}/{total} coins above EMA50 ({total_pct:.0f}%)\n"
        f"Majors: {majors_up}/{majors_total} ({maj_pct:.0f}%) | Alts: {alts_up}/{alts_total} ({alt_pct:.0f}%)"
    )

    # Altcoin season indicator: alts outperforming majors?
    alt_season = "yes (alts outperforming)" if alt_pct > maj_pct + 10 else ("no (majors leading)" if maj_pct > alt_pct + 10 else "neutral")

    # ── Target coin correlation with BTC ────────────────────────────
    coin_df = price_cache.get(coin, {}).get("1h")
    corr_str = "no data"
    if coin_df is not None and btc_1h is not None and len(coin_df) >= 20 and len(btc_1h) >= 20:
        try:
            coin_ret = coin_df["close"].pct_change().iloc[-20:]
            btc_ret  = btc_1h["close"].pct_change().iloc[-20:]
            corr = coin_ret.corr(btc_ret)
            corr_str = f"{corr:.2f} ({'high' if abs(corr) > 0.7 else 'moderate' if abs(corr) > 0.4 else 'low'} correlation with BTC)"
        except Exception:
            pass

    # ── Multi-coin momentum: how many coins gained last 4h ──────────
    gainers, losers = 0, 0
    for c in cfg.WATCHLIST[:20]:
        df = price_cache.get(c, {}).get("1h")
        if df is not None and len(df) >= 5:
            chg = (df["close"].iloc[-1] - df["close"].iloc[-5]) / df["close"].iloc[-5]
            if chg > 0.005:
                gainers += 1
            elif chg < -0.005:
                losers += 1

    momentum_str = f"Gainers vs losers (last 4h, >0.5%): {gainers} up / {losers} down"

    btc_block = "\n".join(btc_lines + btc_moves)

    return f"""Trade signal to evaluate:
Coin: {coin} | Direction: {direction}
Entry: {signal.entry:.4f} | Stop: {signal.suggested_stop:.4f} | TP: {signal.suggested_tp2:.4f}
{coin} correlation with BTC: {corr_str}

=== BTC MULTI-TIMEFRAME ANALYSIS ===
{btc_block}

=== MARKET BREADTH ===
{breadth_str}
Altcoin season: {alt_season}

=== SHORT-TERM MOMENTUM ===
{momentum_str}

Based on macro regime and market structure ONLY, should we take this {direction} trade?
Identify the current regime and apply the rules strictly.
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
        logger.debug(f"agent_c | {signal.coin} {signal.direction} → {decision} | {reason}")
        return decision == "take", reason
    except Exception as e:
        logger.warning(f"agent_c | {signal.coin} error: {e} — abstain (skip)")
        return False, "error — abstain"
