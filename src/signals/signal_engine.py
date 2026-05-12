"""
Signal engine — aggregates all generators, runs pre-checks,
and returns a list of validated signals ready for AI filter.
"""
from loguru import logger

from src.config import cfg
from src.signals.trend_follower import TrendFollower, Signal
from src.signals.breakout import BreakoutGenerator
from src.signals.pre_checks import rule_pre_checks


class SignalEngine:
    def __init__(self):
        self._generators = [
            TrendFollower(),
            BreakoutGenerator(),
        ]

    def check(
        self,
        price_cache: dict,
        portfolio_state: dict,
        news_watcher=None,
    ) -> list[tuple[Signal, list[str]]]:
        """
        Returns list of (signal, failed_reasons).
        failed_reasons is empty when signal passed all pre-checks.
        """
        raw_signals: list[Signal] = []

        for coin in cfg.WATCHLIST:
            dfs = price_cache.get(coin, {})
            df_1h = dfs.get("1h")
            df_4h = dfs.get("4h")
            df_1d = dfs.get("1d")

            if df_1h is None or df_4h is None or df_1d is None:
                logger.debug(f"signal_engine | {coin}: missing candles, skipping")
                continue

            for gen in self._generators:
                sigs = gen.generate(coin, df_1h, df_4h, df_1d)
                raw_signals.extend(sigs)
                if sigs:
                    logger.info(f"signal_engine | {gen.name} → {coin}: {len(sigs)} signal(s)")

        results = []
        for sig in raw_signals:
            passed, fails = rule_pre_checks(sig, price_cache, portfolio_state, news_watcher)
            if passed:
                logger.info(f"signal_engine | ✅ {sig.coin} {sig.direction} from {sig.source} passed pre-checks")
            else:
                logger.info(f"signal_engine | ❌ {sig.coin} {sig.direction} SKIPPED: {', '.join(fails)}")
            results.append((sig, fails))

        return results
