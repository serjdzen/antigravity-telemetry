#!/usr/bin/env python3
"""
Antigravity Quota Watchdog
Checks Antigravity CLI quota periodically.
Sends Telegram notification if remaining quota drops below 20%.
"""

import sys
import os
import json
import base64
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone

ENV_PATH = os.environ.get("AGY_ENV_PATH", os.path.expanduser("~/.env"))
STATE_FILE = os.environ.get("AGY_STATE_FILE", os.path.expanduser("~/.agy_quota_state.json"))
AGY_BIN = shutil.which("agy") or "/usr/local/bin/agy"
ALERT_THRESHOLD = 0.20  # 20%
CRITICAL_THRESHOLD = 0.05  # 5%


def load_telegram_config():
    token, chat_id, thread_id = None, None, None
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().replace(chr(34), "").replace(chr(39), "")
                    if key == "TELEGRAM_BOT_TOKEN":
                        token = val
                    elif key == "TELEGRAM_CHAT_ID":
                        chat_id = val
                    elif key == "TELEGRAM_THREAD_ID":
                        thread_id = val
    return token, chat_id, thread_id


def get_account_name():
    # 1. From ENV_PATH if explicitly specified
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("AGY_ACCOUNT_NAME="):
                        val = line.split("=", 1)[1].strip().replace(chr(34), "").replace(chr(39), "")
                        if val:
                            return val
        except Exception:
            pass

    # 2. Extract from OAuth token files (JWT id_token payload)
    candidate_files = [
        "/root/.gemini/antigravity-cli/antigravity-oauth-token",
        "/root/.gemini/jetski-standalone-oauth-token",
        "/root/.gemini/oauth_creds.json",
        os.path.expanduser("~/.gemini/antigravity-cli/antigravity-oauth-token"),
        os.path.expanduser("~/.gemini/jetski-standalone-oauth-token"),
    ]

    for p in candidate_files:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Check id_token JWT
                for key in ["id_token", "idToken"]:
                    tok = data.get(key)
                    if tok and isinstance(tok, str) and "." in tok:
                        parts = tok.split(".")
                        if len(parts) >= 2:
                            payload_b64 = parts[1]
                            payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                            payload = json.loads(base64.b64decode(payload_b64).decode("utf-8", "ignore"))
                            email = payload.get("email")
                            if email:
                                return email

                if "email" in data and data["email"]:
                    return data["email"]
            except Exception:
                pass

    return "Antigravity CLI (Remote Server)"


