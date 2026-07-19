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


def test_ut_bot_trailing_stop_trails_upward_in_uptrend():
    df = pd.DataFrame({
        "close": [100, 102, 104, 106, 108, 110],
        "high": [101, 103, 105, 107, 109, 111],
        "low": [99, 101, 103, 105, 107, 109],
    })
    stop = strategies.compute_ut_bot_trailing_stop(df, key_value=1.0, atr_period=3)
    # in a clean uptrend the stop should only ratchet up, never down
    diffs = stop.diff().dropna()
    assert (diffs >= 0).all()


def test_ut_bot_signal_ce_on_upward_crossover():
    row = make_row(
        close=110.0, prev_close=100.0,
        **{"ut_stop_kv1.0_atr10": 105.0, "ut_stop_kv1.0_atr10_prev": 101.0},
    )
    assert strategies._signal_ut_bot(row, 1.0, 10) == "CE"


def test_ut_bot_signal_pe_on_downward_crossover():
    row = make_row(
        close=95.0, prev_close=110.0,
        **{"ut_stop_kv1.0_atr10": 100.0, "ut_stop_kv1.0_atr10_prev": 105.0},
    )
    assert strategies._signal_ut_bot(row, 1.0, 10) == "PE"


def test_ut_bot_signal_none_when_columns_missing():
    row = make_row(close=110.0, prev_close=100.0)
    assert strategies._signal_ut_bot(row, 1.0, 10) is None


def test_ut_bot_standard_and_conservative_use_different_columns():
    assert strategies._ut_bot_column(1.0, 10) != strategies._ut_bot_column(2.0, 14)


def test_get_signal_for_strategy_dispatches_correctly():
    row = make_row(bull_cross=True, close=24100.0, EMA50=24000.0)
    assert strategies.get_signal_for_strategy("BASELINE", row) == "CE"
    assert strategies.get_signal_for_strategy("EMA50_TREND_FILTER", row) == "CE"


def test_get_signal_for_strategy_falls_back_to_default_on_unknown():
    row = make_row(bull_cross=True, close=24100.0, EMA50=24000.0)
    assert strategies.get_signal_for_strategy("NOT_A_REAL_STRATEGY", row) == \
        strategies.get_signal_for_strategy(strategies.DEFAULT_STRATEGY, row)


def test_all_strategies_registered():
    assert len(strategies.STRATEGIES) == 9
    assert set(strategies.STRATEGIES) == {
        "BASELINE", "EMA50_TREND_FILTER", "STRICT_ADX", "CONFIRMATION_CANDLE", "PIVOT_POINT",
        "SWING_STRUCTURE", "SWING_STRUCTURE_CANDLE_CONFIRMED", "UT_BOT_STANDARD", "UT_BOT_CONSERVATIVE",
    }


def test_compute_swing_levels_detects_fractal_high():
    # lookback=2 -> 5-bar window; index 3's high (15) is the strict max
    # of highs[1:6] = [11, 12, 15, 12, 11] -> confirmed at index 3+2=5
    df = pd.DataFrame({
        "high": [10, 11, 12, 15, 12, 11, 10, 9, 8, 9, 10],
        "low":  [9,  10, 11, 14, 11, 10, 9,  8, 7, 8, 9],
    })
    swing_high, swing_low = strategies.compute_swing_levels(df, lookback=2)
    assert pd.isna(swing_high.iloc[4])  # not yet confirmed
    assert swing_high.iloc[5] == 15.0   # confirmed exactly 2 bars later
    assert swing_high.iloc[8] == 15.0   # holds (ffill) until a new one confirms


def test_compute_swing_levels_detects_fractal_low():
    # index 3's low (2) is the strict min of lows[1:6] -> confirmed at index 5
    df = pd.DataFrame({
        "high": [10, 9, 8, 5, 8, 9, 10, 11, 12, 11, 10],
        "low":  [9,  8, 7, 2, 7, 8, 9,  10, 11, 10, 9],
    })
    swing_high, swing_low = strategies.compute_swing_levels(df, lookback=2)
    assert pd.isna(swing_low.iloc[4])
    assert swing_low.iloc[5] == 2.0


def test_swing_structure_ce_on_upward_break():
    row = make_row(last_swing_high=24100.0, last_swing_low=23900.0,
                    prev_close=24090.0, close=24110.0)
    assert strategies.signal_swing_structure(row) == "CE"


