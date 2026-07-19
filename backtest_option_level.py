"""
backtest_option_level.py
Formalizes and backtests the "previous-day-close option premium level"
concept described in a Tamil YouTube options-education video (analyzed
2026-07-19): each day, the previous day's close spot + realized vol prices
the ATM strike's one-step OTM call and put via Black-Scholes; their average
becomes a "confirmation level." Entry requires the live CE premium to close
above that level AND the live PE premium to close below it (bullish), or
the mirror image (bearish).

See strategies.signal_option_level_confirmation and
backtest.add_option_level_columns for the full derivation.

*** WHY THIS IS DELIBERATELY KEPT SEPARATE FROM strategies.STRATEGIES ***
This strategy is NOT wired into engine.py for live trading (unlike the 7
strategies in the dashboard dropdown) -- it needs real intraday option
premiums, which live trading gets from Upstox directly, not from a
Black-Scholes simulation. Adding it to strategies.STRATEGIES would make it
selectable in the live dashboard, where it would silently never fire.

*** THE KEY THEORETICAL CAVEAT ***
Because both premiums here are Black-Scholes functions of the SAME spot
price, put-call parity holds exactly by construction in this simulation.
"CE confirms up AND PE confirms up" is therefore mathematically the SAME
event as "spot crossed the level" -- not two independent confirmations.
This backtest can only test whether the DERIVED LEVEL ITSELF is a useful
breakout level (a legitimate question) -- it CANNOT test the video's
implicit premise that real call/put order flow sometimes diverges from
fair value, since simulated premiums structurally cannot diverge that way.
A positive result here says "anchoring a level off previous-day option
mid-points beats/matches other level-based strategies on simulated data."
It does NOT validate the video's real-market edge claim.
"""
import os
import pandas as pd

import config
import cost_model
import strategies
from signal_engine import compute_indicators
from option_selector import round_to_atm
from engine import evaluate_position, compute_sl_points
from backtest import (
    black_scholes_price, compute_realized_vol, add_option_level_columns,
    CSV_FILES, ASSUMED_DAYS_TO_EXPIRY, RISK_FREE_RATE,
)

TRAIN_TEST_SPLIT_DATE = "2025-07-01"  # same split used throughout this repo


def prepare_df(csv_path, cfg):
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    df = compute_indicators(df)  # not used by this strategy's signal, kept for a consistent df shape
    df["realized_vol"] = compute_realized_vol(df)
    df = df.dropna(subset=["realized_vol"]).reset_index(drop=True)
    df = add_option_level_columns(df, cfg)
    return df


def run_strategy(df, instrument_name, cfg):
    years_to_expiry = ASSUMED_DAYS_TO_EXPIRY / 365
    trades = []
    position = None

    for _, row in df.iterrows():
        current_time = row["timestamp"].time()
        spot = row["close"]
        sigma = row["realized_vol"]

        if position is not None:
            option_price = black_scholes_price(
                spot, position["strike"], years_to_expiry, RISK_FREE_RATE, sigma, position["direction"]
            )
            new_sl, new_tsl_armed, exit_reason = evaluate_position(position, option_price, current_time, cfg)
            position["current_sl"] = new_sl
            position["tsl_armed"] = new_tsl_armed
            if exit_reason:
                exit_net = cost_model.apply_slippage(option_price, "SELL")
                qty = cfg["lot_size"]
                gross_pnl = (exit_net - position["entry_ltp_net"]) * qty
                costs_total = cost_model.compute_round_trip_costs(
                    position["entry_ltp_net"], exit_net, qty, cfg["exchange"]
                )
                net_pnl = gross_pnl - costs_total
                trades.append({
                    "instrument": instrument_name, "entry_time": position["entry_time"],
                    "exit_time": row["timestamp"], "exit_reason": exit_reason, "net_pnl": net_pnl,
                })
                position = None
            continue

        signal = strategies.signal_option_level_confirmation(row)
        if signal not in ("CE", "PE"):
            continue

        strike = round_to_atm(spot, cfg["strike_step"])
        entry_raw = black_scholes_price(spot, strike, years_to_expiry, RISK_FREE_RATE, sigma, signal)
        if entry_raw <= 0:
            continue
        sl_points = compute_sl_points(instrument_name, entry_raw)
        if sl_points is None:
            continue
        entry_net = cost_model.apply_slippage(entry_raw, "BUY")
        position = {
            "entry_time": row["timestamp"], "direction": signal, "strike": strike,
            "entry_ltp_net": entry_net, "current_sl": entry_net - sl_points,
            "tsl_armed": False, "target_price": entry_net + cfg["target_points"],
        }

    return trades


def summarize(trades, label):
    if not trades:
        return {"label": label, "trades": 0, "win_rate": None, "total_pnl": 0.0, "avg_pnl": None}
    df = pd.DataFrame(trades)
    wins = int((df["net_pnl"] > 0).sum())
    return {
        "label": label,
        "trades": len(df),
        "win_rate": round(wins / len(df) * 100, 1),
        "total_pnl": round(df["net_pnl"].sum(), 2),
        "avg_pnl": round(df["net_pnl"].mean(), 2),
    }


def main():
    train_trades, test_trades = [], []
    for name, cfg in config.INSTRUMENTS.items():
        csv_path = CSV_FILES[name]
        if not os.path.exists(csv_path):
            continue
        df = prepare_df(csv_path, cfg)
        train_df = df[df["timestamp"] < TRAIN_TEST_SPLIT_DATE]
        test_df = df[df["timestamp"] >= TRAIN_TEST_SPLIT_DATE]
        train_trades += run_strategy(train_df, name, cfg)
        test_trades += run_strategy(test_df, name, cfg)

    results = [
        summarize(train_trades, f"OPTION_LEVEL_CONFIRMATION [TRAIN <{TRAIN_TEST_SPLIT_DATE}]"),
        summarize(test_trades, f"OPTION_LEVEL_CONFIRMATION [TEST >={TRAIN_TEST_SPLIT_DATE}]"),
    ]
    out = pd.DataFrame(results)
    print(out.to_string(index=False))

    if train_trades or test_trades:
        all_trades = pd.DataFrame(train_trades + test_trades)
        print("\nBy instrument (train+test combined):")
        print(all_trades.groupby("instrument")["net_pnl"].agg(["count", "sum", "mean"]).round(2).to_string())
        print("\nBy exit reason (train+test combined):")
        print(all_trades.groupby("exit_reason")["net_pnl"].agg(["count", "sum", "mean"]).round(2).to_string())

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_option_level_results.csv")
    out.to_csv(out_path, index=False)
    print(f"\nSaved to {out_path}")
    print(
        "\nCAVEAT: simulated premiums obey put-call parity exactly by construction, so this "
        "backtest can only test whether the DERIVED LEVEL is a useful breakout level -- it "
        "cannot validate the video's implicit claim that real call/put order flow sometimes "
        "diverges from fair value. See this file's module docstring for the full explanation."
    )


if __name__ == "__main__":
    main()
