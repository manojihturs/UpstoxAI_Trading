"""
engine.py
Long-running backend for the semi-automatic paper trading dashboard.

Every ENGINE_POLL_INTERVAL_SECONDS it:
  1. Manages the single open position (if any): polls live premium, updates
     the trailing stop, and exits on SL/TSL/target/EOD square-off.
  2. Otherwise scans Nifty/BankNifty/Sensex for a new EMA/ADX signal
     (signal_engine.py -- same code path as paper_trader.py) and, if one
     fires, writes a PENDING signal for the dashboard to show. It does NOT
     open a position on its own -- that requires a user confirmation.
  3. Picks up user confirm/reject/reset requests written by dashboard.py.
  4. Recomputes today's P&L and the daily-loss circuit breaker.

No real orders are ever placed here -- paper only. Run this as a background
process; dashboard.py launches it automatically if it isn't already running.
"""
import os
import sys
import time
import json
import threading
import datetime
from zoneinfo import ZoneInfo

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import state_store
import cost_model
import trade_export
from signal_engine import compute_indicators, get_signal, confirm_with_pcr, confirm_with_trend_filter, _ema
from option_selector import (
    get_access_token, get_atm_option, get_intraday_candles, get_live_ltp,
    get_nearest_weekly_expiry, get_option_chain, compute_pcr, get_quotes,
)

IST = ZoneInfo("Asia/Kolkata")


def now_ist():
    return datetime.datetime.now(IST)


def get_headers():
    return {"Accept": "application/json", "Authorization": f"Bearer {get_access_token()}"}


# ---------------------------------------------------------------- test mode

def _read_test_override():
    path = config.TEST_LTP_OVERRIDE_FILE
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _write_test_override(data):
    with open(config.TEST_LTP_OVERRIDE_FILE, "w") as f:
        json.dump(data, f)


def get_test_or_live_ltp(instrument_key, headers):
    """If test_ltp_override.json exists with a non-empty 'prices' list, pop
    and return the next scripted price instead of calling the live Upstox
    API. Lets SL/TSL/target/circuit-breaker logic be exercised end-to-end
    without waiting on the real market. See config.TEST_LTP_OVERRIDE_FILE."""
    override = _read_test_override()
    if override is not None:
        prices = override.get("prices", [])
        if prices:
            price = prices.pop(0)
            _write_test_override(override)
            print(f"[TEST MODE] scripted LTP {price} for {instrument_key}")
            return price
    return get_live_ltp(instrument_key, headers)


# ------------------------------------------------------------- SL sizing

def compute_sl_points(instrument, entry_premium_raw):
    """Size the stop so a full SL hit stays within this trade's slice of the
    daily loss budget, net of estimated costs, but never tighter than the
    instrument's noise floor. Returns None if the budget can't support even
    the floor -- caller should reject the trade rather than exceed budget."""
    cfg = config.INSTRUMENTS[instrument]
    qty = cfg["lot_size"]
    budget_per_trade = config.RISK["DAILY_LOSS_CAP"] / config.RISK["MAX_TRADES_BUDGET_DIVISOR"]
    est_costs = cost_model.estimate_round_trip_costs(entry_premium_raw, qty, cfg["exchange"])
    budget_after_costs = budget_per_trade - est_costs
    if budget_after_costs <= 0:
        return None
    budget_sl_points = budget_after_costs / qty
    pct_sl_points = entry_premium_raw * cfg["sl_pct"]
    sl_points = min(budget_sl_points, pct_sl_points)
    if sl_points < cfg["min_sl_points_floor"]:
        return None
    return sl_points


# --------------------------------------------------------- position logic

def evaluate_position(position, current_ltp, current_time, instrument_cfg):
    """Pure function, no I/O: given a position row, a fresh LTP, and the
    current wall-clock time, return (new_current_sl, new_tsl_armed,
    exit_reason). exit_reason is None if the position should stay open."""
    entry = position["entry_ltp_net"]
    current_sl = position["current_sl"]
    tsl_armed = bool(position["tsl_armed"])

    favorable_move = current_ltp - entry
    if not tsl_armed and favorable_move >= instrument_cfg["tsl_activation_points"]:
        tsl_armed = True
    if tsl_armed:
        candidate = current_ltp - instrument_cfg["tsl_trail_points"]
        if candidate > current_sl + instrument_cfg["tsl_step_points"]:
            current_sl = candidate

    exit_reason = None
    if current_time >= config.TIMING["SQUARE_OFF_TIME"]:
        exit_reason = "EOD_SQUAREOFF"
    elif current_ltp <= current_sl:
        exit_reason = "TSL" if tsl_armed else "SL"
    elif current_ltp >= position["target_price"]:
        exit_reason = "TARGET"

    return current_sl, tsl_armed, exit_reason


