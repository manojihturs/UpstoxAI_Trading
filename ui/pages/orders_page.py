"""
ui/pages/orders_page.py
Trade History table -- this app has no separate broker order book (paper
only), so "Orders" here is the same closed_positions table the original
dashboard.py showed under "Trade History", including the AM/PM display
formatting and CSV export, unchanged.
"""
import pandas as pd
import streamlit as st

import state_store
from ui import components

st.set_page_config(page_title="Orders | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

snapshot = state_store.get_dashboard_snapshot()

components.section_header("Orders", "Trade history -- every paper entry and exit, most recent first.")

closed = snapshot["closed_positions"]
if not closed:
    st.info("No closed trades yet.")
else:
    df = pd.DataFrame(closed)
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
