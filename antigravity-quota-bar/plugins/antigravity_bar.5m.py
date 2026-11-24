#!/usr/bin/env python3
"""
<xbar.title>Antigravity Quota Bar</xbar.title>
<xbar.version>v1.0</xbar.version>
<xbar.author>Antigravity Agent</xbar.author>
<xbar.desc>Monitors AI quotas & reset timers for Google Antigravity across multiple accounts with Liquid Glass native styling.</xbar.desc>
<xbar.dependencies>python3</xbar.dependencies>

SwiftBar / xbar plugin for Antigravity Quota Monitoring.
Runs every 15 minutes, or on demand via 'Refresh'.
"""

import os
import sys
import socket
import subprocess

# Prevent any socket or network call from hanging indefinitely
socket.setdefaulttimeout(8)

# Prevent Python from creating .pyc bytecode or __pycache__ in the plugin directory
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# Import core quota checker from parent project folder (using realpath to support symlinks)
PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
PROJECT_DIR = os.path.dirname(PLUGIN_DIR)
sys.path.insert(0, PROJECT_DIR)

try:
    import check_quota
except ImportError:
    print("⚠️ Ошибка пути")
    print("---")
    print(f"Не найден модуль check_quota.py в {PROJECT_DIR}")
    sys.exit(0)


