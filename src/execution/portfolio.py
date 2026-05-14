"""
Portfolio manager — monitors open positions every minute.
Handles: SL hit, TP1/TP2, breakeven, trailing stop, time-based exit, emergency exit.
Also runs event-driven Hermes exit checks (max 1 per tick, 20-min cooldown per trade).
"""
import asyncio
from datetime import datetime, timedelta, timezone
import pandas as pd
from sqlalchemy import select
from loguru import logger

from src.config import cfg
from src.indicators import atr
from src.database.db import AsyncSessionLocal
from src.database.models import Trade, TradeStatus


class Portfolio:
    def __init__(self, executor, telegram=None, ai_filter=None, news_watcher=None, ws_stream=None):
        self._executor = executor
        self._telegram = telegram
        self._ai_filter = ai_filter
        self._news_watcher = news_watcher
        self._ws_stream = ws_stream   # real-time WebSocket prices (may be None)

        # Hermes exit state — reset each tick
        self._exit_cooldown: dict[int, datetime] = {}  # trade_id → last checked at
        self._exit_busy = False                         # only 1 Hermes call per tick
        self._price_cache: dict = {}                    # set in update_positions

        # Retroactive check: on first tick after restart verify restored positions
        self._first_run = True

    async def update_positions(self, price_cache: dict):
        self._price_cache = price_cache
        self._exit_busy = False  # allow one Hermes exit call this tick

        open_trades = await self._get_open_trades()

        if self._first_run:
            self._first_run = False
            await self._retroactive_check(open_trades, price_cache)
            # Reload — some may have been closed by retroactive check
            open_trades = await self._get_open_trades()

        for trade in open_trades:
            coin_data = price_cache.get(trade.coin, {})
            df_1h = coin_data.get("1h")

            # Priority: WebSocket (real-time, ~1s) → 1h candle last close (60s stale)
            ws_price = self._ws_stream.get_price(trade.coin) \
                if self._ws_stream and self._ws_stream.is_fresh(trade.coin) else None

            if ws_price is not None:
                current_price = ws_price
            else:
                if df_1h is None or df_1h.empty:
                    continue
                current_price = float(df_1h["close"].iloc[-1])

            atr_val = self._get_atr(df_1h) if df_1h is not None else 0.0
            await self._process(trade, current_price, atr_val)

    async def _retroactive_check(self, trades: list, price_cache: dict):
        """
        On first tick after restart: check if any restored position should have
        closed while the bot was offline (stop or TP breached). Closes at the
        stop/TP price level — not the current price — for realistic paper results.
        """
        if not trades:
            return
        logger.info(f"portfolio | retroactive check for {len(trades)} restored position(s)")

        for trade in trades:
            coin_data = price_cache.get(trade.coin, {})
            ws_price = self._ws_stream.get_price(trade.coin) \
                if self._ws_stream and self._ws_stream.is_fresh(trade.coin) else None

            if ws_price is not None:
                price = ws_price
            else:
                df_1h_r = coin_data.get("1h")
                if df_1h_r is None or df_1h_r.empty:
                    logger.warning(f"portfolio | retroactive | {trade.coin}: no price data, skipping")
                    continue
                price = float(df_1h_r["close"].iloc[-1])
            is_long = trade.direction == "LONG"

            # Stop loss breached
            if (is_long and price <= trade.stop_price) or \
               (not is_long and price >= trade.stop_price):
                logger.warning(
                    f"portfolio | retroactive SL | #{trade.id} {trade.coin} "
                    f"stop={trade.stop_price:.4f} current={price:.4f} (bot was offline)"
                )
                await self._close_and_learn(trade, trade.stop_price, TradeStatus.CLOSED_SL)
                continue

            # TP2 breached
            if (is_long and price >= trade.tp2_price) or \
               (not is_long and price <= trade.tp2_price):
                logger.info(
                    f"portfolio | retroactive TP2 | #{trade.id} {trade.coin} "
                    f"tp2={trade.tp2_price:.4f} current={price:.4f} (bot was offline)"
                )
                await self._close_and_learn(trade, trade.tp2_price, TradeStatus.CLOSED_TP2)
                continue

            # TP1 breached but not TP2 — mark as hit, set breakeven
            if not trade.tp1_hit:
                if (is_long and price >= trade.tp1_price) or \
                   (not is_long and price <= trade.tp1_price):
                    async with AsyncSessionLocal() as session:
                        t = await session.get(Trade, trade.id)
                        if t:
                            t.tp1_hit = True
                            t.tp1_exit_price = trade.tp1_price
                            t.breakeven_set = True
                            t.stop_price = t.entry_price
                            await session.commit()
                    logger.info(
                        f"portfolio | retroactive TP1 | #{trade.id} {trade.coin} "
                        f"— tp1 marked hit, stop moved to breakeven (bot was offline)"
                    )

    async def _close_and_learn(self, trade: Trade, price: float, status: str):
        """Close trade, notify Telegram, then trigger Hermes memory update."""
        await self._executor.close_trade(trade, price, status)
        # Update in-memory object so Telegram can read exit_price/status
        trade.exit_price = price
        trade.status = status
        if self._telegram:
            await self._telegram.notify_close(
                trade,
                balance_after=self._executor.balance,
                locked=self._executor.total_locked,
            )
        from src.ai.hermes_memory import update_outcome, generate_lesson
        from src.analytics.agent_log import update_evaluation_outcome
        await update_outcome(trade)
        await update_evaluation_outcome(trade)
        asyncio.create_task(generate_lesson(trade))

    async def emergency_exit_all(self, price_cache: dict, reason: str):
        open_trades = await self._get_open_trades()
        for trade in open_trades:
            ws_price = self._ws_stream.get_price(trade.coin) \
                if self._ws_stream and self._ws_stream.is_fresh(trade.coin) else None
            if ws_price is not None:
                price = ws_price
            else:
                df_1h = price_cache.get(trade.coin, {}).get("1h")
                if df_1h is None or df_1h.empty:
                    continue
                price = float(df_1h["close"].iloc[-1])
            logger.warning(f"portfolio | EMERGENCY EXIT #{trade.id} {trade.coin}: {reason}")
            await self._executor.close_trade(trade, price, TradeStatus.CLOSED_EMERGENCY)
            if self._telegram:
                await self._telegram.notify_close(
                    trade,
                    balance_after=self._executor.balance,
                    locked=self._executor.total_locked,
                )

    async def _process(self, trade: Trade, price: float, atr_val: float):
        is_long = trade.direction == "LONG"

        # Update high watermark + current price (for unrealized PnL in dashboard)
        async with AsyncSessionLocal() as session:
            db_trade = await session.get(Trade, trade.id)
            if db_trade is None:
                return
            hwm = db_trade.high_watermark or db_trade.entry_price
            if (is_long and price > hwm) or (not is_long and price < hwm):
                db_trade.high_watermark = price
                hwm = price
            db_trade.current_price = price
            await session.commit()

        # Reload fresh state
        async with AsyncSessionLocal() as session:
            db_trade = await session.get(Trade, trade.id)
            if db_trade is None or db_trade.status != TradeStatus.OPEN:
                return

        # ── Mechanical exits ────────────────────────────────────────────

        # Stop loss
        if is_long and price <= db_trade.stop_price:
            logger.warning(f"🔴 SL HIT #{db_trade.id} {db_trade.coin} @ {price:.4f}")
            await self._close_and_learn(db_trade, price, TradeStatus.CLOSED_SL)
            return
        if not is_long and price >= db_trade.stop_price:
            logger.warning(f"🔴 SL HIT #{db_trade.id} {db_trade.coin} @ {price:.4f}")
            await self._close_and_learn(db_trade, price, TradeStatus.CLOSED_SL)
            return

        # TP1 — move stop to breakeven
        if not db_trade.tp1_hit:
            if (is_long and price >= db_trade.tp1_price) or (not is_long and price <= db_trade.tp1_price):
                async with AsyncSessionLocal() as session:
                    t = await session.get(Trade, db_trade.id)
                    if t:
                        t.tp1_hit = True
                        t.tp1_exit_price = price
                        t.breakeven_set = True
                        t.stop_price = t.entry_price
                        await session.commit()
                logger.info(f"portfolio | TP1 #{db_trade.id} {db_trade.coin} @ {price} — breakeven set")
                return

        # TP2 + trailing
        if db_trade.tp1_hit:
            if (is_long and price >= db_trade.tp2_price) or (not is_long and price <= db_trade.tp2_price):
                logger.success(f"🟢 TP2 HIT #{db_trade.id} {db_trade.coin} @ {price:.4f}")
                await self._close_and_learn(db_trade, price, TradeStatus.CLOSED_TP2)
                return

            if atr_val > 0:
                hwm = db_trade.high_watermark or price
                new_trail = (hwm - cfg.TRAILING_ATR_MULTIPLIER * atr_val) if is_long \
                    else (hwm + cfg.TRAILING_ATR_MULTIPLIER * atr_val)
                effective = max(db_trade.stop_price, new_trail) if is_long \
                    else min(db_trade.stop_price, new_trail)

                async with AsyncSessionLocal() as session:
                    t = await session.get(Trade, db_trade.id)
                    if t:
                        t.trailing_stop = effective
                        t.stop_price = effective
                        await session.commit()

                if (is_long and price <= effective) or (not is_long and price >= effective):
                    logger.info(f"📉 TRAIL STOP #{db_trade.id} {db_trade.coin} @ {price:.4f}")
                    await self._close_and_learn(db_trade, price, TradeStatus.CLOSED_TRAILING)
                    return

        # Time-based exit
        if db_trade.created_at:
            age = datetime.now(tz=timezone.utc) - db_trade.created_at.replace(tzinfo=timezone.utc)
            if age > timedelta(hours=cfg.TIME_EXIT_HOURS):
                r_dist = abs(db_trade.entry_price - db_trade.stop_price)
                profit_r = ((price - db_trade.entry_price) / r_dist) if is_long else \
                           ((db_trade.entry_price - price) / r_dist) if r_dist else 0
                if profit_r < 1.0:
                    logger.info(f"⏰ TIME EXIT #{db_trade.id} {db_trade.coin} — 24h elapsed, {profit_r:.2f}R")
                    await self._close_and_learn(db_trade, price, TradeStatus.CLOSED_TIME)
                    return

        # ── Event-driven Hermes exit check ──────────────────────────────
        # Runs only if: AI available, not busy this tick, cooldown expired
        if self._ai_filter is not None and not self._exit_busy:
            await self._maybe_hermes_exit(db_trade, price)

    async def _maybe_hermes_exit(self, trade: Trade, price: float):
        """Check triggers; if fired and cooldown expired, ask Hermes whether to exit."""
        now = datetime.now(tz=timezone.utc)
        last_checked = self._exit_cooldown.get(trade.id)
        if last_checked and (now - last_checked).total_seconds() < 1200:  # 20-min cooldown
            return

        from src.execution.hermes_exit import check_exit_triggers, hermes_exit_decision

        triggered, reason = await check_exit_triggers(
            trade, price, self._price_cache, self._news_watcher
        )
        if not triggered:
            return

        # Mark busy so no other trade gets a Hermes call this tick
        self._exit_busy = True
        self._exit_cooldown[trade.id] = now

        logger.info(f"🔔 hermes_exit trigger | #{trade.id} {trade.coin} | {reason}")
        decision = await hermes_exit_decision(trade, price, reason, self._price_cache)

        if decision["action"] == "close":
            logger.warning(
                f"🚪 HERMES EXIT #{trade.id} {trade.coin} @ {price:.4f} | {decision['reason']}"
            )
            await self._close_and_learn(trade, price, TradeStatus.CLOSED_HERMES_EXIT)
            if self._telegram:
                await self._telegram.notify_hermes_exit(
                    trade, price, reason, decision["reason"],
                    balance_after=self._executor.balance,
                    locked=self._executor.total_locked,
                )

    @staticmethod
    def _get_atr(df: pd.DataFrame) -> float:
        atr_s = atr(df, cfg.ATR_PERIOD)
        val = atr_s.iloc[-1]
        return 0.0 if pd.isna(val) else float(val)

    @staticmethod
    async def _get_open_trades() -> list[Trade]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade).where(Trade.status == TradeStatus.OPEN)
            )
            return list(result.scalars().all())
