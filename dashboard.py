"""
dashboard.py
Streamlit "front page" for semi-automatic paper trading.

This file only ever READS state_store (for display) and writes user-intent
rows into control_requests -- it never touches positions/daily_summary/
pending signal status directly. engine.py's main() loop is the only writer
of financial state; it runs in a background daemon thread started by this
file (ensure_background_thread(), started exactly once per process) and
picks up confirm/reject requests from here. That thread model is what lets
this run unmodified on a single-process host like Streamlit Community
Cloud, which has no separate worker process.

No real orders are placed anywhere in this app -- paper only.
"""
import os
import sys
import json
import datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import state_store
import engine
import strategies
import cost_model

st.set_page_config(page_title="Paper Trading Dashboard", layout="wide")


def check_password():
    """Simple password gate -- required before this dashboard is reachable
    from outside this machine (e.g. over Tailscale). Password lives in
    .streamlit/secrets.toml, which is gitignored and never committed."""
    if st.session_state.get("authenticated"):
        return True

    st.title("Paper Trading Dashboard")
    st.caption("Enter the dashboard password to continue.")
    pw = st.text_input("Password", type="password", key="login_pw")
    if st.button("Log in"):
        expected = st.secrets.get("dashboard_password")
        if expected and pw == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False


# Login is togglable via the 'require_login' secret so it can be turned
# back on later without a code change. Defaults to True (safe default) if
# the secret is unset, so a fresh deploy without this key stays protected.
if st.secrets.get("require_login", True) and not check_password():
    st.stop()

st_autorefresh(interval=5000, key="refresh")

engine.ensure_background_thread(app_name="UpstoxAItrading")  # no-op after the first call in this process

snapshot = state_store.get_dashboard_snapshot()
today_str = datetime.date.today().isoformat()

# ---------------------------------------------------------------- header
col_title, col_status = st.columns([4, 1])
with col_title:
    st.title("Paper Trading -- Nifty / BankNifty / Sensex")
    st.caption("Semi-automatic. Paper money only. No real orders are ever placed.")
with col_status:
    if snapshot["engine_alive"]:
        st.success("Engine: running")
    else:
        st.warning("Engine: starting...")

# ------------------------------------------------------------ strategy picker
st.subheader("Active Strategy")
strategy_keys = list(strategies.STRATEGIES.keys())
strategy_labels = [strategies.STRATEGIES[k]["label"] for k in strategy_keys]
current_strategy = snapshot["active_strategy"]
current_index = strategy_keys.index(current_strategy) if current_strategy in strategy_keys else 0

# A selectbox with a `key` remembers the user's last pick across every
# autorefresh rerun (Streamlit widget statefulness) -- comparing that
# remembered value against the live DB value on every rerun (as an earlier
# version of this did) means a stale browser tab keeps re-firing the SAME
# stale choice every 5s, fighting any out-of-band change (e.g. a manual
# reset). on_change fires only on an actual user interaction, not on mere
# reruns, so it can't fight external changes. Resync the widget's
# remembered value if the backend's active strategy changed underneath it
# (e.g. from another browser tab) so this tab doesn't keep showing a stale
# selection either.
pending_strategy = st.session_state.get("_pending_strategy")
if pending_strategy is not None and current_strategy == pending_strategy:
    # engine.py has now applied our own change -- stop suppressing resync
    st.session_state["_pending_strategy"] = None
    pending_strategy = None

# Skip resync while our own change is still in flight (pending_strategy set):
# engine.py only polls control_requests once per loop (~20-30s), so on the
# next ~5s autorefresh the DB would still show the OLD strategy and this
# would otherwise snap the dropdown back before the engine catches up,
# looking like the change silently failed.
if pending_strategy is None and st.session_state.get("_last_known_strategy") != current_strategy:
    st.session_state["strategy_select"] = strategy_labels[current_index]
    st.session_state["_last_known_strategy"] = current_strategy


def _on_strategy_change():
    chosen_label = st.session_state["strategy_select"]
    chosen_key = strategy_keys[strategy_labels.index(chosen_label)]
    state_store.create_control_request("SET_STRATEGY", {"strategy": chosen_key})
    st.session_state["_last_known_strategy"] = chosen_key
    st.session_state["_pending_strategy"] = chosen_key


