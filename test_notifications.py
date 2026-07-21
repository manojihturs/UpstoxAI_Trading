"""
test_notifications.py
Deterministic tests for notifications.py -- network calls are always
mocked/monkeypatched, this suite never hits the real Telegram API.
Credentials are supplied by monkeypatching _get_credential directly
(not via st.secrets or env vars) -- see that function's docstring for
why an env-var fallback isn't used here.
"""
import pytest

import notifications


def _fake_credentials(monkeypatch, token="fake-token", chat_id="12345"):
    values = {"telegram_bot_token": token, "telegram_chat_id": chat_id}
    monkeypatch.setattr(notifications, "_get_credential", lambda key: values.get(key))


def test_is_configured_false_when_nothing_set(monkeypatch):
    _fake_credentials(monkeypatch, token=None, chat_id=None)
    assert notifications.is_configured() is False


def test_is_configured_false_when_only_token_set(monkeypatch):
    _fake_credentials(monkeypatch, token="fake-token", chat_id=None)
    assert notifications.is_configured() is False


def test_is_configured_true_when_both_set(monkeypatch):
    _fake_credentials(monkeypatch)
    assert notifications.is_configured() is True


def test_send_telegram_message_returns_false_when_not_configured(monkeypatch):
    _fake_credentials(monkeypatch, token=None, chat_id=None)
    assert notifications.send_telegram_message("test") is False


def test_send_telegram_message_success(monkeypatch):
    _fake_credentials(monkeypatch)

    class FakeResponse:
        status_code = 200

    def fake_post(url, json, timeout):
        assert "fake-token" in url
        assert json["chat_id"] == "12345"
        return FakeResponse()

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    assert notifications.send_telegram_message("test message") is True


def test_send_telegram_message_handles_network_failure_gracefully(monkeypatch):
    _fake_credentials(monkeypatch)

    def fake_post(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    # Must never raise -- a notification failure can't be allowed to
    # interrupt the trading loop that calls this.
    assert notifications.send_telegram_message("test message") is False


def test_send_telegram_message_handles_bad_status_code(monkeypatch):
    _fake_credentials(monkeypatch)

    class FakeResponse:
        status_code = 401

    monkeypatch.setattr(notifications.requests, "post", lambda *a, **k: FakeResponse())
    assert notifications.send_telegram_message("test message") is False


def test_format_entry_message_contains_key_fields():
    msg = notifications.format_entry_message("NIFTY", "CE", 24100.0, 120.5, 114.1, 130.5, 65)
    assert "NIFTY" in msg
    assert "24100.0" in msg
    assert "CALL (CE)" in msg
    assert "120.50" in msg
    assert "65" in msg


def test_format_entry_message_pe_direction_label():
    msg = notifications.format_entry_message("BANKNIFTY", "PE", 58500.0, 200.0, 190.0, 215.0, 30)
    assert "PUT (PE)" in msg


def test_format_exit_message_profit_tone():
    msg = notifications.format_exit_message("NIFTY", "CE", 24100.0, 128.0, "TARGET", 150.0, 65)
    assert "PROFIT" in msg
    assert "TARGET" in msg
    assert "150.00" in msg


def test_format_exit_message_loss_tone():
    msg = notifications.format_exit_message("NIFTY", "CE", 24100.0, 110.0, "SL", -100.0, 65)
    assert "LOSS" in msg
    assert "SL" in msg


def test_format_entry_message_shows_strategy_label_and_app_name():
    msg = notifications.format_entry_message(
        "NIFTY", "CE", 24100.0, 120.5, 114.1, 130.5, 65,
        strategy="UT_BOT_CONSERVATIVE", app_name="dashboard.py",
    )
    assert "UT Bot conservative" in msg
    assert "dashboard.py" in msg


def test_format_exit_message_shows_strategy_label_and_app_name():
    msg = notifications.format_exit_message(
        "NIFTY", "CE", 24100.0, 128.0, "TARGET", 150.0, 65,
        strategy="SWING_STRUCTURE", app_name="app.py",
    )
    assert "Swing structure break" in msg
    assert "app.py" in msg


def test_format_message_falls_back_gracefully_when_strategy_unknown():
    msg = notifications.format_entry_message(
        "NIFTY", "CE", 24100.0, 120.5, 114.1, 130.5, 65,
        strategy="SOME_RETIRED_STRATEGY_KEY", app_name=None,
    )
    assert "SOME_RETIRED_STRATEGY_KEY" in msg  # falls back to the raw key
    assert "unknown" in msg  # app_name defaults to "unknown" when not given


def test_format_message_handles_no_strategy_at_all():
    # positions opened before the strategy column existed have strategy=None
    msg = notifications.format_entry_message("NIFTY", "CE", 24100.0, 120.5, 114.1, 130.5, 65)
    assert "unknown strategy" in msg


def test_notify_entry_calls_send_with_formatted_message(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifications, "send_telegram_message", lambda text: captured.setdefault("text", text))
    notifications.notify_entry("NIFTY", "CE", 24100.0, 120.5, 114.1, 130.5, 65)
    assert "NIFTY" in captured["text"]
    assert "ENTRY" in captured["text"]


def test_notify_exit_calls_send_with_formatted_message(monkeypatch):
    captured = {}
    monkeypatch.setattr(notifications, "send_telegram_message", lambda text: captured.setdefault("text", text))
    notifications.notify_exit("NIFTY", "CE", 24100.0, 128.0, "TARGET", 150.0, 65)
    assert "EXIT" in captured["text"]
    assert "TARGET" in captured["text"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
