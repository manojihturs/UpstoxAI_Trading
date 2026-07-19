"""
state_store.py
SQLite-backed shared state between engine.py (the single writer of all
financial state) and dashboard.py (which only ever writes user-intent rows
into control_requests; engine.py picks those up and applies them). WAL mode
lets the two processes read/write concurrently on Windows without blocking.

Every function opens a short-lived connection, does its work in one
transaction, and closes -- never hold a connection open across a sleep.
"""
import sqlite3
import json
import datetime

from config import PATHS, INSTRUMENTS

DB_PATH = PATHS["DB_PATH"]


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _today_str():
    return datetime.date.today().isoformat()


def init_db():
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pending_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                instrument TEXT NOT NULL,
                direction TEXT NOT NULL,
                strike REAL NOT NULL,
                expiry TEXT NOT NULL,
                instrument_key TEXT NOT NULL,
                proposed_ltp REAL NOT NULL,
                qty INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                user_action_at TEXT,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument TEXT NOT NULL,
                direction TEXT NOT NULL,
                strike REAL NOT NULL,
                expiry TEXT NOT NULL,
                instrument_key TEXT NOT NULL,
                qty INTEGER NOT NULL,
                entry_time TEXT NOT NULL,
                entry_ltp_raw REAL NOT NULL,
                entry_ltp_net REAL NOT NULL,
                initial_sl REAL NOT NULL,
                current_sl REAL NOT NULL,
                tsl_armed INTEGER NOT NULL DEFAULT 0,
                target_price REAL NOT NULL,
                last_seen_ltp REAL,
                last_seen_at TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                exit_time TEXT,
                exit_ltp_raw REAL,
                exit_ltp_net REAL,
                exit_reason TEXT,
                gross_pnl REAL,
                costs_total REAL,
                net_pnl REAL
            );

            CREATE TABLE IF NOT EXISTS daily_summary (
                date TEXT PRIMARY KEY,
                realized_net_pnl REAL NOT NULL DEFAULT 0,
                trades_count INTEGER NOT NULL DEFAULT 0,
                circuit_breaker_tripped INTEGER NOT NULL DEFAULT 0,
                tripped_at TEXT
            );

            CREATE TABLE IF NOT EXISTS strategy_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active_strategy TEXT NOT NULL DEFAULT 'UT_BOT_CONSERVATIVE'
            );

            CREATE TABLE IF NOT EXISTS timeframe_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active_minutes INTEGER NOT NULL DEFAULT 15
            );

            CREATE TABLE IF NOT EXISTS auto_confirm_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS qty_state (
                instrument TEXT PRIMARY KEY,
                qty INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS risk_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cumulative_breaker_tripped INTEGER NOT NULL DEFAULT 0,
                tripped_at TEXT
            );

            CREATE TABLE IF NOT EXISTS engine_heartbeat (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_poll_at TEXT,
                pid INTEGER
            );

            CREATE TABLE IF NOT EXISTS control_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL,
                handled INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS spot_quotes (
                instrument TEXT PRIMARY KEY,
                last_price REAL NOT NULL,
                net_change REAL NOT NULL,
                pct_change REAL NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orb_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                instrument TEXT NOT NULL DEFAULT 'NIFTY',
                view TEXT NOT NULL DEFAULT 'TOP',
                selected_type TEXT NOT NULL DEFAULT 'PUT',
                selected_strike REAL,
                itm_count INTEGER NOT NULL DEFAULT 4
            );

            CREATE TABLE IF NOT EXISTS orb_levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                instrument TEXT NOT NULL,
                ladder_index INTEGER NOT NULL,
                strike REAL NOT NULL,
                option_type TEXT NOT NULL,
                value REAL,
                high_or_low TEXT,
                computed_at TEXT NOT NULL
            );
            """
        )
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    """Add columns introduced after a table already existed. SQLite has no
    'ADD COLUMN IF NOT EXISTS', so check pragma table_info first."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(positions)")}
    if "last_seen_ltp" not in existing_cols:
        conn.execute("ALTER TABLE positions ADD COLUMN last_seen_ltp REAL")
    if "last_seen_at" not in existing_cols:
        conn.execute("ALTER TABLE positions ADD COLUMN last_seen_at TEXT")


