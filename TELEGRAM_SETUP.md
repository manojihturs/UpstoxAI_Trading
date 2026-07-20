# Telegram Trade Alerts — Setup

Free, takes about 2 minutes. Once set up, you'll get a Telegram message
every time a (paper) position opens and every time it closes, with the
instrument, strike, direction, price, and P&L.

## 1. Create a bot

1. Open Telegram, search for **@BotFather** (the official bot for creating
   bots), and start a chat.
2. Send `/newbot`.
3. Give it a name (anything, e.g. "My Trading Alerts") and a username
   (must end in `bot`, e.g. `manoj_trading_alerts_bot`).
4. BotFather replies with an **API token** — looks like
   `123456789:AAHqK...xyz`. Copy it.

## 2. Get your chat ID

1. Search for your new bot by its username and start a chat with it —
   send it any message (e.g. "hi").
2. In a browser, visit (replace `<TOKEN>` with your token from step 1):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Look for `"chat":{"id":123456789,...}` in the JSON response — that
   number is your **chat ID**. (If you see an empty `"result":[]`, make
   sure you actually sent the bot a message first, then reload the URL.)

## 3. Add both to secrets

Edit `.streamlit/secrets.toml` (gitignored, local-only):

```toml
telegram_bot_token = "123456789:AAHqK...xyz"
telegram_chat_id = "123456789"
```

That's it — no restart needed beyond the next engine poll cycle. Leave
either value blank to disable notifications entirely; `notifications.py`
treats "not configured" as a silent no-op, not an error.

## 4. Verify it works

With the app running and auto-confirm on (or after manually confirming a
signal), you should get a Telegram message on the next entry and again on
the next exit. To test without waiting for a real signal, run:

```python
import notifications
notifications.send_telegram_message("Test message from the trading app")
```

If nothing arrives, double check: the bot token is correct, you sent the
bot at least one message before calling `getUpdates`, and the chat ID has
no extra quotes/spaces pasted in by accident.

## What you'll receive

**On entry:**
```
ENTRY -- NIFTY 24100.0 CALL (CE)
Qty: 65
Entry: Rs 120.50
SL: Rs 114.10  |  Target: Rs 130.50
Paper trade -- no real order placed.
```

**On exit:**
```
EXIT (TARGET) -- NIFTY 24100.0 CALL (CE)
Qty: 65
Exit: Rs 128.00
Net P&L: Rs 150.00 (PROFIT)
Paper trade -- no real order placed.
```

## Why Telegram instead of WhatsApp or SMS

WhatsApp (via Twilio) and SMS gateways both require a paid account and,
for WhatsApp, business-number approval before they'll send anything to a
real number outside a sandbox. Telegram's Bot API is free, has no approval
step, and delivers just as reliably to your phone (push notification, same
as WhatsApp/SMS). If you specifically need WhatsApp or SMS later, the same
`notify_entry`/`notify_exit` call sites in `engine.py` can point at a
different send function — ask, and it can be wired up once you have a
Twilio (or other gateway) account.
