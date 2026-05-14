"""
Hermes memory — stores past decisions and outcomes so Hermes learns over time.

Flow:
  1. Trade opens  → save_decision() stores what Hermes saw and decided
  2. Trade closes → update_outcome() records win/loss
  3.              → generate_lesson() asks Hermes what it learned
  4. Next signal  → get_relevant_memories() feeds experience into next prompt
"""
import json
from datetime import datetime, timezone
from loguru import logger
from sqlalchemy import select

from src.config import cfg
from src.database.db import AsyncSessionLocal
from src.database.models import HermesMemory, Trade


async def save_decision(
    trade: Trade,
    signal,
    verdict: dict,
    adx_4h: float = 0.0,
    btc_trend: str = "unknown",
) -> HermesMemory:
    """Called right after a trade opens — stores Hermes' decision context."""
    entry = HermesMemory(
        trade_id=trade.id,
        coin=trade.coin,
        direction=trade.direction,
        signal_source=trade.source,
        regime=verdict.get("regime", "unknown"),
        adx_4h=adx_4h,
        btc_trend=btc_trend,
        decision=verdict["decision"],
        size_multiplier=verdict.get("size_multiplier", 1.0),
        reason=verdict.get("reason", ""),
    )
    async with AsyncSessionLocal() as session:
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
    logger.debug(f"hermes_memory | saved decision for trade #{trade.id}")
    return entry


async def update_outcome(trade: Trade):
    """Called after trade closes — fills in win/loss result."""
    if trade.r_multiple is None:
        return

    outcome = "win" if trade.r_multiple > 0.1 else ("loss" if trade.r_multiple < -0.1 else "breakeven")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HermesMemory).where(HermesMemory.trade_id == trade.id)
        )
        mem = result.scalar_one_or_none()
        if mem:
            mem.outcome = outcome
            mem.pnl_r = round(trade.r_multiple, 2)
            await session.commit()
            logger.debug(f"hermes_memory | outcome for trade #{trade.id}: {outcome} ({trade.r_multiple:+.2f}R)")


async def generate_lesson(trade: Trade, ollama_model: str = "", ollama_host: str = ""):
    """Asks Hermes to reflect on its decision after seeing the result."""
    if trade.r_multiple is None:
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HermesMemory).where(HermesMemory.trade_id == trade.id)
        )
        mem = result.scalar_one_or_none()
        if not mem or mem.lesson:
            return

    outcome_str = f"{trade.r_multiple:+.2f}R ({'прибуток' if trade.r_multiple > 0 else 'збиток'})"
    prompt = f"""Ти щойно закрив угоду. Проаналізуй своє рішення.

Монета: {trade.coin} {trade.direction}
Твоє рішення: {mem.decision} (size_multiplier={mem.size_multiplier})
Твоя причина тоді: {mem.reason}
Режим ринку: {mem.regime}
ADX(4h): {mem.adx_4h}
BTC тренд: {mem.btc_trend}

Результат: {outcome_str} (статус: {trade.status})

Сформулюй урок в 1-2 реченнях: що ти зробив правильно або де помилився?
Відповідай ТІЛЬКИ JSON: {{"lesson": "текст уроку"}}"""

    try:
        from src.ai.openrouter_client import call_openrouter
        data = await call_openrouter(
            system="Ти торговий бот що вчиться на своїх угодах. Відповідай тільки валідним JSON.",
            user=prompt,
            temperature=0.3,
            max_tokens=128,
        )
        lesson = data.get("lesson", "").strip()

        if lesson:
            async with AsyncSessionLocal() as session:
                m = await session.get(HermesMemory, mem.id)
                if m:
                    m.lesson = lesson
                    await session.commit()
            logger.info(f"🧠 hermes learned | trade #{trade.id}: {lesson}")

    except Exception as e:
        logger.warning(f"hermes_memory | lesson generation failed: {e}")


async def get_relevant_memories(
    coin: str,
    direction: str,
    regime: str = "unknown",
    limit: int = 6,
) -> list[HermesMemory]:
    """Returns past memories relevant to the current signal."""
    async with AsyncSessionLocal() as session:
        # Priority: same coin → same direction → same regime → any with lesson
        result = await session.execute(
            select(HermesMemory)
            .where(
                HermesMemory.outcome.isnot(None),
                HermesMemory.lesson.isnot(None),
            )
            .order_by(HermesMemory.created_at.desc())
            .limit(50)
        )
        all_mem = result.scalars().all()

    # Score by relevance
    def score(m: HermesMemory) -> int:
        s = 0
        if m.coin == coin:
            s += 3
        if m.direction == direction:
            s += 2
        if m.regime == regime:
            s += 1
        return s

    sorted_mem = sorted(all_mem, key=score, reverse=True)
    return sorted_mem[:limit]


def format_memories_for_prompt(memories: list[HermesMemory]) -> str:
    """Formats memories into a readable block for the system prompt."""
    if not memories:
        return "- Немає досвіду ще (перші угоди)"

    lines = []
    for m in memories:
        outcome_icon = "✅" if m.outcome == "win" else ("❌" if m.outcome == "loss" else "➖")
        r_str = f"{m.pnl_r:+.1f}R" if m.pnl_r is not None else "?"
        lines.append(
            f"{outcome_icon} {m.coin} {m.direction} | {m.decision}({m.size_multiplier}) "
            f"| {m.regime} ADX={m.adx_4h:.0f} | {r_str} | Урок: {m.lesson}"
        )
    return "\n".join(lines)