def send_telegram_message(token, chat_id, thread_id, text):
    if not token or not chat_id:
        print("[Error] Missing Telegram token or chat_id", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        try:
            payload["message_thread_id"] = int(thread_id)
        except ValueError:
            pass

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "AgyQuotaWatchdog/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            return res_json.get("ok", False)
    except Exception as e:
        print(f"[Error] Failed to send Telegram message: {e}", file=sys.stderr)
        return False


def get_quota_data():
    try:
        proc = subprocess.run(
            [AGY_BIN, "-p", "/usage", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            print(f"[Error] agy command failed: {proc.stderr}", file=sys.stderr)
            return None

        raw = json.loads(proc.stdout)
        groups = raw.get("command", {}).get("data", {}).get("groups", [])
        return groups
    except Exception as e:
        print(f"[Error] Failed to get quota from agy: {e}", file=sys.stderr)
        return None


def format_duration(seconds):
    if seconds <= 0:
        return "сейчас"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if hours > 0:
        parts.append(f"{hours} ч")
    if minutes > 0 or hours == 0:
        parts.append(f"{minutes} мин")
    return " ".join(parts)


def parse_buckets(groups):
    parsed = []
    now = datetime.now(timezone.utc)
    for grp in groups:
        grp_name = grp.get("name", "Unknown Group")
        for bucket in grp.get("buckets", []):
            bid = bucket.get("id", "")
            name = bucket.get("name", "")
            rem = bucket.get("remaining_fraction", 1.0)
            reset_str = bucket.get("reset_time", "")

            time_left_str = "неизвестно"
            if reset_str:
                try:
                    reset_dt = datetime.fromisoformat(reset_str.replace("Z", "+00:00"))
                    diff = (reset_dt - now).total_seconds()
                    time_left_str = format_duration(diff)
                except Exception:
                    time_left_str = reset_str

            parsed.append({
                "group": grp_name,
                "id": bid,
                "name": name,
                "remaining_fraction": rem,
                "remaining_pct": round(rem * 100, 1),
                "reset_time": reset_str,
                "time_left": time_left_str,
            })
    return parsed


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"alert_active": False, "critical_alerted": False, "last_check": None}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[Warning] Failed to save state: {e}", file=sys.stderr)


def render_progress_bar(pct, width=8):
    filled = int(round(width * (pct / 100.0)))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--check"
    token, chat_id, thread_id = load_telegram_config()
    account_name = get_account_name()
    groups = get_quota_data()
    if not groups:
        print("[Error] Could not fetch quota data.", file=sys.stderr)
        sys.exit(1)

    buckets = parse_buckets(groups)

    if mode == "--status":
        print("\n📊 Antigravity Quota Status")
        print(f"👤 Аккаунт: {account_name}")
        print("=" * 45)
        for b in buckets:
            bar = render_progress_bar(b["remaining_pct"], 10)
            print(f"• {b['group']} - {b['name']}:")
            print(f"  [{bar}] {b['remaining_pct']}% | Сброс: {b['time_left']}")
        print("=" * 45)
        return

    if mode == "--test-alert":
        print(f"Отправка тестового сообщения в Telegram (Аккаунт: {account_name})...")
        msg_lines = [
            "🧪 <b>Тест Antigravity Quota Watchdog</b>",
            f"👤 <b>Аккаунт:</b> <code>{account_name}</code>\n",
            "Скрипт мониторинга успешно подключен.",
            "Текущие лимиты на сервере:\n",
        ]
        for b in buckets:
            bar = render_progress_bar(b["remaining_pct"], 8)
            msg_lines.append(f"• <b>{b['group']} ({b['name']})</b>:\n  <code>[{bar}] {b['remaining_pct']}%</code> (сброс: {b['time_left']})")

        msg_lines.append("\n<i>Запуск проверки настроен в cron каждые 3 часа.</i>")
        test_msg = "\n".join(msg_lines)
        ok = send_telegram_message(token, chat_id, thread_id, test_msg)
        if ok:
            print("✅ Тестовое сообщение успешно отправлено в Telegram!")
        else:
            print("❌ Ошибка отправки тестового сообщения.")
        return

    # Regular --check mode (cron)
    state = load_state()
    state["last_check"] = datetime.now(timezone.utc).isoformat()

    depleted_buckets = [b for b in buckets if b["remaining_fraction"] <= ALERT_THRESHOLD]
    critical_buckets = [b for b in buckets if b["remaining_fraction"] <= CRITICAL_THRESHOLD]

    if depleted_buckets:
        is_critical = len(critical_buckets) > 0
        should_send = False

        if not state.get("alert_active", False):
            should_send = True
        elif is_critical and not state.get("critical_alerted", False):
            should_send = True

        if should_send:
            title = "🚨 <b>КРИТИЧЕСКИЙ ЛИМИТ Antigravity</b>" if is_critical else "⚠️ <b>Предупреждение о лимитах Antigravity</b>"
            lines = [
                title,
                f"👤 <b>Аккаунт:</b> <code>{account_name}</code>\n",
            ]
            for b in depleted_buckets:
                bar = render_progress_bar(b["remaining_pct"], 8)
                lines.append(f"• <b>{b['group']} - {b['name']}</b>:\n  Осталось: <b>{b['remaining_pct']}%</b> <code>[{bar}]</code>\n  Сброс через: <b>{b['time_left']}</b>")

            lines.append("\n<i>Рекомендуется приостановить тяжелые генерации до сброса лимита.</i>")
            text = "\n".join(lines)
            ok = send_telegram_message(token, chat_id, thread_id, text)
            if ok:
                state["alert_active"] = True
                if is_critical:
                    state["critical_alerted"] = True
                save_state(state)
    else:
        # Healthy
        if state.get("alert_active", False):
            lines = [
                "✅ <b>Лимиты Antigravity восстановлены</b>",
                f"👤 <b>Аккаунт:</b> <code>{account_name}</code>\n",
                "Все лимиты вернулись в рабочую зону (>20%):\n",
            ]
            for b in buckets:
                bar = render_progress_bar(b["remaining_pct"], 8)
                lines.append(f"• <b>{b['group']} ({b['name']})</b>: <code>[{bar}] {b['remaining_pct']}%</code>")
            lines.append("\n<i>Можно продолжать выполнение задач.</i>")
            text = "\n".join(lines)
            send_telegram_message(token, chat_id, thread_id, text)

            state["alert_active"] = False
            state["critical_alerted"] = False
            save_state(state)


if __name__ == "__main__":
    main()
