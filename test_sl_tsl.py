"""
test_sl_tsl.py
Deterministic tests for the SL/TSL/target/time-exit logic (evaluate_position)
and the risk-budget stop sizing (compute_sl_points) in engine.py, using
scripted price sequences -- no network, no DB writes, no waiting on the
real market. Run with: pytest test_sl_tsl.py
"""
import datetime

import config
import engine

NIFTY_CFG = config.INSTRUMENTS["NIFTY"]  # tsl_activation=5, tsl_trail=4, tsl_step=1
BEFORE_SQUAREOFF = datetime.time(11, 0)
AFTER_SQUAREOFF = datetime.time(15, 20)


def make_position(entry=120.0, current_sl=113.6, tsl_armed=False, target=130.0):
    return {
        "entry_ltp_net": entry,
        "current_sl": current_sl,
        "tsl_armed": tsl_armed,
        "target_price": target,
    }


def test_sl_hit_before_tsl_arms():
    position = make_position()
    for ltp in (118, 115):
        new_sl, tsl_armed, exit_reason = engine.evaluate_position(position, ltp, BEFORE_SQUAREOFF, NIFTY_CFG)
        assert exit_reason is None
        assert tsl_armed is False
        assert new_sl == 113.6  # never armed, stop doesn't move

    new_sl, tsl_armed, exit_reason = engine.evaluate_position(position, 112, BEFORE_SQUAREOFF, NIFTY_CFG)
    assert exit_reason == "SL"
    assert tsl_armed is False


def test_tsl_arms_then_ratchets_then_hits():
    position = make_position()

    new_sl, tsl_armed, exit_reason = engine.evaluate_position(position, 122, BEFORE_SQUAREOFF, NIFTY_CFG)
    assert exit_reason is None
    assert tsl_armed is False  # favorable move (2) below activation threshold (5)
    assert new_sl == 113.6

    position.update(current_sl=new_sl, tsl_armed=tsl_armed)
    new_sl, tsl_armed, exit_reason = engine.evaluate_position(position, 125, BEFORE_SQUAREOFF, NIFTY_CFG)
    assert tsl_armed is True  # favorable move (5) hits activation
    assert new_sl == 121.0  # 125 - trail(4)
    assert exit_reason is None

    position.update(current_sl=new_sl, tsl_armed=tsl_armed)
    new_sl, tsl_armed, exit_reason = engine.evaluate_position(position, 128, BEFORE_SQUAREOFF, NIFTY_CFG)
    assert new_sl == 124.0  # ratchets up: 128 - trail(4)
    assert exit_reason is None

    position.update(current_sl=new_sl, tsl_armed=tsl_armed)
    new_sl, tsl_armed, exit_reason = engine.evaluate_position(position, 124, BEFORE_SQUAREOFF, NIFTY_CFG)
    assert exit_reason == "TSL"  # price fell to the trailing stop
    assert tsl_armed is True


def test_tsl_only_ratchets_up_never_down():
    position = make_position(current_sl=121.0, tsl_armed=True)
    # a dip that doesn't breach the stop must not lower it
    new_sl, tsl_armed, exit_reason = engine.evaluate_position(position, 122.5, BEFORE_SQUAREOFF, NIFTY_CFG)
    assert new_sl == 121.0  # candidate (122.5-4=118.5) is below current_sl, no change
    assert exit_reason is None


def test_target_hit():
    position = make_position(current_sl=126.0, tsl_armed=True, target=130.0)
    new_sl, tsl_armed, exit_reason = engine.evaluate_position(position, 130.0, BEFORE_SQUAREOFF, NIFTY_CFG)
    assert exit_reason == "TARGET"


def test_time_exit_overrides_everything():
    # even a position sitting comfortably in profit gets EOD-squared-off
    position = make_position(current_sl=126.0, tsl_armed=True, target=130.0)
    new_sl, tsl_armed, exit_reason = engine.evaluate_position(position, 128.0, AFTER_SQUAREOFF, NIFTY_CFG)
    assert exit_reason == "EOD_SQUAREOFF"


def test_compute_sl_points_respects_budget_and_lot_size():
    entry_premium = 120.0
    sl_nifty = engine.compute_sl_points("NIFTY", entry_premium)
    sl_banknifty = engine.compute_sl_points("BANKNIFTY", entry_premium)
    sl_sensex = engine.compute_sl_points("SENSEX", entry_premium)

    for instrument, sl_points in (("NIFTY", sl_nifty), ("BANKNIFTY", sl_banknifty), ("SENSEX", sl_sensex)):
        assert sl_points is not None
        cfg = config.INSTRUMENTS[instrument]
        budget_per_trade = config.RISK["DAILY_LOSS_CAP"] / config.RISK["MAX_TRADES_BUDGET_DIVISOR"]
        worst_case_loss = sl_points * cfg["lot_size"]
        assert worst_case_loss <= budget_per_trade + 1e-6

    # smaller lot size instruments get more points of room within the same rupee budget
    assert sl_nifty < sl_banknifty < sl_sensex


def test_compute_sl_points_rejects_when_budget_cant_support_floor(monkeypatch):
    # force an unreachable noise floor -> sizing must refuse rather than exceed budget
    monkeypatch.setitem(config.INSTRUMENTS["NIFTY"], "min_sl_points_floor", 1000)
    assert engine.compute_sl_points("NIFTY", 120.0) is None


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
