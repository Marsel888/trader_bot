"""
Claude fallback AI filter — used only when local Hermes is unavailable.
"""
import json
from loguru import logger

from src.config import cfg
from src.ai.prompts import SYSTEM_PROMPT


class ClaudeFilter:
    async def evaluate(self, trade_context: str) -> dict:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=cfg.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": trade_context}],
        )
        raw = message.content[0].text
        result = json.loads(raw)
        logger.info(f"claude_filter | decision={result['decision']} mult={result['size_multiplier']}")
        return result
