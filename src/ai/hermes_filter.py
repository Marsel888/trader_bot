"""
Primary AI filter — local Hermes via Ollama.
Falls back to Claude if Ollama is unavailable.
Includes persistent memory: learns from past trade outcomes.
"""
import json
from loguru import logger

from src.config import cfg
from src.ai.prompts import SYSTEM_PROMPT, BATCH_SYSTEM_PROMPT
from src.ai.hermes_memory import get_relevant_memories, format_memories_for_prompt


SAFE_FALLBACK = {
    "decision": "reduced",
    "size_multiplier": 0.5,
    "reason": "AI unavailable — safe default applied",
    "concerns": ["AI service timeout"],
    "regime": "unknown",
}


class HermesFilter:
    async def evaluate(self, trade_context: str, timeout: int = cfg.OLLAMA_TIMEOUT,
                       signal=None, regime: str = "unknown") -> dict:
        if signal is not None:
            memories = await get_relevant_memories(coin=signal.coin, direction=signal.direction, regime=regime)
            if memories:
                memory_block = format_memories_for_prompt(memories)
                trade_context = trade_context.replace(
                    "МІЙ ДОСВІД (схожі минулі угоди):\n- Немає досвіду ще",
                    f"МІЙ ДОСВІД (схожі минулі угоди):\n{memory_block}",
                )

        return await self._call_ai(SYSTEM_PROMPT, trade_context, max_tokens=256)

    async def _call_ai(self, system_prompt: str, user_prompt: str, max_tokens: int = 512) -> dict:
        """Call OpenRouter (primary) → Claude (fallback) → safe default."""
        if cfg.use_openrouter():
            try:
                from src.ai.openrouter_client import call_openrouter
                result = await call_openrouter(system_prompt, user_prompt, max_tokens=max_tokens)
                self._validate(result)
                logger.info(f"🌐 openrouter | {result.get('decision','?')} mult={result.get('size_multiplier','?')}")
                return result
            except Exception as e:
                logger.warning(f"hermes_filter | OpenRouter failed: {e} — trying Claude")

        if cfg.ANTHROPIC_API_KEY and cfg.ANTHROPIC_API_KEY != "none":
            try:
                from src.ai.claude_filter import ClaudeFilter
                return await ClaudeFilter().evaluate(user_prompt)
            except Exception as e:
                logger.warning(f"hermes_filter | Claude failed: {e}")

        return SAFE_FALLBACK

    async def _call_ollama(self, context: str, timeout: int) -> dict:
        import asyncio
        import ollama

        def _sync_call():
            return ollama.chat(
                model=cfg.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                format="json",
                options={"temperature": 0.1, "num_predict": 256, "num_gpu": cfg.OLLAMA_NUM_GPU},
            )

        # Run blocking ollama call in thread pool to avoid freezing asyncio loop
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _sync_call)
        raw = response["message"]["content"]
        result = json.loads(raw)
        self._validate(result)
        decision = result['decision']
        icon = "✅" if decision == "take" else ("⚠️ " if decision == "reduced" else "❌")
        logger.info(f"{icon} hermes | {decision.upper()} mult={result['size_multiplier']} regime={result.get('regime')}")
        return result

    async def evaluate_batch(
        self,
        signals: list,
        price_cache: dict,
        portfolio_state: dict,
        stats: dict,
        news_titles: list[str],
        timeout: int = cfg.OLLAMA_TIMEOUT,
    ) -> dict:
        """Evaluate all signals together — AI sees full picture and compares them."""
        from src.ai.prompts import build_batch_context

        # Load memories for each signal
        memories_by_signal = {}
        for sig in signals:
            key = f"{sig.coin}_{sig.direction}"
            mems = await get_relevant_memories(coin=sig.coin, direction=sig.direction)
            if mems:
                memories_by_signal[key] = mems

        context = build_batch_context(
            signals=signals,
            price_cache=price_cache,
            portfolio_state=portfolio_state,
            stats=stats,
            news_titles=news_titles,
            memories_by_signal=memories_by_signal,
        )

        result = None
        if cfg.use_openrouter():
            try:
                from src.ai.openrouter_client import call_openrouter
                result = await call_openrouter(BATCH_SYSTEM_PROMPT, context, max_tokens=1024)
                self._validate_batch(result)
                logger.info(f"🌐 openrouter batch | regime={result.get('market_regime','?')} | {len(result.get('decisions',[]))} decisions")
            except Exception as e:
                logger.warning(f"hermes_filter | OpenRouter batch failed: {e} — trying Claude")

        if result is None and cfg.ANTHROPIC_API_KEY and cfg.ANTHROPIC_API_KEY != "none":
            try:
                from src.ai.claude_filter import ClaudeFilter
                result = await ClaudeFilter().evaluate_batch(context)
            except Exception as e:
                logger.warning(f"hermes_filter | batch Claude fallback failed: {e}")

        if result is None:
            # Safe fallback: reduced for all signals
            result = {
                "market_regime": "unknown",
                "market_summary": "AI unavailable",
                "decisions": [
                    {
                        "coin": sig.coin,
                        "direction": sig.direction,
                        "action": "reduced",
                        "priority": i + 1,
                        "size_multiplier": 0.5,
                        "reason": "AI unavailable — safe default",
                    }
                    for i, sig in enumerate(signals)
                ],
            }

        return result

    async def _call_ollama_batch(self, context: str, timeout: int) -> dict:
        import asyncio
        import ollama

        def _sync_call():
            return ollama.chat(
                model=cfg.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": BATCH_SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                format="json",
                options={"temperature": 0.1, "num_predict": 512, "num_gpu": cfg.OLLAMA_NUM_GPU},
            )

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _sync_call)
        raw = response["message"]["content"]
        result = json.loads(raw)
        self._validate_batch(result)
        regime = result.get("market_regime", "unknown")
        logger.info(f"🧠 hermes batch | regime={regime} | {len(result['decisions'])} decisions")
        for d in result["decisions"]:
            icon = "✅" if d["action"] == "take" else ("⚠️" if d["action"] == "reduced" else "❌")
            logger.info(f"  {icon} #{d['priority']} {d['coin']} {d['direction']} → {d['action']} x{d['size_multiplier']}")
        return result

    @staticmethod
    def _validate_batch(d: dict):
        assert "decisions" in d and isinstance(d["decisions"], list), "missing decisions list"
        for dec in d["decisions"]:
            assert dec.get("action") in ("take", "reduced", "skip"), f"bad action: {dec}"
            assert 0.0 <= float(dec.get("size_multiplier", 0)) <= 1.0, "size_multiplier out of range"

    @staticmethod
    def _validate(d: dict):
        assert d.get("decision") in ("take", "reduced", "skip"), f"bad decision: {d}"
        assert 0.0 <= float(d.get("size_multiplier", 0)) <= 1.0, "size_multiplier out of range"
