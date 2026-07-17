"""
ui/pages/strategies_page.py
Strategy selection + the historical backtest analysis for the active rule
set. The selectbox, its resync-on-external-change guard, and the
SET_STRATEGY control_request are copied verbatim from dashboard.py -- see
that file's original comment for why the key+on_change pattern (not naive
value-comparison) is required to avoid a stale-tab bug.
"""
import streamlit as st

import state_store
import strategies
from ui import components

st.set_page_config(page_title="Strategies | Paper Trading", layout="wide")
from ui.theme import inject_global_css
inject_global_css()

snapshot = state_store.get_dashboard_snapshot()

components.section_header("Strategies", "Choose which rule set engine.py uses for new signals.")

strategy_keys = list(strategies.STRATEGIES.keys())
strategy_labels = [strategies.STRATEGIES[k]["label"] for k in strategy_keys]
current_strategy = snapshot["active_strategy"]
current_index = strategy_keys.index(current_strategy) if current_strategy in strategy_keys else 0

if st.session_state.get("_last_known_strategy") != current_strategy:
    st.session_state["strategy_select"] = strategy_labels[current_index]
    st.session_state["_last_known_strategy"] = current_strategy


def _on_strategy_change():
    chosen_label = st.session_state["strategy_select"]
    chosen_key = strategy_keys[strategy_labels.index(chosen_label)]
    state_store.create_control_request("SET_STRATEGY", {"strategy": chosen_key})
    st.session_state["_last_known_strategy"] = chosen_key


st.selectbox(
    "Signal strategy engine.py uses for new entries (switches immediately, no restart needed):",
    strategy_labels, key="strategy_select", on_change=_on_strategy_change,
)
st.caption(
    "Changing this only affects NEW signals from now on -- an already-open position keeps "
    "running under whatever strategy proposed it. See the Backtesting page for how each "
    "option performed historically before trusting it; win rates vary a lot between them."
)

st.divider()
st.caption("All 7 available strategies:")
for key in strategy_keys:
    st.markdown(f"- **{strategies.STRATEGIES[key]['label']}**")
