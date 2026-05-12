"""
Live metrics calculator — win rate, R-multiple, drawdown, profit factor, Sharpe.
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from loguru import logger

from src.database.db import AsyncSessionLocal
from src.database.models import Trade, TradeStatus


OPEN_STATUS = TradeStatus.OPEN


async def get_stats(days: int = 30) -> dict:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Trade).where(
                Trade.closed_at >= cutoff,
                Trade.status != OPEN_STATUS,
            )
        )
        trades = result.scalars().all()

    if not trades:
        return _empty_stats()

    total = len(trades)
    wins = [t for t in trades if t.r_multiple and t.r_multiple > 0]
    losses = [t for t in trades if t.r_multiple and t.r_multiple <= 0]

    win_rate = len(wins) / total if total else 0
    avg_r = sum(t.r_multiple for t in trades if t.r_multiple) / total

    gross_profit = sum(t.pnl_usd for t in wins if t.pnl_usd)
    gross_loss = abs(sum(t.pnl_usd for t in losses if t.pnl_usd))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe (daily returns approximation)
    import numpy as np
    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
    if len(r_values) >= 2:
        arr = np.array(r_values)
        sharpe = float(arr.mean() / arr.std()) if arr.std() > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown (on cumulative R)
    cum_r = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x.closed_at):
        if t.r_multiple:
            cum_r += t.r_multiple
            if cum_r > peak:
                peak = cum_r
            dd = peak - cum_r
            if dd > max_dd:
                max_dd = dd

    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 3),
        "avg_r": round(avg_r, 3),
        "profit_factor": round(profit_factor, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_r": round(max_dd, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }


async def get_portfolio_state(current_balance: float, starting_balance: float) -> dict:
    """Returns live portfolio state for pre-checks."""
    async with AsyncSessionLocal() as session:
        open_result = await session.execute(
            select(Trade).where(Trade.status == OPEN_STATUS)
        )
        open_trades = open_result.scalars().all()

    open_positions = len(open_trades)
    open_by_direction: dict[str, int] = {}
    open_coins: set[str] = set()
    for t in open_trades:
        open_by_direction[t.direction] = open_by_direction.get(t.direction, 0) + 1
        open_coins.add(t.coin)

    drawdown_today = (current_balance - starting_balance) / starting_balance if starting_balance else 0.0

    # Loss streak: count consecutive losses from recent closed trades
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Trade)
            .where(Trade.status != OPEN_STATUS)
            .order_by(Trade.closed_at.desc())
            .limit(10)
        )
        recent = result.scalars().all()

    streak = 0
    for t in recent:
        if t.r_multiple is not None and t.r_multiple <= 0:
            streak += 1
        else:
            break

    return {
        "drawdown_today": drawdown_today,
        "loss_streak": streak,
        "open_positions": open_positions,
        "open_by_direction": open_by_direction,
        "open_coins": open_coins,
        "current_balance": current_balance,
    }


def _empty_stats() -> dict:
    return {
        "total": 0, "wins": 0, "losses": 0,
        "win_rate": 0.0, "avg_r": 0.0, "profit_factor": 0.0,
        "sharpe": 0.0, "max_drawdown_r": 0.0,
        "gross_profit": 0.0, "gross_loss": 0.0,
    }
