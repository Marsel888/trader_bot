"""
Event-driven Hermes exit checker.
Fires only on specific triggers — not every tick — to avoid GPU bottleneck.

Triggers:
  1. BTC moved >2% against position direction in last 1h candle
  2. Hot/negative news appeared about this specific coin in last hour
  3. Position stalling — >12h open and floating PnL < 0.3R

Max 1 Hermes call per tick, 20-minute cooldown per trade.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from loguru import logger

from src.config import cfg
from src.database.models import Trade


_EXIT_PROMPT = """Ти risk manager. У тебе відкрита позиція і з'явилась подія яка може змінити ситуацію.

ПОЗИЦІЯ:
- Монета: {coin} {direction}
- Вхід: {entry:.4f} | Поточна: {current:.4f}
- Float PnL: {float_r:+.2f}R | Відкрито: {age_h:.1f}h тому
- Стоп: {stop:.4f} | TP2: {tp2:.4f}

ПРИЧИНА ПЕРЕВІРКИ:
{trigger_reason}

КОНТЕКСТ:
- {coin} тренд 1h: {trend_1h}
- BTC зміна 1h: {btc_1h:+.2f}%

Якщо виходити зараз — ми вийдемо по поточній ціні (не чекаємо стоп).
Відповідай ТІЛЬКИ JSON:
{{"action": "close" | "hold", "reason": "1-2 речення чому"}}"""


def _float_r(trade: Trade, price: float) -> float:
    dist = abs(trade.entry_price - trade.stop_price)
    if dist == 0:
        return 0.0
    if trade.direction == "LONG":
        return (price - trade.entry_price) / dist
    return (trade.entry_price - price) / dist


def _btc_1h_change(price_cache: dict) -> float:
    df = price_cache.get("BTC/USDT", {}).get("1h")
    if df is None or len(df) < 2:
        return 0.0
    return (df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100


def _trend_1h(price_cache: dict, coin: str) -> str:
    from src.indicators import ema as _ema
    df = price_cache.get(coin, {}).get("1h")
    if df is None or len(df) < 55:
        return "unknown"
    e50 = _ema(df, 50).iloc[-1]
    close = df["close"].iloc[-1]
    if len(df) >= 205:
        e200 = _ema(df, 200).iloc[-1]
        if e50 > e200 and close > e50:
            return "bull"
        if e50 < e200 and close < e50:
            return "bear"
        return "chop"
    return "bull" if close > e50 else "bear"


def _negative_coin_news(price_cache: dict, coin: str, news_watcher) -> str | None:
    """Returns the title if hot negative news about this coin appeared in last hour."""
    if news_watcher is None:
        return None
    ticker = coin.split("/")[0].lower()  # "SOL/USDT" → "sol"
    negative_words = {
        "hack", "exploit", "breach", "crash", "ban", "lawsuit", "sec",
        "bankruptcy", "insolvent", "depeg", "rug", "dump", "investigation",
        "arrest", "freeze", "sanction",
    }
    for title in news_watcher.get_recent_titles(hours=1):
        lower = title.lower()
        if ticker in lower and any(w in lower for w in negative_words):
            return title
    return None


async def check_exit_triggers(
    trade: Trade,
    price: float,
    price_cache: dict,
    news_watcher,
) -> tuple[bool, str]:
    """Returns (triggered, reason). True when something warrants an AI exit check."""
    direction = trade.direction
    btc_chg = _btc_1h_change(price_cache)
    float_r = _float_r(trade, price)

    # Trigger 1: BTC moved >2% against our position in last 1h candle
    if direction == "LONG" and btc_chg < -2.0:
        return True, f"BTC впав {btc_chg:.1f}% за останню годину — тиск на лонги"
    if direction == "SHORT" and btc_chg > 2.0:
        return True, f"BTC зріс {btc_chg:.1f}% за останню годину — тиск на шорти"

    # Trigger 2: Hot negative news about this specific coin
    bad_news = _negative_coin_news(price_cache, trade.coin, news_watcher)
    if bad_news:
        return True, f"Негативна новина: {bad_news[:120]}"

    # Trigger 3: Position stalling — >12h open, PnL < 0.3R (not going anywhere)
    if trade.created_at:
        age = datetime.now(tz=timezone.utc) - trade.created_at.replace(tzinfo=timezone.utc)
        if age > timedelta(hours=12) and float_r < 0.3:
            age_h = age.total_seconds() / 3600
            return True, f"Позиція {age_h:.0f}h стоїть на місці ({float_r:+.2f}R) — можливо варто звільнити капітал"

    return False, ""


async def hermes_exit_decision(
    trade: Trade,
    price: float,
    trigger_reason: str,
    price_cache: dict,
) -> dict:
    """Short Hermes prompt: should we exit this position now?"""
    btc_chg = _btc_1h_change(price_cache)
    trend = _trend_1h(price_cache, trade.coin)
    float_r = _float_r(trade, price)

    age_h = 0.0
    if trade.created_at:
        age_h = (datetime.now(tz=timezone.utc) - trade.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600

    prompt = _EXIT_PROMPT.format(
        coin=trade.coin,
        direction=trade.direction,
        entry=trade.entry_price,
        current=price,
        float_r=float_r,
        age_h=age_h,
        stop=trade.stop_price,
        tp2=trade.tp2_price,
        trigger_reason=trigger_reason,
        trend_1h=trend,
        btc_1h=btc_chg,
    )

    try:
        import ollama

        def _call():
            return ollama.chat(
                model=cfg.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                format="json",
                options={"temperature": 0.1, "num_predict": 128, "num_gpu": cfg.OLLAMA_NUM_GPU},
            )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _call)
        result = json.loads(response["message"]["content"])
        action = result.get("action", "hold")
        reason = result.get("reason", "")
        icon = "🚪" if action == "close" else "🤝"
        logger.info(f"{icon} hermes_exit | #{trade.id} {trade.coin} → {action.upper()} | {reason[:80]}")
        return {"action": action, "reason": reason}

    except Exception as e:
        logger.warning(f"hermes_exit | Ollama error for #{trade.id}: {e} — defaulting to hold")
        return {"action": "hold", "reason": f"AI недоступний: {e}"}
