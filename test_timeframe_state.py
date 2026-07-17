"""
test_timeframe_state.py
Tests the live-switchable active candle timeframe in state_store.py
against an isolated temp SQLite file (never the real trading_state.db).
Run with: pytest test_timeframe_state.py
"""
import pytest

import config
import state_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_trading_state.db")
    monkeypatch.setattr(state_store, "DB_PATH", db_path)
    state_store.init_db()
    return state_store


def test_default_active_timeframe_is_15_minutes(store):
    assert store.get_active_timeframe() == 15


def test_set_and_get_active_timeframe(store):
    store.set_active_timeframe(5)
    assert store.get_active_timeframe() == 5


def test_switching_timeframe_multiple_times(store):
    for minutes in config.TIMEFRAME["AVAILABLE_MINUTES"]:
        store.set_active_timeframe(minutes)
        assert store.get_active_timeframe() == minutes


def test_all_configured_timeframes_are_the_ones_verified_live():
    # Documents that these specific values were checked directly against
    # Upstox's intraday API before being offered in the dashboard -- not
    # guessed. See config.py TIMEFRAME comment.
    assert config.TIMEFRAME["AVAILABLE_MINUTES"] == [1, 3, 5, 15, 30, 60]
    assert config.TIMEFRAME["DEFAULT_MINUTES"] == 15


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
