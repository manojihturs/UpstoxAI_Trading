# Monitoring

How you'd actually notice if the engine stopped working during market hours,
instead of finding out at 3pm that nothing traded since 10am.

## What's watching

`scripts/watchdog.ps1`, scheduled every 5 minutes from 09:15-15:15 IST on
weekdays (`PaperTradingApp_Watchdog` in Task Scheduler — see
`scripts/register_scheduled_tasks.ps1`). It runs
`scripts/check_engine_health.py`, which checks two independent things,
read-only:

1. **Engine heartbeat freshness** (`state_store.is_engine_alive`) — catches
   a crashed or hung engine thread. Stale after 180 seconds (the poll
   interval is ~25s, so this is generous slack, not a hair-trigger).
2. **New `ERROR`/`Traceback` lines** in today's
   `logs/app_<date>_err.log` since the last check — catches auth failures,
   API errors, etc. that don't necessarily stop the heartbeat (e.g. one
   instrument's fetch failing while the loop otherwise keeps running).

If either check fails, `watchdog.ps1` pops a **Windows balloon
notification** (no extra module needed — uses `System.Windows.Forms`
directly) and logs the reason to `logs/launcher.log`.

**Deliberately alert-only.** The watchdog never restarts, kills, or
otherwise touches the engine — deciding what to do about a stale engine or
a fresh error is a human judgment call, not something to automate on top of
a system already handling money (even paper money).

## What it does NOT cover

- **No off-machine alerting** — if your PC is off, asleep, or the balloon
  notification is missed because you're away from the screen, there's no
  SMS/email/push fallback today. For anything beyond casual paper testing,
  consider wiring the alert branch in `watchdog.ps1` to a webhook (Slack,
  Telegram, email) instead of/alongside the Windows notification.
- **No engine-internal error classification** — the watchdog greps for the
  literal strings `ERROR` and `Traceback` in the stderr log; it doesn't
  distinguish a harmless one-off retry from something that needs your
  attention. Check `logs/app_<date>_err.log` directly when alerted.
- **No position-level anomaly detection** — e.g. a stuck position that
  never hits SL/TSL/target isn't specifically flagged; only engine-level
  liveness and error logs are checked. The Analytics/Positions pages are
  still the place to eyeball an individual open position.

## Checking it's actually working

```powershell
# Manual run, see the OK/ALERT line directly
powershell -File scripts\watchdog.ps1

# See recent watchdog activity
Get-Content logs\launcher.log -Tail 20

# Force an ALERT to confirm the notification pops (temporary, for testing only)
python -c "import state_store; state_store.is_engine_alive = lambda **k: False"  # illustrative -- see test_live_validation.py-style monkeypatching for a real test harness
```

Confirm the scheduled task itself is registered and enabled:

```powershell
Get-ScheduledTask -TaskName "PaperTradingApp_Watchdog" | Select-Object TaskName, State
```
