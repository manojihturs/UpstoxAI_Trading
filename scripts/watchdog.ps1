<#
scripts/watchdog.ps1
Runs scripts/check_engine_health.py and pops a Windows toast notification
if it reports a problem -- the "someone actually notices" half of
monitoring. Meant to be scheduled every few minutes during market hours
(see register_scheduled_tasks.ps1's PaperTradingApp_Watchdog task).

Deliberately does nothing to the engine itself (no restart, no kill) --
this is alerting only. Deciding what to do about a stale engine or a fresh
error is a human judgment call, not something to automate blindly on top
of a system already handling (paper) money.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$HealthScript = Join-Path $RepoRoot "scripts\check_engine_health.py"
$LogDir = Join-Path $RepoRoot "logs"
$LauncherLog = Join-Path $LogDir "launcher.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$output = & $Python $HealthScript 2>&1
$exitCode = $LASTEXITCODE

Add-Content -Path $LauncherLog -Value "$(Get-Date -Format o) - watchdog: $output"

if ($exitCode -ne 1) {
    exit 0
}

# Windows balloon-tip notification via .NET -- no extra module (e.g.
# BurntToast) needed, works on any Windows box out of the box.
Add-Type -AssemblyName System.Windows.Forms
$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = [System.Drawing.SystemIcons]::Warning
$notifyIcon.Visible = $true
$notifyIcon.ShowBalloonTip(15000, "Paper Trading Engine Alert", "$output", [System.Windows.Forms.ToolTipIcon]::Warning)
Start-Sleep -Seconds 1
$notifyIcon.Dispose()
