"""
test_orb_ladder_logic.py
Tests option_selector.get_orb_ladder()'s pure logic (truth table, ladder
direction, defensive guards) with get_option_chain/get_intraday_candles
monkeypatched -- no real network calls. Verifies the exact behavior spec'd
by the user (analyzed 2026-07-19): which side gets fetched, High vs Low,
and which direction the ITM ladder walks for each (view, selected_type)
combination.
Run with: pytest test_orb_ladder_logic.py
"""
import datetime

import pandas as pd
import pytest

import option_selector


def _fake_chain(strikes):
    """Builds a minimal option-chain response: one row per strike, with
    predictable instrument_keys ('KEY_<strike>_CE'/'KEY_<strike>_PE')."""
    return [
        {
            "strike_price": strike,
            "call_options": {"instrument_key": f"KEY_{strike}_CE"},
            "put_options": {"instrument_key": f"KEY_{strike}_PE"},
        }
        for strike in strikes
    ]


def _candles_df(first_time_str, high, low):
    """One 9:15 (or other) candle followed by a couple of later ones --
    only the FIRST row matters to get_orb_ladder."""
    ts0 = pd.Timestamp(f"2026-07-19 {first_time_str}:00")
    return pd.DataFrame({
        "timestamp": [ts0, ts0 + pd.Timedelta(minutes=5), ts0 + pd.Timedelta(minutes=10)],
        "open": [high - 1, high, low], "high": [high, high, high],
        "low": [low, low, low], "close": [high - 0.5, high - 0.5, low + 0.5],
        "volume": [100, 90, 80], "oi": [0, 0, 0],
    })


@pytest.fixture
def patched(monkeypatch):
    """Every strike's ORB candle is priced as 100 + strike/1000 for High,
    50 + strike/1000 for Low -- distinct, easy-to-check-by-eye values."""
    def fake_get_option_chain(expiry_date, headers, instrument_key=None):
        return _fake_chain([25800, 25850, 25900, 25950, 26000, 26050, 26100])

    def fake_get_intraday_candles(instrument_key, headers, interval_minutes=5):
        strike = int(instrument_key.split("_")[1])
        return _candles_df("09:15", 100 + strike / 1000, 50 + strike / 1000)

    monkeypatch.setattr(option_selector, "get_option_chain", fake_get_option_chain)
    monkeypatch.setattr(option_selector, "get_intraday_candles", fake_get_intraday_candles)
    return option_selector


def test_top_put_fetches_call_high_ladder_goes_down(patched):
    ladder = patched.get_orb_ladder("SPOT_KEY", "2026-07-24", {}, 26000, "PUT", "TOP", 4, 50)
    assert len(ladder) == 5
    assert [row["strike"] for row in ladder] == [26000, 25950, 25900, 25850, 25800]
    assert all(row["option_type"] == "CE" for row in ladder)
    assert all(row["high_or_low"] == "High" for row in ladder)
    assert ladder[0]["value"] == pytest.approx(100 + 26000 / 1000)


def test_top_call_fetches_put_low_ladder_goes_up(patched):
    ladder = patched.get_orb_ladder("SPOT_KEY", "2026-07-24", {}, 26000, "CALL", "TOP", 4, 50)
    assert [row["strike"] for row in ladder] == [26000, 26050, 26100, 26150, 26200]
    assert all(row["option_type"] == "PE" for row in ladder)
    assert all(row["high_or_low"] == "Low" for row in ladder)
    assert ladder[0]["value"] == pytest.approx(50 + 26000 / 1000)


def test_bottom_put_fetches_call_low(patched):
    ladder = patched.get_orb_ladder("SPOT_KEY", "2026-07-24", {}, 26000, "PUT", "BOTTOM", 4, 50)
    assert all(row["option_type"] == "CE" for row in ladder)
    assert all(row["high_or_low"] == "Low" for row in ladder)
    assert [row["strike"] for row in ladder] == [26000, 25950, 25900, 25850, 25800]  # same ITM direction as TOP+PUT


def test_bottom_call_fetches_put_high(patched):
    ladder = patched.get_orb_ladder("SPOT_KEY", "2026-07-24", {}, 26000, "CALL", "BOTTOM", 4, 50)
    assert all(row["option_type"] == "PE" for row in ladder)
    assert all(row["high_or_low"] == "High" for row in ladder)
    assert [row["strike"] for row in ladder] == [26000, 26050, 26100, 26150, 26200]  # same ITM direction as TOP+CALL


def test_itm_count_controls_ladder_length(patched):
    assert len(patched.get_orb_ladder("SPOT_KEY", "2026-07-24", {}, 26000, "PUT", "TOP", 0, 50)) == 1
    assert len(patched.get_orb_ladder("SPOT_KEY", "2026-07-24", {}, 26000, "PUT", "TOP", 2, 50)) == 3


def test_missing_strike_in_chain_gives_none_value(monkeypatch):
    def fake_chain(expiry_date, headers, instrument_key=None):
        return _fake_chain([26000])  # only own strike exists, no ITM strikes below it

    def fake_candles(instrument_key, headers, interval_minutes=5):
        return _candles_df("09:15", 145.0, 130.0)

    monkeypatch.setattr(option_selector, "get_option_chain", fake_chain)
    monkeypatch.setattr(option_selector, "get_intraday_candles", fake_candles)

    ladder = option_selector.get_orb_ladder("SPOT_KEY", "2026-07-24", {}, 26000, "PUT", "TOP", 2, 50)
    assert ladder[0]["value"] is not None       # own strike (26000) exists
    assert ladder[1]["value"] is None           # 25950 not in chain
    assert ladder[2]["value"] is None           # 25900 not in chain


def test_candle_not_starting_at_915_is_rejected(monkeypatch):
    """Defensive guard: if the intraday feed's first candle isn't 09:15,
    don't silently mislabel a later candle as the opening range."""
    def fake_chain(expiry_date, headers, instrument_key=None):
        return _fake_chain([26000])

    def fake_candles(instrument_key, headers, interval_minutes=5):
        return _candles_df("09:30", 145.0, 130.0)  # NOT 9:15

    monkeypatch.setattr(option_selector, "get_option_chain", fake_chain)
    monkeypatch.setattr(option_selector, "get_intraday_candles", fake_candles)

    ladder = option_selector.get_orb_ladder("SPOT_KEY", "2026-07-24", {}, 26000, "PUT", "TOP", 0, 50)
    assert ladder[0]["value"] is None


def test_empty_candles_gives_none_value(monkeypatch):
    def fake_chain(expiry_date, headers, instrument_key=None):
        return _fake_chain([26000])

    def fake_candles(instrument_key, headers, interval_minutes=5):
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

    monkeypatch.setattr(option_selector, "get_option_chain", fake_chain)
    monkeypatch.setattr(option_selector, "get_intraday_candles", fake_candles)

    ladder = option_selector.get_orb_ladder("SPOT_KEY", "2026-07-24", {}, 26000, "PUT", "TOP", 0, 50)
    assert ladder[0]["value"] is None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
