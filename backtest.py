"""
Backtest engine — replays rule-based signals over historical Binance data.

Tests the RAW signal edge (trend_follower + breakout) WITHOUT AI agents.
If raw signals have no edge, no AI filter can fix it.

Usage:
    python backtest.py                      # default setup (trend+breakout), 3000 1h candles
    python backtest.py 1h 3000 squeeze      # test the squeeze-breakout setup
    python backtest.py 1h 3000 trend        # test trend_follower + breakout (old)

Output: win rate, avg R, profit factor, max drawdown, per-direction breakdown.
"""
import asyncio
import sys
import httpx
import pandas as pd
from loguru import logger

from src.config import cfg
from src.indicators import atr as _atr
from src.signals.trend_follower import TrendFollower
from src.signals.breakout import BreakoutGenerator
from src.signals.squeeze_breakout import SqueezeBreakout
from src.data.price_collector import FUTURES_SYMBOL_OVERRIDE, BINANCE_KLINES_URL

# Force REAL signal mode — no TEST spam
cfg.TEST_SIGNALS = False

WARMUP = 210            # bars needed before EMA200 is valid
TIME_EXIT_BARS = 24     # close after N bars if < +1R (24h on 1h timeframe)
MAX_BATCH = 1500        # Binance klines max per request


async def fetch_history(client: httpx.AsyncClient, symbol: str, interval: str, total: int) -> pd.DataFrame:
    """Fetch `total` candles, paginating backwards with endTime."""
    all_raw: list = []
    end_time = None
    while len(all_raw) < total:
        params = {"symbol": symbol, "interval": interval, "limit": MAX_BATCH}
        if end_time:
            params["endTime"] = end_time
        resp = await client.get(BINANCE_KLINES_URL, params=params)
        resp.raise_for_status()
        raw = resp.json()
        if not raw:
            break
        all_raw = raw + all_raw
        end_time = raw[0][0] - 1
        if len(raw) < MAX_BATCH:
            break

    df = pd.DataFrame(all_raw, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "tbb", "tbq", "ignore",
    ])
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df[["open", "high", "low", "close", "volume"]] = \
        df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df.drop_duplicates(subset="timestamp").set_index("timestamp")
    return df


def _close_trade(pos: dict, exit_price: float, exit_idx: int, reason: str) -> dict:
    is_long = pos["direction"] == "LONG"
    pnl_per_unit = (exit_price - pos["entry"]) if is_long else (pos["entry"] - exit_price)
    r = pnl_per_unit / pos["risk"] if pos["risk"] else 0.0
    return {
        "coin": pos["coin"], "direction": pos["direction"], "source": pos["source"],
        "entry": pos["entry"], "exit": exit_price, "reason": reason,
        "bars_held": exit_idx - pos["entry_idx"], "r": round(r, 3),
    }


def _make_signals(setup: str, coin: str, df_slice: pd.DataFrame) -> list:
    """Run the chosen setup's signal generators on a data slice."""
    if setup == "squeeze":
        return SqueezeBreakout()._run(coin, df_slice)
    # default: trend + breakout
    return TrendFollower()._run(coin, df_slice, "1h") + BreakoutGenerator()._run(coin, df_slice)


def simulate_coin(coin: str, df: pd.DataFrame, setup: str = "trend") -> list[dict]:
    """Walk bar by bar, generate signals, simulate one position at a time."""
    atr_series = _atr(df, cfg.ATR_PERIOD)

    closed: list[dict] = []
    pos: dict | None = None

    for i in range(WARMUP, len(df)):
        bar = df.iloc[i]
        atr_val = atr_series.iloc[i]
        if pd.isna(atr_val) or atr_val <= 0:
            atr_val = 0.0

        # ── 1. Process open position against this bar ──────────────
        if pos is not None:
            is_long = pos["direction"] == "LONG"
            closed_now = False

            # Stop loss (checked first — conservative)
            if is_long and bar["low"] <= pos["stop"]:
                closed.append(_close_trade(pos, pos["stop"], i, "SL"))
                closed_now = True
            elif not is_long and bar["high"] >= pos["stop"]:
                closed.append(_close_trade(pos, pos["stop"], i, "SL"))
                closed_now = True

            # TP2 — full exit
            if not closed_now and pos["tp1_hit"]:
                if is_long and bar["high"] >= pos["tp2"]:
                    closed.append(_close_trade(pos, pos["tp2"], i, "TP2"))
                    closed_now = True
                elif not is_long and bar["low"] <= pos["tp2"]:
                    closed.append(_close_trade(pos, pos["tp2"], i, "TP2"))
                    closed_now = True

            # TP1 — mark hit, move stop to breakeven
            if not closed_now and not pos["tp1_hit"]:
                if (is_long and bar["high"] >= pos["tp1"]) or \
                   (not is_long and bar["low"] <= pos["tp1"]):
                    pos["tp1_hit"] = True
                    pos["stop"] = pos["entry"]

            # Trailing stop after breakeven
            if not closed_now and pos["tp1_hit"] and atr_val > 0:
                if is_long:
                    pos["stop"] = max(pos["stop"], bar["high"] - cfg.TRAILING_ATR_MULTIPLIER * atr_val)
                else:
                    pos["stop"] = min(pos["stop"], bar["low"] + cfg.TRAILING_ATR_MULTIPLIER * atr_val)

            # Time exit
            if not closed_now and (i - pos["entry_idx"]) >= TIME_EXIT_BARS:
                profit_r = ((bar["close"] - pos["entry"]) if is_long
                            else (pos["entry"] - bar["close"])) / pos["risk"]
                if profit_r < 1.0:
                    closed.append(_close_trade(pos, bar["close"], i, "TIME"))
                    closed_now = True

            if closed_now:
                pos = None

        # ── 2. No open position — look for a new signal ────────────
        if pos is None:
            df_slice = df.iloc[: i + 1]
            try:
                sigs = _make_signals(setup, coin, df_slice)
            except Exception:
                sigs = []
            if sigs:
                sig = sigs[0]
                risk = abs(sig.entry - sig.suggested_stop)
                if risk > 0:
                    pos = {
                        "coin": coin, "direction": sig.direction, "source": sig.source,
                        "entry": sig.entry, "stop": sig.suggested_stop,
                        "tp1": sig.suggested_tp1, "tp2": sig.suggested_tp2,
                        "risk": risk, "tp1_hit": False, "entry_idx": i,
                    }

    return closed


