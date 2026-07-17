"""
test_activity_log.py
Tests the running activity log in state_store.py against an isolated temp
SQLite file (never the real trading_state.db). Run with:
pytest test_activity_log.py
"""
import pytest

import state_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_trading_state.db")
    monkeypatch.setattr(state_store, "DB_PATH", db_path)
    state_store.init_db()
    return state_store


def test_log_event_persists_and_is_retrievable(store):
    store.log_event("SIGNAL", "SIGNAL: NIFTY CE strike=24800")
    entries = store.get_recent_activity()
    assert len(entries) == 1
    assert entries[0]["event_type"] == "SIGNAL"
    assert entries[0]["message"] == "SIGNAL: NIFTY CE strike=24800"
    assert entries[0]["timestamp"]


def test_recent_activity_returns_newest_first(store):
    store.log_event("SIGNAL", "first")
    store.log_event("ENTRY", "second")
    store.log_event("EXIT", "third")

    entries = store.get_recent_activity()
    assert [e["message"] for e in entries] == ["third", "second", "first"]


def test_recent_activity_respects_limit(store):
    for i in range(10):
        store.log_event("LIFECYCLE", f"event {i}")
    entries = store.get_recent_activity(limit=3)
    assert len(entries) == 3
    assert entries[0]["message"] == "event 9"  # newest first


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
