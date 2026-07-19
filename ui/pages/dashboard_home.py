"""
ui/pages/dashboard_home.py
"Above the fold" overview page: live ticker, KPI cards, AI engine status,
the current pending signal / open position at a glance, and a short recent
activity feed. Every value here is read from the exact same
state_store.get_dashboard_snapshot() call the original single-page
dashboard.py used -- no new calculations, no new state.
"""
import datetime

import pandas as pd
import streamlit as st

import config
import state_store
import engine
from ui import components

st.set_page_config(page_title="Dashboard | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

snapshot = state_store.get_dashboard_snapshot()
today_str = datetime.date.today().isoformat()

components.section_header(
    "Dashboard",
    "Semi-automatic paper trading -- Nifty / BankNifty / Sensex. No real orders are ever placed.",
)

engine_state = "on" if snapshot["engine_alive"] else "warn"
engine_label = "Engine running" if snapshot["engine_alive"] else "Engine starting..."
components.status_pill(engine_label, engine_state)

st.markdown("<br>", unsafe_allow_html=True)

# ------------------------------------------------------------- live ticker
quotes = snapshot["spot_quotes"]
ticker_items = []
for name in config.INSTRUMENTS:
    q = quotes.get(name)
    if q:
        ticker_items.append({"name": name, "price": q["last_price"], "change_pct": q["pct_change"]})
if ticker_items:
    components.ticker_bar(ticker_items)
    latest_update = max(q["updated_at"] for q in quotes.values())
    st.caption(f"Index quotes as of {latest_update} (updates every engine poll cycle).")
else:
    st.caption("Index quotes will appear once the engine has completed its first poll.")

st.markdown("<br>", unsafe_allow_html=True)

# ---------------------------------------------------------------- KPI row
summary = snapshot["daily_summary"]
daily_cap = config.RISK["DAILY_LOSS_CAP"]
today_closed = [p for p in snapshot["closed_positions"] if (p["exit_time"] or "").startswith(today_str)]
realized_net_pnl = sum(p["net_pnl"] for p in today_closed)
trades_count = len(today_closed)
remaining_budget = daily_cap + realized_net_pnl

k1, k2, k3, k4 = st.columns(4)
with k1:
    components.kpi_card("Capital", f"Rs {config.RISK['CAPITAL']:,.0f}")
with k2:
    tone = "profit" if realized_net_pnl >= 0 else "loss"
    components.kpi_card("Today's Net P&L", f"Rs {realized_net_pnl:,.2f}", tone=tone)
with k3:
    components.kpi_card("Remaining Loss Budget", f"Rs {max(remaining_budget, 0):,.2f}", f"of Rs {daily_cap:,.0f} cap")
with k4:
    components.kpi_card("Trades Today", trades_count)

if summary["circuit_breaker_tripped"]:
    st.error(
        f"TRADING HALTED -- daily loss cap of Rs {daily_cap:,.0f} was hit at "
        f"{summary['tripped_at']}. No new entries will be proposed today. "
        f"Any position already open keeps running its own SL/TSL/target."
    )

if today_closed:
    df_today = pd.DataFrame(today_closed)
    breakdown = df_today.groupby("instrument")["net_pnl"].sum().reset_index()
    st.caption("Today's P&L by instrument: " + ", ".join(
        f"{row.instrument} Rs {row.net_pnl:,.2f}" for row in breakdown.itertuples()
    ))

st.markdown("<br>", unsafe_allow_html=True)

# ------------------------------------------------------ signal + position row
col_signal, col_position = st.columns(2)

with col_signal:
    components.section_header("Current AI Signal")
    pending = snapshot["pending_signal"]
    if pending is None:
        st.info("No signal awaiting confirmation right now.")
    else:
        sl_points = engine.compute_sl_points(pending["instrument"], pending["proposed_ltp"])
        target_price = pending["proposed_ltp"] + config.INSTRUMENTS[pending["instrument"]]["target_points"]
        stop_price = pending["proposed_ltp"] - sl_points if sl_points is not None else pending["proposed_ltp"]
        components.ai_signal_card(
            instrument=pending["instrument"],
            direction=pending["direction"],
            entry=pending["proposed_ltp"],
            stop_loss=stop_price,
            target=target_price,
            reasoning=f"Rule-based signal from the active strategy, strike {pending['strike']:.0f}, "
                      f"expiry {pending['expiry']}.",
        )
        st.caption(
            "Entry/Stop/Target above are **estimates** off the proposed premium -- engine.py "
            "re-fetches a fresh live price and applies slippage at the moment you actually "
            "confirm, so the real fill can differ. Go to **Live Trading** to Confirm or Reject."
        )

with col_position:
    components.section_header("Open Position")
    position = snapshot["open_position"]
    if position is None:
        st.info("Flat -- no position open.")
    else:
        live_ltp = position["last_seen_ltp"]
        qty = position["qty"]
        live_pnl_rupees = (live_ltp - position["entry_ltp_net"]) * qty if live_ltp is not None else None
        tone = None
        if live_pnl_rupees is not None:
            tone = "profit" if live_pnl_rupees >= 0 else "loss"
        components.kpi_card(
            f"{position['instrument']} {position['direction']} {position['strike']:.0f}",
            f"Rs {live_pnl_rupees:,.2f}" if live_pnl_rupees is not None else "waiting for engine...",
            sub=f"Entry {position['entry_ltp_net']:.2f} | Stop {position['current_sl']:.2f} "
                f"({'TSL armed' if position['tsl_armed'] else 'initial SL'}) | Target {position['target_price']:.2f}",
            tone=tone,
        )

st.markdown("<br>", unsafe_allow_html=True)

# ------------------------------------------------------------- recent activity
components.section_header("Recent Activity")
activity = snapshot["activity_log"][:8]
if not activity:
    st.info("No activity yet -- signals, entries, exits, and breaker events will appear here as they happen.")
else:
    components.activity_timeline(activity)