def _report(trades: list[dict], interval: str, candles: int):
    if not trades:
        print("\nЖодної угоди не згенеровано — сигнали не спрацювали на цьому періоді.")
        return

    total = len(trades)
    wins = [t for t in trades if t["r"] > 0.05]
    losses = [t for t in trades if t["r"] < -0.05]
    win_rate = len(wins) / total * 100
    sum_r = sum(t["r"] for t in trades)
    avg_r = sum_r / total
    gross_win = sum(t["r"] for t in wins)
    gross_loss = abs(sum(t["r"] for t in losses))
    profit_factor = gross_win / gross_loss if gross_loss else float("inf")

    # Max drawdown on cumulative-R equity curve
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["r"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    by_dir = {}
    for d in ("LONG", "SHORT"):
        dt = [t for t in trades if t["direction"] == d]
        if dt:
            dw = sum(1 for t in dt if t["r"] > 0.05)
            by_dir[d] = (len(dt), dw / len(dt) * 100, sum(t["r"] for t in dt) / len(dt))

    by_reason = {}
    for t in trades:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1

    print("\n" + "=" * 56)
    print(f"  BACKTEST — {interval}, ~{candles} свічок на монету")
    print("=" * 56)
    print(f"  Всього угод:      {total}")
    print(f"  Перемог:          {len(wins)}")
    print(f"  Втрат:            {len(losses)}")
    print(f"  Win rate:         {win_rate:.1f}%")
    print(f"  Сумарний R:       {sum_r:+.1f}R")
    print(f"  Середній R/угода: {avg_r:+.3f}R")
    print(f"  Profit factor:    {profit_factor:.2f}")
    print(f"  Max drawdown:     {max_dd:.1f}R")
    print(f"  Найкраща:         {max(t['r'] for t in trades):+.2f}R")
    print(f"  Найгірша:         {min(t['r'] for t in trades):+.2f}R")
    print("-" * 56)
    print("  За напрямком:")
    for d, (n, wr, ar) in by_dir.items():
        print(f"    {d:5} | угод: {n:3} | WR: {wr:5.1f}% | avg R: {ar:+.3f}")
    print("  За причиною виходу:")
    for r, n in sorted(by_reason.items(), key=lambda x: -x[1]):
        print(f"    {r:6} | {n}")
    print("=" * 56)

    # Verdict
    print("\n  ВИСНОВОК:")
    if avg_r > 0.1 and profit_factor > 1.3:
        print("  ✅ Стратегія має позитивний edge — варто валідувати далі з AI.")
    elif avg_r > 0:
        print("  ⚠️  Ледь позитивна — edge слабкий, AI-фільтр може допомогти.")
    else:
        print("  ❌ Негативний edge — сирі сигнали не працюють.")
        print("     AI-фільтр НЕ врятує. Треба міняти логіку входу.")
    print()


# ── Grid trading simulation ─────────────────────────────────────────

GRID_BAND = 0.30      # grid spans ±30% around the start price
GRID_LEVELS = 20      # number of grid steps
GRID_CAPITAL = 1000.0 # capital allocated per coin
GRID_FEE = 0.0005     # 0.05% taker fee per fill


def simulate_grid_coin(df: pd.DataFrame) -> dict:
    """
    Classic long-only grid: buy at each level below price, sell one step up.
    Returns realized profit, unrealized PnL on the open bag, and equity curve.
    """
    start = float(df["close"].iloc[0])
    low = start * (1 - GRID_BAND)
    high = start * (1 + GRID_BAND)
    spacing = (high - low) / GRID_LEVELS
    grid = [low + k * spacing for k in range(GRID_LEVELS + 1)]
    per_level = GRID_CAPITAL / GRID_LEVELS

    held: dict[float, float] = {}   # level price -> quantity bought
    realized = 0.0
    fees = 0.0
    prev = start
    curve: list[float] = []

    for i in range(1, len(df)):
        price = float(df["close"].iloc[i])
        # Buy — price crossed DOWN through an unowned level
        for lvl in grid:
            if lvl not in held and price <= lvl < prev:
                qty = per_level / lvl
                held[lvl] = qty
                fees += per_level * GRID_FEE
        # Sell — price reached one spacing above a held level
        for lvl in list(held):
            if price >= lvl + spacing:
                qty = held.pop(lvl)
                realized += qty * spacing
                fees += qty * (lvl + spacing) * GRID_FEE
        prev = price
        unreal = sum(q * (price - l) for l, q in held.items())
        curve.append(realized - fees + unreal)

    final = float(df["close"].iloc[-1])
    unrealized = sum(q * (final - l) for l, q in held.items())
    return {
        "realized": realized - fees,
        "unrealized": unrealized,
        "total": realized - fees + unrealized,
        "curve": curve,
        "bag_units": len(held),
    }


def _grid_report(results: dict, interval: str, candles: int):
    if not results:
        print("\nЖодних даних.")
        return

    n = len(results)
    invested = n * GRID_CAPITAL
    total_realized = sum(r["realized"] for r in results.values())
    total_unreal = sum(r["unrealized"] for r in results.values())
    total = sum(r["total"] for r in results.values())
    total_pct = total / invested * 100

    # Combined equity curve (sum across coins, aligned to shortest)
    min_len = min(len(r["curve"]) for r in results.values())
    combined = [sum(r["curve"][i] for r in results.values()) for i in range(min_len)]
    peak, max_dd = (combined[0] if combined else 0.0), 0.0
    for v in combined:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    max_dd_pct = max_dd / invested * 100

    losers = sum(1 for r in results.values() if r["total"] < 0)

    print("\n" + "=" * 56)
    print(f"  GRID BACKTEST — {interval}, ~{candles} свічок, {n} монет")
    print("=" * 56)
    print(f"  Вкладено капіталу:   ${invested:,.0f}")
    print(f"  Realized (флет):     ${total_realized:+,.0f}  ← збір на коливаннях")
    print(f"  Unrealized (мішок):  ${total_unreal:+,.0f}  ← незакриті позиції")
    print(f"  ПІДСУМОК:            ${total:+,.0f}  ({total_pct:+.1f}%)")
    print(f"  Max drawdown:        ${max_dd:,.0f}  ({max_dd_pct:.1f}%)")
    print(f"  Монет у мінусі:      {losers}/{n}")
    print("=" * 56)
    print("\n  ВИСНОВОК:")
    if total > 0 and max_dd_pct < 15:
        print("  ✅ Грід спрацював на цьому періоді.")
    else:
        print("  ❌ Грід: realized-профіт з'їдений 'мішком' незакритих позицій.")
        print("     Класична загибель гріда — тренд пробиває сітку.")
    print()


async def run_backtest(interval: str, candles: int, setup: str):
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    print(f"Сетап: {setup} | Історія: {len(cfg.WATCHLIST)} монет × {candles} свічок ({interval})...")

    # ── Grid mode — different simulation, different report ──────────
    if setup == "grid":
        grid_results: dict = {}
        async with httpx.AsyncClient(timeout=60) as client:
            for n, coin in enumerate(cfg.WATCHLIST, 1):
                symbol = FUTURES_SYMBOL_OVERRIDE.get(coin, coin.replace("/", ""))
                try:
                    df = await fetch_history(client, symbol, interval, candles)
                except Exception as e:
                    print(f"  ⚠️  {coin}: помилка завантаження — {e}")
                    continue
                if len(df) < 100:
                    continue
                res = simulate_grid_coin(df)
                grid_results[coin] = res
                print(f"  [{n:2}/{len(cfg.WATCHLIST)}] {coin:12} — "
                      f"realized ${res['realized']:+.0f} | мішок ${res['unrealized']:+.0f} | "
                      f"разом ${res['total']:+.0f}")
        _grid_report(grid_results, interval, candles)
        return

    all_trades: list[dict] = []
    async with httpx.AsyncClient(timeout=60) as client:
        for n, coin in enumerate(cfg.WATCHLIST, 1):
            symbol = FUTURES_SYMBOL_OVERRIDE.get(coin, coin.replace("/", ""))
            try:
                df = await fetch_history(client, symbol, interval, candles)
            except Exception as e:
                print(f"  ⚠️  {coin}: помилка завантаження — {e}")
                continue
            if len(df) < WARMUP + 50:
                print(f"  ⚠️  {coin}: замало даних ({len(df)} свічок)")
                continue
            trades = simulate_coin(coin, df, setup)
            all_trades.extend(trades)
            print(f"  [{n:2}/{len(cfg.WATCHLIST)}] {coin:12} — {len(trades):3} угод")

    _report(all_trades, interval, candles)


if __name__ == "__main__":
    interval = sys.argv[1] if len(sys.argv) > 1 else "1h"
    candles = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    setup = sys.argv[3] if len(sys.argv) > 3 else "trend"
    asyncio.run(run_backtest(interval, candles, setup))
