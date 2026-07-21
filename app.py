"""
app.py
New multipage entry point for the redesigned dashboard shell -- login gate,
autorefresh, and the engine background thread are identical to the original
dashboard.py; only the navigation/layout changed (collapsible sidebar with
Dashboard / Live Trading / AI Signals / Strategies / Positions / Orders /
Analytics / Backtesting / Settings instead of one long scrolling page).

This file only ever READS state_store (for display) and writes user-intent
rows into control_requests -- it never touches positions/daily_summary/
pending signal status directly. engine.py's main() loop is the only writer
of financial state; it runs in a background daemon thread started here
(ensure_background_thread(), started exactly once per process) and picks up
confirm/reject requests from the pages. That thread model is what lets this
run unmodified on a single-process host like Streamlit Community Cloud,
which has no separate worker process.

No real orders are placed anywhere in this app -- paper only.

The original single-page dashboard.py is left untouched and still works --
this is an additive alternative UI, not a replacement, until you're happy
with it.
"""
import os
import sys

import streamlit as st
from streamlit_autorefresh import st_autorefresh

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import engine

st.set_page_config(page_title="Paper Trading Dashboard", layout="wide", initial_sidebar_state="expanded")


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

engine.ensure_background_thread(app_name="app.py")  # no-op after the first call in this process

pages = [
    st.Page("ui/pages/dashboard_home.py", title="Dashboard", icon=":material/dashboard:", default=True),
    st.Page("ui/pages/live_trading.py", title="Live Trading", icon=":material/bolt:"),
    st.Page("ui/pages/ai_signals.py", title="AI Signals", icon=":material/smart_toy:"),
    st.Page("ui/pages/strategies_page.py", title="Strategies", icon=":material/tune:"),
    st.Page("ui/pages/positions_page.py", title="Positions", icon=":material/account_balance_wallet:"),
    st.Page("ui/pages/orders_page.py", title="Orders", icon=":material/receipt_long:"),
    st.Page("ui/pages/analytics_page.py", title="Analytics", icon=":material/monitoring:"),
    st.Page("ui/pages/backtesting_page.py", title="Backtesting", icon=":material/history:"),
    st.Page("ui/pages/validation_page.py", title="Validation", icon=":material/fact_check:"),
    st.Page("ui/pages/settings_page.py", title="Settings", icon=":material/settings:"),
]
nav = st.navigation(pages)
nav.run()
