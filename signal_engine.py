"""
signal_engine.py
Reusable signal logic — SAME code path for backtest and live paper trading.
This is the core rule: if backtest and live use different logic, results can never be trusted.

No pandas_ta dependency (avoids numba version conflicts) — EMA/ADX/ATR computed directly
with pandas, matching standard indicator formulas.
"""
import pandas as pd
import numpy as np
import datetime

ADX_THRESHOLD = 12
LAST_ENTRY_TIME = datetime.time(14, 30)
SQUARE_OFF_TIME = datetime.time(15, 15)
MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 30)


def _ema(series, length):
    return series.ewm(span=length, adjust=False).mean()


def _atr(high, low, close, length=14):
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/length, adjust=False).mean()


def _adx(high, low, close, length=14):
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/length, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(alpha=1/length, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(alpha=1/length, adjust=False).mean() / atr

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1/length, adjust=False).mean()
    return adx


def compute_indicators(df):
    """
    df must have columns: timestamp, open, high, low, close
    Returns df with EMA9, EMA20, ADX, ATR, bull_cross, bear_cross added.
    """
    df = df.copy()
    df['EMA9'] = _ema(df['close'], 9)
    df['EMA20'] = _ema(df['close'], 20)
    df['ADX'] = _adx(df['high'], df['low'], df['close'], 14)
    df['ATR'] = _atr(df['high'], df['low'], df['close'], 14)
    df = df.dropna().reset_index(drop=True)

    df['ema_diff'] = df['EMA9'] - df['EMA20']
    df['prev_ema_diff'] = df['ema_diff'].shift(1)
    df['bull_cross'] = (df['prev_ema_diff'] <= 0) & (df['ema_diff'] > 0)
    df['bear_cross'] = (df['prev_ema_diff'] >= 0) & (df['ema_diff'] < 0)
    return df


def get_signal(latest_row):
    """
    Given the most recent indicator row, return 'CE', 'PE', or None.
    This is the single source of truth for entry decisions —
    called identically by backtest.py and paper_trader.py.
    """
    t = latest_row['timestamp'].time() if hasattr(latest_row['timestamp'], 'time') else latest_row['timestamp']
    if t > LAST_ENTRY_TIME:
        return None
    if latest_row['ADX'] <= ADX_THRESHOLD:
        return None
    if latest_row['bull_cross']:
        return 'CE'
    if latest_row['bear_cross']:
        return 'PE'
    return None


def confirm_with_pcr(direction, pcr, bullish_min, bearish_max):
    """
    Optional second opinion on top of get_signal(), using option-chain
    put/call OI ratio (free, comes from the same chain fetch used for ATM
    strike selection -- no extra data source).

    direction: 'CE' or 'PE', as returned by get_signal()
    pcr: put OI / call OI summed across the option chain
    Returns True if PCR agrees with the trend direction, False otherwise.
    """
    if direction == 'CE':
        return pcr >= bullish_min
    if direction == 'PE':
        return pcr <= bearish_max
    return False


def confirm_with_trend_filter(direction, close, ema_long):
    """
    Optional second opinion on top of get_signal(): only agree with the
    raw EMA9/EMA20 crossover if price is also on the correct side of a
    longer EMA (default period 50), filtering out counter-trend whipsaws.

    This is "Variant C" from backtest_experiments.py -- the only variant
    tested there that improved results on BOTH a training window and a
    held-out window it was never checked against, which is why it's the
    one wired up as a togglable option (config.STRATEGY) rather than the
    others. Still off by default; see config.py.

    direction: 'CE' or 'PE', as returned by get_signal()
    close: current close price
    ema_long: the longer-period EMA value (e.g. EMA50) at this candle
    Returns True if the trend filter agrees with the direction.
    """
    if direction == 'CE':
        return close > ema_long
    if direction == 'PE':
        return close < ema_long
    return False