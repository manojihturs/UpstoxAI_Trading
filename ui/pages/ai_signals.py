"""
ui/pages/ai_signals.py
Signal-focused view: the current pending signal as a full AI signal card,
plus a scrollable history of past SIGNAL/ENTRY/AUTO_CONFIRM events pulled
straight from the same activity_log the original Activity Log table used.
No new signal logic -- this only reformats state_store data already
computed by engine.py.
"""
import streamlit as st

import config
import state_store
import engine
import strategies
from ui import components

st.set_page_config(page_title="AI Signals | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

snapshot = state_store.get_dashboard_snapshot()

components.section_header(
    "AI Signals",
    f"Active strategy: {strategies.STRATEGIES.get(snapshot['active_strategy'], {}).get('label', snapshot['active_strategy'])}",
)

pending = snapshot["pending_signal"]
if pending is None:
    st.info("No signal awaiting confirmation right now. Recent signal activity is listed below.")
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
        reasoning=f"Strategy: {strategies.STRATEGIES.get(snapshot['active_strategy'], {}).get('label', snapshot['active_strategy'])} "
                  f"| Strike {pending['strike']:.0f} | Expiry {pending['expiry']} | "
                  f"Timeframe {snapshot['active_timeframe']}min. Go to Live Trading to Confirm/Reject.",
    )
    st.caption(
        "Entry/Stop/Target above are **estimates** off the proposed premium -- engine.py "
        "re-fetches a fresh live price and applies slippage at the moment you actually confirm, "
        "so the real fill can differ."
    )

st.markdown("<br>", unsafe_allow_html=True)
components.section_header("Signal History")
signal_events = [e for e in snapshot["activity_log"] if e["event_type"] in
                 ("SIGNAL", "ENTRY", "AUTO_CONFIRM", "REJECTED", "EXIT")]
if not signal_events:
    st.info("No signal activity yet.")
else:
    components.activity_timeline(signal_events[:30])