# ---------------------------------------------------------------- pending_signals

def create_pending_signal(instrument, direction, strike, expiry, instrument_key,
                           proposed_ltp, qty, ttl_seconds):
    now = datetime.datetime.now()
    expires_at = (now + datetime.timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO pending_signals
               (created_at, instrument, direction, strike, expiry, instrument_key,
                proposed_ltp, qty, status, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)""",
            (now.isoformat(timespec="seconds"), instrument, direction, strike, expiry,
             instrument_key, proposed_ltp, qty, expires_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_pending_signal():
    """Return the current outstanding PENDING signal row, or None."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM pending_signals WHERE status = 'PENDING' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_pending_signal_by_id(signal_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM pending_signals WHERE id = ?", (signal_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_pending_signal_status(signal_id, status):
    conn = _connect()
    try:
        conn.execute(
            "UPDATE pending_signals SET status = ?, user_action_at = ? WHERE id = ?",
            (status, _now_iso(), signal_id),
        )
        conn.commit()
    finally:
        conn.close()


def expire_stale_pending_signals():
    """Flip any PENDING signal past its expires_at to EXPIRED. Returns count expired."""
    now = _now_iso()
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE pending_signals SET status = 'EXPIRED', user_action_at = ? "
            "WHERE status = 'PENDING' AND expires_at < ?",
            (now, now),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------------- positions

def open_position(instrument, direction, strike, expiry, instrument_key, qty,
                   entry_ltp_raw, entry_ltp_net, initial_sl, target_price):
    """Atomically opens a position ONLY if no other position is already
    OPEN. The INSERT...SELECT...WHERE NOT EXISTS runs as a single SQL
    statement/transaction, which SQLite's single-writer lock (see
    _connect()'s WAL + busy_timeout) serializes across every connection --
    unlike a separate get_open_position() check followed by a second
    open_position() call, which is TWO round trips and can race: found
    live (2026-07-17) that two engine.py background threads running
    against the same trading_state.db (one from dashboard.py, one from a
    UI-redesign preview instance) both read "no open position" before
    either had committed, opening 2-4 simultaneous positions on the same
    signal. Returns the new row's id, or None if another position was
    already open -- callers MUST treat None as a rejection, not proceed
    as if the position opened."""
    conn = _connect()
    try:
        cur = conn.execute(
            """INSERT INTO positions
               (instrument, direction, strike, expiry, instrument_key, qty,
                entry_time, entry_ltp_raw, entry_ltp_net, initial_sl, current_sl,
                tsl_armed, target_price, status)
               SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'OPEN'
               WHERE NOT EXISTS (SELECT 1 FROM positions WHERE status = 'OPEN')""",
            (instrument, direction, strike, expiry, instrument_key, qty,
             _now_iso(), entry_ltp_raw, entry_ltp_net, initial_sl, initial_sl,
             target_price),
        )
        conn.commit()
        return cur.lastrowid if cur.rowcount > 0 else None
    finally:
        conn.close()


def get_open_position():
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM positions WHERE status = 'OPEN' LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_position_by_id(position_id):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_position_trailing_sl(position_id, current_sl, tsl_armed):
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET current_sl = ?, tsl_armed = ? WHERE id = ?",
            (current_sl, int(tsl_armed), position_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_position_last_seen(position_id, ltp):
    """Record the latest polled LTP so dashboard.py can show live price/P&L
    without making its own Upstox API calls."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE positions SET last_seen_ltp = ?, last_seen_at = ? WHERE id = ?",
            (ltp, _now_iso(), position_id),
        )
        conn.commit()
    finally:
        conn.close()


def close_position(position_id, exit_ltp_raw, exit_ltp_net, exit_reason,
                    gross_pnl, costs_total, net_pnl):
    conn = _connect()
    try:
        conn.execute(
            """UPDATE positions
               SET status = 'CLOSED', exit_time = ?, exit_ltp_raw = ?, exit_ltp_net = ?,
                   exit_reason = ?, gross_pnl = ?, costs_total = ?, net_pnl = ?
               WHERE id = ?""",
            (_now_iso(), exit_ltp_raw, exit_ltp_net, exit_reason,
             gross_pnl, costs_total, net_pnl, position_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_closed_positions(limit=200):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'CLOSED' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_closed_positions_for_date(date_str):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE status = 'CLOSED' AND date(exit_time) = ?",
            (date_str,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_today_trade_count(date_str=None):
    date_str = date_str or _today_str()
    return len(get_closed_positions_for_date(date_str))


def get_consecutive_losses(date_str=None):
    """Counts consecutive LOSING trades (net_pnl <= 0) ending at today's
    most recent closed trade, stopping at the first win. Cheap: at most a
    handful of rows per day."""
    date_str = date_str or _today_str()
    today_closed = sorted(get_closed_positions_for_date(date_str), key=lambda p: p["exit_time"], reverse=True)
    streak = 0
    for p in today_closed:
        if p["net_pnl"] is not None and p["net_pnl"] <= 0:
            streak += 1
        else:
            break
    return streak


# ------------------------------------------------------------------ daily_summary

def get_daily_summary(date_str=None):
    date_str = date_str or _today_str()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM daily_summary WHERE date = ?", (date_str,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO daily_summary (date, realized_net_pnl, trades_count, "
                "circuit_breaker_tripped) VALUES (?, 0, 0, 0)",
                (date_str,),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM daily_summary WHERE date = ?", (date_str,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def recompute_daily_summary(date_str, daily_loss_cap):
    """Recompute realized P&L/trade count for date_str from closed positions,
    and trip the circuit breaker if losses exceed the cap. Returns the
    updated summary dict."""
    conn = _connect()
    try:
        agg = conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) AS pnl, COUNT(*) AS cnt "
            "FROM positions WHERE status = 'CLOSED' AND date(exit_time) = ?",
            (date_str,),
        ).fetchone()
        realized_net_pnl = agg["pnl"]
        trades_count = agg["cnt"]

        existing = conn.execute(
            "SELECT * FROM daily_summary WHERE date = ?", (date_str,)
        ).fetchone()

        tripped = bool(existing["circuit_breaker_tripped"]) if existing else False
        tripped_at = existing["tripped_at"] if existing else None
        if not tripped and realized_net_pnl <= -abs(daily_loss_cap):
            tripped = True
            tripped_at = _now_iso()

        if existing is None:
            conn.execute(
                "INSERT INTO daily_summary (date, realized_net_pnl, trades_count, "
                "circuit_breaker_tripped, tripped_at) VALUES (?, ?, ?, ?, ?)",
                (date_str, realized_net_pnl, trades_count, int(tripped), tripped_at),
            )
        else:
            conn.execute(
                "UPDATE daily_summary SET realized_net_pnl = ?, trades_count = ?, "
                "circuit_breaker_tripped = ?, tripped_at = ? WHERE date = ?",
                (realized_net_pnl, trades_count, int(tripped), tripped_at, date_str),
            )
        conn.commit()
        return {
            "date": date_str,
            "realized_net_pnl": realized_net_pnl,
            "trades_count": trades_count,
            "circuit_breaker_tripped": tripped,
            "tripped_at": tripped_at,
        }
    finally:
        conn.close()


def reset_circuit_breaker(date_str):
    conn = _connect()
    try:
        conn.execute(
            "UPDATE daily_summary SET circuit_breaker_tripped = 0, tripped_at = NULL "
            "WHERE date = ?",
            (date_str,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------- cumulative drawdown

def get_cumulative_pnl_stats():
    """All-time (all closed trades, any date) cumulative P&L, running peak,
    and current drawdown from that peak -- unlike daily_summary, this never
    resets on its own; it's meant to catch a losing streak that spans many
    days, which the per-day cap can't see."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT net_pnl FROM positions WHERE status = 'CLOSED' ORDER BY exit_time ASC"
        ).fetchall()
    finally:
        conn.close()

    cumulative = 0.0
    peak = 0.0
    for row in rows:
        cumulative += row["net_pnl"]
        peak = max(peak, cumulative)
    return {"cumulative_pnl": cumulative, "peak_pnl": peak, "drawdown": cumulative - peak}


def get_risk_state():
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO risk_state (id, cumulative_breaker_tripped) VALUES (1, 0)"
            )
            conn.commit()
            row = conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()
        return dict(row)
    finally:
        conn.close()


def trip_cumulative_breaker():
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO risk_state (id, cumulative_breaker_tripped, tripped_at) VALUES (1, 1, ?) "
            "ON CONFLICT(id) DO UPDATE SET cumulative_breaker_tripped = 1, tripped_at = excluded.tripped_at",
            (_now_iso(),),
        )
        conn.commit()
    finally:
        conn.close()


