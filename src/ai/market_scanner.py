"""
AI Market Scanner — replaces rule-based signal generators.
Sends full market data to AI and lets it find trade opportunities itself.
Returns Signal objects compatible with existing execution pipeline.
"""
import json
from loguru import logger

from src.config import cfg
from src.ai.prompts import SCANNER_SYSTEM_PROMPT, build_scanner_context
from src.ai.hermes_memory import get_relevant_memories
from src.signals.trend_follower import Signal
from src.indicators import atr as _atr


class AIMarketScanner:
    async def scan(
        self,
        price_cache: dict,
        portfolio_state: dict,
        stats: dict,
        news_titles: list[str],
    ) -> tuple[list[Signal], str]:
        """
        Scan the market and return AI-discovered signals.
        Returns (signals, regime).
        """
        # Gather memories across all watchlist coins for context
        memories_summary = await self._build_memories_summary()

        context = build_scanner_context(
            price_cache=price_cache,
            portfolio_state=portfolio_state,
            stats=stats,
            news_titles=news_titles,
            memories_summary=memories_summary,
        )

        try:
            result = await self._call_ollama(context)
        except Exception as e:
            logger.warning(f"ai_scanner | Ollama failed: {e} — trying Claude fallback")
            result = None

        if result is None and cfg.ANTHROPIC_API_KEY and cfg.ANTHROPIC_API_KEY != "none":
            try:
                from src.ai.claude_filter import ClaudeFilter
                result = await ClaudeFilter().scan(context)
            except Exception as e:
                logger.warning(f"ai_scanner | Claude fallback failed: {e}")

        if result is None:
            return [], "unknown"

        regime = result.get("regime", "unknown")
        overview = result.get("market_overview", "")
        raw_signals = result.get("signals", [])

        logger.info(f"🔭 ai_scanner | regime={regime} | found {len(raw_signals)} signal(s)")
        if overview:
            logger.info(f"🌍 {overview[:120]}")

        signals = self._parse_signals(raw_signals, price_cache)
        return signals, regime

    async def _call_ollama(self, context: str) -> dict | None:
        import asyncio
        import ollama

        def _sync_call():
            return ollama.chat(
                model=cfg.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": SCANNER_SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                format="json",
                options={
                    "temperature": 0.2,
                    "num_predict": 1024,
                    "num_gpu": cfg.OLLAMA_NUM_GPU,
                },
            )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _sync_call)
        raw = response["message"]["content"]
        result = json.loads(raw)
        assert "signals" in result and isinstance(result["signals"], list)
        return result

    def _parse_signals(self, raw: list[dict], price_cache: dict) -> list[Signal]:
        """Convert AI JSON output to Signal objects."""
        signals = []
        for item in raw:
            try:
                coin = item["coin"]
                direction = item["direction"].upper()
                entry = float(item["entry"])
                stop = float(item["stop"])
                tp1 = float(item["tp1"])
                tp2 = float(item["tp2"])
                confidence = float(item.get("confidence", 0.6))
                size_mult = float(item.get("size_multiplier", 0.8))
                reason = item.get("reason", "AI scanner")

                if direction not in ("LONG", "SHORT"):
                    continue

                # Validate stop/TP direction
                if direction == "LONG" and not (stop < entry < tp1 < tp2):
                    logger.warning(f"ai_scanner | {coin} LONG invalid levels: stop={stop} entry={entry} tp1={tp1} tp2={tp2}")
                    continue
                if direction == "SHORT" and not (tp2 < tp1 < entry < stop):
                    logger.warning(f"ai_scanner | {coin} SHORT invalid levels: stop={stop} entry={entry} tp1={tp1} tp2={tp2}")
                    continue

                # Get ATR from price data for downstream use
                df_1h = price_cache.get(coin, {}).get("1h")
                atr_val = 0.0
                if df_1h is not None and len(df_1h) > 14:
                    import pandas as pd
                    v = _atr(df_1h, 14).iloc[-1]
                    atr_val = float(v) if not pd.isna(v) else 0.0

                sig = Signal(
                    coin=coin,
                    direction=direction,
                    entry=entry,
                    suggested_stop=stop,
                    suggested_tp1=tp1,
                    suggested_tp2=tp2,
                    confidence=confidence,
                    reason=reason,
                    source="ai_scanner",
                    atr=atr_val,
                    extra={"size_multiplier": size_mult},
                )
                signals.append(sig)
                logger.info(f"  📡 ai_scanner | {coin} {direction} | entry={entry:.4f} stop={stop:.4f} conf={confidence:.2f}")

            except Exception as e:
                logger.warning(f"ai_scanner | failed to parse signal {item}: {e}")
                continue

        return signals

    async def _build_memories_summary(self) -> str:
        """Collect recent lessons across all coins as a summary string."""
        try:
            all_memories = []
            for coin in cfg.WATCHLIST[:10]:  # sample top 10 to keep prompt size manageable
                for direction in ("LONG", "SHORT"):
                    mems = await get_relevant_memories(coin=coin, direction=direction, limit=1)
                    for m in mems:
                        lesson = m.get("lesson", "")
                        if lesson:
                            all_memories.append(f"- {coin} {direction}: {lesson}")
            return "\n".join(all_memories[:15]) if all_memories else ""
        except Exception:
            return ""