st.selectbox(
    "Signal strategy engine.py uses for new entries (switches immediately, no restart needed):",
    strategy_labels, key="strategy_select", on_change=_on_strategy_change,
)
st.caption(
    "Changing this only affects NEW signals from now on -- an already-open position keeps "
    "running under whatever strategy proposed it. Backtest each option in "
    "backtest_experiments.py before trusting it; win rates vary a lot between them."
)

# ------------------------------------------------------------ timeframe picker
timeframe_options = config.TIMEFRAME["AVAILABLE_MINUTES"]
timeframe_labels = [f"{m} min" for m in timeframe_options]
current_timeframe = snapshot["active_timeframe"]
current_tf_index = timeframe_options.index(current_timeframe) if current_timeframe in timeframe_options else \
    timeframe_options.index(config.TIMEFRAME["DEFAULT_MINUTES"])

if st.session_state.get("_last_known_timeframe") != current_timeframe:
    st.session_state["timeframe_select"] = timeframe_labels[current_tf_index]
    st.session_state["_last_known_timeframe"] = current_timeframe


def _on_timeframe_change():
    chosen_label = st.session_state["timeframe_select"]
    chosen_minutes = timeframe_options[timeframe_labels.index(chosen_label)]
    state_store.create_control_request("SET_TIMEFRAME", {"minutes": chosen_minutes})
    st.session_state["_last_known_timeframe"] = chosen_minutes


st.selectbox(
    "Candle timeframe (switches immediately, no restart needed):",
    timeframe_labels, key="timeframe_select", on_change=_on_timeframe_change,
)
st.caption(
    "⚠️ Every strategy's periods (EMA9/20/50, ADX14, UT Bot's ATR10/14, etc.) were "
    "chosen and backtested assuming 15-min candles. Switching timeframe does NOT rescale "
    "them -- EMA20 on 1-min candles is a 20-*minute* trend, not the 5-*hour* one it was "
    "tuned for. There is no backtest data for any timeframe other than 15-min in this repo "
    "-- anything else is genuinely untested live behavior, not a validated variant."
)

# --------------------------------------------------------------- auto-confirm
current_auto_confirm = snapshot["auto_confirm"]

if st.session_state.get("_last_known_auto_confirm") != current_auto_confirm:
    st.session_state["auto_confirm_toggle"] = current_auto_confirm
    st.session_state["_last_known_auto_confirm"] = current_auto_confirm


def _on_auto_confirm_change():
    chosen = st.session_state["auto_confirm_toggle"]
    state_store.create_control_request("SET_AUTO_CONFIRM", {"enabled": chosen})
    st.session_state["_last_known_auto_confirm"] = chosen


st.toggle(
    "Auto-confirm signals (skip manual review)",
    key="auto_confirm_toggle", on_change=_on_auto_confirm_change,
)
if current_auto_confirm:
    st.error(
        "🔴 AUTO-CONFIRM IS ON. Every signal that fires opens a paper position immediately, "
        "with no review window -- the Confirm/Reject step you've been using is bypassed "
        "entirely. All the same risk controls (SL/TSL/daily cap/cumulative breaker) still "
        "apply once a position is open; what's gone is your chance to look at a signal "
        "before it becomes a trade."
    )
else:
    st.caption(
        "Auto-confirm is off right now -- signals will wait for your manual Confirm/Reject "
        "instead of opening automatically."
    )

st.divider()

# ------------------------------------------------------------ qty preference
st.subheader("Order Quantity")
st.caption(
    "Quantity used for each NEW signal, per instrument (switches immediately, no restart "
    "needed). One lot = "
    + ", ".join(f"{name} {cfg['lot_size']}" for name, cfg in config.INSTRUMENTS.items())
    + ". An already-open position keeps the quantity it was opened with."
)
current_qty_by_instrument = snapshot["qty_by_instrument"]
qty_cols = st.columns(len(config.INSTRUMENTS))
for col, name in zip(qty_cols, config.INSTRUMENTS):
    lot_size = config.INSTRUMENTS[name]["lot_size"]
    current_qty = current_qty_by_instrument[name]
    resync_key = f"_last_known_qty_{name}"
    widget_key = f"qty_input_{name}"

    if st.session_state.get(resync_key) != current_qty:
        st.session_state[widget_key] = current_qty
        st.session_state[resync_key] = current_qty

    def _make_on_qty_change(instrument, resync_key, widget_key):
        def _on_qty_change():
            chosen = st.session_state[widget_key]
            state_store.create_control_request("SET_QTY", {"instrument": instrument, "qty": chosen})
            st.session_state[resync_key] = chosen
        return _on_qty_change

    with col:
        st.number_input(
            name, min_value=lot_size, step=lot_size, key=widget_key,
            on_change=_make_on_qty_change(name, resync_key, widget_key),
            help=f"1 lot = {lot_size}. Enter a multiple of {lot_size}.",
        )

