SCANNER_SYSTEM_PROMPT = """Ти досвідчений крипто-трейдер з 10 роками досвіду.
Тобі дають ринкові дані по всіх монетах. Твоя задача — самостійно знайти найкращі торгові можливості.

ЩО ШУКАТИ:
- Сильний тренд (EMA50 > EMA200) + momentum (ADX > 18)
- RSI: перепроданість (RSI < 35 → LONG), перекупленість (RSI > 65 → SHORT)
- Breakout: ціна пробила ключовий рівень з об'ємом
- Confluence: 1h + 4h тренди співпадають
- BTC тренд: підтримує або суперечить?

ПРАВИЛА РИЗИКУ:
- Stop loss: рівень де ідея недійсна (за ATR або ключовим рівнем)
- TP1 = 1× стоп-дистанція від входу (перший вихід)
- TP2 = 2× стоп-дистанція від входу (фінальний вихід)
- Не брати проти BTC тренду якщо BTC рухається >2%/год
- Пропускай якщо RSI між 40-60 і немає чіткого тренду

КРИТИЧНО — РЕАКЦІЯ НА ПОТОЧНІ РЕЗУЛЬТАТИ:
- Якщо Loss streak ≥ 3 → знизь size_multiplier до 0.5 (захист від продовження смуги)
- Якщо Loss streak ≥ 5 → поверни порожній список signals: [] (стоп торгівля, ринок проти твоєї стратегії)
- Якщо Drawdown сьогодні < -5% → знизь size_multiplier до 0.5
- Якщо Drawdown сьогодні < -10% → поверни порожній список signals: []
- Якщо Win rate (30d) < 30% і вже >10 угод → знизь size_multiplier до 0.5

ВРАХОВУЙ ДОСВІД: якщо є минулі уроки по монеті — обов'язково враховуй їх.

Відповідай ТІЛЬКИ валідним JSON без жодного іншого тексту:
{
  "market_overview": "2-3 речення про поточний стан ринку і BTC",
  "regime": "bull_trend" | "bear_trend" | "chop" | "panic",
  "signals": [
    {
      "coin": "SOL/USDT",
      "direction": "LONG",
      "entry": 145.20,
      "stop": 141.80,
      "tp1": 148.60,
      "tp2": 152.00,
      "confidence": 0.75,
      "size_multiplier": 0.8,
      "reason": "чітке обґрунтування чому саме ця монета і зараз"
    }
  ]
}

Знайди від 0 до 5 найкращих можливостей. Краще менше але якісніших.
Якщо ринок невизначений — повертай порожній список signals: []
"""

BATCH_SYSTEM_PROMPT = """Ти досвідчений risk manager криптофонду.
Тобі дають СПИСОК сигналів на вхід разом з повним ринковим контекстом.
Твоя задача — проаналізувати всі сигнали РАЗОМ і прийняти рішення по кожному.

ПОРІВНЮЙ сигнали між собою:
- Який має найсильніший тренд?
- Який має найвищий ADX?
- Який найкраще відповідає BTC тренду?
- Який має найкращий досвід (уроки з минулих угод)?

ВИЗНАЧЕННЯ РЕЖИМУ (КРИТИЧНО — використовуй breadth + BTC 1h, НЕ тільки 4h):
- Market breadth >55% І BTC 1h bullish → bull_trend
- Market breadth <35% АБО BTC 1h bearish → bear_trend
- 4h тренд ЗАПІЗНЮЄТЬСЯ — не покладайся тільки на нього

ПРАВИЛА:
- bear_trend (breadth<35%) → SHORT отримують пріоритет, LONG → skip або reduced
- bull_trend (breadth>55%) → LONG пріоритет, SHORT → skip
- НЕ відхиляй SHORT через "4h ще бичачий" якщо breadth низька і BTC 1h падає
- Не більше 2 однонапрямкових позицій одночасно
- ADX < 15 → skip, ADX 15-18 → reduced, ADX > 18 → розглядай
- Panic режим → тільки skip
- Будь активнішим: краще взяти reduced ніж пропустити гарний сигнал

КРИТИЧНО — РЕАКЦІЯ НА ПОТОЧНІ РЕЗУЛЬТАТИ:
- Loss streak ≥ 3 → ВСІ decisions = "reduced" з size_multiplier 0.5
- Loss streak ≥ 5 → ВСІ decisions = "skip" (стоп торгівля)
- Drawdown сьогодні < -5% → ВСІ decisions max "reduced" з 0.5
- Drawdown сьогодні < -10% → ВСІ decisions = "skip"

Відповідай ТІЛЬКИ валідним JSON без жодного іншого тексту:
{
  "market_regime": "bull_trend" | "bear_trend" | "chop" | "panic",
  "market_summary": "1-2 речення про поточний стан ринку",
  "decisions": [
    {
      "coin": "SOL/USDT",
      "direction": "LONG",
      "action": "take" | "reduced" | "skip",
      "priority": 1,
      "size_multiplier": 0.0..1.0,
      "reason": "коротке обґрунтування"
    }
  ]
}

Відсортуй decisions за priority (1 = найкращий сигнал).
"""

