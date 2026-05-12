"""
Position sizing — deterministic formula from the strategy doc.
size_usd = (balance * risk_pct) / distance_to_stop
Leverage is tiered per coin: BTC/ETH=7x, top alts=6x, rest=5x.
"""
from src.config import cfg
from src.signals.trend_follower import Signal


def calculate_size(
    signal: Signal,
    balance: float,
    size_multiplier: float = 1.0,
) -> dict:
    """
    Returns dict with size_usd, risk_usd, margin_usd, leverage.
    """
    risk_usd = balance * cfg.RISK_PER_TRADE * size_multiplier
    distance = abs(signal.entry - signal.suggested_stop)

    if distance == 0:
        return {"size_usd": 0, "risk_usd": 0, "margin_usd": 0, "coins": 0, "leverage": 1}

    leverage = cfg.get_leverage(signal.coin)
    coins = risk_usd / distance
    size_usd = coins * signal.entry
    margin_usd = size_usd / leverage

    return {
        "size_usd": round(size_usd, 2),
        "risk_usd": round(risk_usd, 2),
        "margin_usd": round(margin_usd, 2),
        "coins": coins,
        "leverage": leverage,
    }
