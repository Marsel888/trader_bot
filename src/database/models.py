from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Boolean, DateTime, Text, Enum
)
from sqlalchemy.orm import DeclarativeBase
import enum


class Base(DeclarativeBase):
    pass


class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_TP1 = "CLOSED_TP1"
    CLOSED_TP2 = "CLOSED_TP2"
    CLOSED_TRAILING = "CLOSED_TRAILING"
    CLOSED_TIME = "CLOSED_TIME"
    CLOSED_EMERGENCY = "CLOSED_EMERGENCY"
    CLOSED_HERMES_EXIT = "CLOSED_HERMES_EXIT"
    CLOSED_MANUAL = "CLOSED_MANUAL"


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)

    coin = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)
    source = Column(String(50), nullable=False)   # which signal generator

    entry_price = Column(Float, nullable=False)
    stop_price = Column(Float, nullable=False)
    tp1_price = Column(Float, nullable=False)
    tp2_price = Column(Float, nullable=False)

    size_usd = Column(Float, nullable=False)       # notional position size
    risk_usd = Column(Float, nullable=False)       # dollars at risk
    size_multiplier = Column(Float, default=1.0)   # AI multiplier applied
    leverage = Column(Integer, default=5)          # actual leverage used (5/6/7x)

    # Filled by executor at close
    exit_price = Column(Float, nullable=True)
    status = Column(String(30), default=TradeStatus.OPEN)
    pnl_usd = Column(Float, nullable=True)
    r_multiple = Column(Float, nullable=True)      # +1R, -1R, etc.

    # TP1 partial close tracking
    tp1_hit = Column(Boolean, default=False)
    tp1_exit_price = Column(Float, nullable=True)
    breakeven_set = Column(Boolean, default=False)

    # Trailing stop (for runner)
    trailing_stop = Column(Float, nullable=True)

    # AI verdict stored for audit
    ai_decision = Column(String(10), nullable=True)
    ai_reason = Column(Text, nullable=True)
    ai_regime = Column(String(30), nullable=True)

    # Post-trade analysis
    post_analysis = Column(Text, nullable=True)

    # High water mark for trailing
    high_watermark = Column(Float, nullable=True)

    # Live price updated by portfolio every minute (for unrealized PnL)
    current_price = Column(Float, nullable=True)

    @property
    def is_open(self):
        return self.status == TradeStatus.OPEN

    @property
    def risk_distance(self):
        if self.direction == Direction.LONG:
            return self.entry_price - self.stop_price
        return self.stop_price - self.entry_price


class Candle(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    coin = Column(String(20), nullable=False)
    timeframe = Column(String(5), nullable=False)   # 1m, 1h, 4h, 1d
    timestamp = Column(DateTime, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)


class NewsItem(Base):
    __tablename__ = "news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)
    source = Column(String(100), nullable=True)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    impact = Column(String(20), nullable=True)   # high / medium / low / none


class HermesMemory(Base):
    __tablename__ = "hermes_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    trade_id = Column(Integer, nullable=True)
    coin = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)
    signal_source = Column(String(50), nullable=True)

    # Market state when Hermes decided
    regime = Column(String(30), nullable=True)
    adx_4h = Column(Float, nullable=True)
    btc_trend = Column(String(20), nullable=True)

    # Hermes decision
    decision = Column(String(10), nullable=False)
    size_multiplier = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)

    # Outcome (filled after close)
    outcome = Column(String(20), nullable=True)   # win / loss / breakeven
    pnl_r = Column(Float, nullable=True)

    # Lesson Hermes generated after seeing the result
    lesson = Column(Text, nullable=True)


class DailyStats(Base):
    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, unique=True)   # YYYY-MM-DD
    starting_balance = Column(Float, nullable=False)
    ending_balance = Column(Float, nullable=True)
    trades_count = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    total_pnl = Column(Float, default=0.0)
    drawdown_pct = Column(Float, default=0.0)
    regime = Column(String(30), nullable=True)