SYSTEM_PROMPT = """Ти консервативний risk manager криптофонду.
Тобі дають сигнал на вхід в угоду разом з повним контекстом ринку.
Твоя задача — VETO або підтвердження.

CHECK LIST (всі повинні бути ✅):
- 1d тренд узгоджений з напрямком?
- 4h тренд узгоджений?
- ADX(4h) > 25?
- Точка входу — pullback до структури?
- Стоп у логічному місці?
- TP ≥ 1.5× стоп-дистанції?
- ATR не екстремальний?
- BTC спокійний (<2%/год)?
- Немає важливих новин у наступну годину?
- Денна просадка <3%?
- Loss streak <2?
- Макс 2 однонапрямкові позиції?

ЯКЩО ВСІ ✅ → take
ЯКЩО 1-2 ⚠️ → reduced (25-50% розмір)
ЯКЩО 3+ ⚠️ → skip

RED FLAGS (одна = skip):
- Режим = panic
- Stablecoin депег
- BTC рухнувся >5% за годину
- Funding rate >0.1% за 8h
- Ліквідації >$1B/годину
- Account drawdown >5%

Відповідай ТІЛЬКИ валідним JSON без жодного іншого тексту:
{
  "decision": "take" | "reduced" | "skip",
  "size_multiplier": 0.0..1.0,
  "reason": "коротке обґрунтування (1-2 речення)",
  "concerns": ["список", "проблем"],
  "regime": "bull_trend" | "bear_trend" | "chop" | "panic"
}"""


def build_trade_context(
    signal,
    price_cache: dict,
    portfolio_state: dict,
    stats: dict,
    news_titles: list[str],
    funding_rate: float = 0.0,
    liquidations_1h_usd: float = 0.0,
    memory_block: str = "",
) -> str:
    from src.indicators import ema, adx
    import pandas as pd

    coin = signal.coin
    df_1h = price_cache.get(coin, {}).get("1h")
    df_4h = price_cache.get(coin, {}).get("4h")
    df_1d = price_cache.get(coin, {}).get("1d")
    df_btc_1h = price_cache.get("BTC/USDT", {}).get("1h")
    df_btc_4h = price_cache.get("BTC/USDT", {}).get("4h")

    current_price = df_1h["close"].iloc[-1] if df_1h is not None and not df_1h.empty else "n/a"
    btc_1h_chg = _hourly_change(df_btc_1h)

    coin_4h_trend = _simple_trend(df_4h)
    coin_1d_trend = _simple_trend(df_1d)
    coin_1h_trend = _simple_trend(df_1h)
    btc_4h_trend = _simple_trend(df_btc_4h)

    adx_4h = _adx_val(df_4h)

    news_block = "\n".join(f"- {t}" for t in news_titles[:10]) or "- No recent news"

    open_positions = portfolio_state.get("open_positions", 0)
    loss_streak = portfolio_state.get("loss_streak", 0)
    drawdown_today = portfolio_state.get("drawdown_today", 0.0)
    open_same = portfolio_state.get("open_by_direction", {}).get(signal.direction, 0)
    win_rate_30 = stats.get("win_rate", 0)
    avg_r = stats.get("avg_r", 0)

    return f"""СИГНАЛ:
- Coin: {signal.coin}
- Direction: {signal.direction}
- Generated by: {signal.source}
- Entry: {signal.entry}
- Suggested stop: {signal.suggested_stop}
- Suggested TP1: {signal.suggested_tp1}
- Suggested TP2: {signal.suggested_tp2}
- Confidence: {signal.confidence:.2f}
- Reason: {signal.reason}

КОНТЕКСТ МОНЕТИ:
- {coin} price now: {current_price}
- {coin} 1d trend: {coin_1d_trend}
- {coin} 4h trend: {coin_4h_trend}
- {coin} 1h trend: {coin_1h_trend}
- ATR(14, 1h): {signal.atr:.4f}
- ADX(4h): {adx_4h:.1f}

КОНТЕКСТ BTC:
- BTC 1h change: {btc_1h_chg:+.2f}%
- BTC 4h trend: {btc_4h_trend}

FUNDING та ЛІКВІДАЦІЇ:
- {coin} funding rate: {funding_rate:.4f}% / 8h
- {coin} liquidations 1h: ${liquidations_1h_usd:,.0f}

НОВИНИ (24h):
{news_block}

МІЙ ДОСВІД (схожі минулі угоди):
{memory_block if memory_block else "- Немає досвіду ще"}

СТАН БОТА:
- Drawdown today: {drawdown_today*100:.1f}%
- Loss streak: {loss_streak}
- Win rate (last 30): {win_rate_30*100:.1f}%
- Avg R (last 30): {avg_r:+.2f}
- Open positions: {open_positions}/3
- Open {signal.direction} positions: {open_same}

Прийми рішення."""


