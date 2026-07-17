"""
ui/pages/live_trading.py
The real-time action page: pending signal (Confirm/Reject) and the open
position monitor. Every control_request kind/payload and every metric here
is copied verbatim from the original dashboard.py -- only the layout and
styling changed.
"""
import streamlit as st

import config
import state_store
import engine
import cost_model
from ui import components

st.set_page_config(page_title="Live Trading | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

snapshot = state_store.get_dashboard_snapshot()
summary = snapshot["daily_summary"]
risk_state = snapshot["risk_state"]

components.section_header("Live Trading", "Confirm/reject signals and monitor the open position in real time.")

# ------------------------------------------------------------ pending signal
components.section_header("Pending Signal")
pending = snapshot["pending_signal"]
if pending is None:
    st.info("No signal awaiting confirmation right now.")
else:
    sl_points = engine.compute_sl_points(pending["instrument"], pending["proposed_ltp"])
    if sl_points is not None:
        est_costs = cost_model.estimate_round_trip_costs(
            pending["proposed_ltp"], pending["qty"], config.INSTRUMENTS[pending["instrument"]]["exchange"]
        )
        worst_case = f"Rs {sl_points * pending['qty'] + est_costs:,.2f} (incl. ~Rs {est_costs:,.0f} est. costs)"
    else:
        worst_case = "N/A (will be rejected on confirm)"

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        components.kpi_card("Instrument", pending["instrument"])
    with c2:
        components.kpi_card("Direction", pending["direction"])
    with c3:
        components.kpi_card("Strike", f"{pending['strike']:.0f}")
    with c4:
        components.kpi_card("Proposed Premium", f"Rs {pending['proposed_ltp']:.2f}")
    with c5:
        components.kpi_card("Qty (lot)", pending["qty"])

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
components.section_header("Open Position")
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
    with p1:
        components.kpi_card("Instrument", f"{position['instrument']} {position['direction']}")
    with p2:
        components.kpi_card("Strike", f"{position['strike']:.0f}")
    with p3:
        components.kpi_card("Entry Premium (net)", f"Rs {position['entry_ltp_net']:.2f}")
    with p4:
        components.kpi_card("Qty (lot)", qty)

    p5, p6, p7, p8 = st.columns(4)
    with p5:
        components.kpi_card("Live LTP", f"Rs {live_ltp:.2f}" if live_ltp is not None else "waiting for engine...")
    with p6:
        tone = None
        if live_pnl_rupees is not None:
            tone = "profit" if live_pnl_rupees >= 0 else "loss"
        components.kpi_card(
            "Live P&L", f"Rs {live_pnl_rupees:,.2f}" if live_pnl_rupees is not None else "-",
            f"{live_pnl_points:+.2f} pts" if live_pnl_points is not None else None, tone=tone,
        )
    with p7:
        components.kpi_card("Current Stop", f"Rs {position['current_sl']:.2f}",
                             "TSL armed" if position["tsl_armed"] else "initial SL")
    with p8:
        components.kpi_card("Target", f"Rs {position['target_price']:.2f}")

    st.caption(f"Entry time: {position['entry_time']} | Last update: {position['last_seen_at'] or '-'}")
