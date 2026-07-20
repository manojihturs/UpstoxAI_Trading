"""
auto_login.py
Automates the daily Upstox access-token refresh via Selenium: logs into
the OAuth authorization page with your account credentials (client code +
PIN + TOTP), captures the redirect's auth code, and exchanges it for a
fresh access token -- the same token-exchange call 1_generate_token.py
uses interactively.

*** RUN THIS YOURSELF FIRST, MANUALLY, BEFORE TRUSTING IT UNATTENDED ***
The CSS/element selectors below (mobileNum, pinCode, otpNum, etc.) are
this script's best-effort guess at Upstox's current login page structure,
based on the documented OAuth flow (client code -> PIN -> TOTP) -- they
were NOT verified against the live page at write time (no web access when
this was built). Upstox can also change their login page layout at any
time regardless. Run `python auto_login.py` yourself and watch it work
(or fail) before wiring it into an unattended scheduled task. If a
selector is wrong, the error message will name which step failed --
inspect the actual page (F12 -> Elements) at that step and update the
matching By.ID/By.NAME call below.

Needs in .streamlit/secrets.toml (gitignored, local only):
  upstox_ucc                -- your trading Client Code / UCC (login username)
  upstox_pin                 -- your trading PIN
  upstox_totp_secret         -- the base32 TOTP secret from when you set up
                                 2FA (NOT the 6-digit code -- pyotp generates
                                 that fresh each run). This is usually shown
                                 as text alongside the QR code when you first
                                 enabled an authenticator app for Upstox --
                                 if you only scanned the QR and never saw the
                                 text secret, you may need to reset 2FA to
                                 get it, since Upstox won't show it again.
  upstox_app_client_id / upstox_app_client_secret -- your developer app's
                                 OAuth credentials (same as UPSTOX_CLIENT_ID/
                                 UPSTOX_CLIENT_SECRET in 1_generate_token.py;
                                 falls back to those env vars if unset here)

*** SECURITY NOTE ***
A PIN + TOTP secret is enough to fully log into your real Upstox account --
this is meaningfully more sensitive than the Telegram bot token or the
Upstox ACCESS token already stored in this file (an access token expires
in hours and only allows API calls; your PIN+TOTP secret allows logging in
as you, indefinitely, anywhere). secrets.toml is gitignored but NOT
encrypted at rest -- anyone with access to this machine/file has full
account access. Only proceed if you're comfortable with that tradeoff on
this specific machine.
"""
import os

import pyotp
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

REDIRECT_URI = "https://127.0.0.1"
LOGIN_TIMEOUT_SECONDS = 30
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_secret(key, env_var=None):
    try:
        import streamlit as st
        value = st.secrets.get(key)
        if value:
            return value
    except Exception:
        pass
    return os.environ.get(env_var) if env_var else None


