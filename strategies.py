"""
strategies.py
Named, selectable signal strategies. Both engine.py (live) and
backtest_experiments.py (historical comparison) call the SAME functions
from this file, so switching which strategy is active live never diverges
from what gets backtested -- the project's core rule.

Each strategy is a plain function(row) -> 'CE'/'PE'/None, operating on a
candle row that already carries whatever extra columns it needs. Call
prepare_columns() once per instrument per cycle (cheap, adds every column
any strategy might need) before evaluating whichever strategy is active --
simpler than per-strategy prep, and the data involved is small.

signal_engine.get_signal() -- the raw EMA9/EMA20+ADX crossover -- stays
untouched as the base building block several of these reuse.
"""
import numpy as np
import pandas as pd

from signal_engine import get_signal, confirm_with_trend_filter, ADX_THRESHOLD, LAST_ENTRY_TIME, _ema, _atr


def _row_time(row):
    ts = row["timestamp"]
    return ts.time() if hasattr(ts, "time") else ts


def signal_baseline(row):
    """Raw EMA9/EMA20 cross + ADX > 12. signal_engine.get_signal(), untouched."""
    return get_signal(row)


def signal_ema50_trend_filter(row):
    """Baseline cross, but only if price also agrees with the longer EMA50
    trend -- the live default as of the backtest_experiments.py Variant C
    validation (improved results in both a train and a held-out window)."""
    signal = get_signal(row)
    if signal is None:
        return None
    ema50 = row.get("EMA50")
    if ema50 is None or not confirm_with_trend_filter(signal, row["close"], ema50):
        return None
    return signal


def signal_strict_adx(row, threshold=20):
    """Same cross, but requires a stronger trend (ADX > 20 instead of 12) --
    fewer, theoretically higher-conviction signals. Backtested worse
    out-of-sample than the trend filter above; kept as a comparison point."""
    if _row_time(row) > LAST_ENTRY_TIME or row["ADX"] <= threshold:
        return None
    if row["bull_cross"]:
        return "CE"
    if row["bear_cross"]:
        return "PE"
    return None


def signal_confirmation_candle(row):
    """Don't enter on the cross candle itself -- wait one more candle and
    only enter if the trend direction still holds. Needs bull_cross_prev/
    bear_cross_prev columns from prepare_columns()."""
    if _row_time(row) > LAST_ENTRY_TIME or row["ADX"] <= ADX_THRESHOLD:
        return None
    if row.get("bull_cross_prev") and row["ema_diff"] > 0:
        return "CE"
    if row.get("bear_cross_prev") and row["ema_diff"] < 0:
        return "PE"
    return None


def signal_pivot_point(row):
    """Classic floor pivot point crossover: PP = (prev day High+Low+Close)/3.
    CE when price crosses above PP, PE when it crosses below. A genuinely
    different technical method from the EMA/ADX family above -- price
    structure relative to a fixed daily reference level, not a moving
    average relationship. Needs pivot_pp/prev_close columns."""
    if _row_time(row) > LAST_ENTRY_TIME:
        return None
    pp = row.get("pivot_pp")
    prev_close = row.get("prev_close")
    if pp is None or prev_close is None or pd.isna(pp) or pd.isna(prev_close):
        return None
    close = row["close"]
    if prev_close <= pp and close > pp:
        return "CE"
    if prev_close >= pp and close < pp:
        return "PE"
    return None


# ------------------------------------------------------------------ UT Bot

UT_BOT_VARIANTS = {
    "UT_BOT_STANDARD": {"key_value": 1.0, "atr_period": 10},
    "UT_BOT_CONSERVATIVE": {"key_value": 2.0, "atr_period": 14},
}


def _ut_bot_column(key_value, atr_period):
    return f"ut_stop_kv{key_value}_atr{atr_period}"


def compute_ut_bot_trailing_stop(df, key_value, atr_period):
    """UT Bot Alerts: an ATR-scaled trailing stop that only ever moves in
    the trend's favor (like a SuperTrend/Chandelier Exit). nLoss = KeyValue
    * ATR sets how far the line trails; the entry signal (see
    _signal_ut_bot) is a price crossover of this line, not a moving-average
    relationship or a fixed daily level -- a third, distinct signal family
    alongside the EMA-cross and pivot-point strategies above."""
    atr = _atr(df["high"], df["low"], df["close"], atr_period)
    n_loss = (key_value * atr).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    stop = np.full(n, np.nan)
    if n == 0:
        return pd.Series(stop, index=df.index)

    stop[0] = close[0] - n_loss[0] if not np.isnan(n_loss[0]) else close[0]
    for i in range(1, n):
        nl = n_loss[i]
        if np.isnan(nl):
            stop[i] = stop[i - 1]
            continue
        prev_stop, prev_close, c = stop[i - 1], close[i - 1], close[i]
        if c > prev_stop and prev_close > prev_stop:
            stop[i] = max(prev_stop, c - nl)
        elif c < prev_stop and prev_close < prev_stop:
            stop[i] = min(prev_stop, c + nl)
        elif c > prev_stop:
            stop[i] = c - nl
        else:
            stop[i] = c + nl
    return pd.Series(stop, index=df.index)


