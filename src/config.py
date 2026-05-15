from dotenv import load_dotenv
import os

load_dotenv()


class Config:
    # Exchange
    EXCHANGE = os.getenv("EXCHANGE", "binance")
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")

    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # AI — OpenRouter (основний) або Ollama (локальний fallback)
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nousresearch/hermes-4-70b")

    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "hermes3:latest")
    OLLAMA_NUM_GPU = int(os.getenv("OLLAMA_NUM_GPU", "99"))
    OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    @classmethod
    def use_openrouter(cls) -> bool:
        return bool(cls.OPENROUTER_API_KEY and cls.OPENROUTER_API_KEY != "none")

    # Trading parameters
    PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
    INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "10000"))
    RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))
    MAX_LEVERAGE = float(os.getenv("MAX_LEVERAGE", "5"))
    MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10"))
    MAX_SAME_DIRECTION = int(os.getenv("MAX_SAME_DIRECTION", "5"))

    # Watchlist
    WATCHLIST = [s.strip() for s in os.getenv("WATCHLIST", "BTC/USDT,ETH/USDT,SOL/USDT").split(",")]

    # Kill switches
    MAX_DAILY_DRAWDOWN = float(os.getenv("MAX_DAILY_DRAWDOWN", "0.15"))
    MAX_WEEKLY_DRAWDOWN = float(os.getenv("MAX_WEEKLY_DRAWDOWN", "0.10"))
    MAX_MONTHLY_DRAWDOWN = float(os.getenv("MAX_MONTHLY_DRAWDOWN", "0.15"))
    MAX_LOSS_STREAK = int(os.getenv("MAX_LOSS_STREAK", "3"))

    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///trading_bot.db")

    # Signal engine intervals (seconds)
    PRICE_INTERVAL = 60       # REST candle fetch
    SIGNAL_INTERVAL = 300     # signal engine
    NEWS_INTERVAL = 900       # news fetch
    REGIME_INTERVAL = 3600    # regime update
    PORTFOLIO_INTERVAL = 5    # stop/TP check (uses WS prices)

    # Test mode — relaxed signal conditions for verifying trade lifecycle
    TEST_SIGNALS = os.getenv("TEST_SIGNALS", "false").lower() == "true"

    # Indicators
    EMA_FAST = 50
    EMA_SLOW = 200
    ADX_PERIOD = 14
    ADX_THRESHOLD = 18
    ATR_PERIOD = 14
    ATR_STOP_MULTIPLIER = 1.5   # stop distance = ATR × this (0.5=tight, 1.5=wide)

    # ATR extremal multiplier (>2x avg = skip)
    ATR_EXTREME_MULTIPLIER = 2.0

    # BTC hourly move threshold to skip entries
    BTC_HOURLY_MOVE_THRESHOLD = 0.02

    # Funding rate thresholds
    FUNDING_EXTREME = 0.001    # 0.1% per 8h → emergency exit
    FUNDING_WARNING = 0.0005   # 0.05% per 8h → skip entry

    # Per-coin leverage tiers (7x → 6x → 5x)
    _LEVERAGE_7X = {"BTC/USDT", "ETH/USDT"}
    _LEVERAGE_6X = {"SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT",
                    "DOT/USDT", "LINK/USDT", "MATIC/USDT", "LTC/USDT", "BCH/USDT",
                    "ATOM/USDT", "UNI/USDT"}

    @classmethod
    def get_leverage(cls, coin: str) -> int:
        if coin in cls._LEVERAGE_7X:
            return 7
        if coin in cls._LEVERAGE_6X:
            return 6
        return 5

    # TP/SL ratios
    TP1_R = 1.0
    TP2_R = 2.0
    TP1_CLOSE_FRACTION = 0.5
    TP2_CLOSE_FRACTION = 0.25
    TRAILING_ATR_MULTIPLIER = 1.0   # tighter trailing (was 2.0)

    # Active trailing — move stop to breakeven once profit reaches this R
    EARLY_BREAKEVEN_R = 0.5

    # Pullback entry — distance to key level (EMA50 / breakout) in ATR units
    PULLBACK_MAX_DISTANCE_ATR = 1.0   # entry must be within 1×ATR of trigger level

    # Time-of-day filter — UTC hours when bot SKIPS new entries (low-liquidity Asian session)
    SKIP_HOURS_UTC = {0, 1, 2, 3, 4, 5}

    # Time-based exit: 24h without +1R move
    TIME_EXIT_HOURS = 24

    # News window: skip trades ±1h around major news
    NEWS_WINDOW_MINUTES = 60


cfg = Config()
