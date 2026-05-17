"""
Funding backtest — replays historical Binance funding rates and simulates
a delta-neutral funding-capture strategy.

Strategy simulated:
  - Universe = the bot's WATCHLIST (major coins — all have spot + perp, liquid,
    no survivorship bias from cherry-picking today's meme winners).
  - Every 8h settlement: hold (delta-neutral) every coin whose TRAILING-average
    funding is positive. Equal weight. Collect that period's funding.
  - Costs charged whenever a coin enters/exits the held basket.

Usage:
    python funding_backtest.py              # 1000 settlements (~333 days)
    python funding_backtest.py 600 9        # custom: limit, trailing window
"""
import asyncio
import sys
import httpx

from src.config import cfg
from src.data.price_collector import FUTURES_SYMBOL_OVERRIDE

FUNDING_HIST_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
COST_PER_CHANGE = 0.001   # 0.1% per coin entry/exit (both legs, modelled)


async def fetch_funding(client: httpx.AsyncClient, symbol: str, limit: int) -> list[tuple[int, float]]:
    resp = await client.get(FUNDING_HIST_URL, params={"symbol": symbol, "limit": limit})
    resp.raise_for_status()
    return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in resp.json()]


async def run_funding_backtest(limit: int, trail: int):
    print(f"Завантаження funding-історії: {len(cfg.WATCHLIST)} монет × {limit} виплат...")

    history: dict[str, dict[int, float]] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for coin in cfg.WATCHLIST:
            symbol = FUTURES_SYMBOL_OVERRIDE.get(coin, coin.replace("/", ""))
            try:
                h = await fetch_funding(client, symbol, limit)
                if h:
                    history[coin] = dict(h)
            except Exception as e:
                print(f"  ⚠️  {coin}: {e}")

    if not history:
        print("Не вдалось завантажити дані.")
        return

    coin_times = {c: sorted(rates.keys()) for c, rates in history.items()}
    all_times = sorted(set(t for rates in history.values() for t in rates))

    equity = 1.0
    prev_held: set[str] = set()
    curve: list[float] = []
    pos_periods = 0
    coin_count_sum = 0
    active_periods = 0

    for t in all_times:
        # Coins eligible to hold — trailing-average funding (strictly before t) > 0
        held: list[str] = []
        for coin, rates in history.items():
            if t not in rates:
                continue
            before = [x for x in coin_times[coin] if x < t][-trail:]
            if len(before) < trail:
                continue
            avg = sum(rates[x] for x in before) / len(before)
            if avg > 0:
                held.append(coin)

        if not held:
            curve.append(equity)
            prev_held = set()
            continue

        k = len(held)
        active_periods += 1
        coin_count_sum += k

        # Funding collected this period (equal weight across held coins)
        period_ret = sum(history[coin][t] for coin in held) / k

        # Costs — coins that entered or left the basket
        held_set = set(held)
        changed = len(held_set ^ prev_held)
        period_cost = (changed / k) * COST_PER_CHANGE

        net = period_ret - period_cost
        if net > 0:
            pos_periods += 1
        equity *= (1 + net)
        curve.append(equity)
        prev_held = held_set

    # ── Stats ───────────────────────────────────────────────────────
    total_periods = len(all_times)
    days = total_periods * 8 / 24
    total_return = (equity - 1) * 100
    annualized = ((equity ** (365 / days)) - 1) * 100 if days > 0 else 0.0

    peak, max_dd = 1.0, 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak * 100)

    avg_coins = coin_count_sum / active_periods if active_periods else 0
    pos_pct = pos_periods / active_periods * 100 if active_periods else 0

    print("\n" + "=" * 58)
    print(f"  FUNDING BACKTEST — {len(history)} монет, ~{days:.0f} днів")
    print("=" * 58)
    print(f"  Період:                {days:.0f} днів ({total_periods} виплат × 8h)")
    print(f"  Загальна дохідність:   {total_return:+.1f}%")
    print(f"  Річна (annualized):    {annualized:+.1f}%")
    print(f"  Max drawdown:          {max_dd:.1f}%")
    print(f"  Прибуткових періодів:  {pos_pct:.0f}%")
    print(f"  Середньо монет тримав: {avg_coins:.1f}")
    print("=" * 58)
    print("\n  ВИСНОВОК:")
    if annualized > 12 and max_dd < 8:
        print("  ✅ Funding дає стабільний реальний дохід — будуємо live бота.")
    elif annualized > 0:
        print(f"  ⚠️  Дохід є ({annualized:+.0f}%/рік) але скромний — оцінюй чи варто.")
    else:
        print("  ❌ На цьому періоді funding не заробив — переважав від'ємний.")
    print("""
  Примітка: дельта-нейтрально (спот лонг + перп шорт) ціновий
  ризик = 0. Цей результат — чистий funding-дохід мінус комісії.
""")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    trail = int(sys.argv[2]) if len(sys.argv) > 2 else 9
    asyncio.run(run_funding_backtest(limit, trail))
