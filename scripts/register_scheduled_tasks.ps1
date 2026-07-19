<#
scripts/register_scheduled_tasks.ps1
One-time setup: registers three Windows Task Scheduler jobs so the paper-
trading dashboard starts, stops, and is watched over itself on weekdays
without you having to launch it manually. Run this once, as your normal
user (no admin needed -- these are per-user scheduled tasks).

  PaperTradingApp_Start    -- Mon-Fri 09:10 IST, runs scripts/start_app.ps1
  PaperTradingApp_Stop     -- Mon-Fri 15:30 IST, runs scripts/stop_app.ps1
  PaperTradingApp_Watchdog -- Mon-Fri 09:15-15:15, every 5 min, runs
                              scripts/watchdog.ps1 -- pops a Windows
                              notification if the engine heartbeat goes
                              stale or a new error shows up in today's log.
                              Alert-only -- never restarts or touches the
                              engine itself.

Timing: start is 5 min before MARKET_OPEN (09:15) so the engine is polling
before the first candle closes. Stop is 15 min after SQUARE_OFF_TIME (15:15,
see signal_engine.py) -- comfortably after engine.py's own automatic
SL/TSL/target/EOD-exit logic has already closed anything open; this task
never substitutes for that, it just stops leaving the process running
unattended overnight.

STILL MANUAL: the Upstox access token (1_generate_token.py's interactive
browser login) -- tokens expire ~3:30 AM daily and this setup does not
automate re-login. Run 1_generate_token.py yourself before market open each
day, or engine.py will just log auth failures and keep retrying.

To undo: Unregister-ScheduledTask -TaskName "PaperTradingApp_Start"
         Unregister-ScheduledTask -TaskName "PaperTradingApp_Stop"
         Unregister-ScheduledTask -TaskName "PaperTradingApp_Watchdog"
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $RepoRoot "scripts\start_app.ps1"
$StopScript = Join-Path $RepoRoot "scripts\stop_app.ps1"
$WatchdogScript = Join-Path $RepoRoot "scripts\watchdog.ps1"
$Weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

$startAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""
$startTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At "09:10"
Register-ScheduledTask -TaskName "PaperTradingApp_Start" `
    -Action $startAction -Trigger $startTrigger `
    -Description "Starts the paper-trading dashboard (app.py) on weekday mornings." `
    -Force | Out-Null
Write-Host "Registered PaperTradingApp_Start (Mon-Fri 09:10)"

$stopAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StopScript`""
$stopTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At "15:30"
Register-ScheduledTask -TaskName "PaperTradingApp_Stop" `
    -Action $stopAction -Trigger $stopTrigger `
    -Description "Stops the paper-trading dashboard (app.py) after square-off." `
    -Force | Out-Null
Write-Host "Registered PaperTradingApp_Stop (Mon-Fri 15:30)"

$watchdogAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$WatchdogScript`""
$watchdogTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Weekdays -At "09:15"
$watchdogTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At "09:15" `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Hours 6)).Repetition
Register-ScheduledTask -TaskName "PaperTradingApp_Watchdog" `
    -Action $watchdogAction -Trigger $watchdogTrigger `
    -Description "Checks engine health every 5 min during market hours and alerts on problems." `
    -Force | Out-Null
Write-Host "Registered PaperTradingApp_Watchdog (Mon-Fri 09:15-15:15, every 5 min)"

Write-Host ""
Write-Host "Reminder: the Upstox access token still needs a manual login (1_generate_token.py)"
Write-Host "before market open each day -- this setup does not automate that."