def manage_open_position(position, headers, current_time):
    raw_ltp = get_test_or_live_ltp(position["instrument_key"], headers)
    instrument_cfg = config.INSTRUMENTS[position["instrument"]]
    new_sl, new_tsl_armed, exit_reason = evaluate_position(position, raw_ltp, current_time, instrument_cfg)

    state_store.update_position_last_seen(position["id"], raw_ltp)
    if new_sl != position["current_sl"] or new_tsl_armed != bool(position["tsl_armed"]):
        state_store.update_position_trailing_sl(position["id"], new_sl, new_tsl_armed)

    if exit_reason:
        exit_net = cost_model.apply_slippage(raw_ltp, "SELL")
        qty = position["qty"]
        gross_pnl = (exit_net - position["entry_ltp_net"]) * qty
        costs_total = cost_model.compute_round_trip_costs(
            position["entry_ltp_net"], exit_net, qty, instrument_cfg["exchange"]
        )
        net_pnl = gross_pnl - costs_total
        state_store.close_position(position["id"], raw_ltp, exit_net, exit_reason,
                                    gross_pnl, costs_total, net_pnl)
        print(f"EXIT {exit_reason}: {position['instrument']} {position['direction']} "
              f"{position['strike']} net_pnl={net_pnl:.2f}")

        try:
            closed = state_store.get_position_by_id(position["id"])
            trade_export.append_closed_trade(closed)
        except Exception as e:
            print(f"WARNING: failed to append closed trade to Excel backup: {e}")
    else:
        print(f"Holding {position['instrument']} {position['direction']} {position['strike']} | "
              f"live={raw_ltp:.2f} sl={new_sl:.2f} tsl_armed={new_tsl_armed}")


# -------------------------------------------------------------- spot quotes

def refresh_spot_quotes(headers):
    """Fetch live LTP + %change for all three indices in one batched call
    and persist them, so the dashboard can show them without making its own
    API calls. Runs every poll cycle regardless of position/signal state."""
    keys = [cfg["spot_instrument_key"] for cfg in config.INSTRUMENTS.values()]
    try:
        quotes = get_quotes(keys, headers)
    except Exception as e:
        print(f"Spot quote refresh failed: {e}")
        return
    for name, cfg in config.INSTRUMENTS.items():
        q = quotes.get(cfg["spot_instrument_key"])
        if q is None:
            continue
        state_store.update_spot_quote(name, q["last_price"], q["net_change"], q["pct_change"])


# ------------------------------------------------------------- signal scan

