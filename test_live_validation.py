"""
test_live_validation.py
Deterministic tests for live_validation.py's pure functions -- synthetic
data, no network/DB.
"""
import pandas as pd
import pytest

import live_validation as lv


def test_binomial_test_none_when_no_trades():
    assert lv.binomial_win_rate_test(0, 0) is None


def test_binomial_test_not_significant_at_exactly_50_percent():
    result = lv.binomial_win_rate_test(50, 100, expected_p=0.5)
    assert result["observed_win_rate"] == 50.0
    assert result["significant"] is False


def test_binomial_test_significant_for_extreme_win_rate():
    result = lv.binomial_win_rate_test(90, 100, expected_p=0.5)
    assert result["significant"] is True
    assert result["p_value"] < 0.05


def test_binomial_test_not_significant_for_small_sample():
    # 3/5 wins is nowhere near enough evidence to reject 50%
    result = lv.binomial_win_rate_test(3, 5, expected_p=0.5)
    assert result["significant"] is False


def make_closed_position(net_pnl, exit_time):
    return {"net_pnl": net_pnl, "exit_time": exit_time, "instrument": "NIFTY", "exit_reason": "TARGET"}


def test_summarize_live_trades_none_when_empty():
    assert lv.summarize_live_trades([]) is None


def test_summarize_live_trades_basic_stats():
    trades = [
        make_closed_position(100.0, "2026-01-01T10:00:00"),
        make_closed_position(-50.0, "2026-01-02T10:00:00"),
        make_closed_position(200.0, "2026-01-03T10:00:00"),
    ]
    summary = lv.summarize_live_trades(trades)
    assert summary["total_trades"] == 3
    assert summary["wins"] == 2
    assert summary["losses"] == 1
    assert summary["win_rate_pct"] == pytest.approx(66.7, abs=0.1)
    assert summary["total_pnl"] == pytest.approx(250.0)
    assert summary["avg_pnl"] == pytest.approx(83.33, abs=0.01)


def test_summarize_live_trades_drawdown_is_negative_or_zero():
    trades = [
        make_closed_position(100.0, "2026-01-01T10:00:00"),
        make_closed_position(-150.0, "2026-01-02T10:00:00"),
        make_closed_position(50.0, "2026-01-03T10:00:00"),
    ]
    summary = lv.summarize_live_trades(trades)
    assert summary["max_drawdown"] <= 0


def test_load_backtest_baseline_returns_none_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(lv, "BACKTEST_EXPERIMENTS_CSV", str(tmp_path / "nonexistent.csv"))
    assert lv.load_backtest_test_window_baseline("UT_BOT_CONSERVATIVE") is None


def test_load_backtest_baseline_parses_test_window_row(monkeypatch, tmp_path):
    csv_path = tmp_path / "results.csv"
    df = pd.DataFrame([
        {"label": "UT_BOT_CONSERVATIVE [TRAIN <2025-07-01]", "trades": 1765, "win_rate": 48.8,
         "total_pnl": 79136.42, "avg_pnl": 44.84},
        {"label": "UT_BOT_CONSERVATIVE [TEST >=2025-07-01]", "trades": 687, "win_rate": 50.9,
         "total_pnl": 52621.36, "avg_pnl": 76.60},
    ])
    df.to_csv(csv_path, index=False)
    monkeypatch.setattr(lv, "BACKTEST_EXPERIMENTS_CSV", str(csv_path))

    baseline = lv.load_backtest_test_window_baseline("UT_BOT_CONSERVATIVE")
    assert baseline["trades"] == 687
    assert baseline["win_rate_pct"] == 50.9
    assert baseline["total_pnl"] == pytest.approx(52621.36)


def test_compare_live_vs_backtest_no_live_trades():
    result = lv.compare_live_vs_backtest("UT_BOT_CONSERVATIVE", [])
    assert result["live"] is None
    assert "No closed live trades" in result["verdict"]


def test_compare_live_vs_backtest_too_few_trades_for_significance():
    trades = [make_closed_position(10.0, "2026-01-01T10:00:00")] * 5
    result = lv.compare_live_vs_backtest("UT_BOT_CONSERVATIVE", trades)
    assert "too few" in result["verdict"]


def test_compare_live_vs_backtest_tracking_verdict(monkeypatch, tmp_path):
    csv_path = tmp_path / "results.csv"
    pd.DataFrame([
        {"label": "UT_BOT_CONSERVATIVE [TEST >=2025-07-01]", "trades": 687, "win_rate": 50.0,
         "total_pnl": 52621.36, "avg_pnl": 76.60},
    ]).to_csv(csv_path, index=False)
    monkeypatch.setattr(lv, "BACKTEST_EXPERIMENTS_CSV", str(csv_path))

    # 55% win rate, positive P&L -- within 10 points of the 50% baseline, same P&L sign
    trades = (
        [make_closed_position(100.0, f"2026-01-{i:02d}T10:00:00") for i in range(1, 23)]
        + [make_closed_position(-50.0, f"2026-02-{i:02d}T10:00:00") for i in range(1, 19)]
    )
    result = lv.compare_live_vs_backtest("UT_BOT_CONSERVATIVE", trades)
    assert "Tracking" in result["verdict"]


def test_compare_live_vs_backtest_diverging_verdict(monkeypatch, tmp_path):
    csv_path = tmp_path / "results.csv"
    pd.DataFrame([
        {"label": "UT_BOT_CONSERVATIVE [TEST >=2025-07-01]", "trades": 687, "win_rate": 80.0,
         "total_pnl": 52621.36, "avg_pnl": 76.60},
    ]).to_csv(csv_path, index=False)
    monkeypatch.setattr(lv, "BACKTEST_EXPERIMENTS_CSV", str(csv_path))

    # 20% win rate live vs 80% backtest baseline -- way off
    trades = (
        [make_closed_position(-50.0, f"2026-01-{i:02d}T10:00:00") for i in range(1, 33)]
        + [make_closed_position(100.0, f"2026-02-{i:02d}T10:00:00") for i in range(1, 9)]
    )
    result = lv.compare_live_vs_backtest("UT_BOT_CONSERVATIVE", trades)
    assert "Diverging" in result["verdict"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
