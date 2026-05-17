"""
Funding rate scanner — shows where you can collect funding income.

Delta-neutral funding capture:
    long spot  +  short perp   (equal size, same coin)
    → net price exposure = 0  → you don't care where price goes
    → every 8h you collect the funding payment

Positive funding = longs pay shorts. So holding the SHORT perp leg earns it.

Usage:
    python funding_scan.py
"""
import asyncio
import httpx

PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
FUNDING_HIST_URL = "https://fapi.binance.com/fapi/v1/fundingRate"

PAYMENTS_PER_DAY = 3      # funding settles every 8h
HISTORY_LIMIT = 30        # last 30 payments ≈ 10 days


def annualized(rate: float) -> float:
    """8h funding rate → annualized %."""
    return rate * PAYMENTS_PER_DAY * 365 * 100


async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(PREMIUM_URL)
        resp.raise_for_status()
        data = resp.json()

        # USDT-margined perps only
        rows = []
        for d in data:
            sym = d["symbol"]
            if not sym.endswith("USDT"):
                continue
            try:
                rate = float(d["lastFundingRate"])
            except (KeyError, ValueError, TypeError):
                continue
            rows.append((sym, rate))

        rows.sort(key=lambda x: -x[1])

        print("\n" + "=" * 60)
        print("  FUNDING SCANNER — Binance Futures")
        print("=" * 60)
        print(f"  Всього USDT-перпів: {len(rows)}")

        print("\n  ── ТОП додатній funding (шортиш perp → ОТРИМУЄШ) ──")
        print(f"  {'Монета':14} {'8h rate':>12} {'річних':>14}")
        for sym, rate in rows[:15]:
            print(f"  {sym:14} {rate*100:>10.4f}%  {annualized(rate):>+11.1f}%")

        print("\n  ── Найбільш від'ємний (лонгуєш perp → отримуєш) ──")
        for sym, rate in rows[-5:]:
            print(f"  {sym:14} {rate*100:>10.4f}%  {annualized(rate):>+11.1f}%")

        # Stability check — average funding over last ~10 days for top coins
        print("\n  ── СТАБІЛЬНІСТЬ топ-10 (середнє за ~10 днів) ──")
        print(f"  {'Монета':14} {'зараз':>10} {'10д сер.':>11} {'річних(сер)':>14}")
        for sym, cur_rate in rows[:10]:
            try:
                h = await client.get(FUNDING_HIST_URL, params={"symbol": sym, "limit": HISTORY_LIMIT})
                hist = h.json()
            except Exception:
                hist = []
            if hist:
                avg = sum(float(x["fundingRate"]) for x in hist) / len(hist)
                neg = sum(1 for x in hist if float(x["fundingRate"]) < 0)
                flag = "⚠️ нестабільний" if neg > len(hist) * 0.2 else "✓ стабільний"
                print(f"  {sym:14} {cur_rate*100:>8.4f}%  {avg*100:>9.4f}%  "
                      f"{annualized(avg):>+11.1f}%  {flag}")

        print("=" * 60)
        print("""
  ЯК ЧИТАТИ:
  - "8h rate" — скільки платять кожні 8 годин
  - "річних" — те саме помножене на 3×365 (груба оцінка)
  - Дельта-нейтрально: купуєш монету на СПОТ + шортиш на ПЕРП
    рівним обсягом. Ціна тобі байдужа — збираєш funding.
  - Бери монети де funding СТАБІЛЬНО додатній (✓), не спайк.
  - Реалістично: гарні монети дають 15-40% річних,
    в гарячі періоди більше, в погані — близько нуля.
""")


if __name__ == "__main__":
    asyncio.run(main())
