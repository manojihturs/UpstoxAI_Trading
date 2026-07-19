"""
scripts/check_engine_health.py
Read-only health check for the running engine -- called by watchdog.ps1 on
a schedule. Deliberately kept OUTSIDE engine.py: this only ever reads
state_store and the day's stderr log, never touches trading state, so it
can't introduce a bug into the live loop that manages real (paper) money.

Checks two independent things:
  1. Heartbeat freshness (state_store.is_engine_alive) -- catches a crashed
     or hung engine thread.
  2. New ERROR lines in today's logs/app_<date>_err.log since the last
     check (tracked via a small offset file) -- catches auth failures,
     API errors, etc. that don't necessarily stop the heartbeat.

Only alerts during market hours (config.TIMING) on a weekday -- an engine
that's correctly idle outside market hours isn't a problem.

Prints one line: "OK" or "ALERT: <reason>". Exit code 0 = OK, 1 = ALERT,
so watchdog.ps1 can branch on it without parsing text.
"""
import datetime
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import state_store

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")
OFFSET_FILE = os.path.join(LOG_DIR, "watchdog_offset.txt")

HEARTBEAT_STALE_AFTER_SECONDS = 180  # 3x the 25s poll interval's rough order of magnitude, with slack


def _is_market_hours_now():
    now = datetime.datetime.now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return config.TIMING["MARKET_OPEN"] <= now.time() <= config.TIMING["MARKET_CLOSE"]


def _check_heartbeat():
    if state_store.is_engine_alive(stale_after_seconds=HEARTBEAT_STALE_AFTER_SECONDS):
        return None
    hb = state_store.get_heartbeat()
    last_seen = hb.get("last_poll_at") if hb else "never"
    return f"engine heartbeat stale (last seen: {last_seen})"


def _check_new_errors():
    date_stamp = datetime.date.today().isoformat()
    err_log = os.path.join(LOG_DIR, f"app_{date_stamp}_err.log")
    if not os.path.exists(err_log):
        return None

    last_offset = 0
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE) as f:
                last_offset = int(f.read().strip() or 0)
        except (ValueError, OSError):
            last_offset = 0

    file_size = os.path.getsize(err_log)
    if file_size < last_offset:
        last_offset = 0  # log rotated/truncated -- start over

    new_lines = []
    with open(err_log, encoding="utf-8", errors="replace") as f:
        f.seek(last_offset)
        for line in f:
            if "ERROR" in line or "Traceback" in line:
                new_lines.append(line.strip())
        new_offset = f.tell()

    with open(OFFSET_FILE, "w") as f:
        f.write(str(new_offset))

    if new_lines:
        preview = new_lines[-1][:200]
        return f"{len(new_lines)} new error line(s) in today's log, most recent: {preview}"
    return None


def main():
    if not _is_market_hours_now():
        print("OK (outside market hours)")
        return 0

    reasons = []
    for check in (_check_heartbeat, _check_new_errors):
        reason = check()
        if reason:
            reasons.append(reason)

    if reasons:
        print("ALERT: " + " | ".join(reasons))
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
