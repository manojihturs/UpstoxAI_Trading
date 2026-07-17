"""
test_auto_confirm_state.py
Tests the live-switchable auto-confirm toggle in state_store.py against an
isolated temp SQLite file (never the real trading_state.db). Run with:
pytest test_auto_confirm_state.py
"""
import pytest

import state_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_trading_state.db")
    monkeypatch.setattr(state_store, "DB_PATH", db_path)
    state_store.init_db()
    return state_store


def test_auto_confirm_defaults_on(store):
    # ON by default per explicit request -- see config.py AUTO_CONFIRM comment
    assert store.get_auto_confirm() is True


def test_set_and_get_auto_confirm_off(store):
    store.set_auto_confirm(False)
    assert store.get_auto_confirm() is False


def test_set_and_get_auto_confirm_on(store):
    store.set_auto_confirm(True)
    assert store.get_auto_confirm() is True


def test_toggle_auto_confirm_back_off(store):
    store.set_auto_confirm(True)
    assert store.get_auto_confirm() is True
    store.set_auto_confirm(False)
    assert store.get_auto_confirm() is False


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
