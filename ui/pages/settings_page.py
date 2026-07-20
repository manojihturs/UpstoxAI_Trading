"""
ui/pages/settings_page.py
All live-switchable engine settings (timeframe, auto-confirm, order
quantity) plus the presentation-only theme picker. Every selectbox/toggle,
its resync-on-external-change guard, and its control_request kind/payload
are copied verbatim from the original dashboard.py.
"""
import streamlit as st

import config
import notifications
import state_store
from ui import components
from ui.theme import inject_global_css, theme_switcher

st.set_page_config(page_title="Settings | Paper Trading", layout="wide")
inject_global_css()

snapshot = state_store.get_dashboard_snapshot()

components.section_header("Settings")

# ------------------------------------------------------------------- theme
st.subheader("Appearance")
theme_switcher()
st.caption("Presentation only -- does not affect trading logic in any way.")

st.divider()

# ------------------------------------------------------------ timeframe picker
st.subheader("Candle Timeframe")
timeframe_options = config.TIMEFRAME["AVAILABLE_MINUTES"]
timeframe_labels = [f"{m} min" for m in timeframe_options]
current_timeframe = snapshot["active_timeframe"]
current_tf_index = timeframe_options.index(current_timeframe) if current_timeframe in timeframe_options else \
    timeframe_options.index(config.TIMEFRAME["DEFAULT_MINUTES"])

if st.session_state.get("_last_known_timeframe") != current_timeframe:
    st.session_state["timeframe_select"] = timeframe_labels[current_tf_index]
    st.session_state["_last_known_timeframe"] = current_timeframe


def _on_timeframe_change():
    chosen_label = st.session_state["timeframe_select"]
    chosen_minutes = timeframe_options[timeframe_labels.index(chosen_label)]
    state_store.create_control_request("SET_TIMEFRAME", {"minutes": chosen_minutes})
    st.session_state["_last_known_timeframe"] = chosen_minutes


st.selectbox(
    "Candle timeframe (switches immediately, no restart needed):",
    timeframe_labels, key="timeframe_select", on_change=_on_timeframe_change,
)
st.caption(
    "⚠️ Every strategy's periods (EMA9/20/50, ADX14, UT Bot's ATR10/14, etc.) were "
    "chosen and backtested assuming 15-min candles. Switching timeframe does NOT rescale "
    "them -- EMA20 on 1-min candles is a 20-*minute* trend, not the 5-*hour* one it was "
    "tuned for. There is no backtest data for any timeframe other than 15-min in this repo "
    "-- anything else is genuinely untested live behavior, not a validated variant."
)

st.divider()

# --------------------------------------------------------------- auto-confirm
st.subheader("Auto-Confirm")
current_auto_confirm = snapshot["auto_confirm"]

if st.session_state.get("_last_known_auto_confirm") != current_auto_confirm:
    st.session_state["auto_confirm_toggle"] = current_auto_confirm
    st.session_state["_last_known_auto_confirm"] = current_auto_confirm


def _on_auto_confirm_change():
    chosen = st.session_state["auto_confirm_toggle"]
    state_store.create_control_request("SET_AUTO_CONFIRM", {"enabled": chosen})
    st.session_state["_last_known_auto_confirm"] = chosen


st.toggle(
    "Auto-confirm signals (skip manual review)",
    key="auto_confirm_toggle", on_change=_on_auto_confirm_change,
)
if current_auto_confirm:
    st.error(
        "🔴 AUTO-CONFIRM IS ON. Every signal that fires opens a paper position immediately, "
        "with no review window -- the Confirm/Reject step on Live Trading is bypassed "
        "entirely. All the same risk controls (SL/TSL/daily cap/cumulative breaker) still "
        "apply once a position is open; what's gone is your chance to look at a signal "
        "before it becomes a trade."
    )
else:
    st.caption(
        "Auto-confirm is off right now -- signals will wait for your manual Confirm/Reject "
        "on the Live Trading page instead of opening automatically."
    )

st.divider()

# ------------------------------------------------------------ qty preference
st.subheader("Order Quantity")
st.caption(
    "Quantity used for each NEW signal, per instrument (switches immediately, no restart "
    "needed). One lot = "
    + ", ".join(f"{name} {cfg['lot_size']}" for name, cfg in config.INSTRUMENTS.items())
    + ". An already-open position keeps the quantity it was opened with."
)
current_qty_by_instrument = snapshot["qty_by_instrument"]
qty_cols = st.columns(len(config.INSTRUMENTS))
for col, name in zip(qty_cols, config.INSTRUMENTS):
    lot_size = config.INSTRUMENTS[name]["lot_size"]
    current_qty = current_qty_by_instrument[name]
    resync_key = f"_last_known_qty_{name}"
    widget_key = f"qty_input_{name}"

    if st.session_state.get(resync_key) != current_qty:
        st.session_state[widget_key] = current_qty
        st.session_state[resync_key] = current_qty

    def _make_on_qty_change(instrument, resync_key, widget_key):
        def _on_qty_change():
            chosen = st.session_state[widget_key]
            state_store.create_control_request("SET_QTY", {"instrument": instrument, "qty": chosen})
            st.session_state[resync_key] = chosen
        return _on_qty_change

    with col:
        st.number_input(
            name, min_value=lot_size, step=lot_size, key=widget_key,
            on_change=_make_on_qty_change(name, resync_key, widget_key),
            help=f"1 lot = {lot_size}. Enter a multiple of {lot_size}.",
        )

st.divider()

# --------------------------------------------------------------- notifications
st.subheader("Telegram Notifications")
st.caption(
    "Sent automatically on every entry and exit (engine.py). Configure "
    "telegram_bot_token / telegram_chat_id in .streamlit/secrets.toml -- see "
    "TELEGRAM_SETUP.md for the 2-minute setup via @BotFather."
)

if notifications.is_configured():
    st.success("Telegram is configured.")
else:
    st.warning(
        "Telegram is NOT configured -- telegram_bot_token and/or telegram_chat_id are "
        "blank in .streamlit/secrets.toml. Entry/exit notifications are silently skipped "
        "until both are set."
    )

if st.button("Send test message"):
    if not notifications.is_configured():
        st.error("Can't send -- Telegram isn't configured yet (see above).")
    else:
        with st.spinner("Sending..."):
            sent = notifications.send_telegram_message(
                "Test message from the Settings page -- Telegram notifications are working."
            )
        if sent:
            st.success("Sent! Check your Telegram chat with the bot.")
        else:
            st.error(
                "Send failed -- Telegram accepted the credentials as present but the API "
                "call didn't succeed. Double-check the bot token and chat ID are correct "
                "(see TELEGRAM_SETUP.md), and that this machine has internet access."
            )