st.divider()

# ------------------------------------------------------------- spot quotes
quotes = snapshot["spot_quotes"]
q1, q2, q3 = st.columns(3)
for col, name in zip((q1, q2, q3), config.INSTRUMENTS.keys()):
    q = quotes.get(name)
    if q is None:
        col.metric(name, "waiting for engine...")
        continue
    col.metric(
        name,
        f"{q['last_price']:,.2f}",
        f"{q['net_change']:+,.2f} ({q['pct_change']:+.2f}%)",
    )
if quotes:
    latest_update = max(q["updated_at"] for q in quotes.values())
    st.caption(f"Index quotes as of {latest_update} (updates every engine poll cycle).")
else:
    st.caption("Index quotes will appear once the engine has completed its first poll.")

st.divider()

# --------------------------------------------------------- daily summary
# Realized P&L / trade count are computed live from closed_positions on
# every render (not read from the persisted daily_summary row), so the
# numbers stay accurate even between engine.py's own recompute ticks.
# circuit_breaker_tripped is the one field that IS read from daily_summary,
# since engine.py is the sole authority on when the breaker trips.
summary = snapshot["daily_summary"]
daily_cap = config.RISK["DAILY_LOSS_CAP"]
today_closed = [p for p in snapshot["closed_positions"] if (p["exit_time"] or "").startswith(today_str)]
realized_net_pnl = sum(p["net_pnl"] for p in today_closed)
trades_count = len(today_closed)
remaining_budget = daily_cap + realized_net_pnl  # net_pnl negative while losing

m1, m2, m3, m4 = st.columns(4)
m1.metric("Capital", f"Rs {config.RISK['CAPITAL']:,.0f}")
m2.metric("Today's Net P&L", f"Rs {realized_net_pnl:,.2f}")
m3.metric("Remaining Loss Budget", f"Rs {max(remaining_budget, 0):,.2f}", f"of Rs {daily_cap:,.0f} cap")
m4.metric("Trades Today", f"{trades_count} / {config.RISK['MAX_TRADES_PER_DAY']}")

if summary["circuit_breaker_tripped"]:
    st.error(
        f"TRADING HALTED -- daily loss cap of Rs {daily_cap:,.0f} was hit at "
        f"{summary['tripped_at']}. No new entries will be proposed today. "
        f"Any position already open keeps running its own SL/TSL/target."
    )
    if st.button("Reset Circuit Breaker (testing only)"):
        state_store.create_control_request("RESET_BREAKER")
        st.rerun()

if trades_count >= config.RISK["MAX_TRADES_PER_DAY"]:
    st.warning(
        f"Daily trade cap reached ({trades_count}/{config.RISK['MAX_TRADES_PER_DAY']}) -- "
        f"no new entries until tomorrow. Any position already open keeps running its own SL/TSL/target."
    )

# consecutive-loss cooldown: counts backwards from today's most recent
# closed trade, stopping at the first win -- same logic as
# state_store.get_consecutive_losses(), computed here from the snapshot
# already in hand instead of a second DB round trip.
today_closed_desc = sorted(today_closed, key=lambda p: p["exit_time"] or "", reverse=True)
consecutive_losses = 0
for p in today_closed_desc:
    if p["net_pnl"] is not None and p["net_pnl"] <= 0:
        consecutive_losses += 1
    else:
        break
if consecutive_losses >= config.RISK["MAX_CONSECUTIVE_LOSSES"]:
    st.warning(
        f"{consecutive_losses} consecutive losing trades today -- new entries paused "
        f"(cooldown) for the rest of the day."
    )
elif consecutive_losses > 0:
    st.caption(
        f"Consecutive losing trades today: {consecutive_losses} "
        f"(cooldown triggers at {config.RISK['MAX_CONSECUTIVE_LOSSES']})."
    )

# per-instrument breakdown for today
if today_closed:
    df_today = pd.DataFrame(today_closed)
    breakdown = df_today.groupby("instrument")["net_pnl"].sum().reset_index()
    st.caption("Today's P&L by instrument: " + ", ".join(
        f"{row.instrument} Rs {row.net_pnl:,.2f}" for row in breakdown.itertuples()
    ))

