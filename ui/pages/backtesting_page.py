"""
ui/pages/backtesting_page.py
Historical backtest analysis for the currently active strategy, moved
out of the original dashboard.py's single-page layout into its own
Backtesting page. Reads the same backtest_summary.json produced by
`python backtest.py` -- no new computation.
"""
import json
import os

import pandas as pd
import streamlit as st

import config
from ui import components

# config.BASE_DIR is the project root (defined once in config.py) -- using
# it here avoids re-deriving the root via dirname(dirname(__file__)), which
# is easy to get wrong once this file lives two levels down in ui/pages/.

st.set_page_config(page_title="Backtesting | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

components.section_header(
    "Backtesting",
    "How the current rule set would have performed historically. Not a live track record.",
)

summary_path = os.path.join(config.BASE_DIR, "backtest_summary.json")
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
        with b1:
            components.kpi_card("Historical trades", s["total_trades"])
        with b2:
            components.kpi_card("Win rate", f"{s['win_rate_pct']}%", f"{s['wins']}W / {s['losses']}L")
        with b3:
            tone = "profit" if s["total_pnl"] >= 0 else "loss"
            components.kpi_card("Total P&L (backtest)", f"Rs {s['total_pnl']:,.2f}", tone=tone)
        with b4:
            components.kpi_card("Max drawdown", f"Rs {s['max_drawdown']:,.2f}", tone="loss")
        st.caption(f"Average P&L per trade: Rs {s['avg_pnl']:,.2f} | "
                   f"Period: {s['date_range'][0][:10]} to {s['date_range'][1][:10]}")

        tab1, tab2 = st.tabs(["By instrument", "By exit reason"])
        with tab1:
            st.dataframe(pd.DataFrame(s["per_instrument"]), use_container_width=True, hide_index=True)
        with tab2:
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