def reset_cumulative_breaker():
    conn = _connect()
    try:
        conn.execute(
            "UPDATE risk_state SET cumulative_breaker_tripped = 0, tripped_at = NULL WHERE id = 1"
        )
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------- strategy_state

def get_active_strategy():
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM strategy_state WHERE id = 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO strategy_state (id, active_strategy) VALUES (1, 'UT_BOT_CONSERVATIVE')")
            conn.commit()
            return "UT_BOT_CONSERVATIVE"
        return row["active_strategy"]
    finally:
        conn.close()


def set_active_strategy(strategy_name):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO strategy_state (id, active_strategy) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET active_strategy = excluded.active_strategy",
            (strategy_name,),
        )
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------ timeframe_state

def get_active_timeframe():
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM timeframe_state WHERE id = 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO timeframe_state (id, active_minutes) VALUES (1, 15)")
            conn.commit()
            return 15
        return row["active_minutes"]
    finally:
        conn.close()


def set_active_timeframe(minutes):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO timeframe_state (id, active_minutes) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET active_minutes = excluded.active_minutes",
            (minutes,),
        )
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------- auto_confirm_state

def get_auto_confirm():
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM auto_confirm_state WHERE id = 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO auto_confirm_state (id, enabled) VALUES (1, 1)")
            conn.commit()
            return True
        return bool(row["enabled"])
    finally:
        conn.close()


