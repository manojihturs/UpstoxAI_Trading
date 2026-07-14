"""
backtest_experiments.py
Tests a handful of well-reasoned rule variations against the baseline
(the rules actually live in config.py/signal_engine.py today), using a
strict chronological TRAIN/TEST split -- variants are only worth trusting
if they hold up on data they were never checked against. This does NOT
modify signal_engine.py (the live contract stays untouched); each variant
reimplements just the entry-decision step on top of the same indicator
columns compute_indicators() already produces.

Nothing here changes live behavior. It's a research report, not a
deployment -- adopting any variant means deliberately editing config.py.
"""
import os
import pandas as pd

import config
import cost_model
from signal_engine import compute_indicators, _ema, ADX_THRESHOLD, LAST_ENTRY_TIME
from option_selector import round_to_atm
from engine import evaluate_position, compute_sl_points
from backtest import black_scholes_price, compute_realized_vol, CSV_FILES, ASSUMED_DAYS_TO_EXPIRY, RISK_FREE_RATE

TRAIN_TEST_SPLIT_DATE = "2025-07-01"  # ~2.5yr train / ~1yr test, chronological (no shuffling)


def prepare_df(csv_path):
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    df = compute_indicators(df)
    df["EMA50"] = _ema(df["close"], 50)
    df["realized_vol"] = compute_realized_vol(df)
    df = df.dropna(subset=["realized_vol", "EMA50"]).reset_index(drop=True)
    df["bull_cross_prev"] = df["bull_cross"].shift(1).fillna(False)
    df["bear_cross_prev"] = df["bear_cross"].shift(1).fillna(False)
    return df


def signal_baseline(row):
    """Exactly what's live today: EMA9/EMA20 cross + ADX > 12."""
    t = row["timestamp"].time()
    if t > LAST_ENTRY_TIME or row["ADX"] <= ADX_THRESHOLD:
        return None
    if row["bull_cross"]:
        return "CE"
    if row["bear_cross"]:
        return "PE"
    return None


def signal_stricter_adx(row, threshold=20):
    """Variant B: raise the trend-strength bar from 12 to 20 -- fewer,
    theoretically higher-conviction signals."""
    t = row["timestamp"].time()
    if t > LAST_ENTRY_TIME or row["ADX"] <= threshold:
        return None
    if row["bull_cross"]:
        return "CE"
    if row["bear_cross"]:
        return "PE"
    return None


def signal_higher_tf_filter(row):
    """Variant C: only take the EMA9/20 cross if price also agrees with
    the longer EMA50 trend -- filters counter-trend whipsaws."""
    t = row["timestamp"].time()
    if t > LAST_ENTRY_TIME or row["ADX"] <= ADX_THRESHOLD:
        return None
    if row["bull_cross"] and row["close"] > row["EMA50"]:
        return "CE"
    if row["bear_cross"] and row["close"] < row["EMA50"]:
        return "PE"
    return None


def signal_confirmation_candle(row):
    """Variant D: don't enter on the cross candle itself -- wait one more
    candle and only enter if the trend direction still holds. Cuts
    single-candle whipsaws at the cost of a slightly worse entry price."""
    t = row["timestamp"].time()
    if t > LAST_ENTRY_TIME or row["ADX"] <= ADX_THRESHOLD:
        return None
    if row["bull_cross_prev"] and row["ema_diff"] > 0:
        return "CE"
    if row["bear_cross_prev"] and row["ema_diff"] < 0:
        return "PE"
    return None


VARIANTS = {
    "A_baseline_live_today": signal_baseline,
    "B_stricter_adx_20": signal_stricter_adx,
    "C_higher_tf_ema50_filter": signal_higher_tf_filter,
    "D_confirmation_candle": signal_confirmation_candle,
}


def run_variant(signal_fn, df, instrument_name, cfg):
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

        signal = signal_fn(row)
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
    for variant_name, signal_fn in VARIANTS.items():
        train_trades, test_trades = [], []
        for instrument_name, cfg in config.INSTRUMENTS.items():
            if instrument_name not in prepared:
                continue
            df = prepared[instrument_name]
            train_df = df[df["timestamp"] < TRAIN_TEST_SPLIT_DATE]
            test_df = df[df["timestamp"] >= TRAIN_TEST_SPLIT_DATE]

            train_trades += run_variant(signal_fn, train_df, instrument_name, cfg)
            test_trades += run_variant(signal_fn, test_df, instrument_name, cfg)

        results.append(summarize(train_trades, f"{variant_name} [TRAIN <{TRAIN_TEST_SPLIT_DATE}]"))
        results.append(summarize(test_trades, f"{variant_name} [TEST >={TRAIN_TEST_SPLIT_DATE}]"))

    out = pd.DataFrame(results)
    print(out.to_string(index=False))
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_experiments_results.csv")
    out.to_csv(out_path, index=False)
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
