"""
STEP 2: Pull historical intraday candle data from Upstox for Nifty 50, Bank Nifty, Sensex.
Run 1_generate_token.py first (same day) to create upstox_token.txt.

Usage:
  python 3_fetch_historical.py

Output:
  nifty50_15min.csv
  banknifty_15min.csv
  sensex_15min.csv

Notes:
- Upstox V3 API limits how much history you get per request depending on interval:
    1-minute  -> ~1 month per request
    15-minute -> longer windows allowed, but we still chunk conservatively
- This script chunks requests month-by-month and concatenates results.
- Adjust START_DATE / END_DATE / INTERVAL_MINUTES below as needed.
"""
import requests
import pandas as pd
import time
from datetime import date, timedelta

with open("upstox_token.txt") as f:
    ACCESS_TOKEN = f.read().strip()

HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {ACCESS_TOKEN}",
}

INSTRUMENTS = {
    "nifty50":   "NSE_INDEX|Nifty 50",
    "banknifty": "NSE_INDEX|Nifty Bank",
    "sensex":    "BSE_INDEX|SENSEX",
}

INTERVAL_MINUTES = 15          # 1, 15, or 30 recommended
START_DATE = date(2023, 1, 1)  # adjust based on how far back you want
END_DATE = date.today()

def month_chunks(start, end):
    """Yield (chunk_start, chunk_end) covering [start, end] in ~30-day windows."""
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=29), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)

def fetch_chunk(instrument_key, interval_minutes, from_date, to_date):
    url = (
        f"https://api.upstox.com/v3/historical-candle/"
        f"{instrument_key}/minutes/{interval_minutes}/{to_date.isoformat()}/{from_date.isoformat()}"
    )
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code != 200:
        print(f"  WARNING: {resp.status_code} for {from_date} to {to_date}: {resp.text[:200]}")
        return []
    data = resp.json()
    candles = data.get("data", {}).get("candles", [])
    return candles

def fetch_full_history(name, instrument_key, interval_minutes, start, end):
    print(f"\nFetching {name} ({instrument_key}) [{interval_minutes}min] {start} -> {end}")
    all_candles = []
    for chunk_start, chunk_end in month_chunks(start, end):
        candles = fetch_chunk(instrument_key, interval_minutes, chunk_start, chunk_end)
        print(f"  {chunk_start} -> {chunk_end}: {len(candles)} candles")
        all_candles.extend(candles)
        time.sleep(0.5)  # be polite to the API / avoid rate limits

    if not all_candles:
        print(f"  No data returned for {name}.")
        return None

    # Upstox candle format: [timestamp, open, high, low, close, volume, oi]
    df = pd.DataFrame(all_candles, columns=["timestamp","open","high","low","close","volume","oi"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates(subset="timestamp").reset_index(drop=True)
    return df

def main():
    for name, key in INSTRUMENTS.items():
        df = fetch_full_history(name, key, INTERVAL_MINUTES, START_DATE, END_DATE)
        if df is not None:
            out_path = f"{name}_{INTERVAL_MINUTES}min.csv"
            df.to_csv(out_path, index=False)
            print(f"  Saved {len(df)} rows to {out_path}")

if __name__ == "__main__":
    main()
