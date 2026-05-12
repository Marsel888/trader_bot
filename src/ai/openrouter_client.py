"""
OpenRouter API client — OpenAI-compatible, supports Hermes 70B and others.
Used as primary AI provider when OPENROUTER_API_KEY is set.
"""
import json
import httpx
from loguru import logger

from src.config import cfg

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def call_openrouter(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.1,
) -> dict:
    """
    Call OpenRouter API and return parsed JSON response.
    Raises on network error or invalid JSON.
    """
    headers = {
        "Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/trade-bot",
        "X-Title": "Trade Bot",
    }

    payload = {
        "model": cfg.OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    raw = data["choices"][0]["message"]["content"]
    result = json.loads(raw)

    model_used = data.get("model", cfg.OPENROUTER_MODEL)
    usage = data.get("usage", {})
    tokens = usage.get("total_tokens", "?")
    logger.debug(f"openrouter | model={model_used} | tokens={tokens}")

    return result
