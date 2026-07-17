"""
ui/pages/analytics_page.py
Cumulative P&L / drawdown stats (with the breaker reset buttons) and the
full Activity Log, exactly as computed and gated in the original
dashboard.py -- same control_request kinds, same conditions.
"""
import pandas as pd
import streamlit as st

import config
import state_store
from ui import components

st.set_page_config(page_title="Analytics | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

snapshot = state_store.get_dashboard_snapshot()

components.section_header("Analytics", "Cumulative performance, drawdown, and the full activity log.")

cum_stats = snapshot["cumulative_pnl_stats"]
risk_state = snapshot["risk_state"]

cd1, cd2, cd3 = st.columns(3)
with cd1:
    tone = "profit" if cum_stats["cumulative_pnl"] >= 0 else "loss"
    components.kpi_card("All-time P&L", f"Rs {cum_stats['cumulative_pnl']:,.2f}", tone=tone)
with cd2:
    components.kpi_card("Peak P&L", f"Rs {cum_stats['peak_pnl']:,.2f}")
with cd3:
    components.kpi_card("Current Drawdown", f"Rs {cum_stats['drawdown']:,.2f}", tone="loss")

if not config.RISK["ENABLE_CUMULATIVE_DRAWDOWN_BREAKER"]:
    st.caption(
        "Cumulative drawdown breaker: OFF. The daily cap can't catch a losing streak spread "
        "across many days -- enable config.RISK.ENABLE_CUMULATIVE_DRAWDOWN_BREAKER to guard "
        "against that too."
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

summary = snapshot["daily_summary"]
if summary["circuit_breaker_tripped"]:
    st.error(
        f"TRADING HALTED (daily) -- cap of Rs {config.RISK['DAILY_LOSS_CAP']:,.0f} was hit at "
        f"{summary['tripped_at']}."
    )
    if st.button("Reset Circuit Breaker (testing only)"):
        state_store.create_control_request("RESET_BREAKER")
        st.rerun()

st.divider()

components.section_header("Activity Log")
activity = snapshot["activity_log"]
if not activity:
    st.info("No activity yet -- signals, entries, exits, and breaker events will appear here as they happen.")
else:
    log_df = pd.DataFrame(activity)[["timestamp", "event_type", "message"]]
    st.dataframe(log_df, use_container_width=True, hide_index=True, height=400)
