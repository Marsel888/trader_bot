"""
OpenRouter API client — supports multiple models via single API key.

Agent A (Technical): nousresearch/hermes-4-70b
Agent B (Volume):    deepseek/deepseek-chat
Agent C (Regime):    qwen/qwen-2.5-72b-instruct
"""
import asyncio
import json
import httpx
from loguru import logger

from src.config import cfg

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def call_openrouter(
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.1,
    retries: int = 3,
) -> dict:
    """
    Call OpenRouter API and return parsed JSON response.
    model=None → uses cfg.OPENROUTER_MODEL (default: hermes-4-70b).
    Retries up to `retries` times on 429 with exponential backoff.
    """
    model_id = model or cfg.OPENROUTER_MODEL

    headers = {
        "Authorization": f"Bearer {cfg.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/trade-bot",
        "X-Title": "Trade Bot",
    }

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    last_error = None
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(OPENROUTER_URL, headers=headers, json=payload)

                if resp.status_code == 429:
                    wait = 2 ** attempt * 5  # 5s, 10s, 20s
                    logger.warning(f"openrouter | 429 rate limit ({model_id}) — retry in {wait}s")
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

            raw = data["choices"][0]["message"]["content"]
            result = json.loads(raw)

            usage = data.get("usage", {})
            logger.debug(f"openrouter | {model_id} | tokens={usage.get('total_tokens', '?')}")

            return result

        except (json.JSONDecodeError, KeyError) as e:
            last_error = e
            logger.warning(f"openrouter | {model_id} | parse error: {e} — retrying")
            await asyncio.sleep(2 ** attempt)
            continue

        except httpx.HTTPStatusError as e:
            last_error = e
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)
            continue

    raise RuntimeError(f"openrouter | {model_id} | failed after {retries} attempts: {last_error}")