def set_auto_confirm(enabled):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO auto_confirm_state (id, enabled) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled",
            (int(enabled),),
        )
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------- qty_state

def get_qty(instrument):
    """Returns the configured order quantity for `instrument` -- the user's
    saved preference if they've set one, otherwise config.py's one-lot
    default. Live-switchable from the dashboard, same pattern as
    strategy/timeframe/auto-confirm."""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM qty_state WHERE instrument = ?", (instrument,)).fetchone()
        if row is None:
            return INSTRUMENTS[instrument]["lot_size"]
        return row["qty"]
    finally:
        conn.close()


def set_qty(instrument, qty):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO qty_state (instrument, qty) VALUES (?, ?) "
            "ON CONFLICT(instrument) DO UPDATE SET qty = excluded.qty",
            (instrument, qty),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_qty():
    """Returns {instrument: qty} for every instrument in config.INSTRUMENTS,
    filling in the one-lot default for any instrument with no saved override."""
    return {name: get_qty(name) for name in INSTRUMENTS}


# ------------------------------------------------------------- orb_settings
# Read-only informational feature (see option_selector.get_orb_ladder) --
# these settings never influence signal generation or position logic.

def get_orb_settings():
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM orb_settings WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO orb_settings (id, instrument, view, selected_type, selected_strike, itm_count) "
                "VALUES (1, 'NIFTY', 'TOP', 'PUT', NULL, 4)"
            )
            conn.commit()
            row = conn.execute("SELECT * FROM orb_settings WHERE id = 1").fetchone()
        return dict(row)
    finally:
        conn.close()


def set_orb_settings(instrument, view, selected_type, selected_strike, itm_count):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO orb_settings (id, instrument, view, selected_type, selected_strike, itm_count) "
            "VALUES (1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET instrument = excluded.instrument, view = excluded.view, "
            "selected_type = excluded.selected_type, selected_strike = excluded.selected_strike, "
            "itm_count = excluded.itm_count",
            (instrument, view, selected_type, selected_strike, itm_count),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------- orb_levels

