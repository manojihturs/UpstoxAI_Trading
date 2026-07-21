"""
test_trade_throttling.py
Tests the race-proof single-position guard in state_store.open_position()
(found live 2026-07-17: two engine.py instances against the same DB both
passed the old check-then-open pattern, opening up to 4 simultaneous
positions on one signal) and the MAX_TRADES_PER_DAY / MAX_CONSECUTIVE_LOSSES
helpers engine.py uses to throttle entries. Isolated temp SQLite file, never
the real trading_state.db. Run with: pytest test_trade_throttling.py
"""
import sqlite3

import pytest

import state_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_trading_state.db")
    monkeypatch.setattr(state_store, "DB_PATH", db_path)
    state_store.init_db()
    return state_store


def _backdate_exit_time(store, position_id, date_str, time_str):
    conn = sqlite3.connect(store.DB_PATH)
    conn.execute("UPDATE positions SET exit_time = ? WHERE id = ?",
                 (f"{date_str}T{time_str}", position_id))
    conn.commit()
    conn.close()


def _open_and_close(store, date_str, time_str, net_pnl):
    pid = store.open_position("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST", 65,
                               120.0, 120.6, 113.6, 130.6)
    store.close_position(pid, 110.0, 109.5, "SL" if net_pnl <= 0 else "TARGET", net_pnl, 10.0, net_pnl)
    _backdate_exit_time(store, pid, date_str, time_str)
    return pid


def test_open_position_race_guard_blocks_second_open(store):
    """Simulates the exact live failure: two 'threads' both see no open
    position, then both call open_position(). Only the first should
    succeed; the second must get None back, not a second OPEN row."""
    first_id = store.open_position("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST", 65,
                                    120.0, 120.6, 113.6, 130.6)
    assert first_id is not None

    second_id = store.open_position("BANKNIFTY", "PE", 58000, "2026-07-17", "NSE_FO|TEST2", 30,
                                     600.0, 603.0, 588.0, 615.0)
    assert second_id is None  # the race-proof guard rejects it

    conn = sqlite3.connect(store.DB_PATH)
    conn.row_factory = sqlite3.Row
    open_rows = conn.execute("SELECT * FROM positions WHERE status = 'OPEN'").fetchall()
    conn.close()
    assert len(open_rows) == 1
    assert open_rows[0]["id"] == first_id


def test_open_position_succeeds_again_once_flat(store):
    first_id = store.open_position("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST", 65,
                                    120.0, 120.6, 113.6, 130.6)
    store.close_position(first_id, 130.0, 129.5, "TARGET", 500.0, 10.0, 490.0)

    second_id = store.open_position("BANKNIFTY", "PE", 58000, "2026-07-17", "NSE_FO|TEST2", 30,
                                     600.0, 603.0, 588.0, 615.0)
    assert second_id is not None  # flat now, so a fresh open succeeds


def test_today_trade_count(store):
    today = "2026-07-17"
    assert store.get_today_trade_count(today) == 0
    _open_and_close(store, today, "10:00:00", 100)
    _open_and_close(store, today, "11:00:00", -50)
    assert store.get_today_trade_count(today) == 2


def test_today_trade_count_ignores_other_days(store):
    _open_and_close(store, "2026-07-16", "10:00:00", 100)
    assert store.get_today_trade_count("2026-07-17") == 0


def test_consecutive_losses_counts_from_most_recent(store):
    today = "2026-07-17"
    _open_and_close(store, today, "10:00:00", 100)   # win
    _open_and_close(store, today, "11:00:00", -50)   # loss 1
    _open_and_close(store, today, "12:00:00", -75)   # loss 2
    assert store.get_consecutive_losses(today) == 2


def test_consecutive_losses_resets_on_a_win(store):
    today = "2026-07-17"
    _open_and_close(store, today, "10:00:00", -50)   # loss
    _open_and_close(store, today, "11:00:00", -75)   # loss
    _open_and_close(store, today, "12:00:00", 200)   # win breaks the streak
    assert store.get_consecutive_losses(today) == 0


def test_consecutive_losses_zero_with_no_trades(store):
    assert store.get_consecutive_losses("2026-07-17") == 0


def test_open_position_persists_strategy(store):
    pid = store.open_position("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST", 65,
                               120.0, 120.6, 113.6, 130.6, strategy="UT_BOT_CONSERVATIVE")
    position = store.get_open_position()
    assert position["id"] == pid
    assert position["strategy"] == "UT_BOT_CONSERVATIVE"


def test_open_position_strategy_defaults_to_none(store):
    store.open_position("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST", 65,
                         120.0, 120.6, 113.6, 130.6)
    position = store.get_open_position()
    assert position["strategy"] is None


def test_closed_position_keeps_its_entry_strategy(store):
    pid = store.open_position("NIFTY", "CE", 24800, "2026-07-17", "NSE_FO|TEST", 65,
                               120.0, 120.6, 113.6, 130.6, strategy="SWING_STRUCTURE")
    store.close_position(pid, 130.0, 129.5, "TARGET", 500.0, 10.0, 490.0)
    closed = store.get_position_by_id(pid)
    assert closed["strategy"] == "SWING_STRUCTURE"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
