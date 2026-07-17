"""
config.py
Single source of truth for instruments, risk budget, cost assumptions,
SL/TSL parameters, timing, and paths shared by engine.py, dashboard.py,
cost_model.py, and state_store.py.
"""
import os
from signal_engine import LAST_ENTRY_TIME, SQUARE_OFF_TIME, MARKET_OPEN, MARKET_CLOSE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- Instruments ----
# Lot sizes / strike steps are approximate as of 2025 and are revised
# periodically by NSE/BSE contract-size circulars. This file is NOT a
# source of truth for them -- verify against the current exchange circular
# before relying on these for anything beyond paper trading.
INSTRUMENTS = {
    "NIFTY": {
        "spot_instrument_key": "NSE_INDEX|Nifty 50",
        "strike_step": 50,
        "lot_size": 75,
        "exchange": "NSE",
        "sl_pct": 0.30,
        "target_points": 10,
        "tsl_activation_points": 5,
        "tsl_trail_points": 4,
        "tsl_step_points": 1,
        "min_sl_points_floor": 3,
    },
    "BANKNIFTY": {
        "spot_instrument_key": "NSE_INDEX|Nifty Bank",
        "strike_step": 100,
        "lot_size": 30,
        "exchange": "NSE",
        "sl_pct": 0.30,
        "target_points": 15,
        "tsl_activation_points": 8,
        "tsl_trail_points": 6,
        "tsl_step_points": 1,
        "min_sl_points_floor": 4,
    },
    "SENSEX": {
        "spot_instrument_key": "BSE_INDEX|SENSEX",
        "strike_step": 100,
        "lot_size": 20,
        "exchange": "BSE",
        "sl_pct": 0.30,
        "target_points": 20,
        "tsl_activation_points": 10,
        "tsl_trail_points": 8,
        "tsl_step_points": 1,
        "min_sl_points_floor": 5,
    },
}

# ---- Risk ----
RISK = {
    "DAILY_LOSS_CAP": 2000,           # rupees; soft stop -- see engine.py circuit breaker
    "CAPITAL": 50000,                 # rupees; informational only, not enforced as margin here
    "MAX_TRADES_BUDGET_DIVISOR": 4,   # sub-divides DAILY_LOSS_CAP across potential trades/day
    "SINGLE_POSITION_ONLY": True,     # only one open position across all instruments at a time

    # Off by default. The daily cap above only limits loss WITHIN a single
    # day and resets every morning -- it does nothing to stop a losing
    # streak that compounds across many days/weeks (backtest.py found a
    # ~1-year stretch where cumulative P&L stayed below -Rs 50,000, the
    # full stated capital, well before it eventually recovered). This
    # breaker tracks all-time cumulative P&L vs. its running peak and
    # blocks ALL new entries once the drawdown from that peak exceeds
    # MAX_CUMULATIVE_DRAWDOWN. Unlike the daily breaker it does NOT
    # auto-reset -- once tripped it requires a manual reset, since the
    # point is to force a real pause, not silently resume on a small uptick.
    "ENABLE_CUMULATIVE_DRAWDOWN_BREAKER": True,
    "MAX_CUMULATIVE_DRAWDOWN": 15000,  # rupees; 30% of CAPITAL above
}

# ---- Auto-confirm ----
# Off by default -- this is the core safety design of the whole app: a
# signal proposes, a human confirms, THEN it becomes a real (paper)
# position. Turning this on removes that human check entirely -- every
# signal that fires opens a position immediately and automatically, with
# no review window. Live-switchable from the dashboard (a toggle, not
# buried in code) via state_store.get/set_auto_confirm(), same pattern as
# the strategy/timeframe dropdowns.
AUTO_CONFIRM = {
    "ENABLED": False,
}

