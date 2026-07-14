"""
paper_trader.py
Live PAPER trading engine for Nifty options (CE/PE), using REAL market premiums.
No real money, no real orders — but every price used is genuinely live.

Run this during market hours (9:15 AM - 3:30 PM IST), on a day you have a fresh
upstox_token.txt (regenerate daily via 1_generate_token.py).

Usage:
  python paper_trader.py

What it does, every 15 minutes aligned to candle close:
  1. Pulls the latest Nifty spot candles (intraday, so far today + recent history)
  2. Computes indicators via signal_engine (SAME code as backtest.py)
  3. If a new CE/PE signal fires and we're flat, "buys" the ATM option at its
     real live premium (paper — just logged, no order placed)
  4. If in a position, polls the option's live LTP every cycle and checks:
       - Stop loss:  premium has dropped SL_PCT from entry
       - Target:     premium has risen TARGET_PCT from entry
       - Time exit:  reached SQUARE_OFF_TIME
  5. Logs every entry/exit to paper_trades.csv (append-only, this is your track record)

IMPORTANT: this script trusts your local system clock and Upstox's API for pricing.
It's a PAPER system — always cross-check paper_trades.csv against your own judgement
before ever wiring in real order placement.
"""
import pandas as pd
import time
import datetime
import os
import sys
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

sys.path.append(os.path.dirname(__file__))
from signal_engine import compute_indicators, get_signal, SQUARE_OFF_TIME, MARKET_OPEN, MARKET_CLOSE
from option_selector import get_atm_option, get_live_ltp, get_access_token, get_intraday_candles

def now_ist():
    return datetime.datetime.now(IST)

# ---- Config ----
SL_PCT = 0.30      # exit if premium falls 30% from entry
TARGET_PCT = 0.50  # exit if premium rises 50% from entry
POLL_INTERVAL_SECONDS = 60 * 15   # 15-min candles
TRADE_LOG = "paper_trades.csv"
INSTRUMENT_KEY = "NSE_INDEX|Nifty 50"
STRIKE_STEP = 50

def get_headers():
    return {"Accept": "application/json", "Authorization": f"Bearer {get_access_token()}"}

def log_trade(row):
    file_exists = os.path.isfile(TRADE_LOG)
    df = pd.DataFrame([row])
    df.to_csv(TRADE_LOG, mode='a', header=not file_exists, index=False)
    print(f"LOGGED: {row}")

def wait_until_next_candle():
    now = now_ist()
    # align to next 15-min mark
    minutes_to_next = 15 - (now.minute % 15)
    next_time = (now + datetime.timedelta(minutes=minutes_to_next)).replace(second=5, microsecond=0)
    sleep_secs = (next_time - now).total_seconds()
    print(f"Sleeping {sleep_secs:.0f}s until next candle check at {next_time.time()} IST")
    time.sleep(max(sleep_secs, 1))

def main():
    position = None  # dict when holding a paper position

    print("Paper trading engine started. Ctrl+C to stop.")
    print(f"Local machine time: {datetime.datetime.now()}")
    print(f"IST time (used for market hours): {now_ist()}")
    while True:
        now = now_ist()
        current_time = now.time()

        if current_time < MARKET_OPEN:
            print(f"Market not open yet ({current_time}). Waiting...")
            time.sleep(60)
            continue
        if current_time > MARKET_CLOSE:
            print("Market closed for the day. Exiting.")
            break

        headers = get_headers()

        try:
            candles = get_intraday_candles(INSTRUMENT_KEY, headers, interval_minutes=15)
            if len(candles) < 25:
                print(f"Not enough candles yet ({len(candles)}), waiting...")
                wait_until_next_candle()
                continue

            df = compute_indicators(candles)
            latest = df.iloc[-1]

            # ---- Manage open position ----
            if position is not None:
                live_ltp = get_live_ltp(position["instrument_key"], headers)
                pnl_pct = (live_ltp - position["entry_ltp"]) / position["entry_ltp"]

                exit_reason = None
                if current_time >= SQUARE_OFF_TIME:
                    exit_reason = "EOD_SQUAREOFF"
                elif pnl_pct <= -SL_PCT:
                    exit_reason = "SL"
                elif pnl_pct >= TARGET_PCT:
                    exit_reason = "TARGET"

                if exit_reason:
                    log_trade({
                        "event": "EXIT",
                        "timestamp": now.isoformat(),
                        "direction": position["direction"],
                        "strike": position["strike"],
                        "entry_ltp": position["entry_ltp"],
                        "exit_ltp": live_ltp,
                        "pnl_pct": round(pnl_pct * 100, 2),
                        "exit_reason": exit_reason,
                    })
                    position = None
                else:
                    print(f"Holding {position['direction']} {position['strike']} | "
                          f"entry={position['entry_ltp']} live={live_ltp} pnl={pnl_pct*100:.1f}%")

            # ---- Look for new entry ----
            if position is None:
                signal = get_signal(latest)
                if signal in ("CE", "PE"):
                    opt = get_atm_option(signal, headers, instrument_key=INSTRUMENT_KEY,
                                          strike_step=STRIKE_STEP)
                    position = {
                        "direction": signal,
                        "instrument_key": opt["instrument_key"],
                        "strike": opt["strike"],
                        "entry_ltp": opt["ltp"],
                        "entry_time": now,
                    }
                    log_trade({
                        "event": "ENTRY",
                        "timestamp": now.isoformat(),
                        "direction": signal,
                        "strike": opt["strike"],
                        "entry_ltp": opt["ltp"],
                        "exit_ltp": "",
                        "pnl_pct": "",
                        "exit_reason": "",
                    })

        except Exception as e:
            print(f"ERROR in main loop: {e}")

        wait_until_next_candle()

if __name__ == "__main__":
    main()