def build_batch_context(
    signals: list,
    price_cache: dict,
    portfolio_state: dict,
    stats: dict,
    news_titles: list[str],
    memories_by_signal: dict | None = None,
) -> str:
    """Build a single prompt with ALL signals for batch AI evaluation."""
    df_btc_1h = price_cache.get("BTC/USDT", {}).get("1h")
    df_btc_4h = price_cache.get("BTC/USDT", {}).get("4h")
    btc_1h_chg = _hourly_change(df_btc_1h)
    btc_4h_trend = _simple_trend(df_btc_4h)
    btc_1h_trend = _simple_trend(df_btc_1h)

    # Market breadth — % of coins above their EMA50 (1h)
    from src.indicators import ema as _ema_ind
    _up, _total = 0, 0
    for _c, _tfs in price_cache.items():
        _df = _tfs.get("1h")
        if _df is not None and len(_df) >= 50:
            try:
                if _df["close"].iloc[-1] > _ema_ind(_df, 50).iloc[-1]:
                    _up += 1
                _total += 1
            except Exception:
                pass
    breadth_pct = (_up / _total * 100) if _total else 0

    open_positions = portfolio_state.get("open_positions", 0)
    loss_streak = portfolio_state.get("loss_streak", 0)
    drawdown_today = portfolio_state.get("drawdown_today", 0.0)
    open_by_dir = portfolio_state.get("open_by_direction", {})
    win_rate_30 = stats.get("win_rate", 0)
    avg_r = stats.get("avg_r", 0)

    news_block = "\n".join(f"- {t}" for t in news_titles[:8]) or "- No recent news"

    signals_block = ""
    for i, sig in enumerate(signals, 1):
        df_1h = price_cache.get(sig.coin, {}).get("1h")
        df_4h = price_cache.get(sig.coin, {}).get("4h")
        df_1d = price_cache.get(sig.coin, {}).get("1d")
        cur = df_1h["close"].iloc[-1] if df_1h is not None and not df_1h.empty else sig.entry
        trend_1d = _simple_trend(df_1d)
        trend_4h = _simple_trend(df_4h)
        trend_1h = _simple_trend(df_1h)
        adx_4h = _adx_val(df_4h)

        mem_text = ""
        if memories_by_signal:
            mems = memories_by_signal.get(f"{sig.coin}_{sig.direction}", [])
            if mems:
                mem_text = f"\n  Досвід: {'; '.join(m.get('lesson','') for m in mems[:2])}"

        signals_block += f"""
Сигнал #{i}: {sig.coin} {sig.direction}
  Джерело: {sig.source} | Confidence: {sig.confidence:.2f}
  Ціна зараз: {cur:.4f} | Entry: {sig.entry:.4f}
  Stop: {sig.suggested_stop:.4f} | TP1: {sig.suggested_tp1:.4f} | TP2: {sig.suggested_tp2:.4f}
  Тренд: 1d={trend_1d} 4h={trend_4h} 1h={trend_1h}
  ADX(4h): {adx_4h:.1f} | ATR(1h): {sig.atr:.4f}
  Причина сигналу: {sig.reason}{mem_text}
"""

    return f"""РИНКОВИЙ КОНТЕКСТ:
- BTC 1h зміна: {btc_1h_chg:+.2f}%
- BTC тренд: 1h={btc_1h_trend} | 4h={btc_4h_trend}
- Market breadth: {breadth_pct:.0f}% монет вище EMA50 (>55%=бичачий, <35%=ведмежий)
- Відкриті позиції: {open_positions} | LONG: {open_by_dir.get('LONG',0)} SHORT: {open_by_dir.get('SHORT',0)}
- Drawdown сьогодні: {drawdown_today*100:.1f}%
- Loss streak: {loss_streak}
- Win rate (30d): {win_rate_30*100:.1f}% | Avg R: {avg_r:+.2f}

НОВИНИ (24h):
{news_block}

СИГНАЛИ ДЛЯ АНАЛІЗУ ({len(signals)} шт.):
{signals_block}
Проаналізуй всі сигнали разом. Порівняй їх між собою. Прийми рішення по кожному."""


