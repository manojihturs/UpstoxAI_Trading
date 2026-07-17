"""
test_signal_confirmations.py
Deterministic tests for the optional signal-confirmation filters in
signal_engine.py (PCR and trend-filter) -- pure functions, no network.
Run with: pytest test_signal_confirmations.py
"""
from signal_engine import confirm_with_pcr, confirm_with_trend_filter


def test_pcr_confirms_bullish_ce():
    assert confirm_with_pcr("CE", pcr=1.2, bullish_min=1.1, bearish_max=0.9) is True


def test_pcr_rejects_ce_when_not_bullish():
    assert confirm_with_pcr("CE", pcr=1.0, bullish_min=1.1, bearish_max=0.9) is False


def test_pcr_confirms_bearish_pe():
    assert confirm_with_pcr("PE", pcr=0.8, bullish_min=1.1, bearish_max=0.9) is True


def test_pcr_rejects_pe_when_not_bearish():
    assert confirm_with_pcr("PE", pcr=1.0, bullish_min=1.1, bearish_max=0.9) is False


def test_trend_filter_confirms_ce_above_ema():
    assert confirm_with_trend_filter("CE", close=24100, ema_long=24000) is True


def test_trend_filter_rejects_ce_below_ema():
    assert confirm_with_trend_filter("CE", close=23900, ema_long=24000) is False


def test_trend_filter_confirms_pe_below_ema():
    assert confirm_with_trend_filter("PE", close=23900, ema_long=24000) is True


def test_trend_filter_rejects_pe_above_ema():
    assert confirm_with_trend_filter("PE", close=24100, ema_long=24000) is False


def test_trend_filter_rejects_unknown_direction():
    assert confirm_with_trend_filter("HOLD", close=24100, ema_long=24000) is False


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
