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

Respects auto_mode in .streamlit/secrets.toml: auto_mode = false skips the
scheduled launch entirely (task still fires at 09:10, it just does nothing),
so you can flip auto-start off on days you don't want to trade without
touching Task Scheduler itself.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
$DateStamp = Get-Date -Format "yyyy-MM-dd"
$LauncherLog = Join-Path $LogDir "launcher.log"
$StdOutLog = Join-Path $LogDir ("app_{0}.log" -f $DateStamp)
$StdErrLog = Join-Path $LogDir ("app_{0}_err.log" -f $DateStamp)
$SecretsFile = Join-Path $RepoRoot ".streamlit\secrets.toml"
$Port = 8503

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

# Minimal TOML read -- just this one bool key, not a general parser. Missing
# file or missing key both default to auto_mode = true (fail toward the
# existing always-on behavior rather than silently never starting).
$AutoMode = $true
if (Test-Path $SecretsFile) {
    $line = Select-String -Path $SecretsFile -Pattern '^\s*auto_mode\s*=\s*(true|false)\s*$' -CaseSensitive:$false
    if ($line) {
        $AutoMode = ($line.Matches[0].Groups[1].Value.ToLower() -eq "true")
    }
}

if (-not $AutoMode) {
    Add-Content -Path $LauncherLog -Value "$(Get-Date -Format o) - auto_mode is false in secrets.toml, skipping scheduled start."
    exit 0
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
