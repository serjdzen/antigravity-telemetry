# 🤖 Server Quota Watchdog (`server-watchdog`)

> **Automated headless server / VPS quota monitoring for Google Antigravity CLI (`agy`) with proactive Telegram alerts.**

---

## 📌 Overview & Purpose

When running autonomous coding agents, cron tasks, or background workflows on a remote Linux server / VPS:
- You do not have the macOS Menu Bar widget or a browser dashboard actively open.
- Long-running agent loops can rapidly consume 5-hour rolling limits or 7-day quota pools without immediate visibility.
- If quotas run dry, pipelines and background agents silently fail.

**Server Quota Watchdog** solves this by:
1. Running periodically (e.g. via `cron` every 3 hours).
2. Querying Google Cloud Code Assist quota buckets directly via the local `agy` binary (`agy --quota --json`).
3. Automatically decoding OAuth tokens to detect the active account email.
4. Sending clean, formatted HTML notifications to your Telegram chat/topic:
   - ⚠️ **Warning Alert:** When remaining quota drops to $\le 20\%$ (with countdown to limit reset).
   - 🚨 **Critical Alert:** When remaining quota drops to $\le 5\%$.
   - ✅ **Recovery Notification:** When quotas reset and return to normal ($> 20\%$).
5. Preventing notification spam using a local state file (`.agy_quota_state.json`).

---

## 🚀 Quick Start

### 1. Configuration
Create a `.env` file in this directory (or specify its path via `AGY_ENV_PATH`):

```bash
cp .env.example .env
```

Configure your Telegram bot credentials in `.env`:
```env
TELEGRAM_BOT_TOKEN="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
TELEGRAM_CHAT_ID="-1001234567890"

# Optional: Topic ID for Telegram Supergroups / Forum topics
# TELEGRAM_THREAD_ID="1234"

# Optional: Custom account label in alerts
# AGY_ACCOUNT_NAME="Production Agent"
```

### 2. Available Commands

```bash
# Print current quota balances and visual progress bars to the console:
python3 agy_quota_watchdog.py --status

# Send a test alert to your Telegram chat to verify connectivity:
python3 agy_quota_watchdog.py --test-alert

# Scheduled check mode (dispatches alerts only when thresholds are crossed):
python3 agy_quota_watchdog.py --check
```

---

## ⏰ Cron Setup (Every 3 Hours)

To run the watchdog automatically on your Linux VPS:

```bash
crontab -e
```

Add the following entry:
```cron
0 */3 * * * /usr/bin/python3 /path/to/server-watchdog/agy_quota_watchdog.py --check >> /var/log/agy_watchdog.log 2>&1
```

---

## 🔒 Security

- Credentials (`.env`), state files (`.agy_quota_state.json`), and logs are strictly ignored by `.gitignore`.
- Does not expose any open network ports; communicates outbound only with official Google OAuth and Telegram Bot API endpoints.
