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


async def run_backtest(interval: str, candles: int, setup: str):
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    print(f"Сетап: {setup} | Історія: {len(cfg.WATCHLIST)} монет × {candles} свічок ({interval})...")

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
