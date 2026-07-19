"""
test_orb_state.py
Tests the ORB Strike Mapper's state layer (settings + cached daily levels)
in state_store.py against an isolated temp SQLite file (never the real
trading_state.db). This feature is read-only/informational -- it never
touches positions, pending signals, or any trading decision; these tests
only cover the get/set/cache-invalidate plumbing itself.
Run with: pytest test_orb_state.py
"""
import pytest

import state_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_trading_state.db")
    monkeypatch.setattr(state_store, "DB_PATH", db_path)
    state_store.init_db()
    return state_store


def test_default_orb_settings(store):
    settings = store.get_orb_settings()
    assert settings["instrument"] == "NIFTY"
    assert settings["view"] == "TOP"
    assert settings["selected_type"] == "PUT"
    assert settings["selected_strike"] is None
    assert settings["itm_count"] == 4


def test_set_and_get_orb_settings(store):
    store.set_orb_settings("BANKNIFTY", "BOTTOM", "CALL", 58000.0, 3)
    settings = store.get_orb_settings()
    assert settings["instrument"] == "BANKNIFTY"
    assert settings["view"] == "BOTTOM"
    assert settings["selected_type"] == "CALL"
    assert settings["selected_strike"] == 58000.0
    assert settings["itm_count"] == 3


def test_orb_settings_overwrite_not_duplicate(store):
    store.set_orb_settings("NIFTY", "TOP", "PUT", 24000.0, 4)
    store.set_orb_settings("NIFTY", "TOP", "PUT", 24050.0, 4)
    settings = store.get_orb_settings()
    assert settings["selected_strike"] == 24050.0


def test_store_and_get_orb_levels(store):
    ladder = [
        {"index": 0, "strike": 26000, "option_type": "CE", "value": 145.5, "high_or_low": "High"},
        {"index": 1, "strike": 25950, "option_type": "CE", "value": 168.2, "high_or_low": "High"},
    ]
    store.store_orb_levels("2026-07-19", "NIFTY", ladder)
    levels = store.get_orb_levels("2026-07-19", "NIFTY")
    assert len(levels) == 2
    assert levels[0]["ladder_index"] == 0
    assert levels[0]["strike"] == 26000
    assert levels[0]["value"] == 145.5
    assert levels[1]["ladder_index"] == 1


def test_get_orb_levels_empty_when_none_stored(store):
    assert store.get_orb_levels("2026-07-19", "NIFTY") == []


def test_store_orb_levels_replaces_not_appends(store):
    ladder_v1 = [{"index": 0, "strike": 26000, "option_type": "CE", "value": 145.5, "high_or_low": "High"}]
    ladder_v2 = [{"index": 0, "strike": 24000, "option_type": "PE", "value": 90.0, "high_or_low": "Low"}]
    store.store_orb_levels("2026-07-19", "NIFTY", ladder_v1)
    store.store_orb_levels("2026-07-19", "NIFTY", ladder_v2)
    levels = store.get_orb_levels("2026-07-19", "NIFTY")
    assert len(levels) == 1
    assert levels[0]["strike"] == 24000


def test_orb_levels_scoped_per_instrument(store):
    store.store_orb_levels("2026-07-19", "NIFTY", [
        {"index": 0, "strike": 26000, "option_type": "CE", "value": 145.5, "high_or_low": "High"}])
    store.store_orb_levels("2026-07-19", "BANKNIFTY", [
        {"index": 0, "strike": 58000, "option_type": "PE", "value": 610.0, "high_or_low": "Low"}])
    assert len(store.get_orb_levels("2026-07-19", "NIFTY")) == 1
    assert len(store.get_orb_levels("2026-07-19", "BANKNIFTY")) == 1
    assert store.get_orb_levels("2026-07-19", "NIFTY")[0]["strike"] == 26000


def test_clear_orb_levels(store):
    store.store_orb_levels("2026-07-19", "NIFTY", [
        {"index": 0, "strike": 26000, "option_type": "CE", "value": 145.5, "high_or_low": "High"}])
    assert len(store.get_orb_levels("2026-07-19", "NIFTY")) == 1
    store.clear_orb_levels("2026-07-19", "NIFTY")
    assert store.get_orb_levels("2026-07-19", "NIFTY") == []


def test_dashboard_snapshot_includes_orb_fields(store):
    store.set_orb_settings("NIFTY", "TOP", "PUT", 24000.0, 4)
    store.store_orb_levels(store._today_str(), "NIFTY", [
        {"index": 0, "strike": 24000, "option_type": "CE", "value": 145.5, "high_or_low": "High"}])
    snap = store.get_dashboard_snapshot()
    assert snap["orb_settings"]["selected_strike"] == 24000.0
    assert len(snap["orb_levels"]) == 1


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
