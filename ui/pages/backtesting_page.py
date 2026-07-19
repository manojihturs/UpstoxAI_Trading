"""
ui/pages/backtesting_page.py
Two backtest views:
  1. On-demand: pick a date range, click "Run Backtest", and it backtests
     the CURRENTLY ACTIVE strategy (whatever's selected on the Strategies
     page) over exactly that window -- via backtest_experiments.run_ui_backtest(),
     the same strategy functions engine.py trades live with.
  2. Static snapshot: the full-history backtest_summary.json produced by
     `python backtest.py` (always the EMA baseline + optional trend filter,
     not strategy-aware) -- kept below as a longer-history reference point.
"""
import datetime
import json
import os
import threading

import pandas as pd
import streamlit as st

import config
import state_store
import strategies
from backtest import compute_summary
from backtest_experiments import load_prepared_instruments, run_ui_backtest
from ui import components

# Job store for the on-demand backtest -- this page sits under a 5s
# st_autorefresh (see app.py). A ~20s computation run synchronously inside a
# button's on-click handler gets cut off by the next autorefresh-triggered
# rerun before it ever finishes (Streamlit starts each rerun fresh), so the
# button appeared to silently do nothing. Running the work in a background
# thread and polling on every rerun sidesteps that -- each rerun is now
# cheap regardless of how long the job takes.
#
# IMPORTANT: a bare module-level `_JOBS = {}` does NOT survive this --
# Streamlit re-executes this file's top-level statements on EVERY rerun (it's
# a script, not a one-time import), so a plain global gets silently reset to
# {} on the very next autorefresh, discarding whatever the background thread
# had just written. st.cache_resource is the only thing here guaranteed to
# return the SAME object across reruns, so the store has to live behind it.
@st.cache_resource
def _job_store():
    return {}, threading.Lock()


_JOBS, _JOBS_LOCK = _job_store()


def _run_backtest_job(job_key, strategy_name, from_date, to_date):
    try:
        prepared = _cached_prepared_instruments()
        trades = run_ui_backtest(strategy_name, from_date, to_date, prepared=prepared)
        with _JOBS_LOCK:
            _JOBS[job_key] = {"status": "done", "trades": trades}
    except Exception as e:
        with _JOBS_LOCK:
            _JOBS[job_key] = {"status": "error", "error": str(e)}