# ------------------------------------------------------- cumulative drawdown
cum_stats = snapshot["cumulative_pnl_stats"]
risk_state = snapshot["risk_state"]
cd1, cd2, cd3 = st.columns(3)
cd1.metric("All-time P&L", f"Rs {cum_stats['cumulative_pnl']:,.2f}")
cd2.metric("Peak P&L", f"Rs {cum_stats['peak_pnl']:,.2f}")
cd3.metric("Current Drawdown", f"Rs {cum_stats['drawdown']:,.2f}")

if not config.RISK["ENABLE_CUMULATIVE_DRAWDOWN_BREAKER"]:
    st.caption(
        "Cumulative drawdown breaker: OFF. The daily cap above resets every day and can't "
        "catch a losing streak spread across many days -- enable "
        "config.RISK.ENABLE_CUMULATIVE_DRAWDOWN_BREAKER to guard against that too."
    )
elif risk_state["cumulative_breaker_tripped"]:
    st.error(
        f"TRADING HALTED -- cumulative drawdown breached -Rs "
        f"{config.RISK['MAX_CUMULATIVE_DRAWDOWN']:,.0f} at {risk_state['tripped_at']}. "
        f"No new entries anywhere until this is manually reset (it does NOT auto-reset daily)."
    )
    if st.button("Reset Cumulative Drawdown Breaker (testing only)"):
        state_store.create_control_request("RESET_CUMULATIVE_BREAKER")
        st.rerun()
else:
    st.caption(
        f"Cumulative drawdown breaker: ON. Halts all new entries if drawdown exceeds "
        f"-Rs {config.RISK['MAX_CUMULATIVE_DRAWDOWN']:,.0f} from its running peak."
    )

st.divider()

# ------------------------------------------------------------ pending signal
st.subheader("Pending Signal")
pending = snapshot["pending_signal"]
if pending is None:
    st.info("No signal awaiting confirmation right now.")
else:
    sl_points = engine.compute_sl_points(pending["instrument"], pending["proposed_ltp"])
    if sl_points is not None:
        # sl_points*qty is only the price-movement portion of the risk
        # budget -- compute_sl_points already sizes it net of estimated
        # round-trip costs, so the HONEST worst-case total also needs
        # those costs added back, or this understates what a real SL hit
        # actually costs (found live: a signal showing ~Rs440 here
        # actually costs ~Rs500 all-in before any overshoot).
        est_costs = cost_model.estimate_round_trip_costs(
            pending["proposed_ltp"], pending["qty"], config.INSTRUMENTS[pending["instrument"]]["exchange"]
        )
        worst_case = f"Rs {sl_points * pending['qty'] + est_costs:,.2f} (incl. ~Rs {est_costs:,.0f} est. costs)"
    else:
        worst_case = "N/A (will be rejected on confirm)"

    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1])
    c1.metric("Instrument", pending["instrument"])
    c2.metric("Direction", pending["direction"])
    c3.metric("Strike", f"{pending['strike']:.0f}")
    c4.metric("Proposed Premium", f"Rs {pending['proposed_ltp']:.2f}")
    c5.metric("Qty (lot)", pending["qty"])
    st.caption(f"Expiry: {pending['expiry']} | Worst-case loss at SL (estimate): {worst_case} | "
               f"Proposal expires: {pending['expires_at']}")

    confirm_disabled = bool(summary["circuit_breaker_tripped"]) or bool(risk_state["cumulative_breaker_tripped"])
    bc1, bc2 = st.columns(2)
    with bc1:
        if st.button("Confirm Entry", type="primary", disabled=confirm_disabled, use_container_width=True):
            state_store.create_control_request("CONFIRM_SIGNAL", {"signal_id": pending["id"]})
            st.rerun()
    with bc2:
        if st.button("Reject", use_container_width=True):
            state_store.create_control_request("REJECT_SIGNAL", {"signal_id": pending["id"]})
            st.rerun()
    if confirm_disabled:
        st.warning("Confirm is disabled while a circuit breaker (daily or cumulative drawdown) is tripped.")

st.divider()

# ------------------------------------------------------------ open position
st.subheader("Open Position")
position = snapshot["open_position"]
if position is None:
    st.info("Flat -- no position open.")
