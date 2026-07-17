"""
test_cumulative_drawdown.py
Tests the all-time cumulative drawdown circuit breaker in state_store.py
and engine.py, against an isolated temp SQLite file (never the real
trading_state.db). Run with: pytest test_cumulative_drawdown.py
"""
import sqlite3

import pytest

import config
import state_store
import engine

MAX_DRAWDOWN = 10000


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_trading_state.db")
    monkeypatch.setattr(state_store, "DB_PATH", db_path)
    monkeypatch.setattr(engine, "state_store", state_store)
    state_store.init_db()
    return state_store


def _backdate_exit_time(store, position_id, iso_time):
    conn = sqlite3.connect(store.DB_PATH)
    conn.execute("UPDATE positions SET exit_time = ? WHERE id = ?", (iso_time, position_id))
    conn.commit()
    conn.close()


def _make_trade(store, exit_time, net_pnl):
    pid = store.open_position("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST", 75,
                               120.0, 120.6, 113.6, 130.6)
    store.close_position(pid, 110.0, 109.5, "SL", net_pnl, 10.0, net_pnl)
    _backdate_exit_time(store, pid, exit_time)


def test_cumulative_stats_track_peak_and_drawdown(store):
    _make_trade(store, "2026-01-01T10:00:00", 1000)   # cum 1000, peak 1000
    _make_trade(store, "2026-01-02T10:00:00", 2000)   # cum 3000, peak 3000
    _make_trade(store, "2026-01-03T10:00:00", -1500)  # cum 1500, peak stays 3000

    stats = store.get_cumulative_pnl_stats()
    assert stats["cumulative_pnl"] == 1500
    assert stats["peak_pnl"] == 3000
    assert stats["drawdown"] == 1500 - 3000


def test_breaker_trips_when_drawdown_exceeds_threshold(store, monkeypatch):
    monkeypatch.setitem(config.RISK, "ENABLE_CUMULATIVE_DRAWDOWN_BREAKER", True)
    monkeypatch.setitem(config.RISK, "MAX_CUMULATIVE_DRAWDOWN", MAX_DRAWDOWN)

    _make_trade(store, "2026-01-01T10:00:00", 5000)     # peak 5000
    assert engine.check_cumulative_drawdown_breaker() is False

    _make_trade(store, "2026-01-02T10:00:00", -16000)   # cum -11000, drawdown -16000
    assert engine.check_cumulative_drawdown_breaker() is True
    assert store.get_risk_state()["cumulative_breaker_tripped"] == 1


def test_breaker_never_trips_when_disabled(store, monkeypatch):
    monkeypatch.setitem(config.RISK, "ENABLE_CUMULATIVE_DRAWDOWN_BREAKER", False)
    _make_trade(store, "2026-01-01T10:00:00", -50000)
    assert engine.check_cumulative_drawdown_breaker() is False


def test_breaker_does_not_auto_reset_on_recovery(store, monkeypatch):
    monkeypatch.setitem(config.RISK, "ENABLE_CUMULATIVE_DRAWDOWN_BREAKER", True)
    monkeypatch.setitem(config.RISK, "MAX_CUMULATIVE_DRAWDOWN", MAX_DRAWDOWN)

    _make_trade(store, "2026-01-01T10:00:00", -16000)  # trips immediately
    assert engine.check_cumulative_drawdown_breaker() is True

    _make_trade(store, "2026-01-02T10:00:00", 20000)  # recovers on paper
    assert engine.check_cumulative_drawdown_breaker() is True  # still tripped -- manual reset only


def test_manual_reset_clears_tripped_flag(store, monkeypatch):
    monkeypatch.setitem(config.RISK, "ENABLE_CUMULATIVE_DRAWDOWN_BREAKER", True)
    monkeypatch.setitem(config.RISK, "MAX_CUMULATIVE_DRAWDOWN", MAX_DRAWDOWN)

    _make_trade(store, "2026-01-01T10:00:00", -16000)
    assert engine.check_cumulative_drawdown_breaker() is True

    store.reset_cumulative_breaker()
    assert store.get_risk_state()["cumulative_breaker_tripped"] == 0
    # re-tripping requires drawdown to still breach the threshold at next check;
    # cumulative P&L here is still -16000 with peak 0, so it re-trips immediately --
    # confirming reset genuinely clears the flag rather than being a no-op
    assert engine.check_cumulative_drawdown_breaker() is True


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
