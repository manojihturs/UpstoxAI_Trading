"""
test_cost_model.py
Hand-calculated checks for brokerage/STT/GST/stamp-duty/slippage, for both
NSE and BSE exchange settings. Run with: pytest test_cost_model.py
"""
import cost_model
from config import COSTS


def hand_calc_leg(premium, qty, side, exchange):
    turnover = premium * qty
    brokerage = min(COSTS["BROKERAGE_FLAT"], turnover * COSTS["BROKERAGE_PCT"])
    exchange_txn = turnover * COSTS["EXCHANGE_TXN_PCT"][exchange]
    sebi = turnover * COSTS["SEBI_PCT"]
    gst = COSTS["GST_PCT"] * (brokerage + exchange_txn)
    stt = turnover * COSTS["STT_SELL_PCT"] if side == "SELL" else 0.0
    stamp_duty = turnover * COSTS["STAMP_DUTY_BUY_PCT"] if side == "BUY" else 0.0
    total = brokerage + exchange_txn + sebi + gst + stt + stamp_duty
    return total


def test_apply_slippage_buy_fills_higher():
    raw = 100.0
    net = cost_model.apply_slippage(raw, "BUY")
    assert net > raw
    assert abs(net - raw * (1 + COSTS["SLIPPAGE_PCT"])) < 1e-9


def test_apply_slippage_sell_fills_lower():
    raw = 100.0
    net = cost_model.apply_slippage(raw, "SELL")
    assert net < raw
    assert abs(net - raw * (1 - COSTS["SLIPPAGE_PCT"])) < 1e-9


def test_apply_slippage_invalid_side_raises():
    try:
        cost_model.apply_slippage(100.0, "HOLD")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_leg_charges_buy_has_no_stt_but_has_stamp_duty_nse():
    charges = cost_model.compute_leg_charges(120.0, 75, "BUY", "NSE")
    assert charges["stt"] == 0.0
    assert charges["stamp_duty"] > 0.0
    expected = hand_calc_leg(120.0, 75, "BUY", "NSE")
    assert abs(charges["total"] - expected) < 1e-9


def test_leg_charges_sell_has_stt_but_no_stamp_duty_nse():
    charges = cost_model.compute_leg_charges(130.0, 75, "SELL", "NSE")
    assert charges["stt"] > 0.0
    assert charges["stamp_duty"] == 0.0
    expected = hand_calc_leg(130.0, 75, "SELL", "NSE")
    assert abs(charges["total"] - expected) < 1e-9


def test_leg_charges_bse_uses_bse_exchange_rate():
    charges_nse = cost_model.compute_leg_charges(200.0, 20, "SELL", "NSE")
    charges_bse = cost_model.compute_leg_charges(200.0, 20, "SELL", "BSE")
    assert charges_nse["exchange_txn"] != charges_bse["exchange_txn"]
    expected_bse = hand_calc_leg(200.0, 20, "SELL", "BSE")
    assert abs(charges_bse["total"] - expected_bse) < 1e-9


def test_round_trip_costs_sum_both_legs():
    qty = 75
    entry_net = 120.6
    exit_net = 130.35
    total = cost_model.compute_round_trip_costs(entry_net, exit_net, qty, "NSE")
    expected = (
        hand_calc_leg(entry_net, qty, "BUY", "NSE")
        + hand_calc_leg(exit_net, qty, "SELL", "NSE")
    )
    assert abs(total - expected) < 1e-9


def test_estimate_round_trip_costs_applies_slippage_both_legs():
    raw = 120.0
    qty = 75
    estimate = cost_model.estimate_round_trip_costs(raw, qty, "NSE")
    entry_net = cost_model.apply_slippage(raw, "BUY")
    exit_net = cost_model.apply_slippage(raw, "SELL")
    expected = cost_model.compute_round_trip_costs(entry_net, exit_net, qty, "NSE")
    assert abs(estimate - expected) < 1e-9


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
