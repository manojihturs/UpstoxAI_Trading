"""
STEP 1: Generate Upstox access token.
Run this once. Access tokens are valid until ~3:30 AM the next day (Upstox session policy).

Prereqs:
  pip install requests

Before running, set these two environment variables (your app credentials
from https://developer.upstox.com -> My Apps). Never hardcode them here --
this file is committed to source control.

  PowerShell:  $env:UPSTOX_CLIENT_ID="..."; $env:UPSTOX_CLIENT_SECRET="..."
  bash:        export UPSTOX_CLIENT_ID=...; export UPSTOX_CLIENT_SECRET=...
"""
import os
import requests
import webbrowser

CLIENT_ID = os.environ.get("UPSTOX_CLIENT_ID")
CLIENT_SECRET = os.environ.get("UPSTOX_CLIENT_SECRET")
REDIRECT_URI = "https://127.0.0.1"   # must EXACTLY match what you set in the Upstox app

if not CLIENT_ID or not CLIENT_SECRET:
    raise SystemExit(
        "Set UPSTOX_CLIENT_ID and UPSTOX_CLIENT_SECRET environment variables before running this script."
    )

def main():
    auth_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
    )
    print("Opening browser for login...")
    print("If it doesn't open automatically, paste this URL into your browser:\n")
    print(auth_url)
    webbrowser.open(auth_url)

    print("\nAfter logging in, you'll be redirected to a URL like:")
    print(f"  {REDIRECT_URI}/?code=XXXXXX")
    print("Copy the value of 'code' from that URL and paste it below.\n")

    code = input("Paste the 'code' value here: ").strip()

    token_url = "https://api.upstox.com/v2/login/authorization/token"
    payload = {
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    headers = {"accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}

    resp = requests.post(token_url, data=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    access_token = data.get("access_token")
    if not access_token:
        print("ERROR: No access token in response:", data)
        return

    with open("upstox_token.txt", "w") as f:
        f.write(access_token)

    print("\nSuccess! Access token saved to upstox_token.txt")
    print("This token is valid until ~3:30 AM tomorrow. Re-run this script daily.")

if __name__ == "__main__":
    main()
