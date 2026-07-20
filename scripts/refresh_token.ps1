<#
scripts/refresh_token.ps1
Runs auto_login.py to refresh the Upstox access token, for the Task
Scheduler job that does this automatically each weekday morning BEFORE
scripts/start_app.ps1 launches the dashboard.

NOT auto-registered by register_scheduled_tasks.ps1 -- deliberately. Run
`python auto_login.py` yourself first and confirm it actually logs in
successfully (Upstox's login page selectors are unverified guesses, see
auto_login.py's docstring) before adding this to the schedule. Once
confirmed working, register it yourself:

  $action = New-ScheduledTaskAction -Execute "powershell.exe" `
      -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\refresh_token.ps1`""
  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "09:00"
  Register-ScheduledTask -TaskName "PaperTradingApp_TokenRefresh" -Action $action -Trigger $trigger `
      -Description "Refreshes the Upstox access token before market open."

(09:00 -- 10 min before PaperTradingApp_Start at 09:10, so the token is
ready before the engine needs it.)
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
$LauncherLog = Join-Path $LogDir "launcher.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Add-Content -Path $LauncherLog -Value "$(Get-Date -Format o) - refreshing Upstox token..."

$output = & $Python (Join-Path $RepoRoot "auto_login.py") 2>&1
$exitCode = $LASTEXITCODE

Add-Content -Path $LauncherLog -Value "$(Get-Date -Format o) - token refresh: $output"

if ($exitCode -ne 0) {
    Add-Type -AssemblyName System.Windows.Forms
    $notifyIcon = New-Object System.Windows.Forms.NotifyIcon
    $notifyIcon.Icon = [System.Drawing.SystemIcons]::Error
    $notifyIcon.Visible = $true
    $notifyIcon.ShowBalloonTip(15000, "Upstox Token Refresh Failed",
        "auto_login.py failed -- engine will start without a valid token. Check logs\launcher.log.",
        [System.Windows.Forms.ToolTipIcon]::Error)
    Start-Sleep -Seconds 1
    $notifyIcon.Dispose()
}

exit $exitCode