def scan_for_signal(instrument_states, headers):
    """Look for a new candle-close signal on each flat instrument, in turn.
    Only one instrument's signal is proposed per cycle (single-position
    design) -- the rest are simply reconsidered next cycle if still flat."""
    for name, cfg in config.INSTRUMENTS.items():
        state = instrument_states[name]
        try:
            candles = get_intraday_candles(cfg["spot_instrument_key"], headers, interval_minutes=15)
        except Exception as e:
            print(f"{name}: failed to fetch candles: {e}")
            continue

        if len(candles) < 25:
            continue

        df = compute_indicators(candles)
        if config.STRATEGY["ENABLE_TREND_FILTER"]:
            ema_period = config.STRATEGY["TREND_FILTER_EMA_PERIOD"]
            df[f"EMA{ema_period}"] = _ema(df["close"], ema_period)
        latest = df.iloc[-1]
        if state["last_candle_ts"] == latest["timestamp"]:
            continue
        state["last_candle_ts"] = latest["timestamp"]

        signal = get_signal(latest)
        if signal not in ("CE", "PE"):
            continue

        if config.STRATEGY["ENABLE_TREND_FILTER"]:
            ema_period = config.STRATEGY["TREND_FILTER_EMA_PERIOD"]
            if not confirm_with_trend_filter(signal, latest["close"], latest[f"EMA{ema_period}"]):
                print(f"{name}: {signal} signal rejected by trend filter (close vs EMA{ema_period})")
                continue

        if config.PCR["ENABLE_PCR_CONFIRMATION"]:
            try:
                expiry = get_nearest_weekly_expiry(headers, instrument_key=cfg["spot_instrument_key"])
                chain = get_option_chain(expiry, headers, instrument_key=cfg["spot_instrument_key"])
                pcr = compute_pcr(chain)
            except Exception as e:
                print(f"{name}: PCR lookup failed ({e}), skipping PCR confirmation this cycle")
                pcr = None
            if pcr is not None and not confirm_with_pcr(signal, pcr, config.PCR["BULLISH_MIN"], config.PCR["BEARISH_MAX"]):
                print(f"{name}: {signal} signal rejected by PCR confirmation (pcr={pcr:.2f})")
                continue

        try:
            opt = get_atm_option(signal, headers, instrument_key=cfg["spot_instrument_key"],
                                  strike_step=cfg["strike_step"])
        except Exception as e:
            print(f"{name}: failed to fetch ATM option: {e}")
            continue

        qty = cfg["lot_size"]
        state_store.create_pending_signal(name, signal, opt["strike"], opt["expiry"], opt["instrument_key"],
                                           opt["ltp"], qty, config.TIMING["PENDING_SIGNAL_TTL_SECONDS"])
        print(f"SIGNAL: {name} {signal} strike={opt['strike']} ltp={opt['ltp']} -- awaiting confirmation")
        return  # single-position design: stop scanning once one signal is proposed


# -------------------------------------------------------- control requests

def check_cumulative_drawdown_breaker():
    """If enabled, trips (persists) the cumulative breaker the first time
    all-time drawdown from its running peak exceeds MAX_CUMULATIVE_DRAWDOWN.
    Once tripped it stays tripped until a manual reset -- see config.py for
    why this doesn't auto-reset like the daily one. Returns True if new
    entries should be blocked right now."""
    if not config.RISK["ENABLE_CUMULATIVE_DRAWDOWN_BREAKER"]:
        return False
    state = state_store.get_risk_state()
    if state["cumulative_breaker_tripped"]:
        return True
    stats = state_store.get_cumulative_pnl_stats()
    if stats["drawdown"] <= -config.RISK["MAX_CUMULATIVE_DRAWDOWN"]:
        state_store.trip_cumulative_breaker()
        print(f"CUMULATIVE DRAWDOWN BREAKER TRIPPED: drawdown {stats['drawdown']:.2f} "
              f"breached -{config.RISK['MAX_CUMULATIVE_DRAWDOWN']}. Blocking new entries "
              f"until manually reset.")
        return True
    return False


def handle_confirm(signal_id, headers, today):
    sig = state_store.get_pending_signal_by_id(signal_id)
    if not sig or sig["status"] != "PENDING":
        return  # stale click on an already-resolved signal

    if datetime.datetime.fromisoformat(sig["expires_at"]) < datetime.datetime.now():
        state_store.set_pending_signal_status(signal_id, "EXPIRED")
        return

    summary = state_store.recompute_daily_summary(today, config.RISK["DAILY_LOSS_CAP"])
    if summary["circuit_breaker_tripped"]:
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        print(f"Confirm rejected for signal {signal_id}: daily circuit breaker is tripped")
        return

    if check_cumulative_drawdown_breaker():
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        print(f"Confirm rejected for signal {signal_id}: cumulative drawdown breaker is tripped")
        return

    if state_store.get_open_position():
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        print(f"Confirm rejected for signal {signal_id}: a position is already open")
        return

    raw_ltp = get_test_or_live_ltp(sig["instrument_key"], headers)
    sl_points = compute_sl_points(sig["instrument"], raw_ltp)
    if sl_points is None:
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        print(f"Confirm rejected for signal {signal_id}: risk budget can't support a safe "
              f"stop at premium {raw_ltp}")
        return

    entry_net = cost_model.apply_slippage(raw_ltp, "BUY")
    initial_sl = entry_net - sl_points
    target_price = entry_net + config.INSTRUMENTS[sig["instrument"]]["target_points"]

    state_store.open_position(sig["instrument"], sig["direction"], sig["strike"], sig["expiry"],
                               sig["instrument_key"], sig["qty"], raw_ltp, entry_net,
                               initial_sl, target_price)
    state_store.set_pending_signal_status(signal_id, "CONFIRMED")
    print(f"ENTRY: {sig['instrument']} {sig['direction']} {sig['strike']} @ net {entry_net:.2f} "
          f"(raw {raw_ltp:.2f}) SL={initial_sl:.2f} target={target_price:.2f}")


