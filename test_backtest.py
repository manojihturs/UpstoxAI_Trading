"""
test_backtest.py
Sanity checks for the Black-Scholes pricing helper used in backtest.py --
not a test of "does the strategy make money" (that's not a testable
invariant), just that the pricing math behaves the way option pricing
should. Run with: pytest test_backtest.py
"""
import pytest

import backtest


def test_atm_call_and_put_roughly_equal_at_zero_rate():
    # put-call parity: at r=0 and S=K, call and put should be ~equal
    call = backtest.black_scholes_price(100, 100, 3 / 365, 0.0, 0.2, "CE")
    put = backtest.black_scholes_price(100, 100, 3 / 365, 0.0, 0.2, "PE")
    assert abs(call - put) < 1e-6


def test_call_price_increases_with_spot():
    low = backtest.black_scholes_price(95, 100, 3 / 365, 0.065, 0.2, "CE")
    high = backtest.black_scholes_price(105, 100, 3 / 365, 0.065, 0.2, "CE")
    assert high > low


def test_put_price_increases_as_spot_falls():
    high_spot = backtest.black_scholes_price(105, 100, 3 / 365, 0.065, 0.2, "PE")
    low_spot = backtest.black_scholes_price(95, 100, 3 / 365, 0.065, 0.2, "PE")
    assert low_spot > high_spot


def test_price_never_negative():
    for spot in (50, 100, 150):
        for opt in ("CE", "PE"):
            price = backtest.black_scholes_price(spot, 100, 3 / 365, 0.065, 0.2, opt)
            assert price >= 0


def test_deep_itm_call_approaches_intrinsic_value():
    price = backtest.black_scholes_price(200, 100, 3 / 365, 0.065, 0.1, "CE")
    intrinsic = 200 - 100
    assert price >= intrinsic  # time value is non-negative, price >= intrinsic
    assert price - intrinsic < 5  # deep ITM short-dated option has little time value


def test_zero_time_to_expiry_returns_intrinsic_value():
    assert backtest.black_scholes_price(110, 100, 0, 0.065, 0.2, "CE") == 10
    assert backtest.black_scholes_price(90, 100, 0, 0.065, 0.2, "CE") == 0
    assert backtest.black_scholes_price(90, 100, 0, 0.065, 0.2, "PE") == 10


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