# ---- Costs (retail discount-broker assumptions; approximate, tune as needed) ----
COSTS = {
    "BROKERAGE_FLAT": 20.0,        # rupees per executed order
    "BROKERAGE_PCT": 0.0005,       # 0.05% of turnover -- brokerage is whichever is LOWER
    "STT_SELL_PCT": 0.001,         # options STT, sell/exit leg only (approx)
    "EXCHANGE_TXN_PCT": {
        "NSE": 0.0003503,          # NSE options exchange transaction charge (approx)
        "BSE": 0.0000375,          # BSE options exchange transaction charge (approx)
    },
    "GST_PCT": 0.18,                # on (brokerage + exchange transaction charges)
    "SEBI_PCT": 0.0000001,          # SEBI turnover charges (~Rs 10 per crore)
    "STAMP_DUTY_BUY_PCT": 0.00003,  # buy/entry leg only
    "SLIPPAGE_PCT": 0.005,          # adverse fill vs last-seen LTP, applied on each leg
}

# ---- PCR / sentiment confirmation ----
# Optional extra filter layered on the EMA/ADX trend signal, using OI already
# present in the option-chain response fetched for ATM strike selection --
# no separate or paid data source. Off by default to keep Phase 1 simple;
# flip on once the base EMA/ADX signal has a baseline track record.
PCR = {
    "ENABLE_PCR_CONFIRMATION": False,
    "BULLISH_MIN": 1.1,   # PCR >= this confirms CE (bullish)
    "BEARISH_MAX": 0.9,   # PCR <= this confirms PE (bearish)
}

# ---- Trend filter confirmation (signal_engine.confirm_with_trend_filter) ----
# "Variant C" from backtest_experiments.py: only take the EMA9/EMA20 cross if
# price also agrees with a longer EMA. The only variant tested there that
# improved results on both a training window and a held-out window it never
# saw -- still off by default so you can compare it against the baseline
# live before trusting it. Flip ENABLE_TREND_FILTER to True to turn it on;
# used identically by engine.py (live) and backtest.py (so they never diverge).
STRATEGY = {
    "ENABLE_TREND_FILTER": True,
    "TREND_FILTER_EMA_PERIOD": 50,
}

# ---- Candle timeframe ----
# Live-switchable from the dashboard (state_store.get/set_active_timeframe),
# same pattern as the strategy dropdown -- no restart needed. All 6 options
# verified directly against Upstox's intraday API before being listed here.
#
# IMPORTANT: every strategy's periods (EMA9/20/50, ADX14, pivot, UT Bot's
# ATR10/14) were chosen and backtested assuming 15-min candles. Switching
# timeframe does NOT rescale those periods -- EMA20 on 1-min candles is a
# 20-*minute* trend instead of a 5-*hour* one, a fundamentally different
# sensitivity, not just "the same strategy, checked more often." There is
# no backtest data for any timeframe other than 15-min in this repo
# (nifty50_15min.csv etc.) -- switching live to another timeframe is
# running genuinely untested behavior.
TIMEFRAME = {
    "AVAILABLE_MINUTES": [1, 3, 5, 15, 30, 60],
    "DEFAULT_MINUTES": 15,
}

# ---- Timing ----
TIMING = {
    "MARKET_OPEN": MARKET_OPEN,
    "MARKET_CLOSE": MARKET_CLOSE,
    "LAST_ENTRY_TIME": LAST_ENTRY_TIME,
    "SQUARE_OFF_TIME": SQUARE_OFF_TIME,
    "ENGINE_POLL_INTERVAL_SECONDS": 25,
    "PENDING_SIGNAL_TTL_SECONDS": 5 * 60,
}

# ---- Paths ----
PATHS = {
    "DB_PATH": os.path.join(BASE_DIR, "trading_state.db"),
    "TOKEN_FILE": os.path.join(BASE_DIR, "upstox_token.txt"),
}

# If present, engine.py replays this scripted LTP sequence for the open
# position's price polling instead of calling the live Upstox API, so
# SL/TSL/target/circuit-breaker behavior can be tested deterministically
# without waiting on the real market. See engine.py test-mode section.
TEST_LTP_OVERRIDE_FILE = os.path.join(BASE_DIR, "test_ltp_override.json")
