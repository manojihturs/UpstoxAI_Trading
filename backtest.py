"""
backtest.py
Replays the historical spot CSVs (nifty50_15min.csv, banknifty_15min.csv,
sensex_15min.csv) through the EXACT same rules engine.py trades live with:
signal_engine.compute_indicators/get_signal for entries, engine.py's
evaluate_position for SL/TSL/target/EOD exits, engine.py's compute_sl_points
for stop sizing, and cost_model for brokerage/STT/GST/slippage. Reusing
these functions directly (not reimplementing them) is what makes this a
genuine backtest of the live rules, not a lookalike.

*** IMPORTANT CAVEAT -- READ BEFORE TRUSTING ANY NUMBER BELOW ***
This repo only has historical SPOT index candles, not historical option
premiums (Upstox doesn't offer a practical way to pull years of per-strike
option history for expired weekly contracts). So CE/PE premiums here are
SIMULATED with the Black-Scholes formula, using:
  - a fixed assumed time-to-expiry (ASSUMED_DAYS_TO_EXPIRY below), because
    the actual historical weekly-expiry calendar isn't tracked in this
    dataset and NSE/BSE have changed expiry-day rules multiple times over
    the years covered here. The live engine instead asks Upstox for the
    REAL nearest expiry every trade, so live time-to-expiry will vary.
  - realized volatility computed from the spot series itself as a stand-in
    for implied volatility (no historical IV/option-chain data available).
    Real IV is usually higher than realized vol and has its own skew.
  - no bid-ask spread or liquidity effects beyond the flat SLIPPAGE_PCT
    already in cost_model.py.
This means the numbers below are a rough, theoretical approximation of how
this rule set would have behaved -- not a measurement of real historical
option P&L, and NOT a promise about future performance either way.
"""
import math
import os
import json
import datetime

import numpy as np
import pandas as pd

import config
import cost_model
from signal_engine import compute_indicators, get_signal, confirm_with_trend_filter, _ema
from option_selector import round_to_atm
from engine import evaluate_position, compute_sl_points

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CSV_FILES = {
    "NIFTY": os.path.join(BASE_DIR, "nifty50_15min.csv"),
    "BANKNIFTY": os.path.join(BASE_DIR, "banknifty_15min.csv"),
    "SENSEX": os.path.join(BASE_DIR, "sensex_15min.csv"),
}

ASSUMED_DAYS_TO_EXPIRY = 3      # see caveat above -- real expiry varies live
RISK_FREE_RATE = 0.065          # approximate, negligible impact at this horizon
REALIZED_VOL_WINDOW = 500       # ~20 trading days of 15-min candles
CANDLES_PER_YEAR = 25 * 252     # ~25 fifteen-min candles/trading day, 252 trading days/year
MIN_SIGMA = 0.05                # floor to avoid near-zero-vol degenerate pricing


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black_scholes_price(spot, strike, years_to_expiry, rate, sigma, option_type):
    """Theoretical CE/PE premium. See module docstring for why this is an
    approximation rather than real historical option data."""
    sigma = max(sigma, MIN_SIGMA)
    if years_to_expiry <= 0:
        intrinsic = (spot - strike) if option_type == "CE" else (strike - spot)
        return max(0.0, intrinsic)

    d1 = (math.log(spot / strike) + (rate + sigma ** 2 / 2) * years_to_expiry) / (sigma * math.sqrt(years_to_expiry))
    d2 = d1 - sigma * math.sqrt(years_to_expiry)

    if option_type == "CE":
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * years_to_expiry) * _norm_cdf(d2)
    return strike * math.exp(-rate * years_to_expiry) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def compute_realized_vol(df):
    """Causal (no lookahead) annualized realized volatility from the spot
    close series, used as the implied-vol stand-in for pricing."""
    log_ret = np.log(df["close"] / df["close"].shift(1))
    vol = log_ret.rolling(REALIZED_VOL_WINDOW, min_periods=100).std() * math.sqrt(CANDLES_PER_YEAR)
    return vol.shift(1)  # shift again so today's decision never sees today's own return


def backtest_instrument(name, csv_path, cfg):
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    df = compute_indicators(df)
    if config.STRATEGY["ENABLE_TREND_FILTER"]:
        ema_period = config.STRATEGY["TREND_FILTER_EMA_PERIOD"]
        df[f"EMA{ema_period}"] = _ema(df["close"], ema_period)
    df["realized_vol"] = compute_realized_vol(df)
    df = df.dropna(subset=["realized_vol"]).reset_index(drop=True)

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
                    "instrument": name,
                    "entry_time": position["entry_time"],
                    "exit_time": row["timestamp"],
                    "direction": position["direction"],
                    "strike": position["strike"],
                    "entry_premium": position["entry_ltp_net"],
                    "exit_premium": exit_net,
                    "exit_reason": exit_reason,
                    "gross_pnl": gross_pnl,
                    "costs_total": costs_total,
                    "net_pnl": net_pnl,
                })
                position = None
            continue

        signal = get_signal(row)
        if signal not in ("CE", "PE"):
            continue

        if config.STRATEGY["ENABLE_TREND_FILTER"]:
            ema_period = config.STRATEGY["TREND_FILTER_EMA_PERIOD"]
            if not confirm_with_trend_filter(signal, row["close"], row[f"EMA{ema_period}"]):
                continue

        strike = round_to_atm(spot, cfg["strike_step"])
        entry_raw = black_scholes_price(spot, strike, years_to_expiry, RISK_FREE_RATE, sigma, signal)
        if entry_raw <= 0:
            continue

        sl_points = compute_sl_points(name, entry_raw)
        if sl_points is None:
            continue  # same guard engine.py uses live: budget can't support a safe stop

        entry_net = cost_model.apply_slippage(entry_raw, "BUY")
        position = {
            "entry_time": row["timestamp"],
            "direction": signal,
            "strike": strike,
            "entry_ltp_net": entry_net,
            "current_sl": entry_net - sl_points,
            "tsl_armed": False,
            "target_price": entry_net + cfg["target_points"],
        }

    return trades