def _get_access_token_via_browser(app_client_id, app_client_secret, ucc, pin, totp_secret):
    auth_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={app_client_id}&redirect_uri={REDIRECT_URI}"
    )

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1280,900")
    driver = webdriver.Chrome(options=options)

    try:
        driver.get(auth_url)
        wait = WebDriverWait(driver, LOGIN_TIMEOUT_SECONDS)

        # Step 1: Client Code / UCC
        try:
            ucc_field = wait.until(EC.presence_of_element_located((By.ID, "mobileNum")))
        except Exception as e:
            raise RuntimeError(
                "Could not find the Client Code / UCC input field (tried id='mobileNum'). "
                "Upstox's login page structure may have changed -- inspect the page "
                "(F12 -> Elements) and update the selector in auto_login.py."
            ) from e
        ucc_field.clear()
        ucc_field.send_keys(ucc)
        driver.find_element(By.ID, "getOtp").click()

        # Step 2: PIN
        try:
            pin_field = wait.until(EC.presence_of_element_located((By.ID, "pinCode")))
        except Exception as e:
            raise RuntimeError(
                "Could not find the PIN input field (tried id='pinCode') after entering "
                "the Client Code -- check the page structure at this step."
            ) from e
        pin_field.send_keys(pin)
        driver.find_element(By.ID, "pinContinueBtn").click()

        # Step 3: TOTP (generated fresh from the secret, not a fixed code)
        try:
            totp_field = wait.until(EC.presence_of_element_located((By.ID, "otpNum")))
        except Exception as e:
            raise RuntimeError(
                "Could not find the TOTP input field (tried id='otpNum') after entering "
                "the PIN -- check the page structure at this step."
            ) from e
        totp_code = pyotp.TOTP(totp_secret).now()
        totp_field.send_keys(totp_code)
        driver.find_element(By.ID, "continueBtn").click()

        # Wait for the redirect back to REDIRECT_URI with ?code=...
        try:
            wait.until(lambda d: d.current_url.startswith(REDIRECT_URI))
        except Exception as e:
            raise RuntimeError(
                f"Login did not redirect to {REDIRECT_URI} within {LOGIN_TIMEOUT_SECONDS}s -- "
                f"still on: {driver.current_url}. Likely a wrong PIN/TOTP, an unexpected extra "
                f"step (e.g. a consent screen), or a login-page change."
            ) from e
        redirected_url = driver.current_url
    finally:
        driver.quit()

    if "code=" not in redirected_url:
        raise RuntimeError(f"Redirected, but no auth code in the URL -- got: {redirected_url}")
    auth_code = redirected_url.split("code=")[1].split("&")[0]

    token_url = "https://api.upstox.com/v2/login/authorization/token"
    payload = {
        "code": auth_code,
        "client_id": app_client_id,
        "client_secret": app_client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    headers = {"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(token_url, data=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"No access_token in the token-exchange response: {data}")
    return access_token


def _write_token(access_token):
    """Writes to BOTH upstox_token.txt and secrets.toml so
    option_selector.get_access_token() picks it up regardless of which
    source it prefers (secrets.toml first, then the local file)."""
    token_file = os.path.join(BASE_DIR, "upstox_token.txt")
    with open(token_file, "w") as f:
        f.write(access_token)

    secrets_path = os.path.join(BASE_DIR, ".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        with open(secrets_path) as f:
            lines = f.readlines()
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith("upstox_token"):
                lines[i] = f'upstox_token = "{access_token}"\n'
                found = True
                break
        if not found:
            lines.append(f'\nupstox_token = "{access_token}"\n')
        with open(secrets_path, "w") as f:
            f.writelines(lines)


def refresh_token():
    """Returns the new access token, or raises with a specific reason."""
    ucc = _get_secret("upstox_ucc")
    pin = _get_secret("upstox_pin")
    totp_secret = _get_secret("upstox_totp_secret")
    app_client_id = _get_secret("upstox_app_client_id", "UPSTOX_CLIENT_ID")
    app_client_secret = _get_secret("upstox_app_client_secret", "UPSTOX_CLIENT_SECRET")

    missing = [name for name, val in [
        ("upstox_ucc", ucc), ("upstox_pin", pin), ("upstox_totp_secret", totp_secret),
        ("upstox_app_client_id / UPSTOX_CLIENT_ID", app_client_id),
        ("upstox_app_client_secret / UPSTOX_CLIENT_SECRET", app_client_secret),
    ] if not val]
    if missing:
        raise RuntimeError(
            f"Missing credentials for auto-login: {', '.join(missing)}. "
            f"Add them to .streamlit/secrets.toml -- see auto_login.py's module docstring."
        )

    access_token = _get_access_token_via_browser(app_client_id, app_client_secret, ucc, pin, totp_secret)
    _write_token(access_token)
    return access_token


if __name__ == "__main__":
    token = refresh_token()
    print(f"Access token refreshed successfully. First 20 chars: {token[:20]}...")
