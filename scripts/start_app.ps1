<#
scripts/start_app.ps1
Launches the paper-trading dashboard (app.py) hidden, for the Windows Task
Scheduler job that starts it automatically on weekday mornings -- see
scripts/register_scheduled_tasks.ps1.

Does NOT touch the Upstox access token -- 1_generate_token.py's interactive
browser login (~3:30 AM daily expiry) still has to be run manually before
this starts, or engine.py will just log auth failures and retry per its
existing error handling. This script only saves you from remembering to
launch Streamlit itself every morning.

If the app is already running (port already listening), this is a no-op --
safe to run more than once without spawning duplicate engine loops.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
$DateStamp = Get-Date -Format "yyyy-MM-dd"
$LauncherLog = Join-Path $LogDir "launcher.log"
$StdOutLog = Join-Path $LogDir ("app_{0}.log" -f $DateStamp)
$StdErrLog = Join-Path $LogDir ("app_{0}_err.log" -f $DateStamp)
$Port = 8503

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Add-Content -Path $LauncherLog -Value "$(Get-Date -Format o) - app already running on port $Port, skipping start."
    exit 0
}

Add-Content -Path $LauncherLog -Value "$(Get-Date -Format o) - starting app.py on port $Port"

Start-Process -FilePath $Python `
    -ArgumentList "-m", "streamlit", "run", "app.py", "--server.port", $Port, "--server.headless", "true" `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdOutLog `
    -RedirectStandardError $StdErrLog
