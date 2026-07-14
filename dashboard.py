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
import datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import state_store
import engine

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


if not check_password():
    st.stop()

st_autorefresh(interval=5000, key="refresh")

engine.ensure_background_thread()  # no-op after the first call in this process

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
m4.metric("Trades Today", trades_count)

if summary["circuit_breaker_tripped"]:
    st.error(
        f"TRADING HALTED -- daily loss cap of Rs {daily_cap:,.0f} was hit at "
        f"{summary['tripped_at']}. No new entries will be proposed today. "
        f"Any position already open keeps running its own SL/TSL/target."
    )
    if st.button("Reset Circuit Breaker (testing only)"):
        state_store.create_control_request("RESET_BREAKER")
        st.rerun()

# per-instrument breakdown for today
if today_closed:
    df_today = pd.DataFrame(today_closed)
    breakdown = df_today.groupby("instrument")["net_pnl"].sum().reset_index()
    st.caption("Today's P&L by instrument: " + ", ".join(
        f"{row.instrument} Rs {row.net_pnl:,.2f}" for row in breakdown.itertuples()
    ))

st.divider()

# ------------------------------------------------------------ pending signal
st.subheader("Pending Signal")
pending = snapshot["pending_signal"]
if pending is None:
    st.info("No signal awaiting confirmation right now.")
else:
    sl_points = engine.compute_sl_points(pending["instrument"], pending["proposed_ltp"])
    worst_case = f"Rs {sl_points * pending['qty']:,.2f}" if sl_points is not None else "N/A (will be rejected on confirm)"

    c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1])
    c1.metric("Instrument", pending["instrument"])
    c2.metric("Direction", pending["direction"])
    c3.metric("Strike", f"{pending['strike']:.0f}")
    c4.metric("Proposed Premium", f"Rs {pending['proposed_ltp']:.2f}")
    c5.metric("Qty (lot)", pending["qty"])
    st.caption(f"Expiry: {pending['expiry']} | Worst-case loss at SL (estimate): {worst_case} | "
               f"Proposal expires: {pending['expires_at']}")

    confirm_disabled = bool(summary["circuit_breaker_tripped"])
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
        st.warning("Confirm is disabled while the daily circuit breaker is tripped.")

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

# ------------------------------------------------------------ trade history
st.subheader("Trade History")
closed = snapshot["closed_positions"]
if not closed:
    st.info("No closed trades yet.")
else:
    df = pd.DataFrame(closed)
    display_cols = ["entry_time", "instrument", "direction", "strike", "qty",
                     "entry_ltp_net", "exit_time", "exit_ltp_net", "exit_reason",
                     "gross_pnl", "costs_total", "net_pnl"]
    st.dataframe(df[display_cols].rename(columns={
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