def process_control_requests(headers, today):
    for req in state_store.get_unhandled_control_requests():
        try:
            if req["kind"] == "CONFIRM_SIGNAL":
                handle_confirm(req["payload"]["signal_id"], headers, today)
            elif req["kind"] == "REJECT_SIGNAL":
                state_store.set_pending_signal_status(req["payload"]["signal_id"], "REJECTED")
            elif req["kind"] == "RESET_BREAKER":
                state_store.reset_circuit_breaker(today)
                print("Circuit breaker manually reset (testing mode).")
            elif req["kind"] == "RESET_CUMULATIVE_BREAKER":
                state_store.reset_cumulative_breaker()
                print("Cumulative drawdown breaker manually reset.")
        except Exception as e:
            print(f"ERROR handling control request {req['id']} ({req['kind']}): {e}")
        state_store.mark_control_request_handled(req["id"])


# --------------------------------------------------------------- main loop

def main():
    """Runs forever (idles outside market hours, resumes each session) so it
    works equally as a standalone process or as a background thread inside
    a long-lived host process (e.g. Streamlit Community Cloud, which has no
    separate worker process -- see ensure_background_thread())."""
    print("Trading engine started.")
    print(f"Local machine time: {datetime.datetime.now()}")
    print(f"IST time (used for market hours): {now_ist()}")

    instrument_states = {name: {"last_candle_ts": None} for name in config.INSTRUMENTS}
    poll_seconds = config.TIMING["ENGINE_POLL_INTERVAL_SECONDS"]
    market_was_open = None  # tracks transitions so status messages don't spam every cycle

    while True:
        now = now_ist()
        current_time = now.time()
        today = datetime.date.today().isoformat()

        state_store.update_heartbeat(os.getpid())

        market_open_now = config.TIMING["MARKET_OPEN"] <= current_time <= config.TIMING["MARKET_CLOSE"]
        if not market_open_now:
            if market_was_open is not False:
                if current_time < config.TIMING["MARKET_OPEN"]:
                    print(f"Market not open yet ({current_time}). Waiting...")
                else:
                    print("Market closed for the day. Waiting for next session...")
            market_was_open = False
            time.sleep(poll_seconds)
            continue
        market_was_open = True

        headers = get_headers()

        try:
            refresh_spot_quotes(headers)
            state_store.expire_stale_pending_signals()
            process_control_requests(headers, today)

            open_position = state_store.get_open_position()
            if open_position:
                manage_open_position(open_position, headers, current_time)
            else:
                summary = state_store.recompute_daily_summary(today, config.RISK["DAILY_LOSS_CAP"])
                pending = state_store.get_pending_signal()
                cumulative_tripped = check_cumulative_drawdown_breaker()
                if (not pending and not summary["circuit_breaker_tripped"] and not cumulative_tripped
                        and current_time <= config.TIMING["LAST_ENTRY_TIME"]):
                    scan_for_signal(instrument_states, headers)
        except Exception as e:
            print(f"ERROR in engine loop: {e}")

        time.sleep(poll_seconds)


_background_thread_lock = threading.Lock()
_background_thread_started = False


def ensure_background_thread():
    """Start the engine loop in a background daemon thread, exactly once
    per process. Safe to call on every Streamlit rerun -- a no-op once the
    thread is already running. This is how dashboard.py keeps the engine
    alive on hosts (like Streamlit Community Cloud) that only run a single
    process with no separate worker."""
    global _background_thread_started
    with _background_thread_lock:
        if _background_thread_started:
            return
        threading.Thread(target=main, daemon=True, name="engine-loop").start()
        _background_thread_started = True


if __name__ == "__main__":
    main()
