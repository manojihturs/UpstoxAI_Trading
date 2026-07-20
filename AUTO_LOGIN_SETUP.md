# Upstox Auto-Login Setup

Automates the daily access-token refresh (`1_generate_token.py`'s
interactive browser login) via Selenium, so the engine has a fresh token
before market open without you doing it by hand every morning.

**Read the security note before setting this up.**

## Security note (read this first)

`1_generate_token.py` and the `upstox_token` secret only ever store an
**access token** — it expires in hours and only allows API calls (place
orders, fetch data). This feature is different: it stores your **PIN and
TOTP secret**, which are enough to fully log into your real Upstox account,
indefinitely, from anywhere. `.streamlit/secrets.toml` is gitignored but
**not encrypted at rest** — anyone with access to this machine or that file
has full account access.

Only set this up if you're comfortable with that tradeoff specifically on
this machine. If not, keep running `1_generate_token.py` by hand — it's a
30-second daily task, not a big cost.

## 1. Get your TOTP secret

This is the base32 text string shown *underneath* the QR code when you
first set up an authenticator app (Google Authenticator, Authy, etc.) for
Upstox 2FA — not the 6-digit code the app currently shows. If you only
scanned the QR code and never saw or saved the text version, Upstox
generally won't show it to you again; you'd need to reset/re-enable 2FA to
get a fresh one.

## 2. Add credentials to `.streamlit/secrets.toml`

```toml
upstox_ucc = "YOUR_CLIENT_CODE"      # your login Client Code / UCC
upstox_pin = "YOUR_PIN"
upstox_totp_secret = "YOUR_BASE32_SECRET"
```

`upstox_app_client_id` / `upstox_app_client_secret` are optional — if
`UPSTOX_CLIENT_ID`/`UPSTOX_CLIENT_SECRET` are already set as environment
variables (they likely are, from setting up `1_generate_token.py`),
`auto_login.py` picks those up automatically.

## 3. Test it manually — do not skip this

```
python auto_login.py
```

Watch what happens. This script's login-page selectors (which field is
which) were written from the documented Upstox OAuth flow but **not
verified against the live page** — Upstox's actual login page may differ,
or change over time. If it fails, the error message tells you which step
(Client Code / PIN / TOTP / redirect) it got stuck on — open the real login
page in a browser, inspect that field (F12 → Elements), and update the
matching `By.ID(...)` call in `auto_login.py`.

On success, it prints `Access token refreshed successfully.` and updates
both `upstox_token.txt` and the `upstox_token` line in `secrets.toml`.

## 4. Only once step 3 works: schedule it

`scripts/refresh_token.ps1` is **not** auto-registered — on purpose, so
nothing runs this unattended before you've confirmed it actually logs in.
Once verified:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\refresh_token.ps1`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "09:00"
Register-ScheduledTask -TaskName "PaperTradingApp_TokenRefresh" -Action $action -Trigger $trigger `
    -Description "Refreshes the Upstox access token before market open."
```

That's 09:00 — 10 minutes before `PaperTradingApp_Start` (09:10), so the
token is ready before the engine needs it. If the refresh fails, you'll
get a Windows notification (same pattern as `scripts/watchdog.ps1`).

## Undoing this

```powershell
Unregister-ScheduledTask -TaskName "PaperTradingApp_TokenRefresh"
```

And remove `upstox_ucc` / `upstox_pin` / `upstox_totp_secret` from
`secrets.toml` if you want to fully revert to manual login.
