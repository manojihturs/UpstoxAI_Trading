"""
test_strategy_state.py
Tests the live-switchable active strategy selection in state_store.py
against an isolated temp SQLite file (never the real trading_state.db).
Run with: pytest test_strategy_state.py
"""
import pytest

import state_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_trading_state.db")
    monkeypatch.setattr(state_store, "DB_PATH", db_path)
    state_store.init_db()
    return state_store


def test_default_active_strategy_is_ut_bot_conservative(store):
    assert store.get_active_strategy() == "UT_BOT_CONSERVATIVE"


def test_set_and_get_active_strategy(store):
    store.set_active_strategy("PIVOT_POINT")
    assert store.get_active_strategy() == "PIVOT_POINT"


def test_switching_strategy_multiple_times(store):
    for name in ("BASELINE", "STRICT_ADX", "CONFIRMATION_CANDLE", "PIVOT_POINT", "EMA50_TREND_FILTER"):
        store.set_active_strategy(name)
        assert store.get_active_strategy() == name


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
