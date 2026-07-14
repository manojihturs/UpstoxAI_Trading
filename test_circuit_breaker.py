"""
test_circuit_breaker.py
Tests the daily loss circuit breaker and pending-signal TTL expiry in
state_store.py against an isolated temp SQLite file (never the real
trading_state.db). Run with: pytest test_circuit_breaker.py
"""
import sqlite3
import datetime

import pytest

import state_store

DAILY_LOSS_CAP = 2000


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point state_store at a throwaway DB for this test only."""
    db_path = str(tmp_path / "test_trading_state.db")
    monkeypatch.setattr(state_store, "DB_PATH", db_path)
    state_store.init_db()
    return state_store


def _backdate_exit_time(store, position_id, date_str):
    conn = sqlite3.connect(store.DB_PATH)
    conn.execute("UPDATE positions SET exit_time = ? WHERE id = ?",
                 (f"{date_str}T15:20:00", position_id))
    conn.commit()
    conn.close()


def _make_losing_trade(store, date_str, net_pnl):
    pid = store.open_position("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST", 75,
                               120.0, 120.6, 113.6, 130.6)
    store.close_position(pid, 110.0, 109.5, "SL", net_pnl, 10.0, net_pnl)
    _backdate_exit_time(store, pid, date_str)
    return pid


def test_breaker_trips_when_losses_exceed_cap(store):
    today = "2026-07-13"
    _make_losing_trade(store, today, -1200)
    summary = store.recompute_daily_summary(today, DAILY_LOSS_CAP)
    assert summary["circuit_breaker_tripped"] is False  # -1200 alone hasn't breached -2000

    _make_losing_trade(store, today, -900)  # cumulative -2100
    summary = store.recompute_daily_summary(today, DAILY_LOSS_CAP)
    assert summary["circuit_breaker_tripped"] is True
    assert summary["realized_net_pnl"] == -2100
    assert summary["trades_count"] == 2


def test_yesterdays_loss_does_not_trip_todays_fresh_summary(store):
    yesterday = "2026-07-12"
    today = "2026-07-13"
    _make_losing_trade(store, yesterday, -3000)  # well past cap, but dated yesterday

    yesterday_summary = store.recompute_daily_summary(yesterday, DAILY_LOSS_CAP)
    assert yesterday_summary["circuit_breaker_tripped"] is True

    today_summary = store.recompute_daily_summary(today, DAILY_LOSS_CAP)
    assert today_summary["circuit_breaker_tripped"] is False
    assert today_summary["realized_net_pnl"] == 0
    assert today_summary["trades_count"] == 0


def test_tripped_breaker_blocks_pending_signal_check(store):
    """The breaker flag itself doesn't block signal creation (engine.py's
    loop does that by checking the flag before calling create_pending_signal)
    -- this test asserts the flag state engine.py relies on is correct."""
    today = "2026-07-13"
    _make_losing_trade(store, today, -2500)
    summary = store.recompute_daily_summary(today, DAILY_LOSS_CAP)
    assert summary["circuit_breaker_tripped"] is True

    # engine.py's gate: `if not pending and not summary["circuit_breaker_tripped"]`
    should_scan = not summary["circuit_breaker_tripped"]
    assert should_scan is False


def test_reset_circuit_breaker(store):
    today = "2026-07-13"
    _make_losing_trade(store, today, -2500)
    summary = store.recompute_daily_summary(today, DAILY_LOSS_CAP)
    assert summary["circuit_breaker_tripped"] is True

    store.reset_circuit_breaker(today)
    fresh = store.get_daily_summary(today)
    assert bool(fresh["circuit_breaker_tripped"]) is False
    assert fresh["tripped_at"] is None


def test_pending_signal_ttl_expiry(store):
    sig_id = store.create_pending_signal("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST",
                                          120.0, 75, ttl_seconds=-1)  # already expired
    expired_count = store.expire_stale_pending_signals()
    assert expired_count == 1
    sig = store.get_pending_signal_by_id(sig_id)
    assert sig["status"] == "EXPIRED"
    assert store.get_pending_signal() is None  # get_pending_signal only returns PENDING rows


def test_fresh_pending_signal_not_expired(store):
    sig_id = store.create_pending_signal("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST",
                                          120.0, 75, ttl_seconds=300)
    expired_count = store.expire_stale_pending_signals()
    assert expired_count == 0
    sig = store.get_pending_signal_by_id(sig_id)
    assert sig["status"] == "PENDING"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
