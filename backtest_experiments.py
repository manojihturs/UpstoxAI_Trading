"""
backtest_experiments.py
Compares all 5 named strategies in strategies.py against a strict
chronological TRAIN/TEST split -- a strategy is only worth trusting if it
holds up on data it was never checked against. Uses the exact same
strategy functions engine.py dispatches live (strategies.py), so this is a
genuine comparison of what's selectable in the dashboard dropdown, not a
lookalike reimplementation.

Nothing here changes live behavior by itself -- it's a research report.
Switching the active strategy is done via the dashboard or
config.STRATEGY_STATE.
"""
import os
import pandas as pd

import config
import cost_model
import strategies
from signal_engine import compute_indicators
from option_selector import round_to_atm
from engine import evaluate_position, compute_sl_points
from backtest import black_scholes_price, compute_realized_vol, add_pivot_column, CSV_FILES, ASSUMED_DAYS_TO_EXPIRY, RISK_FREE_RATE

TRAIN_TEST_SPLIT_DATE = "2025-07-01"  # ~2.5yr train / ~1yr test, chronological (no shuffling)


def prepare_df(csv_path):
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    df = compute_indicators(df)
    df = add_pivot_column(df)              # adds pivot_pp, prev_close (backtest-specific)
    df = strategies.prepare_columns(df)     # adds EMA50, bull/bear_cross_prev (overwrites prev_close identically)
    df["realized_vol"] = compute_realized_vol(df)
    df = df.dropna(subset=["realized_vol"]).reset_index(drop=True)
    return df


def run_strategy(strategy_name, df, instrument_name, cfg):
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
                    "instrument": instrument_name, "exit_time": row["timestamp"],
                    "exit_reason": exit_reason, "net_pnl": net_pnl,
                })
                position = None
            continue

        signal = strategies.get_signal_for_strategy(strategy_name, row)
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
    prepared = {
        name: prepare_df(CSV_FILES[name])
        for name in config.INSTRUMENTS
        if os.path.exists(CSV_FILES[name])
    }

    results = []
    for strategy_name in strategies.STRATEGIES:
        train_trades, test_trades = [], []
        for instrument_name, cfg in config.INSTRUMENTS.items():
            if instrument_name not in prepared:
                continue
            df = prepared[instrument_name]
            train_df = df[df["timestamp"] < TRAIN_TEST_SPLIT_DATE]
            test_df = df[df["timestamp"] >= TRAIN_TEST_SPLIT_DATE]

            train_trades += run_strategy(strategy_name, train_df, instrument_name, cfg)
            test_trades += run_strategy(strategy_name, test_df, instrument_name, cfg)

        label = strategies.STRATEGIES[strategy_name]["label"]
        results.append(summarize(train_trades, f"{strategy_name} [TRAIN <{TRAIN_TEST_SPLIT_DATE}]"))
        results.append(summarize(test_trades, f"{strategy_name} [TEST >={TRAIN_TEST_SPLIT_DATE}]"))
        print(f"{strategy_name}: {label}")

    out = pd.DataFrame(results)
    print(out.to_string(index=False))
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_experiments_results.csv")
    out.to_csv(out_path, index=False)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