def _signal_ut_bot(row, key_value, atr_period):
    if _row_time(row) > LAST_ENTRY_TIME:
        return None
    col = _ut_bot_column(key_value, atr_period)
    stop = row.get(col)
    prev_stop = row.get(col + "_prev")
    prev_close = row.get("prev_close")
    if any(v is None or pd.isna(v) for v in (stop, prev_stop, prev_close)):
        return None
    close = row["close"]
    if prev_close <= prev_stop and close > stop:
        return "CE"
    if prev_close >= prev_stop and close < stop:
        return "PE"
    return None


def signal_ut_bot_standard(row):
    p = UT_BOT_VARIANTS["UT_BOT_STANDARD"]
    return _signal_ut_bot(row, p["key_value"], p["atr_period"])


def signal_ut_bot_conservative(row):
    p = UT_BOT_VARIANTS["UT_BOT_CONSERVATIVE"]
    return _signal_ut_bot(row, p["key_value"], p["atr_period"])


STRATEGIES = {
    "BASELINE": {
        "label": "Baseline: EMA9/EMA20 cross + ADX",
        "signal_fn": signal_baseline,
    },
    "EMA50_TREND_FILTER": {
        "label": "EMA9/20 cross + EMA50 trend filter",
        "signal_fn": signal_ema50_trend_filter,
    },
    "STRICT_ADX": {
        "label": "Stricter trend strength (ADX > 20)",
        "signal_fn": signal_strict_adx,
    },
    "CONFIRMATION_CANDLE": {
        "label": "Wait 1 confirmation candle after the cross",
        "signal_fn": signal_confirmation_candle,
    },
    "PIVOT_POINT": {
        "label": "Pivot point crossover (previous day's PP)",
        "signal_fn": signal_pivot_point,
    },
    "UT_BOT_STANDARD": {
        "label": "UT Bot (ATR trailing stop, KeyValue=1, ATR=10)",
        "signal_fn": signal_ut_bot_standard,
    },
    "UT_BOT_CONSERVATIVE": {
        "label": "UT Bot conservative (KeyValue=2, ATR=14)",
        "signal_fn": signal_ut_bot_conservative,
    },
}

DEFAULT_STRATEGY = "EMA50_TREND_FILTER"


def get_signal_for_strategy(strategy_name, row):
    strategy = STRATEGIES.get(strategy_name, STRATEGIES[DEFAULT_STRATEGY])
    return strategy["signal_fn"](row)


def prepare_columns(df, prev_day_ohlc=None):
    """Adds every extra column any strategy might need, once, regardless of
    which strategy ends up active. df must already have gone through
    signal_engine.compute_indicators() (EMA9/20/ADX/bull_cross/bear_cross).

    prev_day_ohlc: {'high','low','close'} for the previous COMPLETE trading
    day, used for the pivot point strategy. Live usage passes this in
    (fetched once per day via option_selector.get_previous_trading_day_ohlc).
    Backtest usage instead computes a per-day pivot column separately (see
    backtest.add_pivot_column) since historical data spans many days.
    """
    df = df.copy()
    df["EMA50"] = _ema(df["close"], 50)
    df["bull_cross_prev"] = df["bull_cross"].shift(1).fillna(False)
    df["bear_cross_prev"] = df["bear_cross"].shift(1).fillna(False)
    df["prev_close"] = df["close"].shift(1)

    if prev_day_ohlc is not None:
        pp = (prev_day_ohlc["high"] + prev_day_ohlc["low"] + prev_day_ohlc["close"]) / 3
        df["pivot_pp"] = pp
    elif "pivot_pp" not in df.columns:
        # backtest path already computed a real per-day pivot_pp column via
        # backtest.add_pivot_column() before calling this -- don't clobber it
        df["pivot_pp"] = None

    for params in UT_BOT_VARIANTS.values():
        col = _ut_bot_column(params["key_value"], params["atr_period"])
        stop = compute_ut_bot_trailing_stop(df, params["key_value"], params["atr_period"])
        df[col] = stop
        df[col + "_prev"] = stop.shift(1)

    return df
