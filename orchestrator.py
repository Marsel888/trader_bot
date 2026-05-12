"""
Orchestrator — main process. Run this to start the trading bot.

Usage:
    python orchestrator.py

The bot runs in PAPER_TRADING mode by default (no real orders).
Set PAPER_TRADING=false in .env only after successful paper validation.
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger

from src.config import cfg
from src.database.db import init_db
from src.data.price_collector import PriceCollector
from src.data.ws_price_stream import WSPriceStream
from src.data.news_watcher import NewsWatcher
from src.signals.signal_engine import SignalEngine
from src.ai.hermes_filter import HermesFilter
from src.ai.market_scanner import AIMarketScanner
from src.ai.prompts import build_batch_context
from src.risk.sizing import calculate_size
from src.execution.paper_executor import PaperExecutor
from src.execution.portfolio import Portfolio
from src.analytics.metrics import get_stats, get_portfolio_state
from src.notifications.telegram import TelegramNotifier

# ─── Logging setup ────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    colorize=True,
    format=(
        "<green>{time:HH:mm:ss}</green> "
        "| <level>{level:<7}</level> "
        "| <level>{message}</level>"
    ),
)
logger.add(
    "logs/bot_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="30 days",
    level="DEBUG",
    colorize=False,
    format="{time} | {level} | {message}",
)


async def _write_prices_loop(ws_stream: "WSPriceStream", price_cache_ref: list):
    """Background task: writes live WS prices to file every 1s for dashboard."""
    prices_path = Path("data/live_prices.json")
    while True:
        try:
            live: dict[str, float] = {}
            for coin in cfg.WATCHLIST:
                ws_p = ws_stream.get_price(coin) if ws_stream.is_fresh(coin) else None
                if ws_p:
                    live[coin] = ws_p
                else:
                    df = price_cache_ref[0].get(coin, {}).get("1h")
                    if df is not None and not df.empty:
                        live[coin] = float(df["close"].iloc[-1])
            prices_path.write_text(json.dumps({"prices": live, "ts": time.time()}))
        except Exception:
            pass
        await asyncio.sleep(1)


async def main():
    logger.info("=" * 60)
    logger.info(f"Trading Bot starting — PAPER={cfg.PAPER_TRADING}")
    logger.info(f"Watchlist: {cfg.WATCHLIST}")
    logger.info("=" * 60)

    await init_db()

    price_collector = PriceCollector()
    ws_stream = WSPriceStream()
    ws_stream.start()   # background task — real-time prices via WebSocket
    price_cache_ref: list[dict] = [{}]  # mutable ref shared with price writer
    asyncio.create_task(_write_prices_loop(ws_stream, price_cache_ref))
    news_watcher = NewsWatcher()
    signal_engine = SignalEngine()
    ai_filter = HermesFilter()
    ai_scanner = AIMarketScanner()
    executor = PaperExecutor()
    await executor.restore_from_db()  # Reload open positions after restart
    telegram = TelegramNotifier()
    portfolio = Portfolio(executor, telegram, ai_filter, news_watcher, ws_stream)

    await telegram.send("🤖 Trading bot started (PAPER mode)" if cfg.PAPER_TRADING else "🤖 Trading bot started (LIVE mode)")

    # Tick counters (base tick = PORTFOLIO_INTERVAL = 5s)
    tick = 0
    last_price_tick   = -999   # REST candle fetch (every 60s)
    last_news_tick    = -999   # news fetch (every 900s)
    last_signal_tick  = -999   # signal engine (every 300s)
    last_daily_summary_day = None
    btc_ref_price: float | None = None  # reference for BTC emergency check

    TICKS_PER_PRICE  = cfg.PRICE_INTERVAL   // cfg.PORTFOLIO_INTERVAL   # 12
    TICKS_PER_NEWS   = cfg.NEWS_INTERVAL    // cfg.PORTFOLIO_INTERVAL   # 180
    TICKS_PER_SIGNAL = cfg.SIGNAL_INTERVAL  // cfg.PORTFOLIO_INTERVAL   # 60

    # Initial data fetch
    logger.info("Fetching initial market data...")
    price_cache = await price_collector.fetch()
    news_items = await news_watcher.fetch_latest()

    while True:
        try:
            tick += 1
            now = datetime.now(tz=timezone.utc)

            # 1. REST candle fetch every 60s (for signals + ATR)
            if tick - last_price_tick >= TICKS_PER_PRICE:
                price_cache = await price_collector.fetch()
                last_price_tick = tick

            # 2. Portfolio stop/TP check every 5s (uses WS prices)
            await portfolio.update_positions(price_cache)

            # Update shared price cache ref (used by background price writer)
            price_cache_ref[0] = price_cache

            # 3. Emergency BTC check every 5s via WS
            btc_ws = ws_stream.get_price("BTC/USDT") if ws_stream.is_fresh("BTC/USDT") else None
            if btc_ws:
                if btc_ref_price is None:
                    btc_ref_price = btc_ws
                btc_move = abs(btc_ws - btc_ref_price) / btc_ref_price
                # Reset reference every minute so we detect fresh moves, not old ones
                if tick % TICKS_PER_PRICE == 0:
                    btc_ref_price = btc_ws
            else:
                btc_df = price_cache.get("BTC/USDT", {}).get("1h")
                btc_move = abs(btc_df.iloc[-1]["close"] - btc_df.iloc[-2]["close"]) / btc_df.iloc[-2]["close"] \
                    if btc_df is not None and len(btc_df) >= 2 else 0.0
            if btc_move > 0.05:
                logger.warning(f"🚨 BTC moved {btc_move*100:.1f}% — emergency exit all LONG positions")
                await portfolio.emergency_exit_all(price_cache, f"BTC crashed {btc_move*100:.1f}%")
                await telegram.notify_kill_switch(f"BTC moved {btc_move*100:.1f}% — all longs closed")
                btc_ref_price = btc_ws  # reset after trigger

            # 4. News update every 15 min
            if tick - last_news_tick >= TICKS_PER_NEWS:
                news_items = await news_watcher.fetch_latest()
                last_news_tick = tick

            # 5. Signal engine every 5 min
            if tick - last_signal_tick >= TICKS_PER_SIGNAL:
                last_signal_tick = tick
                stats = await get_stats(days=30)
                portfolio_state = await get_portfolio_state(
                    current_balance=executor.total_equity,
                    starting_balance=executor._starting_balance_today,
                )
                logger.debug(
                    f"📊 tick={tick} | equity=${executor.total_equity:.2f} "
                    f"(free=${executor.balance:.2f} locked=${executor.total_locked:.0f})"
                    f"| open={portfolio_state['open_positions']} "
                    f"| streak={portfolio_state['loss_streak']}"
                )

                # Daily kill switch check
                drawdown_today = portfolio_state["drawdown_today"]
                if drawdown_today <= -cfg.MAX_DAILY_DRAWDOWN:
                    logger.warning(f"🛑 KILL SWITCH — daily drawdown {drawdown_today*100:.1f}%")
                    await telegram.notify_kill_switch(f"Daily drawdown {drawdown_today*100:.1f}% reached — no new trades today")
                else:
                    from src.ai.hermes_memory import save_decision
                    from src.ai.prompts import _adx_val, _simple_trend
                    from src.signals.pre_checks import rule_pre_checks

                    news_titles = news_watcher.get_recent_titles(hours=24)

                    # ── 1. AI Scanner — primary source ──────────────────────
                    scanner_sigs, regime = await ai_scanner.scan(
                        price_cache=price_cache,
                        portfolio_state=portfolio_state,
                        stats=stats,
                        news_titles=news_titles,
                    )
                    logger.info(f"🔭 ai_scanner | regime={regime} | {len(scanner_sigs)} signal(s)")

                    # ── 2. Rule engine — backup source ──────────────────────
                    rule_validated = signal_engine.check(price_cache, portfolio_state, news_watcher)
                    rule_passing = [sig for sig, fails in rule_validated if not fails]
                    for sig, fails in rule_validated:
                        if fails:
                            logger.debug(f"⏭  rule | {sig.coin} {sig.direction} skipped: {', '.join(fails[:2])}")

                    # ── 3. Merge: scanner first, then rule (skip duplicates) ─
                    seen_keys: set[tuple] = {(s.coin, s.direction) for s in scanner_sigs}
                    new_rule_sigs = [s for s in rule_passing if (s.coin, s.direction) not in seen_keys]
                    all_signals = scanner_sigs + new_rule_sigs

                    if not all_signals:
                        logger.debug("🔍 no signals this tick")
                    else:
                        logger.info(f"🔍 total signals: {len(all_signals)} (scanner={len(scanner_sigs)} rule={len(new_rule_sigs)})")

                        # Rule-engine signals need batch AI eval; scanner signals are already evaluated
                        if new_rule_sigs:
                            batch_result = await ai_filter.evaluate_batch(
                                signals=new_rule_sigs,
                                price_cache=price_cache,
                                portfolio_state=portfolio_state,
                                stats=stats,
                                news_titles=news_titles,
                            )
                            regime = batch_result.get("market_regime", regime)
                            # Build evaluated signal list from batch result
                            rule_decisions = {
                                (d["coin"], d["direction"]): d
                                for d in batch_result.get("decisions", [])
                            }
                        else:
                            rule_decisions = {}

                        # ── 4. Execute all signals ───────────────────────────
                        for sig in all_signals:
                            # Portfolio limits pre-check (not market checks — AI handled those)
                            _, port_fails = rule_pre_checks(sig, price_cache, portfolio_state, news_watcher=None)
                            port_fails = [f for f in port_fails if any(
                                kw in f for kw in ("max", "already", "drawdown", "loss streak", "R:R")
                            )]
                            if port_fails:
                                logger.warning(f"⏭  {sig.coin} {sig.direction} portfolio limit: {port_fails[0]}")
                                continue

                            # Get action/size_multiplier
                            if sig.source == "ai_scanner":
                                action = "take"
                                size_mult = float(sig.extra.get("size_multiplier", 0.8))
                                reason = sig.reason
                            else:
                                dec = rule_decisions.get((sig.coin, sig.direction), {})
                                action = dec.get("action", "skip")
                                size_mult = float(dec.get("size_multiplier", 0.5))
                                reason = dec.get("reason", sig.reason)
                                if action == "skip":
                                    logger.warning(f"🤖 AI SKIP | {sig.coin} {sig.direction} | {reason}")
                                    await telegram.notify_signal_skipped(sig.coin, sig.direction, [reason])
                                    continue

                            logger.info(f"🤖 {action.upper()} | {sig.coin} {sig.direction} [{sig.source}] | mult={size_mult:.1f} | {reason[:60]}")

                            size_info = calculate_size(sig, executor.balance, size_mult)
                            if size_info["size_usd"] == 0:
                                logger.warning(f"⚠️  zero size for {sig.coin}, skipping")
                                continue
                            if executor.balance < size_info["margin_usd"]:
                                logger.warning(f"⚠️  insufficient margin for {sig.coin}: need ${size_info['margin_usd']:.0f}, have ${executor.balance:.2f}")
                                continue

                            trade = await executor.open_trade(
                                signal=sig,
                                size_usd=size_info["size_usd"],
                                risk_usd=size_info["risk_usd"],
                                ai_decision=action,
                                ai_reason=reason,
                                ai_regime=regime,
                                size_multiplier=size_mult,
                                leverage=size_info["leverage"],
                            )
                            logger.success(f"✅ TRADE OPENED #{trade.id} | {sig.coin} {sig.direction} | entry={sig.entry:.4f} | size=${size_info['size_usd']:.0f} x{size_info['leverage']}")
                            await telegram.notify_open(
                                trade, sig, action, reason,
                                balance_after=executor.balance,
                                locked=executor.total_locked,
                            )

                            df_4h = price_cache.get(sig.coin, {}).get("4h")
                            df_btc_4h = price_cache.get("BTC/USDT", {}).get("4h")
                            verdict_compat = {"decision": action, "size_multiplier": size_mult, "reason": reason, "regime": regime}
                            await save_decision(
                                trade=trade, signal=sig, verdict=verdict_compat,
                                adx_4h=_adx_val(df_4h), btc_trend=_simple_trend(df_btc_4h),
                            )

                            # Refresh portfolio state after each trade so next signal sees updated positions
                            portfolio_state = await get_portfolio_state(
                                current_balance=executor.total_equity,
                                starting_balance=executor._starting_balance_today,
                            )

            # 6. Daily summary at midnight UTC
            today_str = now.strftime("%Y-%m-%d")
            if last_daily_summary_day != today_str and now.hour == 0 and now.minute < 1:
                stats = await get_stats(days=1)
                await telegram.notify_daily_summary(stats, executor.balance, executor.total_locked)
                executor.reset_daily_balance()
                last_daily_summary_day = today_str

        except Exception as e:
            logger.exception(f"orchestrator | main loop error: {e}")
            await telegram.send(f"⚠️ Bot error: {e}")

        await asyncio.sleep(cfg.PORTFOLIO_INTERVAL)


if __name__ == "__main__":
    import os
    os.makedirs("logs", exist_ok=True)
    asyncio.run(main())
