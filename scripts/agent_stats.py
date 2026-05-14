"""
Print consensus voting statistics.

Usage:
    docker exec tradebot_app python -m scripts.agent_stats [days]
    # default: 7 days
"""
import asyncio
import sys
from src.database.db import init_db
from src.analytics.agent_log import get_consensus_stats


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7

    await init_db()
    stats = await get_consensus_stats(days=days)

    if stats.get("total_evaluations", 0) == 0:
        print(f"Немає оцінок за останні {days} днів")
        return

    print(f"\n=== СТАТИСТИКА АГЕНТІВ ЗА {days} ДНІВ ===\n")
    print(f"Всього оцінок:     {stats['total_evaluations']}")
    print(f"  Відкрито угод:   {stats['taken']}")
    print(f"  Пропущено:       {stats['skipped']}")
    if stats.get("skip_reasons"):
        print(f"  Причини скіпу:")
        for r, n in stats["skip_reasons"].items():
            print(f"    - {r}: {n}")

    print(f"\n--- Як часто кожен агент каже 'TAKE' ---")
    rates = stats["agent_take_rate"]
    print(f"  A (Hermes):  {rates['A_hermes']}%")
    print(f"  B (Volume):  {rates['B_volume']}%")
    print(f"  C (Regime):  {rates['C_regime']}%")

    print(f"\n--- Згода з Agent A ---")
    agree = stats["agent_agreement_with_A"]
    print(f"  B погоджується з A:  {agree['B_volume']}%")
    print(f"  C погоджується з A:  {agree['C_regime']}%")
    print(f"  B і C однакові:      {stats['B_C_agree_rate']}%")

    print(f"\n--- Win rate за рівнем консенсусу ---")
    wrc = stats["win_rate_by_consensus"]
    for level, wr in wrc.items():
        wr_str = f"{wr}%" if wr is not None else "немає даних"
        print(f"  {level}/3 голосів: {wr_str}")

    print(f"\n--- Точність агента (коли голосував TAKE → виграв) ---")
    acc = stats["agent_accuracy_when_take"]
    for k, v in acc.items():
        v_str = f"{v}%" if v is not None else "немає даних"
        print(f"  {k}: {v_str}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
