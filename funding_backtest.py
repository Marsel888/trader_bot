"""
Funding backtest v2 — honest test of delta-neutral funding capture.

Fixes over v1:
  - Universe = LIQUID alt perps that ALSO have a Binance SPOT market
    (you need spot to build the delta-neutral hedge). No majors (≈0 funding),
    no perp-only meme coins (can't hedge).
  - Sticky positions with hysteresis — enter on clearly-positive trailing
    funding, hold until it turns negative. Low churn → realistic costs.
  - Reports the period split in halves so you see bull vs bear behaviour.

Strategy: long spot + short perp on coins paying positive funding.
Earns funding every 8h. Price-neutral. Loses funding only if funding flips
negative while still held.

Usage:
    python funding_backtest.py                  # defaults
    python funding_backtest.py 1000 21 30       # limit, trail window, min $M volume
"""
import asyncio
import sys
import httpx

PERP_24H_URL   = "https://fapi.binance.com/fapi/v1/ticker/24hr"
SPOT_INFO_URL  = "https://api.binance.com/api/v3/exchangeInfo"
FUNDING_URL    = "https://fapi.binance.com/fapi/v1/fundingRate"

ENTRY_THRESH = 0.00005    # enter when trailing avg funding > 0.005% / 8h
EXIT_THRESH  = 0.0        # exit when trailing avg funding < 0
COST_PER_SIDE = 0.0007    # 0.07% per enter and per exit (both legs modelled)
MAX_UNIVERSE = 50         # cap number of coins fetched


async def build_universe(client: httpx.AsyncClient, min_vol_m: float) -> list[str]:
    """Perp symbols that also have a spot market and enough 24h volume."""
    perp_resp = await client.get(PERP_24H_URL)
    perp_resp.raise_for_status()
    perps = {
        d["symbol"]: float(d["quoteVolume"])
        for d in perp_resp.json()
        if d["symbol"].endswith("USDT")
    }

    spot_resp = await client.get(SPOT_INFO_URL)
    spot_resp.raise_for_status()
    spot_symbols = {
        s["symbol"]
        for s in spot_resp.json()["symbols"]
        if s["status"] == "TRADING" and s["quoteAsset"] == "USDT"
    }

    universe = [
        sym for sym, vol in sorted(perps.items(), key=lambda x: -x[1])
        if sym in spot_symbols and vol > min_vol_m * 1_000_000
    ]
    # Drop the very largest majors — their funding is ≈0
    majors = {"BTCUSDT", "ETHUSDT"}
    universe = [s for s in universe if s not in majors]
    return universe[:MAX_UNIVERSE]


async def fetch_funding(client: httpx.AsyncClient, symbol: str, limit: int) -> dict[int, float]:
    resp = await client.get(FUNDING_URL, params={"symbol": symbol, "limit": limit})
    resp.raise_for_status()
    return {int(x["fundingTime"]): float(x["fundingRate"]) for x in resp.json()}


def _drawdown(curve: list[float]) -> float:
    peak, mdd = 1.0, 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak * 100)
    return mdd


async def run_funding_backtest(limit: int, trail: int, min_vol_m: float):
    async with httpx.AsyncClient(timeout=40) as client:
        print(f"Будую всесвіт: ліквідні alt-перпи зі спот-ринком (>{min_vol_m:.0f}M об'єм)...")
        universe = await build_universe(client, min_vol_m)
        print(f"Монет у всесвіті: {len(universe)}")
        print(f"Завантаження funding-історії ({limit} виплат кожна)...")

        history: dict[str, dict[int, float]] = {}
        for sym in universe:
            try:
                h = await fetch_funding(client, sym, limit)
                if len(h) > trail + 10:
                    history[sym] = h
            except Exception:
                pass

    if not history:
        print("Не вдалось завантажити дані.")
        return

    coin_times = {c: sorted(r.keys()) for c, r in history.items()}
    all_times = sorted(set(t for r in history.values() for t in r))

    equity = 1.0
    held: set[str] = set()
    curve: list[float] = []
    pos_periods = active = coin_sum = 0

    for t in all_times:
        # Update basket with hysteresis
        new_held = set(held)
        for coin, rates in history.items():
            if t not in rates:
                new_held.discard(coin)
                continue
            before = [x for x in coin_times[coin] if x < t][-trail:]
            if len(before) < trail:
                continue
            avg = sum(rates[x] for x in before) / len(before)
            if coin in new_held and avg < EXIT_THRESH:
                new_held.discard(coin)
            elif coin not in new_held and avg > ENTRY_THRESH:
                new_held.add(coin)

        changed = len(new_held ^ held)
        held = new_held

        eligible = [c for c in held if t in history[c]]
        if not eligible:
            curve.append(equity)
            continue

        k = len(eligible)
        active += 1
        coin_sum += k
        period_ret = sum(history[c][t] for c in eligible) / k
        period_cost = (changed / k) * COST_PER_SIDE
        net = period_ret - period_cost
        if net > 0:
            pos_periods += 1
        equity *= (1 + net)
        curve.append(equity)

    # ── Stats ───────────────────────────────────────────────────────
    days = len(all_times) * 8 / 24
    total_ret = (equity - 1) * 100
    annualized = ((equity ** (365 / days)) - 1) * 100 if days > 0 else 0.0
    mdd = _drawdown(curve)
    avg_coins = coin_sum / active if active else 0
    pos_pct = pos_periods / active * 100 if active else 0

    half = len(curve) // 2
    h1 = (curve[half] / curve[0] - 1) * 100 if half else 0
    h2 = (curve[-1] / curve[half] - 1) * 100 if half else 0

    print("\n" + "=" * 58)
    print(f"  FUNDING BACKTEST v2 — {len(history)} монет, ~{days:.0f} днів")
    print("=" * 58)
    print(f"  Загальна дохідність:   {total_ret:+.1f}%")
    print(f"  Річна (annualized):    {annualized:+.1f}%")
    print(f"  Max drawdown:          {mdd:.1f}%")
    print(f"  Прибуткових періодів:  {pos_pct:.0f}%")
    print(f"  Середньо монет тримав: {avg_coins:.1f}")
    print(f"  Перша половина:        {h1:+.1f}%")
    print(f"  Друга половина:        {h2:+.1f}%")
    print("=" * 58)
    print("\n  ВИСНОВОК:")
    if annualized > 10 and mdd < 10:
        print(f"  ✅ Funding дає реальний дохід ({annualized:+.0f}%/рік) — варто будувати.")
    elif annualized > 0:
        print(f"  ⚠️  Дохід є але скромний ({annualized:+.0f}%/рік) — рішення за тобою.")
    else:
        print("  ❌ Навіть на правильному всесвіті funding не заробив.")
    print()


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    trail = int(sys.argv[2]) if len(sys.argv) > 2 else 21
    min_vol_m = float(sys.argv[3]) if len(sys.argv) > 3 else 30
    asyncio.run(run_funding_backtest(limit, trail, min_vol_m))