else:
    live_ltp = position["last_seen_ltp"]
    qty = position["qty"]
    if live_ltp is not None:
        live_pnl_points = live_ltp - position["entry_ltp_net"]
        live_pnl_rupees = live_pnl_points * qty
    else:
        live_pnl_points = None
        live_pnl_rupees = None

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Instrument", f"{position['instrument']} {position['direction']}")
    p2.metric("Strike", f"{position['strike']:.0f}")
    p3.metric("Entry Premium (net)", f"Rs {position['entry_ltp_net']:.2f}")
    p4.metric("Qty (lot)", qty)

    p5, p6, p7, p8 = st.columns(4)
    p5.metric("Live LTP", f"Rs {live_ltp:.2f}" if live_ltp is not None else "waiting for engine...")
    p6.metric("Live P&L", f"Rs {live_pnl_rupees:,.2f}" if live_pnl_rupees is not None else "-",
              f"{live_pnl_points:+.2f} pts" if live_pnl_points is not None else None)
    p7.metric("Current Stop", f"Rs {position['current_sl']:.2f}",
              "TSL armed" if position["tsl_armed"] else "initial SL")
    p8.metric("Target", f"Rs {position['target_price']:.2f}")

    st.caption(f"Entry time: {position['entry_time']} | Last update: {position['last_seen_at'] or '-'}")

st.divider()

# -------------------------------------------------------------- activity log
st.subheader("Activity Log")
activity = snapshot["activity_log"]
if not activity:
    st.info("No activity yet -- signals, entries, exits, and breaker events will appear here as they happen.")
else:
    log_df = pd.DataFrame(activity)[["timestamp", "event_type", "message"]]
    st.dataframe(log_df, use_container_width=True, hide_index=True, height=250)

st.divider()

# ------------------------------------------------------------ trade history
st.subheader("Trade History")
closed = snapshot["closed_positions"]
if not closed:
    st.info("No closed trades yet.")
else:
    df = pd.DataFrame(closed)
    # entry_time/exit_time are stored as 24h ISO timestamps -- format to a
    # human-readable 12h AM/PM string for the display table only; the raw
    # ISO values are still available in the "Full cost breakdown" expander
    # and the CSV export below.
    df["entry_time_display"] = pd.to_datetime(df["entry_time"]).dt.strftime("%Y-%m-%d %I:%M:%S %p")
    df["exit_time_display"] = pd.to_datetime(df["exit_time"]).dt.strftime("%Y-%m-%d %I:%M:%S %p")
    display_cols = ["entry_time_display", "instrument", "direction", "strike", "qty",
                     "entry_ltp_net", "exit_time_display", "exit_ltp_net", "exit_reason",
                     "gross_pnl", "costs_total", "net_pnl"]
    st.dataframe(df[display_cols].rename(columns={
        "entry_time_display": "entry_time", "exit_time_display": "exit_time",
        "entry_ltp_net": "entry_net", "exit_ltp_net": "exit_net",
    }), use_container_width=True, hide_index=True)

    with st.expander("Full cost breakdown / raw prices"):
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.download_button(
        "Download trade history (CSV)",
        data=df.to_csv(index=False),
        file_name="paper_trades_dashboard.csv",
        mime="text/csv",
    )

st.divider()

# --------------------------------------------------- ORB strike mapper
st.subheader("ORB Strike Mapper (Experimental, Read-Only)")
st.caption(
    "Formalizes a Pine Script concept (analyzed 2026-07-19): marks where the OPPOSITE option "
    "type's real 9:15-9:20 opening-range extreme sits, across a ladder of ITM strikes on your "
    "selected strike. **Purely informational** -- comparing a PUT's premium against a "
    "CALL-derived level (or vice versa) mixes two different premium scales, so this does NOT "
    "generate trading signals and has zero effect on entries, exits, or any other part of the "
    "trading engine. Levels lock in once per day, right after 9:20 IST, using real option "
    "premiums (not simulated)."
)

orb_settings = snapshot["orb_settings"]
instrument_keys = list(config.INSTRUMENTS.keys())

oc1, oc2, oc3, oc4, oc5 = st.columns(5)
with oc1:
    orb_instrument = st.selectbox(
        "Instrument", instrument_keys,
        index=instrument_keys.index(orb_settings["instrument"]) if orb_settings["instrument"] in instrument_keys else 0,
        key="orb_instrument",
    )
with oc2:
    orb_view = st.selectbox("View", ["TOP", "BOTTOM"],
                             index=["TOP", "BOTTOM"].index(orb_settings["view"]), key="orb_view")