def test_swing_structure_pe_on_downward_break():
    row = make_row(last_swing_high=24100.0, last_swing_low=23900.0,
                    prev_close=23910.0, close=23890.0)
    assert strategies.signal_swing_structure(row) == "PE"


def test_swing_structure_none_inside_range():
    row = make_row(last_swing_high=24100.0, last_swing_low=23900.0,
                    prev_close=24000.0, close=24010.0)
    assert strategies.signal_swing_structure(row) is None


def test_swing_structure_none_when_levels_missing():
    row = make_row(last_swing_high=None, last_swing_low=None,
                    prev_close=24000.0, close=24010.0)
    assert strategies.signal_swing_structure(row) is None


def test_swing_structure_respects_last_entry_time():
    row = make_row(last_swing_high=24100.0, last_swing_low=23900.0,
                    prev_close=24090.0, close=24110.0,
                    timestamp=pd.Timestamp("2026-07-17 15:00:00"))
    assert strategies.signal_swing_structure(row) is None


def test_is_doji_true_for_small_body():
    row = make_row(open=100.0, close=100.05, high=101.0, low=99.0)
    assert strategies.is_doji(row) is True


def test_is_doji_false_for_large_body():
    row = make_row(open=99.0, close=101.0, high=101.2, low=98.8)
    assert strategies.is_doji(row) is False


def test_is_doji_false_when_open_missing():
    row = make_row(open=None, close=100.0, high=101.0, low=99.0)
    assert strategies.is_doji(row) is False


def test_is_inside_bar_true_when_contained():
    row = make_row(high=100.5, low=99.5, prev_high=101.0, prev_low=99.0)
    assert strategies.is_inside_bar(row) is True


def test_is_inside_bar_false_when_breaks_prev_range():
    row = make_row(high=101.5, low=99.5, prev_high=101.0, prev_low=99.0)
    assert strategies.is_inside_bar(row) is False


def test_is_inside_bar_false_when_prev_missing():
    row = make_row(high=100.5, low=99.5, prev_high=None, prev_low=None)
    assert strategies.is_inside_bar(row) is False


def test_swing_structure_candle_confirmed_fires_on_doji_breakout():
    row = make_row(
        last_swing_high=24100.0, last_swing_low=23900.0,
        prev_close=24090.0, close=24110.0,
        open=24109.0, high=24111.0, low=24089.0,  # small body -> doji
    )
    assert strategies.signal_swing_structure_candle_confirmed(row) == "CE"


def test_swing_structure_candle_confirmed_blocks_without_candle_pattern():
    row = make_row(
        last_swing_high=24100.0, last_swing_low=23900.0,
        prev_close=24090.0, close=24110.0,
        open=24000.0, high=24111.0, low=23990.0,  # big body, not inside prev range either
        prev_high=24050.0, prev_low=24040.0,
    )
    assert strategies.signal_swing_structure_candle_confirmed(row) is None


def test_swing_structure_candle_confirmed_fires_on_inside_bar():
    row = make_row(
        last_swing_high=24100.0, last_swing_low=23900.0,
        prev_close=24090.0, close=24110.0,
        open=24095.0, high=24105.0, low=24098.0,  # big-ish body, but inside prev bar
        prev_high=24150.0, prev_low=24050.0,
    )
    assert strategies.signal_swing_structure_candle_confirmed(row) == "CE"


def test_prepare_columns_does_not_clobber_existing_pivot_pp():
    df = pd.DataFrame({
        "close": [100.0, 101.0, 102.0],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "bull_cross": [False, False, False],
        "bear_cross": [False, False, False],
        "pivot_pp": [50.0, 50.0, 50.0],  # pretend backtest.add_pivot_column already ran
    })
    out = strategies.prepare_columns(df, prev_day_ohlc=None)
    assert (out["pivot_pp"] == 50.0).all()


def test_prepare_columns_uses_prev_day_ohlc_when_given():
    df = pd.DataFrame({
        "close": [100.0, 101.0, 102.0],
        "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0],
        "bull_cross": [False, False, False],
        "bear_cross": [False, False, False],
    })
    out = strategies.prepare_columns(df, prev_day_ohlc={"high": 110.0, "low": 90.0, "close": 100.0})
    expected_pp = (110.0 + 90.0 + 100.0) / 3
    assert (out["pivot_pp"] == expected_pp).all()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
