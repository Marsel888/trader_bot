"""
Agent voting log + analytics.
Saves every consensus evaluation and provides query helpers for analysis.
"""
from datetime import datetime, timedelta, timezone
from loguru import logger
from sqlalchemy import select, func

from src.database.db import AsyncSessionLocal
from src.database.models import SignalEvaluation, Trade
from src.indicators import rsi as _rsi, ema as _ema
from src.config import cfg


async def log_evaluation(
    signal,
    agent_a_vote: str,
    agent_a_reason: str,
    agent_a_multiplier: float,
    agent_b_vote: str,
    agent_b_reason: str,
    agent_c_vote: str,
    agent_c_reason: str,
    consensus_votes: int,
    taken: bool,
    trade_id: int | None = None,
    skip_reason: str | None = None,
    regime: str | None = None,
    price_cache: dict | None = None,
) -> SignalEvaluation:
    """Save evaluation of a signal that reached the consensus stage."""

    btc_rsi_1h = None
    coin_rsi_1h = None
    breadth_pct = None

    if price_cache:
        btc_1h = price_cache.get("BTC/USDT", {}).get("1h")
        if btc_1h is not None and len(btc_1h) >= 14:
            try:
                btc_rsi_1h = float(_rsi(btc_1h, 14).iloc[-1])
            except Exception:
                pass

        coin_1h = price_cache.get(signal.coin, {}).get("1h")
        if coin_1h is not None and len(coin_1h) >= 14:
            try:
                coin_rsi_1h = float(_rsi(coin_1h, 14).iloc[-1])
            except Exception:
                pass

        # Breadth
        up, total = 0, 0
        for c in cfg.WATCHLIST[:25]:
            df = price_cache.get(c, {}).get("1h")
            if df is not None and len(df) >= 50:
                try:
                    above = df["close"].iloc[-1] > _ema(df, 50).iloc[-1]
                    up += int(above)
                    total += 1
                except Exception:
                    pass
        breadth_pct = (up / total * 100) if total else None

    entry = SignalEvaluation(
        coin=signal.coin,
        direction=signal.direction,
        source=signal.source,
        entry=float(signal.entry),
        stop=float(signal.suggested_stop),
        tp2=float(signal.suggested_tp2),
        agent_a_vote=agent_a_vote,
        agent_a_reason=(agent_a_reason or "")[:500],
        agent_a_multiplier=agent_a_multiplier,
        agent_b_vote=agent_b_vote,
        agent_b_reason=(agent_b_reason or "")[:500],
        agent_c_vote=agent_c_vote,
        agent_c_reason=(agent_c_reason or "")[:500],
        consensus_votes=consensus_votes,
        taken=taken,
        trade_id=trade_id,
        skip_reason=skip_reason,
        regime=regime,
        btc_rsi_1h=btc_rsi_1h,
        coin_rsi_1h=coin_rsi_1h,
        breadth_pct=breadth_pct,
    )

    async with AsyncSessionLocal() as session:
        session.add(entry)
        await session.commit()
        await session.refresh(entry)

    return entry


async def update_evaluation_outcome(trade: Trade):
    """Called when a trade closes — fills outcome for the linked evaluation."""
    if trade.r_multiple is None:
        return

    outcome = "win" if trade.r_multiple > 0.1 else ("loss" if trade.r_multiple < -0.1 else "breakeven")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SignalEvaluation).where(SignalEvaluation.trade_id == trade.id)
        )
        ev = result.scalar_one_or_none()
        if ev:
            ev.outcome = outcome
            ev.pnl_r = round(trade.r_multiple, 2)
            await session.commit()


async def get_consensus_stats(days: int = 7) -> dict:
    """
    Returns aggregated stats over last N days:
      - total evaluations, taken vs skipped
      - agent agreement matrix
      - win rate by consensus level
      - per-agent accuracy (when they say "take", how often it wins?)
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SignalEvaluation).where(SignalEvaluation.created_at >= since)
        )
        evals = result.scalars().all()

    if not evals:
        return {"total": 0, "message": "no evaluations yet"}

    total = len(evals)
    taken = sum(1 for e in evals if e.taken)
    skipped = total - taken

    # Agent vote distribution
    a_take = sum(1 for e in evals if e.agent_a_vote in ("take", "reduced"))
    b_take = sum(1 for e in evals if e.agent_b_vote == "take")
    c_take = sum(1 for e in evals if e.agent_c_vote == "take")

    # Agent agreement (B and C agree with A)
    b_agrees = sum(1 for e in evals
                   if (e.agent_a_vote in ("take", "reduced") and e.agent_b_vote == "take")
                   or (e.agent_a_vote == "skip" and e.agent_b_vote == "skip"))
    c_agrees = sum(1 for e in evals
                   if (e.agent_a_vote in ("take", "reduced") and e.agent_c_vote == "take")
                   or (e.agent_a_vote == "skip" and e.agent_c_vote == "skip"))
    bc_agrees = sum(1 for e in evals if e.agent_b_vote == e.agent_c_vote)

    # Outcomes by consensus level
    outcomes_by_consensus = {0: [], 1: [], 2: [], 3: []}
    for e in evals:
        if e.outcome and e.outcome != "breakeven":
            outcomes_by_consensus[e.consensus_votes].append(1 if e.outcome == "win" else 0)

    win_rate_by_consensus = {
        k: (sum(v) / len(v) * 100) if v else None
        for k, v in outcomes_by_consensus.items()
    }

    # Per-agent accuracy: when agent voted "take" and trade was opened, how often win?
    def agent_accuracy(votes_field: str) -> float | None:
        wins, losses = 0, 0
        for e in evals:
            if not e.taken or not e.outcome or e.outcome == "breakeven":
                continue
            vote = getattr(e, votes_field)
            voted_take = vote in ("take", "reduced")
            if voted_take:
                if e.outcome == "win":
                    wins += 1
                else:
                    losses += 1
        return (wins / (wins + losses) * 100) if (wins + losses) else None

    return {
        "period_days": days,
        "total_evaluations": total,
        "taken": taken,
        "skipped": skipped,
        "skip_reasons": _count_skip_reasons(evals),
        "agent_take_rate": {
            "A_hermes":   round(a_take / total * 100, 1),
            "B_volume":   round(b_take / total * 100, 1),
            "C_regime":   round(c_take / total * 100, 1),
        },
        "agent_agreement_with_A": {
            "B_volume": round(b_agrees / total * 100, 1),
            "C_regime": round(c_agrees / total * 100, 1),
        },
        "B_C_agree_rate": round(bc_agrees / total * 100, 1),
        "win_rate_by_consensus": {k: (round(v, 1) if v is not None else None) for k, v in win_rate_by_consensus.items()},
        "agent_accuracy_when_take": {
            "A_hermes": (lambda v: round(v, 1) if v is not None else None)(agent_accuracy("agent_a_vote")),
            "B_volume": (lambda v: round(v, 1) if v is not None else None)(agent_accuracy("agent_b_vote")),
            "C_regime": (lambda v: round(v, 1) if v is not None else None)(agent_accuracy("agent_c_vote")),
        },
    }


def _count_skip_reasons(evals: list) -> dict:
    counts: dict[str, int] = {}
    for e in evals:
        if not e.taken and e.skip_reason:
            counts[e.skip_reason] = counts.get(e.skip_reason, 0) + 1
    return counts
