"""
option_selector.py
All Upstox HTTP calls used for option-chain lookups, ATM strike selection,
live premium polling, and intraday spot candles. Generalized to accept an
instrument (key/strike step) instead of assuming Nifty, so engine.py can
drive Nifty/BankNifty/Sensex through the same functions.
"""
import requests
import pandas as pd
import datetime

# Defaults preserve the original Nifty-only behavior for any caller that
# doesn't pass an instrument explicitly (e.g. paper_trader.py).
INSTRUMENT_KEY = "NSE_INDEX|Nifty 50"
STRIKE_STEP = 50  # Nifty strikes are in steps of 50


def get_access_token():
    """Prefer Streamlit secrets (used on Streamlit Community Cloud, where
    there's no local upstox_token.txt to read); fall back to the local file
    for local runs (paper_trader.py, tests, 1_generate_token.py workflow)."""
    try:
        import streamlit as st
        if "upstox_token" in st.secrets:
            return st.secrets["upstox_token"]
    except Exception:
        pass
    with open("upstox_token.txt") as f:
        return f.read().strip()


def get_nearest_weekly_expiry(headers, instrument_key=INSTRUMENT_KEY):
    """Fetch available expiries and return the nearest one -- STRICTLY after
    today, never today itself. Picking today's own expiry would mean buying
    an option with only hours of remaining life, where time decay and
    pricing behave very differently from the rest of the week; this guard
    always rolls forward to next week's contract on expiry day instead."""
    url = f"https://api.upstox.com/v2/option/contract?instrument_key={instrument_key}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    expiries = sorted(set(item["expiry"] for item in data))
    today = datetime.date.today().isoformat()
    future_expiries = [e for e in expiries if e > today]
    if not future_expiries:
        raise ValueError("No future expiries found")
    return future_expiries[0]


def round_to_atm(spot_price, step=STRIKE_STEP):
    return round(spot_price / step) * step


def get_option_chain(expiry_date, headers, instrument_key=INSTRUMENT_KEY):
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={instrument_key}&expiry_date={expiry_date}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_atm_option(direction, headers, expiry_date=None,
                    instrument_key=INSTRUMENT_KEY, strike_step=STRIKE_STEP):
    """
    direction: 'CE' or 'PE'
    Returns dict: {instrument_key, strike, ltp, expiry, spot_at_entry}
    """
    if expiry_date is None:
        expiry_date = get_nearest_weekly_expiry(headers, instrument_key=instrument_key)

    chain = get_option_chain(expiry_date, headers, instrument_key=instrument_key)
    if not chain:
        raise ValueError("Empty option chain returned")

    spot = chain[0]["underlying_spot_price"]
    atm_strike = round_to_atm(spot, step=strike_step)

    # find the row matching ATM strike
    match = next((row for row in chain if row["strike_price"] == atm_strike), None)
    if match is None:
        # fallback: closest available strike
        match = min(chain, key=lambda r: abs(r["strike_price"] - atm_strike))

    leg = match["call_options"] if direction == "CE" else match["put_options"]
    return {
        "instrument_key": leg["instrument_key"],
        "strike": match["strike_price"],
        "ltp": leg["market_data"]["ltp"],
        "expiry": expiry_date,
        "spot_at_entry": spot,
    }


def compute_pcr(chain):
    """
    Put/Call OI ratio summed across the whole option chain -- free, comes
    from the same chain response already fetched for ATM strike selection.
    Returns None if call OI is zero (can't compute a ratio) or the chain
    doesn't carry OI data.

    NOTE: verify the 'oi' field name inside market_data against a live
    response before relying on this -- it wasn't confirmed from a
    read-only pass over the API docs.
    """
    call_oi_total = 0
    put_oi_total = 0
    for row in chain:
        call_oi_total += row.get("call_options", {}).get("market_data", {}).get("oi", 0) or 0
        put_oi_total += row.get("put_options", {}).get("market_data", {}).get("oi", 0) or 0
    if call_oi_total == 0:
        return None
    return put_oi_total / call_oi_total


def get_live_ltp(instrument_key, headers):
    """Poll current LTP for an already-selected option instrument."""
    url = f"https://api.upstox.com/v2/market-quote/ltp?instrument_key={instrument_key}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json().get("data", {})
    # response keyed by symbol, grab the first (only) entry
    for v in data.values():
        return v["last_price"]
    return None


def get_quotes(instrument_keys, headers):
    """Batched live quote for one or more spot instruments (e.g. the three
    index keys). Returns {instrument_key: {last_price, net_change, pct_change}}.

    Uses 'net_change' straight from the Upstox quotes response (last_price
    minus previous day's close) rather than ohlc.close, since ohlc.close
    tracks the still-forming candle intraday and isn't the previous close.
    """
    url = "https://api.upstox.com/v2/market-quote/quotes"
    resp = requests.get(url, headers=headers, params={"instrument_key": ",".join(instrument_keys)})
    resp.raise_for_status()
    data = resp.json().get("data", {})

    out = {}
    for row in data.values():
        key = row.get("instrument_token")
        last_price = row.get("last_price")
        net_change = row.get("net_change")
        if key is None or last_price is None or net_change is None:
            continue
        prev_close = last_price - net_change
        pct_change = (net_change / prev_close * 100) if prev_close else 0.0
        out[key] = {"last_price": last_price, "net_change": net_change, "pct_change": pct_change}
    return out


def get_intraday_candles(instrument_key, headers, interval_minutes=15):
    """Pull today's intraday candles for a spot index via Upstox intraday V3 API."""
    url = f"https://api.upstox.com/v3/historical-candle/intraday/{instrument_key}/minutes/{interval_minutes}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    candles = resp.json().get("data", {}).get("candles", [])
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def get_previous_trading_day_ohlc(instrument_key, headers):
    """Most recent COMPLETE trading day's High/Low/Close for a spot index,
    used for pivot-point calculations. Looks back a week of calendar days
    (via the daily historical-candle endpoint) so weekends/holidays don't
    leave this with no data."""
    to_date = datetime.date.today() - datetime.timedelta(days=1)
    from_date = to_date - datetime.timedelta(days=7)
    url = (f"https://api.upstox.com/v3/historical-candle/{instrument_key}/days/1/"
           f"{to_date.isoformat()}/{from_date.isoformat()}")
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    candles = resp.json().get("data", {}).get("candles", [])
    if not candles:
        raise ValueError(f"No historical daily candles found for {instrument_key}")
    candles_sorted = sorted(candles, key=lambda c: c[0])
    _, _open, high, low, close, _volume, _oi = candles_sorted[-1]
    return {"high": high, "low": low, "close": close}