def print_caveat():
    print("=" * 78)
    print("CAVEAT: option premiums are SIMULATED via Black-Scholes using realized")
    print("volatility as a stand-in for implied vol, and a fixed assumed")
    print(f"{ASSUMED_DAYS_TO_EXPIRY}-day time-to-expiry (real historical expiry calendars aren't")
    print("available in this dataset). This is a theoretical approximation of the")
    print("rule set's behavior, NOT a measurement of real historical option P&L,")
    print("and it is not a prediction of future performance either way.")
    print("=" * 78)


def compute_summary(trades_df):
    """Shared by summarize() (console) and write_summary_json() (dashboard
    display) so the two never report different numbers."""
    if trades_df.empty:
        return None

    total = len(trades_df)
    wins = int((trades_df["net_pnl"] > 0).sum())
    losses = total - wins
    cum = trades_df.sort_values("exit_time")["net_pnl"].cumsum()
    running_max = cum.cummax()

    per_instrument = (
        trades_df.groupby("instrument")["net_pnl"].agg(trades="count", total="sum", average="mean").round(2)
    )
    per_exit_reason = (
        trades_df.groupby("exit_reason")["net_pnl"].agg(trades="count", total="sum", average="mean").round(2)
    )

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / total * 100, 1),
        "avg_pnl": round(trades_df["net_pnl"].mean(), 2),
        "total_pnl": round(trades_df["net_pnl"].sum(), 2),
        "max_drawdown": round((cum - running_max).min(), 2),
        "date_range": [str(trades_df["exit_time"].min()), str(trades_df["exit_time"].max())],
        "per_instrument": per_instrument.reset_index().to_dict(orient="records"),
        "per_exit_reason": per_exit_reason.reset_index().to_dict(orient="records"),
    }


def summarize(trades_df):
    summary = compute_summary(trades_df)
    if summary is None:
        print("\nNo trades were generated over the backtest period.")
        return

    print(f"\nTotal trades: {summary['total_trades']}")
    print(f"Win rate: {summary['win_rate_pct']}%  ({summary['wins']} wins / {summary['losses']} losses)")
    print(f"Average net P&L per trade: Rs {summary['avg_pnl']:,.2f}")
    print(f"Total net P&L: Rs {summary['total_pnl']:,.2f}")
    print(f"Max cumulative drawdown: Rs {summary['max_drawdown']:,.2f}")

    print("\nBy instrument:")
    print(pd.DataFrame(summary["per_instrument"]).set_index("instrument"))

    print("\nBy exit reason:")
    print(pd.DataFrame(summary["per_exit_reason"]).set_index("exit_reason"))


def write_summary_json(trades_df, out_path):
    """Compact summary the dashboard reads to show a 'Strategy Analysis'
    section -- local-only (like trade_history.xlsx): the historical CSVs
    this is computed from aren't shipped to the cloud deploy, so this file
    won't exist there and the dashboard shows a friendly fallback instead."""
    summary = compute_summary(trades_df)
    payload = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "trend_filter_enabled": config.STRATEGY["ENABLE_TREND_FILTER"],
        "assumed_days_to_expiry": ASSUMED_DAYS_TO_EXPIRY,
        "summary": summary,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved summary to {out_path}")


def main():
    all_trades = []
    for name, cfg in config.INSTRUMENTS.items():
        csv_path = CSV_FILES[name]
        if not os.path.exists(csv_path):
            print(f"Skipping {name}: {csv_path} not found.")
            continue
        print(f"Backtesting {name} ({csv_path})...")
        trades = backtest_instrument(name, csv_path, cfg)
        print(f"  {len(trades)} trades")
        all_trades.extend(trades)

    trades_df = pd.DataFrame(all_trades)
    out_path = os.path.join(BASE_DIR, "backtest_results.csv")
    trades_df.to_csv(out_path, index=False)
    print(f"\nSaved {len(trades_df)} trades to {out_path}")

    write_summary_json(trades_df, os.path.join(BASE_DIR, "backtest_summary.json"))

    print_caveat()
    summarize(trades_df)


if __name__ == "__main__":
    main()
