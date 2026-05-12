"""
Paper executor — simulates leveraged trading without touching a real exchange.
Balance tracks actual capital (margin), not notional position size.

  open:  balance -= margin_usd  (margin_usd = size_usd / leverage)
  close: balance += margin_usd + pnl_usd
"""
from loguru import logger
from sqlalchemy import select

from src.config import cfg
from src.signals.trend_follower import Signal
from src.analytics.journal import record_open, record_close
from src.database.db import AsyncSessionLocal
from src.database.models import Trade, TradeStatus


class PaperExecutor:
    def __init__(self):
        self.balance = cfg.INITIAL_BALANCE
        self._starting_balance_today = cfg.INITIAL_BALANCE
        self._locked: dict[int, float] = {}  # trade_id → locked margin_usd

    async def restore_from_db(self):
        """Re-populate _locked from DB open trades after a restart."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade).where(Trade.status == TradeStatus.OPEN)
            )
            open_trades = list(result.scalars().all())
        for t in open_trades:
            margin = t.size_usd / (t.leverage or 5)
            self._locked[t.id] = margin
            self.balance -= margin
        # Track total equity (free + locked), not just free balance
        self._starting_balance_today = self.balance + self.total_locked
        if open_trades:
            logger.info(
                f"paper_executor | restored {len(open_trades)} open positions | "
                f"margin locked=${self.total_locked:.0f} | balance=${self.balance:.2f}"
            )

    async def open_trade(
        self,
        signal: Signal,
        size_usd: float,
        risk_usd: float,
        ai_decision: str,
        ai_reason: str,
        ai_regime: str,
        size_multiplier: float,
        leverage: int = 5,
    ) -> Trade:
        trade = await record_open(
            signal=signal,
            size_usd=size_usd,
            risk_usd=risk_usd,
            ai_decision=ai_decision,
            ai_reason=ai_reason,
            ai_regime=ai_regime,
            size_multiplier=size_multiplier,
            leverage=leverage,
        )
        margin_usd = size_usd / leverage
        self.balance -= margin_usd
        self._locked[trade.id] = margin_usd
        logger.info(
            f"paper_executor | відкрито #{trade.id} {signal.coin} {signal.direction} "
            f"entry={signal.entry:.4f} notional=${size_usd:.0f} margin=${margin_usd:.0f} x{leverage} | "
            f"баланс=${self.balance:.2f} (маржа=${self.total_locked:.0f})"
        )
        return trade

    async def close_trade(
        self,
        trade: Trade,
        exit_price: float,
        status: str,
        post_analysis: str = "",
    ):
        await record_close(trade, exit_price, status, post_analysis)

        margin = self._locked.pop(trade.id, trade.size_usd / (trade.leverage or 5))
        coins = trade.size_usd / trade.entry_price

        if trade.direction == "LONG":
            pnl = (exit_price - trade.entry_price) * coins
        else:
            pnl = (trade.entry_price - exit_price) * coins

        self.balance += margin + pnl
        logger.info(
            f"paper_executor | закрито #{trade.id} {trade.coin} "
            f"pnl={pnl:+.2f}$ | баланс=${self.balance:.2f}"
        )

    @property
    def total_locked(self) -> float:
        return sum(self._locked.values())

    @property
    def available(self) -> float:
        return self.balance

    def reset_daily_balance(self):
        self._starting_balance_today = self.balance + self.total_locked

    @property
    def total_equity(self) -> float:
        return self.balance + self.total_locked

    @property
    def daily_pnl(self) -> float:
        return self.total_equity - self._starting_balance_today