st.set_page_config(page_title="Backtesting | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

components.section_header(
    "Backtesting",
    "How the active strategy would have performed historically. Not a live track record.",
)

# ------------------------------------------------------- on-demand backtest
snapshot = state_store.get_dashboard_snapshot()
active_strategy = snapshot["active_strategy"]
active_label = strategies.STRATEGIES.get(active_strategy, {}).get("label", active_strategy)

st.markdown(f"**Strategy under test:** {active_label} &nbsp;·&nbsp; "
            f"change it on the Strategies page -- this always tests whatever's live.",
            unsafe_allow_html=True)

@st.cache_resource(show_spinner=False)
def _cached_prepared_instruments():
    # Cached server-side (st.cache_resource), NOT session_state, and only
    # ever called from inside a button click below -- this page is on a
    # 5s st_autorefresh (see app.py); calling this eagerly on every page
    # load meant the ~10-20s indicator computation kept getting cancelled
    # and restarted by the next autorefresh before it could ever finish,
    # so the page silently never rendered past the strategy line above.
    return load_prepared_instruments()


col1, col2, col3, col4 = st.columns([1, 1, 0.6, 0.6])
with col1:
    from_date = st.date_input("From", value=datetime.date(2023, 1, 1),
                               min_value=datetime.date(2020, 1, 1),
                               max_value=datetime.date.today())
with col2:
    to_date = st.date_input("To", value=datetime.date.today(),
                             min_value=datetime.date(2020, 1, 1),
                             max_value=datetime.date.today())
with col3:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    run_clicked = st.button("Run Backtest", type="primary", use_container_width=True)
with col4:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    refresh_clicked = st.button("Refresh", use_container_width=True)

if refresh_clicked:
    _cached_prepared_instruments.clear()
    with _JOBS_LOCK:
        _JOBS.clear()
    st.session_state.pop("_active_job_key", None)
    threading.Thread(target=_cached_prepared_instruments, daemon=True).start()
    st.rerun()

if run_clicked:
    if from_date > to_date:
        st.error("'From' date is after 'To' date.")
    else:
        job_key = (active_strategy, str(from_date), str(to_date))
        with _JOBS_LOCK:
            already_running = _JOBS.get(job_key, {}).get("status") == "running"
            if not already_running:
                _JOBS[job_key] = {"status": "running"}
        if not already_running:
            threading.Thread(
                target=_run_backtest_job, args=(job_key, active_strategy, from_date, to_date), daemon=True,
            ).start()
        st.session_state["_active_job_key"] = job_key

active_job_key = st.session_state.get("_active_job_key")
if active_job_key:
    with _JOBS_LOCK:
        job = dict(_JOBS.get(active_job_key, {}))

    job_strategy, job_from, job_to = active_job_key
    stale = job_strategy != active_strategy

    if job.get("status") == "running":
        st.info(f"Running backtest for **{strategies.STRATEGIES.get(job_strategy, {}).get('label', job_strategy)}** "
                f"from {job_from} to {job_to} -- this page checks back automatically every few seconds.")
    elif job.get("status") == "error":
        st.error(f"Backtest failed: {job['error']}")
    elif job.get("status") == "done":
        if stale:
            st.warning(
                f"This result is for **{strategies.STRATEGIES.get(job_strategy, {}).get('label', job_strategy)}** "
                f"-- the active strategy has since changed to **{active_label}**. Click Run Backtest again to refresh."
            )
        st.caption(f"Result for {job_from} to {job_to}")

        trades_df = pd.DataFrame(job["trades"])
        summary = compute_summary(trades_df)
        if summary is None:
            st.info("No trades were generated over this date range -- try widening it.")
        else:
            r1, r2, r3, r4 = st.columns(4)
            with r1:
                components.kpi_card("Trades", summary["total_trades"])
            with r2:
                components.kpi_card("Win rate", f"{summary['win_rate_pct']}%",
                                     f"{summary['wins']}W / {summary['losses']}L")
            with r3:
                tone = "profit" if summary["total_pnl"] >= 0 else "loss"
                components.kpi_card("Total P&L", f"Rs {summary['total_pnl']:,.2f}", tone=tone)
            with r4:
                components.kpi_card("Max drawdown", f"Rs {summary['max_drawdown']:,.2f}", tone="loss")
            st.caption(f"Average P&L per trade: Rs {summary['avg_pnl']:,.2f}")

            t1, t2 = st.tabs(["By instrument", "By exit reason"])
            with t1:
                st.dataframe(pd.DataFrame(summary["per_instrument"]), use_container_width=True, hide_index=True)
            with t2:
                st.dataframe(pd.DataFrame(summary["per_exit_reason"]), use_container_width=True, hide_index=True)
else:
    st.info("Pick a date range and click **Run Backtest** to test the active strategy.")

with st.expander("Important caveats about these numbers"):
    st.markdown(
        "- Option premiums are **simulated** via Black-Scholes using realized volatility "
        "as a stand-in for implied vol, and a fixed assumed 3-day time-to-expiry -- real "
        "historical expiry calendars aren't available in this dataset.\n"
        "- This is a theoretical approximation of the rule set's behavior, **not** a "
        "measurement of real historical option P&L.\n"
        "- Past backtest performance is **not** a promise about future results, live or otherwise.\n"
        "- Signals fire relatively rarely -- a narrow date range or a day or two with zero "
        "trades is expected, not a malfunction."
    )

st.divider()

# --------------------------------------------------- static full-history snapshot
components.section_header(
    "Full-History Snapshot (EMA baseline)",
    "From backtest_summary.json, generated by `python backtest.py` -- always the EMA9/20 "
    "baseline (+ trend filter if enabled), regardless of which strategy is active above.",
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
            "This snapshot was generated with a DIFFERENT strategy setting than what's "
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
