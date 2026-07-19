<#
scripts/stop_app.ps1
Stops the Streamlit process for the paper-trading dashboard. Scheduled for
weekday afternoons AFTER SQUARE_OFF_TIME (15:15, see signal_engine.py) --
never before it, since engine.py's own automatic SL/TSL/target/EOD-exit
logic is what actually closes any open position, and killing the process
before that has run would abandon a real (paper) open position mid-trade
with no one watching it.

This is purely a "stop leaving the process running overnight" convenience --
not a substitute for the app's own timing rules. Safe to run even if
nothing is listening (no-op).
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot "logs"
$LauncherLog = Join-Path $LogDir "launcher.log"
$Port = 8503

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    Add-Content -Path $LauncherLog -Value "$(Get-Date -Format o) - nothing listening on port $Port, nothing to stop."
    exit 0
}

$procId = $listening[0].OwningProcess
Stop-Process -Id $procId -Force
Add-Content -Path $LauncherLog -Value "$(Get-Date -Format o) - stopped app.py (PID $procId) on port $Port"
