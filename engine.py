"""
engine.py
Long-running backend for the semi-automatic paper trading dashboard.

Every ENGINE_POLL_INTERVAL_SECONDS it:
  1. Manages the single open position (if any): polls live premium, updates
     the trailing stop, and exits on SL/TSL/target/EOD square-off.
  2. Otherwise scans Nifty/BankNifty/Sensex for a new signal, using
     whichever strategy is currently active (strategies.py -- switchable
     live from the dashboard dropdown, no restart needed) and, if one
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
import strategies
import notifications
from signal_engine import compute_indicators, confirm_with_pcr
from option_selector import (
    get_access_token, get_atm_option, get_intraday_candles, get_live_ltp,
    get_nearest_weekly_expiry, get_option_chain, compute_pcr, get_quotes,
    get_previous_trading_day_ohlc, get_orb_ladder,
)

IST = ZoneInfo("Asia/Kolkata")


def now_ist():
    return datetime.datetime.now(IST)


def get_headers():
    return {"Accept": "application/json", "Authorization": f"Bearer {get_access_token()}"}


def log(event_type, message):
    """Print (for the console/Monitor) AND persist to activity_log so the
    dashboard can show a running log of what the engine has done, not just
    its current snapshot state."""
    print(message)
    state_store.log_event(event_type, message)


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
    qty = state_store.get_qty(instrument)
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
        log("EXIT", f"EXIT {exit_reason}: {position['instrument']} {position['direction']} "
                     f"{position['strike']} net_pnl={net_pnl:.2f}")

        try:
            closed = state_store.get_position_by_id(position["id"])
            trade_export.append_closed_trade(closed)
        except Exception as e:
            print(f"WARNING: failed to append closed trade to Excel backup: {e}")

        try:
            notifications.notify_exit(
                position["instrument"], position["direction"], position["strike"],
                exit_net, exit_reason, net_pnl, qty,
            )
        except Exception as e:
            print(f"WARNING: exit notification failed: {e}")
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

def get_cached_prev_day_ohlc(instrument_states, name, cfg, headers):
    """Previous trading day's H/L/C, needed by the pivot-point strategy.
    Fetched once per calendar day per instrument and cached -- it doesn't
    change intraday, so no need to hit the API every poll cycle."""
    state = instrument_states[name]
    today = datetime.date.today()
    if state.get("pivot_date") == today and state.get("prev_day_ohlc") is not None:
        return state["prev_day_ohlc"]
    try:
        ohlc = get_previous_trading_day_ohlc(cfg["spot_instrument_key"], headers)
        state["prev_day_ohlc"] = ohlc
        state["pivot_date"] = today
        return ohlc
    except Exception as e:
        print(f"{name}: failed to fetch previous-day OHLC for pivot point: {e}")
        return state.get("prev_day_ohlc")  # fall back to yesterday's cached value if any


def refresh_orb_ladder_if_needed(headers, today):
    """Read-only ORB Strike Mapper refresh (see option_selector.get_orb_ladder
    for the full derivation and its dimensional-mismatch caveat). Computes
    once per day, after 9:20 IST, and caches -- completely separate from
    scan_for_signal()/manage_open_position(): it never reads a position,
    never writes one, and its output (state_store.orb_levels) is read
    ONLY by the dashboard display. A failure here is swallowed and logged,
    never allowed to interrupt the main trading loop."""
    settings = state_store.get_orb_settings()
    if settings["selected_strike"] is None:
        return  # user hasn't picked a strike from the dashboard yet
    if state_store.get_orb_levels(today, settings["instrument"]):
        return  # already computed today

    if now_ist().time() < datetime.time(9, 20):
        return  # today's opening-range candle hasn't closed yet

    cfg = config.INSTRUMENTS.get(settings["instrument"])
    if cfg is None:
        return
    try:
        expiry = get_nearest_weekly_expiry(headers, instrument_key=cfg["spot_instrument_key"])
        ladder = get_orb_ladder(
            cfg["spot_instrument_key"], expiry, headers,
            settings["selected_strike"], settings["selected_type"], settings["view"],
            settings["itm_count"], cfg["strike_step"],
        )
        state_store.store_orb_levels(today, settings["instrument"], ladder)
        log("ORB", f"ORB Strike Mapper levels locked: {settings['instrument']} "
                    f"{settings['selected_type']} {settings['selected_strike']} (view={settings['view']})")
    except Exception as e:
        print(f"ERROR refreshing ORB ladder: {e}")


def scan_for_signal(instrument_states, headers, today):
    """Look for a new candle-close signal on each flat instrument, in turn,
    using whichever strategy is currently active (dashboard-selectable).
    Only one instrument's signal is proposed per cycle (single-position
    design) -- the rest are simply reconsidered next cycle if still flat.
    If auto-confirm is on, the freshly-created pending signal is confirmed
    immediately via the exact same handle_confirm() path a manual click
    uses -- no separate "auto-entry" logic, so it can never diverge from
    what a manual confirm does."""
    active_strategy = state_store.get_active_strategy()
    active_timeframe = state_store.get_active_timeframe()

    for name, cfg in config.INSTRUMENTS.items():
        state = instrument_states[name]
        try:
            candles = get_intraday_candles(cfg["spot_instrument_key"], headers, interval_minutes=active_timeframe)
        except Exception as e:
            print(f"{name}: failed to fetch candles: {e}")
            continue

        if len(candles) < 25:
            continue

        df = compute_indicators(candles)
        prev_day_ohlc = get_cached_prev_day_ohlc(instrument_states, name, cfg, headers)
        df = strategies.prepare_columns(df, prev_day_ohlc=prev_day_ohlc)
        latest = df.iloc[-1]
        if state["last_candle_ts"] == latest["timestamp"]:
            continue
        state["last_candle_ts"] = latest["timestamp"]

        signal = strategies.get_signal_for_strategy(active_strategy, latest)
        if signal not in ("CE", "PE"):
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

        qty = state_store.get_qty(name)
        signal_id = state_store.create_pending_signal(
            name, signal, opt["strike"], opt["expiry"], opt["instrument_key"],
            opt["ltp"], qty, config.TIMING["PENDING_SIGNAL_TTL_SECONDS"])
        log("SIGNAL", f"SIGNAL ({active_strategy}, {active_timeframe}min): {name} {signal} "
                       f"strike={opt['strike']} ltp={opt['ltp']} -- awaiting confirmation")

        if state_store.get_auto_confirm():
            log("AUTO_CONFIRM", f"Auto-confirm is ON -- confirming signal {signal_id} immediately.")
            handle_confirm(signal_id, headers, today)

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
        log("BREAKER", f"CUMULATIVE DRAWDOWN BREAKER TRIPPED: drawdown {stats['drawdown']:.2f} "
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
        log("REJECTED", f"Confirm rejected for signal {signal_id}: daily circuit breaker is tripped")
        return

    if check_cumulative_drawdown_breaker():
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        log("REJECTED", f"Confirm rejected for signal {signal_id}: cumulative drawdown breaker is tripped")
        return

    if state_store.get_today_trade_count(today) >= config.RISK["MAX_TRADES_PER_DAY"]:
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        log("REJECTED", f"Confirm rejected for signal {signal_id}: daily trade cap reached")
        return

    if state_store.get_consecutive_losses(today) >= config.RISK["MAX_CONSECUTIVE_LOSSES"]:
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        log("REJECTED", f"Confirm rejected for signal {signal_id}: consecutive-loss cooldown active")
        return

    if state_store.get_open_position():
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        log("REJECTED", f"Confirm rejected for signal {signal_id}: a position is already open")
        return

    raw_ltp = get_test_or_live_ltp(sig["instrument_key"], headers)
    sl_points = compute_sl_points(sig["instrument"], raw_ltp)
    if sl_points is None:
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        log("REJECTED", f"Confirm rejected for signal {signal_id}: risk budget can't support a safe "
                         f"stop at premium {raw_ltp}")
        return

    entry_net = cost_model.apply_slippage(raw_ltp, "BUY")
    initial_sl = entry_net - sl_points
    target_price = entry_net + config.INSTRUMENTS[sig["instrument"]]["target_points"]

    new_position_id = state_store.open_position(
        sig["instrument"], sig["direction"], sig["strike"], sig["expiry"],
        sig["instrument_key"], sig["qty"], raw_ltp, entry_net, initial_sl, target_price)
    if new_position_id is None:
        # The earlier get_open_position() check above passed, but another
        # engine loop (e.g. a second process against the same DB) won the
        # atomic insert first -- open_position() is the real, race-proof
        # guard; this check was just a cheap early-exit. Treat exactly
        # like the early rejection above, not as a silent no-op.
        state_store.set_pending_signal_status(signal_id, "REJECTED")
        log("REJECTED", f"Confirm rejected for signal {signal_id}: a position is already open "
                         f"(lost the race to another engine instance)")
        return
    state_store.set_pending_signal_status(signal_id, "CONFIRMED")
    log("ENTRY", f"ENTRY: {sig['instrument']} {sig['direction']} {sig['strike']} @ net {entry_net:.2f} "
                  f"(raw {raw_ltp:.2f}) SL={initial_sl:.2f} target={target_price:.2f}")

    try:
        notifications.notify_entry(
            sig["instrument"], sig["direction"], sig["strike"],
            entry_net, initial_sl, target_price, sig["qty"],
        )
    except Exception as e:
        print(f"WARNING: entry notification failed: {e}")


def process_control_requests(headers, today):
    for req in state_store.get_unhandled_control_requests():
        try:
            if req["kind"] == "CONFIRM_SIGNAL":
                handle_confirm(req["payload"]["signal_id"], headers, today)
            elif req["kind"] == "REJECT_SIGNAL":
                state_store.set_pending_signal_status(req["payload"]["signal_id"], "REJECTED")
                log("REJECTED", f"Signal {req['payload']['signal_id']} rejected by user.")
            elif req["kind"] == "RESET_BREAKER":
                state_store.reset_circuit_breaker(today)
                log("BREAKER", "Daily circuit breaker manually reset (testing mode).")
            elif req["kind"] == "RESET_CUMULATIVE_BREAKER":
                state_store.reset_cumulative_breaker()
                log("BREAKER", "Cumulative drawdown breaker manually reset.")
            elif req["kind"] == "SET_STRATEGY":
                new_strategy = req["payload"]["strategy"]
                state_store.set_active_strategy(new_strategy)
                label = strategies.STRATEGIES.get(new_strategy, {}).get("label", new_strategy)
                log("STRATEGY", f"Active strategy switched to {new_strategy}: {label}")
            elif req["kind"] == "SET_TIMEFRAME":
                new_minutes = req["payload"]["minutes"]
                state_store.set_active_timeframe(new_minutes)
                log("TIMEFRAME", f"Active candle timeframe switched to {new_minutes}min "
                                  f"(UNTESTED at this timeframe -- backtests only cover 15min)")
            elif req["kind"] == "SET_AUTO_CONFIRM":
                enabled = req["payload"]["enabled"]
                state_store.set_auto_confirm(enabled)
                state_text = "ON -- new signals will open a paper position immediately, no manual review" \
                    if enabled else "OFF -- back to manual confirm for every signal"
                log("AUTO_CONFIRM", f"Auto-confirm switched {state_text}")
            elif req["kind"] == "SET_QTY":
                instrument = req["payload"]["instrument"]
                new_qty = req["payload"]["qty"]
                state_store.set_qty(instrument, new_qty)
                log("QTY", f"Order quantity for {instrument} switched to {new_qty} "
                            f"(applies to NEW signals only, not the open position if any)")
            elif req["kind"] == "SET_ORB_SETTINGS":
                p = req["payload"]
                old_instrument = state_store.get_orb_settings()["instrument"]
                state_store.set_orb_settings(p["instrument"], p["view"], p["selected_type"],
                                              p["selected_strike"], p["itm_count"])
                # Invalidate today's cache (both the old and new instrument,
                # in case the instrument itself changed) so the next cycle
                # recomputes under the new settings instead of reusing a
                # stale ladder from before the change.
                state_store.clear_orb_levels(today, old_instrument)
                state_store.clear_orb_levels(today, p["instrument"])
                log("ORB", f"ORB Mapper settings updated: {p['instrument']} {p['selected_type']} "
                            f"{p['selected_strike']} view={p['view']} itm={p['itm_count']} "
                            f"(will recompute at/after next 9:20)")
        except Exception as e:
            print(f"ERROR handling control request {req['id']} ({req['kind']}): {e}")
        state_store.mark_control_request_handled(req["id"])


# --------------------------------------------------------------- main loop

def main():
    """Runs forever (idles outside market hours, resumes each session) so it
    works equally as a standalone process or as a background thread inside
    a long-lived host process (e.g. Streamlit Community Cloud, which has no
    separate worker process -- see ensure_background_thread())."""
    log("LIFECYCLE", f"Trading engine started. IST time: {now_ist()}")

    existing_heartbeat = state_store.get_heartbeat()
    if existing_heartbeat and existing_heartbeat.get("pid") not in (None, os.getpid()):
        last_poll = existing_heartbeat.get("last_poll_at")
        if last_poll:
            age_seconds = (datetime.datetime.now() - datetime.datetime.fromisoformat(last_poll)).total_seconds()
            if age_seconds < config.TIMING["ENGINE_POLL_INTERVAL_SECONDS"] * 3:
                log("LIFECYCLE",
                    f"WARNING: another engine instance (PID {existing_heartbeat['pid']}) polled "
                    f"{age_seconds:.0f}s ago against this same trading_state.db -- running two "
                    f"engine loops at once causes duplicate signals/log spam (open_position() "
                    f"itself is now race-proof, so this can no longer duplicate positions, but "
                    f"it's still wasteful). Stop the other dashboard/app process if unintended.")

    instrument_states = {name: {"last_candle_ts": None} for name in config.INSTRUMENTS}
    poll_seconds = config.TIMING["ENGINE_POLL_INTERVAL_SECONDS"]
    market_was_open = None  # tracks transitions so status messages don't spam every cycle
    trade_cap_logged_for = None       # date string, so the MAX_TRADES_PER_DAY block logs once/day
    loss_cooldown_logged_for = None   # date string, so the MAX_CONSECUTIVE_LOSSES block logs once/day

    while True:
        now = now_ist()
        current_time = now.time()
        today = datetime.date.today().isoformat()

        state_store.update_heartbeat(os.getpid())

        # Settings toggles (auto-confirm, strategy, timeframe, breaker resets) are
        # user-safety controls, not trading actions -- process them every cycle
        # regardless of market hours so an off-hours click (e.g. turning
        # auto-confirm off before tomorrow's open) takes effect immediately
        # instead of silently queuing until the market reopens.
        try:
            process_control_requests(None, today)
        except Exception as e:
            print(f"ERROR processing control requests: {e}")

        market_open_now = config.TIMING["MARKET_OPEN"] <= current_time <= config.TIMING["MARKET_CLOSE"]
        if not market_open_now:
            if market_was_open is not False:
                if current_time < config.TIMING["MARKET_OPEN"]:
                    log("LIFECYCLE", f"Market not open yet ({current_time}). Waiting...")
                else:
                    log("LIFECYCLE", "Market closed for the day. Waiting for next session...")
            market_was_open = False
            time.sleep(poll_seconds)
            continue
        market_was_open = True

        headers = get_headers()

        try:
            refresh_spot_quotes(headers)
            state_store.expire_stale_pending_signals()

            # Read-only informational panel -- isolated in its own
            # try/except so a failure here (e.g. a bad expiry lookup)
            # can never interrupt position management or signal scanning.
            try:
                refresh_orb_ladder_if_needed(headers, today)
            except Exception as e:
                print(f"ERROR in ORB ladder refresh: {e}")

            open_position = state_store.get_open_position()
            if open_position:
                manage_open_position(open_position, headers, current_time)
            else:
                summary = state_store.recompute_daily_summary(today, config.RISK["DAILY_LOSS_CAP"])
                pending = state_store.get_pending_signal()
                cumulative_tripped = check_cumulative_drawdown_breaker()

                trade_count_today = state_store.get_today_trade_count(today)
                trade_cap_hit = trade_count_today >= config.RISK["MAX_TRADES_PER_DAY"]
                if trade_cap_hit and trade_cap_logged_for != today:
                    log("BREAKER", f"Daily trade cap reached ({trade_count_today}/"
                                    f"{config.RISK['MAX_TRADES_PER_DAY']}) -- no new entries until tomorrow.")
                    trade_cap_logged_for = today

                consecutive_losses = state_store.get_consecutive_losses(today)
                loss_cooldown_hit = consecutive_losses >= config.RISK["MAX_CONSECUTIVE_LOSSES"]
                if loss_cooldown_hit and loss_cooldown_logged_for != today:
                    log("BREAKER", f"{consecutive_losses} consecutive losing trades today -- "
                                    f"pausing new entries for the rest of the day (cooldown).")
                    loss_cooldown_logged_for = today

                if (not pending and not summary["circuit_breaker_tripped"] and not cumulative_tripped
                        and not trade_cap_hit and not loss_cooldown_hit
                        and current_time <= config.TIMING["LAST_ENTRY_TIME"]):
                    scan_for_signal(instrument_states, headers, today)
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
