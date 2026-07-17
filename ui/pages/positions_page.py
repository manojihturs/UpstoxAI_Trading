"""
ui/pages/positions_page.py
Standalone open-position monitor. Same fields/metrics as the Open Position
section of the original dashboard.py, just its own page.
"""
import streamlit as st

import state_store
from ui import components

st.set_page_config(page_title="Positions | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

snapshot = state_store.get_dashboard_snapshot()

components.section_header("Positions", "The current open paper position, if any.")

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
