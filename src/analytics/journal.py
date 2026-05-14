"""
Trade journal — records every trade open/close event to the database.
"""
from datetime import datetime, timezone
from loguru import logger

from src.database.db import AsyncSessionLocal
from src.database.models import Trade, TradeStatus
from src.signals.trend_follower import Signal


async def record_open(
    signal: Signal,
    size_usd: float,
    risk_usd: float,
    ai_decision: str,
    ai_reason: str,
    ai_regime: str,
    size_multiplier: float,
    leverage: int = 5,
) -> Trade:
    async with AsyncSessionLocal() as session:
        trade = Trade(
            coin=signal.coin,
            direction=signal.direction,
            source=signal.source,
            entry_price=signal.entry,
            stop_price=signal.suggested_stop,
            tp1_price=signal.suggested_tp1,
            tp2_price=signal.suggested_tp2,
            size_usd=size_usd,
            risk_usd=risk_usd,
            size_multiplier=size_multiplier,
            leverage=leverage,
            high_watermark=signal.entry,
            ai_decision=ai_decision,
            ai_reason=ai_reason,
            ai_regime=ai_regime,
            status=TradeStatus.OPEN,
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        logger.info(f"journal | opened trade #{trade.id} {trade.coin} {trade.direction} @ {trade.entry_price}")
        return trade


async def record_close(
    trade: Trade,
    exit_price: float,
    status: str,
    post_analysis: str = "",
):
    async with AsyncSessionLocal() as session:
        db_trade = await session.get(Trade, trade.id)
        if db_trade is None:
            return

        db_trade.closed_at = datetime.now(tz=timezone.utc)
        db_trade.exit_price = exit_price
        db_trade.status = status

        if db_trade.direction == "LONG":
            pnl_per_unit = exit_price - db_trade.entry_price
        else:
            pnl_per_unit = db_trade.entry_price - exit_price

        coins = db_trade.size_usd / db_trade.entry_price
        db_trade.pnl_usd = round(pnl_per_unit * coins, 2)
        # Use risk_usd (fixed at open) to avoid -R bug when trailing stop moves above entry
        risk_per_coin = db_trade.risk_usd / coins if coins else 0
        db_trade.r_multiple = round(pnl_per_unit / risk_per_coin, 3) if risk_per_coin else 0

        if post_analysis:
            db_trade.post_analysis = post_analysis

        await session.commit()
        logger.info(
            f"journal | closed #{db_trade.id} {db_trade.coin} {db_trade.direction} "
            f"@ {exit_price} | PnL: {db_trade.pnl_usd:+.2f} USD | {db_trade.r_multiple:+.2f}R | {status}"
        )
