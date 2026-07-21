"""
notifications.py
Telegram alerts for trade entries/exits -- called from engine.py right
after a position opens or closes (see manage_open_position/confirm_signal).

Why Telegram and not WhatsApp/SMS: WhatsApp (via Twilio) and SMS gateways
both need a paid account and business-number approval before they'll send
anything; Telegram's Bot API is free and works within minutes of creating a
bot via @BotFather. See TELEGRAM_SETUP.md for the one-time setup.

Every function here is fail-safe by design: a notification failure (bad
token, network blip, Telegram API down) NEVER raises past this module --
alerting about a trade must never be able to interrupt the trade itself.
Callers don't need their own try/except around these calls, but engine.py
wraps them anyway (defense in depth, same pattern as trade_export's
isolated try/except).
"""
import requests

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT_SECONDS = 10


def _get_credential(secret_key):
    """Reads from Streamlit secrets (.streamlit/secrets.toml) -- same
    single source as every other credential in this app (dashboard_password,
    auto_mode, upstox_token). Returns None if unset or blank.

    Deliberately NOT backed by an environment-variable fallback: Streamlit
    syncs every secrets.toml key into os.environ the moment st.secrets is
    first touched, and on Windows os.environ lookups are case-insensitive --
    so an empty `telegram_bot_token = ""` placeholder in secrets.toml
    silently clobbers an intentionally-set `TELEGRAM_BOT_TOKEN` env var
    process-wide, not just for this one lookup. Simpler and more reliable
    to have one source of truth. Tests monkeypatch this function directly.
    """
    try:
        import streamlit as st
        return st.secrets.get(secret_key) or None
    except Exception:
        return None


def is_configured():
    return bool(_get_credential("telegram_bot_token") and _get_credential("telegram_chat_id"))


def send_telegram_message(text):
    """Returns True if the message was sent, False otherwise (including
    "not configured" -- check is_configured() separately if the caller
    needs to distinguish that from a send failure). Never raises."""
    token = _get_credential("telegram_bot_token")
    chat_id = _get_credential("telegram_chat_id")
    if not token or not chat_id:
        return False

    try:
        resp = requests.post(
            TELEGRAM_API_URL.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"WARNING: Telegram notification failed: {e}")
        return False


def _strategy_label(strategy_key):
    """Human-readable label for a strategies.STRATEGIES key, e.g.
    "UT_BOT_CONSERVATIVE" -> "UT Bot conservative (KeyValue=2, ATR=14)".
    Falls back to the raw key (or "unknown") rather than raising, since a
    notification failure must never interrupt the trading loop that
    triggers it -- imported lazily to avoid a module-load-order dependency
    between notifications.py and strategies.py."""
    if not strategy_key:
        return "unknown strategy"
    try:
        import strategies
        return strategies.STRATEGIES.get(strategy_key, {}).get("label", strategy_key)
    except Exception:
        return strategy_key


def format_entry_message(instrument, direction, strike, entry_net, sl, target, qty,
                          strategy=None, app_name=None):
    direction_label = "CALL (CE)" if direction == "CE" else "PUT (PE)"
    return (
        f"*ENTRY* -- {instrument} {strike} {direction_label}\n"
        f"Strategy: {_strategy_label(strategy)}\n"
        f"App: {app_name or 'unknown'}\n"
        f"Qty: {qty}\n"
        f"Entry: Rs {entry_net:,.2f}\n"
        f"SL: Rs {sl:,.2f}  |  Target: Rs {target:,.2f}\n"
        f"_Paper trade -- no real order placed._"
    )


def format_exit_message(instrument, direction, strike, exit_net, exit_reason, net_pnl, qty,
                         strategy=None, app_name=None):
    direction_label = "CALL (CE)" if direction == "CE" else "PUT (PE)"
    tone = "PROFIT" if net_pnl >= 0 else "LOSS"
    return (
        f"*EXIT ({exit_reason})* -- {instrument} {strike} {direction_label}\n"
        f"Strategy: {_strategy_label(strategy)}\n"
        f"App: {app_name or 'unknown'}\n"
        f"Qty: {qty}\n"
        f"Exit: Rs {exit_net:,.2f}\n"
        f"Net P&L: Rs {net_pnl:,.2f} ({tone})\n"
        f"_Paper trade -- no real order placed._"
    )


def notify_entry(instrument, direction, strike, entry_net, sl, target, qty, strategy=None, app_name=None):
    send_telegram_message(
        format_entry_message(instrument, direction, strike, entry_net, sl, target, qty, strategy, app_name)
    )


def notify_exit(instrument, direction, strike, exit_net, exit_reason, net_pnl, qty, strategy=None, app_name=None):
    send_telegram_message(
        format_exit_message(instrument, direction, strike, exit_net, exit_reason, net_pnl, qty, strategy, app_name)
    )