def build_scanner_context(
    price_cache: dict,
    portfolio_state: dict,
    stats: dict,
    news_titles: list[str],
    memories_summary: str = "",
) -> str:
    """Build full market scan context for AI to find signals itself."""
    from src.indicators import ema as _ema, adx as _adx, atr as _atr, rsi as _rsi
    import pandas as pd

    df_btc_1h = price_cache.get("BTC/USDT", {}).get("1h")
    df_btc_4h = price_cache.get("BTC/USDT", {}).get("4h")
    btc_chg = _hourly_change(df_btc_1h)
    btc_trend = _simple_trend(df_btc_4h)
    btc_price = df_btc_1h["close"].iloc[-1] if df_btc_1h is not None and not df_btc_1h.empty else 0

    open_positions = portfolio_state.get("open_positions", 0)
    open_coins = portfolio_state.get("open_coins", set())
    open_by_dir = portfolio_state.get("open_by_direction", {})
    drawdown = portfolio_state.get("drawdown_today", 0.0)
    loss_streak = portfolio_state.get("loss_streak", 0)

    news_block = "\n".join(f"- {t}" for t in news_titles[:6]) or "- No recent news"

    # Build per-coin summary
    coins_block = ""
    for coin in price_cache:
        if coin in open_coins:
            continue  # skip already open

        df_1h = price_cache[coin].get("1h")
        df_4h = price_cache[coin].get("4h")
        df_1d = price_cache[coin].get("1d")

        if df_1h is None or df_1h.empty or len(df_1h) < 50:
            continue

        try:
            close = float(df_1h["close"].iloc[-1])
            rsi_val = float(_rsi(df_1h, 14).iloc[-1])
            atr_val = float(_atr(df_1h, 14).iloc[-1])
            trend_1h = _simple_trend(df_1h)
            trend_4h = _simple_trend(df_4h) if df_4h is not None else "unknown"
            adx_4h = _adx_val(df_4h) if df_4h is not None else 0.0
            vol_avg = df_1h["volume"].rolling(20).mean().iloc[-1]
            vol_ratio = df_1h["volume"].iloc[-1] / vol_avg if vol_avg else 1.0

            # 24h price change
            chg_24h = (close - df_1h["close"].iloc[-24]) / df_1h["close"].iloc[-24] * 100 \
                if len(df_1h) >= 24 else 0.0

            coins_block += (
                f"\n{coin}: ${close:.4f} | chg24h={chg_24h:+.1f}% | "
                f"trend=1h:{trend_1h}/4h:{trend_4h} | ADX(4h)={adx_4h:.0f} | "
                f"RSI(1h)={rsi_val:.0f} | ATR={atr_val:.4f} | vol={vol_ratio:.1f}x"
            )
        except Exception:
            continue

    return f"""СТАН РИНКУ:
- BTC: ${btc_price:,.0f} | 1h зміна: {btc_chg:+.2f}% | тренд: {btc_trend}
- Відкриті позиції: {open_positions} | LONG={open_by_dir.get('LONG',0)} SHORT={open_by_dir.get('SHORT',0)}
- Вже відкрито монети: {', '.join(open_coins) if open_coins else 'немає'}
- Drawdown сьогодні: {drawdown*100:.1f}% | Loss streak: {loss_streak}
- Win rate (30d): {stats.get('win_rate',0)*100:.1f}% | Avg R: {stats.get('avg_r',0):+.2f}

НОВИНИ (24h):
{news_block}

МІЙ ТОРГОВИЙ ДОСВІД:
{memories_summary if memories_summary else "- Ще немає досвіду"}

ДАНІ МОНЕТ (формат: ціна | зміна24h | тренд | ADX | RSI | ATR | об'єм):
{coins_block}

Проаналізуй всі монети. Знайди найкращі торгові можливості прямо зараз."""


def hourly_change(df) -> float:
    return _hourly_change(df)


def _hourly_change(df) -> float:
    if df is None or len(df) < 2:
        return 0.0
    last = df["close"].iloc[-1]
    prev = df["close"].iloc[-2]
    return (last - prev) / prev * 100


def _simple_trend(df) -> str:
    from src.indicators import ema as _ema
    import pandas as pd
    if df is None or len(df) < 210:
        return "unknown"
    ef = _ema(df, 50).iloc[-1]
    es = _ema(df, 200).iloc[-1]
    close = df["close"].iloc[-1]
    if ef > es and close > es:
        return "bull"
    if ef < es and close < es:
        return "bear"
    return "chop"


def _adx_val(df) -> float:
    from src.indicators import adx as _adx
    import pandas as pd
    if df is None or len(df) < 30:
        return 0.0
    adx_s, _, _ = _adx(df, 14)
    val = adx_s.iloc[-1]
    return 0.0 if pd.isna(val) else float(val)
