"""
test_strategies.py
Deterministic tests for the named strategy functions in strategies.py --
pure functions operating on synthetic rows, no network. Run with:
pytest test_strategies.py
"""
import datetime

import pandas as pd
import pytest

import strategies

BEFORE_LAST_ENTRY = datetime.time(11, 0)
AFTER_LAST_ENTRY = datetime.time(15, 0)  # LAST_ENTRY_TIME is 14:45


def make_row(**overrides):
    base = {
        "timestamp": pd.Timestamp("2026-07-17 11:00:00"),
        "close": 24100.0,
        "ADX": 20.0,
        "bull_cross": False,
        "bear_cross": False,
        "bull_cross_prev": False,
        "bear_cross_prev": False,
        "ema_diff": 0.0,
        "EMA50": 24000.0,
        "pivot_pp": 24050.0,
        "prev_close": 24040.0,
    }
    base.update(overrides)
    return pd.Series(base)


def test_baseline_ce_on_bull_cross():
    row = make_row(bull_cross=True)
    assert strategies.signal_baseline(row) == "CE"


def test_baseline_pe_on_bear_cross():
    row = make_row(bear_cross=True)
    assert strategies.signal_baseline(row) == "PE"


def test_baseline_none_when_adx_weak():
    row = make_row(bull_cross=True, ADX=5.0)
    assert strategies.signal_baseline(row) is None


def test_ema50_filter_confirms_ce_above_ema():
    row = make_row(bull_cross=True, close=24100.0, EMA50=24000.0)
    assert strategies.signal_ema50_trend_filter(row) == "CE"


def test_ema50_filter_rejects_ce_below_ema():
    row = make_row(bull_cross=True, close=23900.0, EMA50=24000.0)
    assert strategies.signal_ema50_trend_filter(row) is None


def test_strict_adx_rejects_below_threshold():
    row = make_row(bull_cross=True, ADX=15.0)  # passes baseline's 12 but not strict's 20
    assert strategies.signal_baseline(row) == "CE"
    assert strategies.signal_strict_adx(row) is None


def test_strict_adx_confirms_above_threshold():
    row = make_row(bull_cross=True, ADX=25.0)
    assert strategies.signal_strict_adx(row) == "CE"


def test_confirmation_candle_waits_for_prev_cross():
    # cross happened last candle, not this one -- ema_diff still agrees
    row = make_row(bull_cross=False, bull_cross_prev=True, ema_diff=5.0)
    assert strategies.signal_confirmation_candle(row) == "CE"


def test_confirmation_candle_ignores_cross_on_current_candle():
    row = make_row(bull_cross=True, bull_cross_prev=False)
    assert strategies.signal_confirmation_candle(row) is None


def test_pivot_point_ce_on_upward_crossover():
    row = make_row(pivot_pp=24050.0, prev_close=24040.0, close=24060.0)
    assert strategies.signal_pivot_point(row) == "CE"


def test_pivot_point_pe_on_downward_crossover():
    row = make_row(pivot_pp=24050.0, prev_close=24060.0, close=24040.0)
    assert strategies.signal_pivot_point(row) == "PE"


def test_pivot_point_none_when_no_crossover():
    row = make_row(pivot_pp=24050.0, prev_close=24060.0, close=24070.0)  # stayed above PP
    assert strategies.signal_pivot_point(row) is None


def test_pivot_point_none_when_pivot_missing():
    row = make_row(pivot_pp=None, prev_close=24040.0, close=24060.0)
    assert strategies.signal_pivot_point(row) is None


def test_pivot_point_none_when_pivot_is_nan():
    row = make_row(pivot_pp=float("nan"), prev_close=24040.0, close=24060.0)
    assert strategies.signal_pivot_point(row) is None


def test_pivot_point_respects_last_entry_time():
    row = make_row(pivot_pp=24050.0, prev_close=24040.0, close=24060.0,
                    timestamp=pd.Timestamp("2026-07-17 15:00:00"))
    assert strategies.signal_pivot_point(row) is None


def test_get_signal_for_strategy_dispatches_correctly():
    row = make_row(bull_cross=True, close=24100.0, EMA50=24000.0)
    assert strategies.get_signal_for_strategy("BASELINE", row) == "CE"
    assert strategies.get_signal_for_strategy("EMA50_TREND_FILTER", row) == "CE"


def test_get_signal_for_strategy_falls_back_to_default_on_unknown():
    row = make_row(bull_cross=True, close=24100.0, EMA50=24000.0)
    assert strategies.get_signal_for_strategy("NOT_A_REAL_STRATEGY", row) == \
        strategies.get_signal_for_strategy(strategies.DEFAULT_STRATEGY, row)


def test_all_five_strategies_registered():
    assert len(strategies.STRATEGIES) == 5
    assert set(strategies.STRATEGIES) == {
        "BASELINE", "EMA50_TREND_FILTER", "STRICT_ADX", "CONFIRMATION_CANDLE", "PIVOT_POINT",
    }


def test_prepare_columns_does_not_clobber_existing_pivot_pp():
    df = pd.DataFrame({
        "close": [100.0, 101.0, 102.0],
        "bull_cross": [False, False, False],
        "bear_cross": [False, False, False],
        "pivot_pp": [50.0, 50.0, 50.0],  # pretend backtest.add_pivot_column already ran
    })
    out = strategies.prepare_columns(df, prev_day_ohlc=None)
    assert (out["pivot_pp"] == 50.0).all()


def test_prepare_columns_uses_prev_day_ohlc_when_given():
    df = pd.DataFrame({
        "close": [100.0, 101.0, 102.0],
        "bull_cross": [False, False, False],
        "bear_cross": [False, False, False],
    })
    out = strategies.prepare_columns(df, prev_day_ohlc={"high": 110.0, "low": 90.0, "close": 100.0})
    expected_pp = (110.0 + 90.0 + 100.0) / 3
    assert (out["pivot_pp"] == expected_pp).all()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