def trigger_background_token_sync():
    """
    Launches background token analytics sync detached from SwiftBar.
    Ensures active sessions are incrementally cached before they can be deleted from UI.
    """
    token_sync_script = os.path.join(os.path.dirname(PROJECT_DIR), "token_analytics", "sync_cache.py")
    if os.path.exists(token_sync_script):
        try:
            subprocess.Popen(
                [sys.executable, token_sync_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True
            )
        except Exception:
            pass


def get_color_for_pct(pct):
    if pct <= 15:
        return " color=#ff453a"  # Red
    elif pct <= 35:
        return " color=#f59e0b"  # Amber
    return ""                    # Native text color


def main():
    trigger_background_token_sync()
    accounts_data = check_quota.get_all_quotas_data(force_refresh=True)

    # Keep static dashboard.html synchronized with fresh menubar data
    try:
        check_quota.generate_static_dashboard()
    except Exception:
        pass

    # Detect currently active account in Antigravity desktop app
    active_email = check_quota.get_active_antigravity_account()
    active_key = check_quota.email_key(active_email or "")
    visible_accounts = [
        acc for acc in accounts_data
        if check_quota.email_key(acc["email"]) == active_key or acc.get("show_in_swiftbar", True)
    ]
    visible_accounts.sort(key=lambda acc: check_quota.email_key(acc["email"]) != active_key)
    primary_acc = visible_accounts[0] if visible_accounts else {}

    bar_title = "Antigravity"

    if primary_acc.get("status") == "ok" and primary_acc.get("models"):
        models = primary_acc["models"]
        g_m = next((m for m in models if m.get("pool_id") == "gemini" or "gemini" in m.get("name", "").lower()), None)

        if g_m:
            pct = g_m["percentage"]
            reset_str = g_m["reset"]
            if pct == 100 or reset_str == "готов":
                bar_title = f"{pct}%"
            else:
                bar_title = f"{pct}% · {reset_str}"
        else:
            bar_title = "Antigravity"
    elif primary_acc.get("status") == "error":
        bar_title = "⚠️ Ошибка"

    # Line 1: In the Menu Bar (strictly Gemini, native macOS high-contrast text)
    print(f"{bar_title} | font=SFPro-Medium size=13")
    print("---")

    py_bin = sys.executable
    add_script = os.path.join(PROJECT_DIR, "check_quota.py")

    # Dropdown content (crisp dark text for macOS Light & Dark Mode)
    if not visible_accounts:
        print("ℹ️ Все аккаунты скрыты" if accounts_data else "ℹ️ Нет подключенных аккаунтов")
    for acc in visible_accounts:
        email = acc["email"]
        is_active = check_quota.email_key(email) == active_key
        label = email[:-len("@gmail.com")] if email.lower().endswith("@gmail.com") else email
        note = acc.get("note", "")
        if note:
            safe_note = " ".join(note.splitlines()).replace("|", "｜")
            label = f"{label} ({safe_note})"
        badge = " (Активен)" if is_active else ""
        if acc.get("cached_fallback"):
            badge += " (кэш)"

        if is_active:
            action = (
                'bash="/usr/bin/osascript" param1="-e" '
                'param2="display notification \\"Аккаунт уже активен\\" with title \\"Antigravity Account Switcher\\"" '
                'terminal=false'
            )
        else:
            target_param = check_quota.email_key(email)
            action = (
                f'bash="{py_bin}" param1="{add_script}" '
                f'param2="--switch-interactive" param3="{target_param}" '
                'terminal=false refresh=true'
            )

        print(f"{label}{badge} | font=SFPro-Bold size=13 color={'#0284c7' if is_active else '#000000'} {action}")

        if acc.get("status") == "error":
            print(f"   ⚠️ {acc.get('error')} | color=#dc2626 size=12 trim=false")
        else:
            for model in acc.get("models", []):
                # Only show Gemini (skip Claude, image, and legacy Argon pools)
                if model.get("pool_id") in ("image", "claude", "argon") or any(
                    kw in model.get("name", "").lower() for kw in ("claude", "argon")
                ):
                    continue

                # 1. 5-hour quota row (only for models with 5h session pool)
                if model.get("has_5h", True):
                    m_pct = model.get("percentage", 100)
                    m_reset = model.get("reset", "готов")
                    m_frac = model.get("fraction", 1.0)
                    filled_5h = int(round(m_frac * 8))
                    bar_5h = "█" * filled_5h + "░" * (8 - filled_5h)

                    if m_pct <= 15:
                        c_5h = " color=#dc2626"
                    elif m_pct <= 35:
                        c_5h = " color=#d97706"
                    else:
                        c_5h = " color=#000000"

                    row_5h = f"   5ч:    [{bar_5h}] {m_pct:>3}%  ({m_reset})"
                    print(f"{row_5h} | font=Menlo size=12 trim=false{c_5h}")

                # 2. 7-day quota row
                w_pct = model.get("weekly_percentage", 100)
                w_reset = model.get("weekly_reset", "готов")
                w_frac = model.get("weekly_fraction", 1.0)

                # If in weekly cooldown/lockout, display as 0% with empty progress bar
                is_lockout = (w_pct == 0) or "кулдаун" in str(w_reset).lower() or (model.get("percentage") == 0 and "д" in str(model.get("reset", "")))
                if is_lockout:
                    w_pct = 0
                    w_frac = 0.0
                    m_reset = model.get("reset", "")
                    if "д" in str(m_reset) and "д" not in str(w_reset):
                        w_reset = m_reset
                    elif "кулдаун" in str(w_reset).lower():
                        w_reset = str(w_reset).replace("⚠️", "").replace("Кулдаун", "").strip()

                filled_w = int(round((w_frac if w_frac is not None else 1.0) * 8))
                bar_w = "█" * filled_w + "░" * (8 - filled_w)

                if w_pct <= 15:
                    c_w = " color=#dc2626"
                elif w_pct <= 35:
                    c_w = " color=#d97706"
                else:
                    c_w = " color=#000000"

                row_w = f"   7дн:   [{bar_w}] {w_pct:>3}%  ({w_reset})"
                print(f"{row_w} | font=Menlo size=12 trim=false{c_w}")

    # Separator before actions
    print("---")

    # Actions
    print("🔄 Обновить сейчас | refresh=true")
    print(f"➕ Добавить аккаунт... | bash=\"{py_bin}\" param1=\"{add_script}\" param2=\"--add\" terminal=true")
    print(f"🌐 Открыть Liquid Glass Dashboard | bash=\"{py_bin}\" param1=\"{add_script}\" param2=\"--dashboard\" terminal=false")


if __name__ == "__main__":
    main()
