# Upstox Auto-Login Setup (Semi-Automatic)

Automates most of the daily access-token refresh (`1_generate_token.py`'s
interactive browser login) via Selenium: mobile number entry, TOTP/PIN if
needed, and the token exchange all happen automatically. **One manual step
remains and can't be removed**: typing in the SMS OTP Upstox texts you.

## Why this can't be fully unattended

Confirmed against the live login page on 2026-07-20 (not guessed): Upstox's
OAuth login is "Login with mobile number" — it always sends a fresh SMS OTP
to your registered phone. That code is generated server-side per attempt;
unlike a TOTP code, it is **not derivable from any stored secret**. No
script — this one or otherwise — can know it in advance. Genuinely
eliminating this step would need a separate phone-side integration
(something forwarding the SMS text to the script automatically), which
isn't built here.

Because of this, `scripts/refresh_token.ps1` / Task Scheduler are **not**
part of this setup — a scheduled task can't show you a prompt and wait for
you to type an SMS code. Run `auto_login.py` yourself each morning instead
(a desktop shortcut works well) — it's still much faster than the fully
manual browser flow.

## Security note (read this first)

`1_generate_token.py` and the `upstox_token` secret only ever store an
**access token** — it expires in hours and only allows API calls. This
feature is different: it stores your **PIN and TOTP secret** (used only if
Upstox asks for a second factor after the SMS OTP — unconfirmed whether it
does), which are enough to fully log into your real Upstox account,
indefinitely, from anywhere. `.streamlit/secrets.toml` is gitignored but
**not encrypted at rest** — anyone with access to this machine or that file
has full account access.

Only set this up if you're comfortable with that tradeoff specifically on
this machine.

## 1. Add credentials to `.streamlit/secrets.toml`

```toml
upstox_mobile_number = "9XXXXXXXXX"   # your Upstox-registered 10-digit mobile number
upstox_pin = "YOUR_PIN"                # optional -- only used if a PIN step appears
upstox_totp_secret = "YOUR_BASE32_SECRET"  # optional -- only used if a TOTP step appears
```

`upstox_pin` / `upstox_totp_secret` are only actually used if a second
factor shows up after the SMS OTP — as of writing this hasn't been
confirmed either way. `upstox_app_client_id` / `upstox_app_client_secret`
are optional too — if `UPSTOX_CLIENT_ID`/`UPSTOX_CLIENT_SECRET` are already
env vars (they likely are, from `1_generate_token.py`), those get picked
up automatically.

## 2. Windows-on-ARM only: get a working ChromeDriver

Skip this section on regular (x64) Windows -- Selenium's built-in driver
downloader handles it automatically.

On Windows-on-ARM (e.g. Snapdragon-based laptops), Selenium Manager can't
resolve a driver for either Chrome or Edge (`Unsupported platform/
architecture combination: win32/arm64`), and Microsoft's own EdgeDriver
hosting (`msedgedriver.azurewebsites.net`) is retired/access-blocked as of
2026-07-20. The fix: download a **win64** ChromeDriver build manually --
it runs fine via Windows' built-in x64 emulation, since the driver only
talks to Chrome over a local network port (DevTools Protocol), not native
code, so the driver/browser architecture mismatch doesn't matter.

```powershell
# 1. Find your installed Chrome version
(Get-Item "C:\Program Files\Google\Chrome\Application\chrome.exe").VersionInfo.ProductVersion

# 2. Find the matching (or closest same major.minor.build) chromedriver at
#    https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json
#    -- look up your version's "downloads.chromedriver" entry for platform "win64"

# 3. Download and extract into drivers/ (gitignored -- this binary never gets committed)
Invoke-WebRequest -Uri "<the win64 chromedriver.zip URL from step 2>" -OutFile "drivers\chromedriver-win64.zip"
Expand-Archive -Path "drivers\chromedriver-win64.zip" -DestinationPath "drivers" -Force
```

`auto_login.py` expects it at `drivers/chromedriver-win64/chromedriver.exe`
(the path inside the zip already). If Chrome auto-updates far enough ahead
that this driver stops working, redo these steps with the new version.

## 3. Run it

```
python auto_login.py
```

The browser window opens visibly (not headless — you need to see it reach
the OTP step). It fills your mobile number, clicks "Get OTP", then prompts:

```
*** Check your phone -- Upstox just sent you an SMS OTP. ***
Enter the OTP here:
```

Type the code from the SMS and press Enter. If Upstox asks for a PIN or
TOTP after that, it's filled in automatically from your stored secret; if
not, login just completes on the OTP alone.

If a selector doesn't match (Upstox changed their page), the error message
saves a screenshot + HTML dump under `drivers/debug/` (gitignored, with any
password/PIN/OTP field values redacted before saving) — share that with
whoever's fixing the code, no live DOM inspection needed.

On success, it prints `Access token refreshed successfully.` and updates
both `upstox_token.txt` and the `upstox_token` line in `secrets.toml`.

## Undoing this

Remove `upstox_mobile_number` / `upstox_pin` / `upstox_totp_secret` from
`secrets.toml` if you want to revert to running `1_generate_token.py`
fully manually.