with oc3:
    orb_type = st.selectbox("Selected Type", ["PUT", "CALL"],
                             index=["PUT", "CALL"].index(orb_settings["selected_type"]), key="orb_type")
with oc4:
    orb_step = config.INSTRUMENTS[orb_instrument]["strike_step"]
    orb_strike_default = orb_settings["selected_strike"] or 0.0
    orb_strike = st.number_input("Selected Strike", value=float(orb_strike_default), step=float(orb_step), key="orb_strike")
with oc5:
    orb_itm = st.number_input("ITM Count", min_value=0, max_value=10,
                               value=orb_settings["itm_count"], key="orb_itm")

if st.button("Update ORB Settings"):
    state_store.create_control_request("SET_ORB_SETTINGS", {
        "instrument": orb_instrument, "view": orb_view, "selected_type": orb_type,
        "selected_strike": orb_strike, "itm_count": int(orb_itm),
    })
    st.rerun()

today_orb_levels = snapshot["orb_levels"]
if orb_settings["selected_strike"] is None:
    st.info("Pick a strike above and click \"Update ORB Settings\" to start tracking.")
elif not today_orb_levels:
    st.info("No levels yet today -- waiting for the 9:15-9:20 opening-range candle to close "
            "(or settings were just changed and haven't recomputed yet).")
else:
    orb_df = pd.DataFrame(today_orb_levels)[["ladder_index", "strike", "option_type", "high_or_low", "value"]]
    orb_df = orb_df.rename(columns={"ladder_index": "rung (0=own strike)"})
    st.dataframe(orb_df, use_container_width=True, hide_index=True)

st.divider()

# --------------------------------------------------------- strategy analysis
st.subheader("Strategy Analysis (Historical Backtest)")
st.caption(
    "Context for days with few or no trades: this is how often the current rule set "
    "actually fires, and what it would have done historically. It is NOT a live track "
    "record -- see the caveats below."
)

summary_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_summary.json")
if not os.path.exists(summary_path):
    st.info(
        "No backtest summary found. Run `python backtest.py` locally to generate one -- "
        "it needs the historical CSVs, which aren't shipped to this environment if you're "
        "viewing this on the hosted/cloud copy."
    )
else:
    with open(summary_path) as f:
        backtest_data = json.load(f)
    s = backtest_data.get("summary")

    filter_label = "ON (EMA50 trend filter)" if backtest_data["trend_filter_enabled"] else "OFF (baseline)"
    st.caption(f"Backtest generated {backtest_data['generated_at']} | Strategy config at that time: {filter_label}")

    if backtest_data["trend_filter_enabled"] != config.STRATEGY["ENABLE_TREND_FILTER"]:
        st.warning(
            "This backtest was generated with a DIFFERENT strategy setting than what's "
            "running live right now -- re-run backtest.py to refresh it."
        )

    if s is None:
        st.info("No trades were generated over the backtest period.")
    else:
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Historical trades", s["total_trades"])
        b2.metric("Win rate", f"{s['win_rate_pct']}%", f"{s['wins']}W / {s['losses']}L")
        b3.metric("Total P&L (backtest)", f"Rs {s['total_pnl']:,.2f}")
        b4.metric("Max drawdown", f"Rs {s['max_drawdown']:,.2f}")
        st.caption(f"Average P&L per trade: Rs {s['avg_pnl']:,.2f} | "
                   f"Period: {s['date_range'][0][:10]} to {s['date_range'][1][:10]}")

        c1, c2 = st.columns(2)
        with c1:
            st.caption("By instrument")
            st.dataframe(pd.DataFrame(s["per_instrument"]), use_container_width=True, hide_index=True)
        with c2:
            st.caption("By exit reason")
            st.dataframe(pd.DataFrame(s["per_exit_reason"]), use_container_width=True, hide_index=True)

    with st.expander("Important caveats about these numbers"):
        st.markdown(
            "- Option premiums are **simulated** via Black-Scholes using realized volatility "
            "as a stand-in for implied vol, and a fixed assumed "
            f"**{backtest_data['assumed_days_to_expiry']}-day time-to-expiry** -- real historical "
            "expiry calendars aren't available in this dataset.\n"
            "- This is a theoretical approximation of the rule set's behavior, **not** a "
            "measurement of real historical option P&L.\n"
            "- Past backtest performance is **not** a promise about future results, live or "
            "otherwise.\n"
            "- Signals fire relatively rarely (roughly once every several trading hours per "
            "instrument) -- a day or two with zero trades is expected, not a malfunction."
        )
