"""
cost_model.py
Brokerage, statutory charges, and slippage modeling for paper trades, so
logged P&L reflects realistic trading costs instead of a frictionless
simulation. Reused by engine.py (actual fills) and by SL sizing (cost
estimates at entry time, before the exit price is known).
"""
from config import COSTS


def apply_slippage(raw_price, side):
    """Worsen a fill price to model bid-ask spread / market impact.
    side: 'BUY' fills higher, 'SELL' fills lower.
    """
    adj = raw_price * COSTS["SLIPPAGE_PCT"]
    if side == "BUY":
        return raw_price + adj
    if side == "SELL":
        return raw_price - adj
    raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")


def compute_leg_charges(premium, qty, side, exchange):
    """Statutory + brokerage charges for one leg (buy or sell) of a trade.
    premium: the executed (net, post-slippage) premium per unit.
    Returns a breakdown dict with a 'total' key.
    """
    turnover = premium * qty

    brokerage = min(COSTS["BROKERAGE_FLAT"], turnover * COSTS["BROKERAGE_PCT"])
    exchange_txn = turnover * COSTS["EXCHANGE_TXN_PCT"][exchange]
    sebi = turnover * COSTS["SEBI_PCT"]
    gst = COSTS["GST_PCT"] * (brokerage + exchange_txn)

    stt = turnover * COSTS["STT_SELL_PCT"] if side == "SELL" else 0.0
    stamp_duty = turnover * COSTS["STAMP_DUTY_BUY_PCT"] if side == "BUY" else 0.0

    total = brokerage + exchange_txn + sebi + gst + stt + stamp_duty
    return {
        "brokerage": brokerage,
        "exchange_txn": exchange_txn,
        "sebi": sebi,
        "gst": gst,
        "stt": stt,
        "stamp_duty": stamp_duty,
        "total": total,
    }


def compute_round_trip_costs(entry_premium_net, exit_premium_net, qty, exchange):
    """Total charges for a completed buy-then-sell round trip, given the
    actual net (post-slippage) fill prices on both legs."""
    entry_charges = compute_leg_charges(entry_premium_net, qty, "BUY", exchange)
    exit_charges = compute_leg_charges(exit_premium_net, qty, "SELL", exchange)
    return entry_charges["total"] + exit_charges["total"]


def estimate_round_trip_costs(entry_premium_raw, qty, exchange):
    """Estimate round-trip costs at entry time, before the real exit price
    is known (used to size the stop-loss). Assumes slippage on both legs
    and an exit premium approximately equal to entry -- charges are
    turnover-proportional, so this stays a reasonable estimate even though
    the real exit premium will differ."""
    entry_net = apply_slippage(entry_premium_raw, "BUY")
    exit_net = apply_slippage(entry_premium_raw, "SELL")
    return compute_round_trip_costs(entry_net, exit_net, qty, exchange)