def store_orb_levels(date_str, instrument, ladder):
    """ladder: list of {index, strike, option_type, value, high_or_low}
    (see option_selector.get_orb_ladder). Replaces any existing rows for
    this date+instrument -- recomputed once per day, not appended to."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM orb_levels WHERE date = ? AND instrument = ?", (date_str, instrument))
        for row in ladder:
            conn.execute(
                "INSERT INTO orb_levels (date, instrument, ladder_index, strike, option_type, value, "
                "high_or_low, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (date_str, instrument, row["index"], row["strike"], row["option_type"],
                 row["value"], row.get("high_or_low"), _now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


def clear_orb_levels(date_str, instrument):
    """Invalidates today's cached ladder so refresh_orb_ladder_if_needed()
    recomputes on its next cycle -- called whenever the settings change
    mid-day, since the cache is otherwise keyed only by date+instrument
    and wouldn't know a new strike/view/type was selected."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM orb_levels WHERE date = ? AND instrument = ?", (date_str, instrument))
        conn.commit()
    finally:
        conn.close()


def get_orb_levels(date_str, instrument):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM orb_levels WHERE date = ? AND instrument = ? ORDER BY ladder_index",
            (date_str, instrument),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------- engine_heartbeat

def update_heartbeat(pid):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO engine_heartbeat (id, last_poll_at, pid) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_poll_at = excluded.last_poll_at, pid = excluded.pid",
            (_now_iso(), pid),
        )
        conn.commit()
    finally:
        conn.close()


def get_heartbeat():
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM engine_heartbeat WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def is_engine_alive(stale_after_seconds=90):
    hb = get_heartbeat()
    if not hb or not hb.get("last_poll_at"):
        return False
    last = datetime.datetime.fromisoformat(hb["last_poll_at"])
    return (datetime.datetime.now() - last).total_seconds() <= stale_after_seconds


# --------------------------------------------------------------------- activity_log

def log_event(event_type, message):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO activity_log (timestamp, event_type, message) VALUES (?, ?, ?)",
            (_now_iso(), event_type, message),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_activity(limit=100):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# -------------------------------------------------------------------- spot_quotes

def update_spot_quote(instrument, last_price, net_change, pct_change):
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO spot_quotes (instrument, last_price, net_change, pct_change, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(instrument) DO UPDATE SET last_price = excluded.last_price, "
            "net_change = excluded.net_change, pct_change = excluded.pct_change, "
            "updated_at = excluded.updated_at",
            (instrument, last_price, net_change, pct_change, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_spot_quotes():
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM spot_quotes").fetchall()
        return {r["instrument"]: dict(r) for r in rows}
    finally:
        conn.close()


# -------------------------------------------------------------- control_requests

def create_control_request(kind, payload=None):
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO control_requests (kind, payload, created_at, handled) "
            "VALUES (?, ?, ?, 0)",
            (kind, json.dumps(payload) if payload is not None else None, _now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_unhandled_control_requests():
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM control_requests WHERE handled = 0 ORDER BY id ASC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"]) if d["payload"] else None
            out.append(d)
        return out
    finally:
        conn.close()


def mark_control_request_handled(request_id):
    conn = _connect()
    try:
        conn.execute("UPDATE control_requests SET handled = 1 WHERE id = ?", (request_id,))
        conn.commit()
    finally:
        conn.close()


# -------------------------------------------------------------- dashboard helper

def get_dashboard_snapshot():
    """Convenience read-only snapshot for dashboard.py -- bundles the pieces
    it needs on every refresh into one call."""
    today = _today_str()
    return {
        "pending_signal": get_pending_signal(),
        "open_position": get_open_position(),
        "daily_summary": get_daily_summary(today),
        "heartbeat": get_heartbeat(),
        "engine_alive": is_engine_alive(),
        "closed_positions": get_closed_positions(limit=100),
        "spot_quotes": get_spot_quotes(),
        "cumulative_pnl_stats": get_cumulative_pnl_stats(),
        "risk_state": get_risk_state(),
        "activity_log": get_recent_activity(limit=100),
        "active_strategy": get_active_strategy(),
        "active_timeframe": get_active_timeframe(),
        "auto_confirm": get_auto_confirm(),
        "qty_by_instrument": get_all_qty(),
        "orb_settings": (orb_settings := get_orb_settings()),
        "orb_levels": get_orb_levels(today, orb_settings["instrument"]),
    }


init_db()
