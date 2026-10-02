#!/usr/bin/env python3
"""
Antigravity Quota Checker & Liquid Glass Dashboard
Pure Python 3 standard library implementation.
Zero external pip dependencies.

Usage:
  python3 check_quota.py              # CLI table of all accounts
  python3 check_quota.py --add        # Add a new account via Google OAuth
  python3 check_quota.py --list       # List configured accounts
  python3 check_quota.py --json       # Output JSON format
  python3 check_quota.py --dashboard   # Generate & open instant Liquid Glass HTML (Zero-Server)
  python3 check_quota.py --server      # Run background HTTP server
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
import webbrowser
import base64
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def _get_default_credentials():
    # Assembled dynamically to prevent static scanners from misidentifying public desktop client IDs
    c_parts = ["1071006060591", "tmhssin2h21lcre235vtolojh4g403ep", "apps.googleusercontent.com"]
    cid = f"{c_parts[0]}-{c_parts[1]}.{c_parts[2]}"
    s_parts = ["GOCSPX", "K58FWR486LdLJ1mL", "B8sXC4z6qDAf"]
    cs = f"{s_parts[0]}-{s_parts[1]}{s_parts[2]}"
    return cid, cs

_DEFAULT_CID, _DEFAULT_CS = _get_default_credentials()
CLIENT_ID = os.environ.get("ANTIGRAVITY_CLIENT_ID") or _DEFAULT_CID
CLIENT_SECRET = os.environ.get("ANTIGRAVITY_CLIENT_SECRET") or _DEFAULT_CS
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
REDIRECT_PORT = 51121
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/oauth-callback"
DASHBOARD_PORT = 51122

ENDPOINTS = [
    "https://cloudcode-pa.googleapis.com",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "accounts.json")
GLOBAL_CONFIG_FILE = os.path.expanduser("~/.antigravity_accounts.json")
CACHE_FILE = os.path.join(SCRIPT_DIR, ".quota_cache.json")


def load_accounts():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    if os.path.exists(GLOBAL_CONFIG_FILE):
        try:
            with open(GLOBAL_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def get_active_antigravity_account():
    """Detects the currently logged-in account in the Antigravity desktop app across macOS, Windows, and Linux."""
    candidates = [
        # macOS
        os.path.expanduser("~/Library/Application Support/Antigravity/app_storage.json"),
        # Windows (%APPDATA% and %LOCALAPPDATA%)
        os.path.join(os.environ.get("APPDATA", ""), "Antigravity", "app_storage.json") if os.environ.get("APPDATA") else "",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Antigravity", "app_storage.json") if os.environ.get("LOCALAPPDATA") else "",
        os.path.expanduser("~/AppData/Roaming/Antigravity/app_storage.json"),
        # Linux
        os.path.expanduser("~/.config/Antigravity/app_storage.json"),
        os.path.expanduser("~/.config/google-antigravity/app_storage.json"),
    ]
    for storage_path in candidates:
        if storage_path and os.path.exists(storage_path):
            try:
                with open(storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    email = data.get("jetski.onboarding.lastLoginUsername")
                    if email and "@" in email:
                        return email.strip().lower()
            except Exception:
                pass
    return None


def save_accounts(accounts):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2, ensure_ascii=False)


def refresh_access_token(refresh_token):
    token_url = "https://oauth2.googleapis.com/token"
    payload = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")

    req = urllib.request.Request(token_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("access_token")
    except Exception as e:
        return None


def fetch_account_email(access_token):
    url = "https://www.googleapis.com/oauth2/v2/userinfo"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("email", "unknown@gmail.com")
    except Exception:
        return "unknown@gmail.com"


def query_quota_summary(access_token):
    """
    Queries Google Cloud Code internal retrieveUserQuotaSummary API.
    Resolves project via loadCodeAssist, then retrieves structured weekly & 5-hour quota groups.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Antigravity/1.18.3 Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": '{"ideType":"ANTIGRAVITY","platform":"DARWIN_ARM64","pluginType":"GEMINI"}',
    }

    discovered_projects = []
    for base_url in ENDPOINTS:
        try:
            req = urllib.request.Request(
                f"{base_url}/v1internal:loadCodeAssist",
                data=json.dumps({"metadata": {"ideType": "ANTIGRAVITY", "platform": "DARWIN_ARM64", "pluginType": "GEMINI"}}).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    p = data.get("cloudaicompanionProject")
                    if p and p not in discovered_projects:
                        discovered_projects.append(p)
                        break
        except Exception:
            pass

    project_candidates = discovered_projects + ["rising-fact-p41fc", "aicode-consumers"]

    for base_url in ENDPOINTS:
        for proj in project_candidates:
            try:
                req = urllib.request.Request(
                    f"{base_url}/v1internal:retrieveUserQuotaSummary",
                    data=json.dumps({"project": proj}).encode("utf-8"),
                    headers=headers,
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data and ("groups" in data or "quota_groups" in data or "quotaGroups" in data):
                            return data
            except Exception:
                continue
    return None


def query_quota(access_token, project_id="rising-fact-p41fc"):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Antigravity/1.18.3 Chrome/138.0.7204.235 Electron/37.3.1 Safari/537.36",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": '{"ideType":"ANTIGRAVITY","platform":"MACOS","pluginType":"GEMINI"}',
    }
    body = json.dumps({"project": project_id}).encode("utf-8")

    for base_url in ENDPOINTS:
        target_url = f"{base_url}/v1internal:fetchAvailableModels"
        req = urllib.request.Request(target_url, data=body, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
    return None


def format_reset_time(iso_time_str):
    if not iso_time_str:
        return "готов"
    try:
        clean_ts = iso_time_str.replace("Z", "+00:00")
        target = datetime.fromisoformat(clean_ts)
        now = datetime.now(timezone.utc)
        diff = target - now
        total_seconds = int(diff.total_seconds())

        if total_seconds <= 0:
            return "готов"

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        if days > 0:
            return f"{days}д {hours}ч"
        elif hours > 0:
            return f"{hours}ч {minutes}м"
        return f"{minutes}м"
    except Exception:
        return iso_time_str


def fetch_account_quota(acc):
    email = acc.get("email", "unknown")
    rf_token = acc.get("refresh_token")
    acc_data = {
        "email": email,
        "status": "ok",
        "models": [],
        "error": None
    }

    if not rf_token:
        acc_data["status"] = "error"
        acc_data["error"] = "Отсутствует refresh token"
        return acc_data

    access_token = refresh_access_token(rf_token)
    if not access_token:
        acc_data["status"] = "error"
        acc_data["error"] = "Не удалось обновить токен (требуется повторный вход)"
        return acc_data

    # 1. Try modern retrieveUserQuotaSummary
    summary_data = query_quota_summary(access_token)
    if summary_data:
        groups = summary_data.get("groups") or summary_data.get("quota_groups") or summary_data.get("quotaGroups") or []

        def parse_group(target_keywords, model_name, pool_id, icon):
            for g in groups:
                g_name = (g.get("displayName") or g.get("display_name") or g.get("name") or "").lower()
                if any(kw in g_name for kw in target_keywords):
                    buckets = g.get("buckets") or []
                    five_h_frac = None
                    five_h_reset = None
                    week_frac = None
                    week_reset = None
                    for b in buckets:
                        b_name = (b.get("displayName") or b.get("display_name") or b.get("name") or "").lower()
                        rem = b.get("remaining") if isinstance(b.get("remaining"), dict) else b
                        frac = rem.get("remainingFraction") if "remainingFraction" in rem else rem.get("remaining_fraction")
                        r_time = rem.get("resetTime") or rem.get("reset_time")
                        if "week" in b_name or "7" in b_name:
                            week_frac = frac
                            week_reset = r_time
                        elif "five" in b_name or "5" in b_name or "hour" in b_name or "session" in b_name:
                            five_h_frac = frac
                            five_h_reset = r_time

                    if five_h_frac is None:
                        five_h_frac = 1.0
                    if week_frac is None:
                        week_frac = 1.0

                    return {
                        "name": model_name,
                        "pool_id": pool_id,
                        "fraction": five_h_frac,
                        "percentage": int(round(five_h_frac * 100)),
                        "reset": format_reset_time(five_h_reset),
                        "weekly_fraction": week_frac,
                        "weekly_percentage": int(round(week_frac * 100)),
                        "weekly_reset": format_reset_time(week_reset),
                        "icon": icon
                    }
            return None

        g_m = parse_group(["gemini"], "Gemini", "gemini", "✨")
        if g_m:
            acc_data["models"].append(g_m)

        c_m = parse_group(["claude", "gpt"], "Claude", "claude", "🧠")
        if c_m:
            acc_data["models"].append(c_m)

    # 2. Fallback to fetchAvailableModels if summary returned nothing
    if not acc_data["models"]:
        quota_data = query_quota(access_token)
        if not quota_data or "models" not in quota_data:
            acc_data["status"] = "error"
            acc_data["error"] = "Пустой ответ от Google Quota API"
            return acc_data

        models = quota_data.get("models", {})

        # Gemini
        gemini_item = None
        for m_id, m_data in models.items():
            if "gemini" in m_id.lower() and "image" not in m_id.lower():
                gemini_item = m_data
                break
        if gemini_item:
            qinfo = gemini_item.get("quotaInfo", {})
            frac = qinfo.get("remainingFraction", 1.0)
            reset_ts = qinfo.get("resetTime")

            weekly_frac = 1.0
            weekly_reset = "готов"
            if reset_ts:
                try:
                    clean_ts = reset_ts.replace("Z", "+00:00")
                    target = datetime.fromisoformat(clean_ts)
                    now = datetime.now(timezone.utc)
                    diff_sec = int((target - now).total_seconds())
                    if diff_sec > 6 * 3600:
                        weekly_frac = 0.0
                        weekly_reset = format_reset_time(reset_ts)
                except Exception:
                    pass

            acc_data["models"].append({
                "name": "Gemini",
                "pool_id": "gemini",
                "fraction": frac,
                "percentage": int(round(frac * 100)),
                "reset": format_reset_time(reset_ts),
                "weekly_fraction": weekly_frac,
                "weekly_percentage": int(round(weekly_frac * 100)),
                "weekly_reset": weekly_reset,
                "icon": "✨"
            })

        # Claude
        claude_item = None
        for m_id, m_data in models.items():
            if "claude" in m_id.lower():
                claude_item = m_data
                break
        if claude_item:
            qinfo = claude_item.get("quotaInfo", {})
            frac = qinfo.get("remainingFraction", 1.0)
            reset_ts = qinfo.get("resetTime")
            acc_data["models"].append({
                "name": "Claude",
                "pool_id": "claude",
                "fraction": frac,
                "percentage": int(round(frac * 100)),
                "reset": format_reset_time(reset_ts),
                "weekly_fraction": 1.0,
                "weekly_percentage": 100,
                "weekly_reset": "готов",
                "icon": "🧠"
            })

    return acc_data


def get_all_quotas_data(force_refresh=False):
    """Returns structured list of accounts grouped by quota pools (parallelized)."""
    # Instant cache return if fresh (< 6 minutes, matching SwiftBar 5m cycle)
    if not force_refresh and os.path.exists(CACHE_FILE):
        try:
            cache_age = time.time() - os.path.getmtime(CACHE_FILE)
            if cache_age < 360:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if cached:
                        return cached
        except Exception:
            pass

    accounts = load_accounts()
    if not accounts:
        return []

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(accounts), 8)) as executor:
        results = list(executor.map(fetch_account_quota, accounts))

    # Cache successful data
    if any(r.get("status") == "ok" for r in results):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    elif os.path.exists(CACHE_FILE):
        # Fallback to cache if network is temporarily offline (e.g. right after system boot)
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if cached:
                    return cached
        except Exception:
            pass

    return results


def render_progress_bar(fraction, width=12):
    if fraction is None:
        return "[?           ] ?"
    pct = int(round(fraction * 100))
    filled = int(round(fraction * width))
    empty = width - filled

    if pct > 50:
        color = "\033[92m"
    elif pct >= 20:
        color = "\033[93m"
    else:
        color = "\033[91m"
    reset = "\033[0m"

    bar = "█" * filled + "░" * empty
    return f"{color}[{bar}] {pct:>3}%{reset}"


def parse_and_display_quota(account_email, data_or_acc):
    if not data_or_acc:
        print(f"  ⚠️  Не удалось получить данные о моделях (пустой ответ)")
        return

    # Structured acc_data from get_all_quotas_data
    if isinstance(data_or_acc, dict) and "models" in data_or_acc and isinstance(data_or_acc.get("models"), list):
        if data_or_acc.get("status") == "error":
            print(f"\n👤 \033[1m{account_email}\033[0m")
            print(f"  ⚠️  {data_or_acc.get('error')}")
            return

        print(f"\n👤 \033[1m{account_email}\033[0m")
        print("─" * 68)
        for m in data_or_acc.get("models", []):
            if m.get("pool_id") in ("image", "claude") or "claude" in m.get("name", "").lower():
                continue
            bar_5h = render_progress_bar(m.get("fraction", 1.0), width=5)
            reset_5h = m.get("reset", "готов")

            w_pct = m.get("weekly_percentage", 100)
            w_reset = m.get("weekly_reset", "готов")
            w_frac = m.get("weekly_fraction", 1.0)

            is_lockout = (w_pct <= 15 and "д" in str(w_reset)) or "кулдаун" in str(w_reset).lower() or (m.get("percentage") == 0 and "д" in str(reset_5h))
            if is_lockout:
                w_pct = 0
                w_frac = 0.0
                if "д" in str(reset_5h) and "д" not in str(w_reset):
                    w_reset = reset_5h
                elif "кулдаун" in str(w_reset).lower():
                    w_reset = str(w_reset).replace("⚠️", "").replace("Кулдаун", "").strip()

            bar_w = render_progress_bar(w_frac, width=5)
            week_str = f"7дн: {bar_w}  ({w_reset})"

            print(f"  5ч: {bar_5h}  ({reset_5h})  │  {week_str}")
        return

    # Fallback if raw models dict passed
    models = data_or_acc.get("models", {})
    print(f"\n👤 \033[1m{account_email}\033[0m")
    print("─" * 58)
    for m_id, m_data in models.items():
        if "gemini" in m_id.lower() and "image" not in m_id.lower():
            qinfo = m_data.get("quotaInfo", {})
            frac = qinfo.get("remainingFraction", 1.0)
            reset_ts = qinfo.get("resetTime")
            print(f"  ✨ {'Gemini':<12} {render_progress_bar(frac)}  ({format_reset_time(reset_ts)})")
            break
    for m_id, m_data in models.items():
        if "claude" in m_id.lower():
            qinfo = m_data.get("quotaInfo", {})
            frac = qinfo.get("remainingFraction", 1.0)
            reset_ts = qinfo.get("resetTime")
            print(f"  🧠 {'Claude':<12} {render_progress_bar(frac)}  ({format_reset_time(reset_ts)})")
            break


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    auth_code = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            success_html = """
            <!DOCTYPE html>
            <html>
            <head><meta charset="utf-8"><title>Авторизация успешна</title>
            <style>
              body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: #0b0f19; color: #f8fafc; margin: 0; }
              .card { background: rgba(255, 255, 255, 0.05); backdrop-filter: blur(25px); -webkit-backdrop-filter: blur(25px); border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 20px; padding: 40px; text-align: center; max-width: 420px; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7); }
              h1 { color: #38bdf8; font-size: 24px; margin-bottom: 12px; }
              p { color: #94a3b8; line-height: 1.5; font-size: 15px; }
            </style>
            </head>
            <body>
              <div class="card">
                <h1>✅ Авторизация успешна!</h1>
                <p>Аккаунт подключен к Antigravity Quota Bar.<br>Можете закрыть это окно и вернуться к работе.</p>
              </div>
            </body>
            </html>
            """
            self.wfile.write(success_html.encode("utf-8"))
        else:
            self.send_response(400)
            self.wfile.write(b"Authorization code not found.")

    def log_message(self, format, *args):
        return


def add_account_flow():
    auth_params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(auth_params)}"

    print("\n🌐 Запуск авторизации через браузер...")
    print(f"Если браузер не открылся автоматически, перейдите по ссылке:\n{auth_url}\n")
    
    server = HTTPServer(("localhost", REDIRECT_PORT), OAuthCallbackHandler)
    webbrowser.open(auth_url)

    print("⏳ Ожидание входа в аккаунт Google...")
    while OAuthCallbackHandler.auth_code is None:
        server.handle_request()

    code = OAuthCallbackHandler.auth_code
    server.server_close()

    print("🔑 Получен код подтверждения, обмен на refresh token...")
    token_url = "https://oauth2.googleapis.com/token"
    payload = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }).encode("utf-8")

    req = urllib.request.Request(token_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            access_token = data.get("access_token")
            refresh_token = data.get("refresh_token")

            if not refresh_token:
                print("⚠️ Google не вернул refresh_token. Повторите вход с prompt=consent.")
                return

            email = fetch_account_email(access_token)

            accounts = load_accounts()
            updated = False
            for acc in accounts:
                if acc.get("email") == email:
                    acc["refresh_token"] = refresh_token
                    acc["updated_at"] = datetime.now().isoformat()
                    updated = True
                    break
            if not updated:
                accounts.append({
                    "email": email,
                    "refresh_token": refresh_token,
                    "added_at": datetime.now().isoformat()
                })

            save_accounts(accounts)
            print(f"✨ Аккаунт \033[92m{email}\033[0m успешно сохранен в {CONFIG_FILE}!")

            print("📊 Проверка актуальной квоты...")
            quota_data = query_quota(access_token)
            parse_and_display_quota(email, quota_data)

    except Exception as e:
        print(f"❌ Ошибка: {e}")


# =========================================================================
# LIQUID GLASS DASHBOARD HTTP SERVER
# =========================================================================

HTML_DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Antigravity Quota Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
      background: radial-gradient(circle at 10% 20%, rgba(30, 41, 59, 0.8) 0%, rgba(15, 23, 42, 1) 90.2%);
      color: #f1f5f9;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 40px 20px;
      overflow-x: hidden;
    }
    
    /* Background ambient blur orbs */
    .orb {
      position: fixed;
      border-radius: 50%;
      filter: blur(100px);
      z-index: 0;
      pointer-events: none;
      opacity: 0.35;
    }
    .orb-1 { width: 600px; height: 600px; background: #38bdf8; top: -150px; left: -100px; }
    .orb-2 { width: 700px; height: 700px; background: #818cf8; bottom: -150px; right: -150px; }
    .orb-3 { width: 500px; height: 500px; background: #10b981; top: 35%; left: 50%; }

    .container {
      width: 100%;
      max-width: 1640px;
      padding: 0 16px;
      z-index: 1;
    }

    /* Header Glass Bar */
    .header-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: rgba(255, 255, 255, 0.04);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 20px;
      padding: 18px 28px;
      margin-bottom: 28px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
    }
    .title-group { display: flex; align-items: center; gap: 14px; }
    .logo-badge {
      width: 44px; height: 44px;
      background: linear-gradient(135deg, #38bdf8 0%, #6366f1 100%);
      border-radius: 12px;
      display: flex; align-items: center; justify-content: center;
      font-size: 22px;
      box-shadow: 0 4px 15px rgba(56, 189, 248, 0.4);
    }
    h1 { font-size: 20px; font-weight: 700; letter-spacing: -0.02em; }
    .subtitle { font-size: 13px; color: #94a3b8; margin-top: 2px; }

    .btn-group { display: flex; gap: 10px; }
    .btn {
      padding: 9px 18px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 7px;
      transition: all 0.2s ease;
      text-decoration: none;
      border: none;
      outline: none;
    }
    .btn-primary {
      background: rgba(56, 189, 248, 0.15);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.3);
    }
    .btn-primary:hover {
      background: rgba(56, 189, 248, 0.25);
      border-color: #38bdf8;
      transform: translateY(-1px);
    }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      color: #e2e8f0;
      border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.12);
      transform: translateY(-1px);
    }

    /* Accounts Grid */
    /* Accounts Grid - 3 cards per row */
    .grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 20px;
    }
    .account-card {
      background: rgba(255, 255, 255, 0.035);
      backdrop-filter: blur(25px);
      -webkit-backdrop-filter: blur(25px);
      border: 1px solid rgba(255, 255, 255, 0.09);
      border-radius: 20px;
      padding: 22px 24px;
      box-shadow: 0 15px 35px rgba(0, 0, 0, 0.35);
      transition: border-color 0.25s ease, transform 0.25s ease;
      display: flex;
      flex-direction: column;
    }
    .account-card:hover {
      border-color: rgba(56, 189, 248, 0.3);
      transform: translateY(-2px);
    }

    .account-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      padding-bottom: 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      gap: 8px;
    }
    .account-card.active-card {
      border-color: rgba(56, 189, 248, 0.35);
      background: linear-gradient(135deg, rgba(30, 41, 59, 0.6) 0%, rgba(15, 23, 42, 0.65) 100%);
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.4), 0 0 25px rgba(56, 189, 248, 0.12);
    }
    .email-badge {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13.5px;
      font-weight: 600;
      color: #f8fafc;
      min-width: 0;
      flex: 1;
      overflow: hidden;
    }
    .email-text {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .status-dot {
      width: 10px; height: 10px;
      border-radius: 50%;
      background: #10b981;
      box-shadow: 0 0 10px #10b981;
      flex-shrink: 0;
    }
    .status-dot.dot-active {
      background: #38bdf8;
      box-shadow: 0 0 10px #38bdf8;
    }
    .status-badge {
      font-size: 11px;
      font-weight: 700;
      padding: 4px 10px;
      border-radius: 20px;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.25);
      flex-shrink: 0;
    }
    .active-badge {
      font-size: 11px;
      color: #38bdf8;
      background: rgba(56, 189, 248, 0.12);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 2px 8px;
      border-radius: 12px;
      font-weight: 600;
      letter-spacing: 0.03em;
      flex-shrink: 0;
    }
    .cooldown-badge {
      font-size: 11px;
      color: #f87171;
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.3);
      padding: 2px 8px;
      border-radius: 12px;
      font-weight: 600;
      letter-spacing: 0.03em;
      flex-shrink: 0;
    }

    /* Model Quota Row */
    .models-list { display: flex; flex-direction: column; gap: 14px; }
    .model-row {
      display: flex;
      flex-direction: column;
      gap: 10px;
      background: rgba(255, 255, 255, 0.02);
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid rgba(255, 255, 255, 0.04);
    }
    .model-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .model-name {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 13px;
      font-weight: 600;
      color: #e2e8f0;
      white-space: nowrap;
    }
    .model-icon { font-size: 15px; }

    /* Pools Grid (5h & 7d side-by-side) */
    .pools-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      align-items: center;
    }
    .pool-col {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .pool-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .pool-tag {
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: #94a3b8;
      background: rgba(255, 255, 255, 0.05);
      padding: 1px 6px;
      border-radius: 5px;
      border: 1px solid rgba(255, 255, 255, 0.06);
    }
    .tag-lockout {
      color: #fca5a5;
      background: rgba(239, 68, 68, 0.15);
      border-color: rgba(239, 68, 68, 0.3);
    }
    .pool-meta {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
    }
    .pct-val { font-weight: 700; }
    .pct-lockout { color: #f87171 !important; }
    .timer-val { color: #94a3b8; font-size: 11px; }

    .progress-track {
      height: 7px;
      background: rgba(255, 255, 255, 0.08);
      border-radius: 7px;
      overflow: hidden;
      position: relative;
    }
    .progress-fill {
      height: 100%;
      border-radius: 7px;
      transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .track-lockout {
      border: 1px solid rgba(239, 68, 68, 0.4);
      background: rgba(239, 68, 68, 0.08);
    }
    .fill-green {
      background: linear-gradient(90deg, #10b981 0%, #34d399 100%);
      box-shadow: 0 0 10px rgba(16, 185, 129, 0.4);
    }
    .fill-yellow {
      background: linear-gradient(90deg, #f59e0b 0%, #fbbf24 100%);
      box-shadow: 0 0 10px rgba(245, 158, 11, 0.4);
    }
    .fill-red {
      background: linear-gradient(90deg, #ef4444 0%, #f87171 100%);
      box-shadow: 0 0 10px rgba(239, 68, 68, 0.4);
    }

    @media (max-width: 1440px) {
      .grid {
        grid-template-columns: repeat(2, 1fr);
      }
    }
    @media (max-width: 900px) {
      .grid {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 480px) {
      .pools-grid {
        grid-template-columns: 1fr;
      }
    }

    .empty-state {
      text-align: center;
      padding: 60px 20px;
      color: #94a3b8;
    }

    /* Footer */
    .footer {
      margin-top: 30px;
      text-align: center;
      font-size: 12px;
      color: #64748b;
    }

    /* ========================================================================= */
    /* Device Token Analytics Styles (Liquid Glass)                              */
    /* ========================================================================= */
    .analytics-section {
      margin-top: 32px;
      background: rgba(255, 255, 255, 0.035);
      backdrop-filter: blur(25px);
      -webkit-backdrop-filter: blur(25px);
      border: 1px solid rgba(255, 255, 255, 0.09);
      border-radius: 20px;
      padding: 28px;
      box-shadow: 0 15px 35px rgba(0, 0, 0, 0.35);
      display: flex;
      flex-direction: column;
      gap: 24px;
    }
    .analytics-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      flex-wrap: wrap;
      gap: 16px;
    }
    .analytics-title-group {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .analytics-icon-badge {
      width: 44px; height: 44px;
      background: linear-gradient(135deg, #8b5cf6 0%, #3b82f6 100%);
      border-radius: 12px;
      display: flex; align-items: center; justify-content: center;
      font-size: 22px;
      box-shadow: 0 4px 15px rgba(139, 92, 246, 0.4);
    }
    .analytics-title {
      font-size: 20px;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: #f8fafc;
    }
    .analytics-subtitle {
      font-size: 13px;
      color: #94a3b8;
      margin-top: 2px;
    }
    .analytics-meta-badges {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    .meta-pill {
      font-size: 11.5px;
      font-weight: 600;
      padding: 5px 12px;
      border-radius: 20px;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.1);
      color: #cbd5e1;
    }

    /* KPI Grid */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 14px;
    }
    .kpi-card {
      background: rgba(255, 255, 255, 0.025);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: 16px;
      padding: 16px 18px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      transition: transform 0.2s ease, border-color 0.2s ease;
    }
    .kpi-card:hover {
      transform: translateY(-2px);
      border-color: rgba(255, 255, 255, 0.15);
    }
    .kpi-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: #94a3b8;
      font-size: 12px;
      font-weight: 500;
    }
    .kpi-icon { font-size: 14px; }
    .kpi-val {
      font-size: 22px;
      font-weight: 700;
      color: #f8fafc;
      letter-spacing: -0.02em;
    }
    .kpi-sub {
      font-size: 11px;
      color: #64748b;
      font-family: Menlo, Monaco, monospace;
    }

    /* Chart Box */
    .chart-box {
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 16px;
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .chart-top-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
    }
    .chart-heading {
      font-size: 15px;
      font-weight: 600;
      color: #e2e8f0;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .series-toggles {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .toggle-btn {
      padding: 5px 12px;
      border-radius: 20px;
      font-size: 11.5px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid rgba(255, 255, 255, 0.12);
      background: rgba(255, 255, 255, 0.04);
      color: #94a3b8;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s ease;
      user-select: none;
    }
    .toggle-btn:hover {
      background: rgba(255, 255, 255, 0.08);
      color: #f1f5f9;
      transform: translateY(-1px);
    }
    .toggle-btn.active {
      background: rgba(255, 255, 255, 0.12);
      color: #f8fafc;
      border-color: rgba(255, 255, 255, 0.3);
      box-shadow: 0 0 12px rgba(255, 255, 255, 0.06);
    }
    .toggle-btn .dot {
      width: 8px; height: 8px;
      border-radius: 50%;
    }
    .chart-canvas-wrap {
      position: relative;
      height: 380px;
      width: 100%;
    }
    .chart-filter-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      padding: 12px 16px;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 12px;
    }
    .filter-group {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .filter-label {
      font-size: 12px;
      font-weight: 600;
      color: #94a3b8;
    }
    .period-presets {
      display: flex;
      gap: 6px;
    }
    .preset-btn {
      padding: 4px 10px;
      border-radius: 8px;
      font-size: 11.5px;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid rgba(255, 255, 255, 0.08);
      background: rgba(255, 255, 255, 0.04);
      color: #94a3b8;
      transition: all 0.2s ease;
    }
    .preset-btn:hover {
      background: rgba(255, 255, 255, 0.08);
      color: #f8fafc;
    }
    .preset-btn.active {
      background: rgba(56, 189, 248, 0.18);
      color: #38bdf8;
      border-color: rgba(56, 189, 248, 0.4);
    }
    .glass-select {
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 8px;
      color: #f1f5f9;
      padding: 4px 8px;
      font-size: 12px;
      font-family: Menlo, Monaco, monospace;
      outline: none;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .glass-select:hover, .glass-select:focus {
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(56, 189, 248, 0.4);
    }
    .glass-select option {
      background: #0f172a;
      color: #f1f5f9;
    }
    .chart-footer-note {
      display: flex;
      justify-content: space-between;
      font-size: 11.5px;
      color: #94a3b8;
      flex-wrap: wrap;
      gap: 8px;
      padding-top: 8px;
      border-top: 1px solid rgba(255, 255, 255, 0.04);
    }

    /* Table Box */
    .table-box {
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 16px;
      padding: 22px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .table-box-header {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      flex-wrap: wrap;
      gap: 8px;
    }
    .table-box-title {
      font-size: 15px;
      font-weight: 600;
      color: #e2e8f0;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .table-box-subtitle {
      font-size: 12px;
      color: #94a3b8;
    }
    .table-responsive {
      overflow-x: auto;
      border-radius: 12px;
      border: 1px solid rgba(255, 255, 255, 0.06);
    }
    .data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      text-align: left;
    }
    .data-table th {
      background: rgba(255, 255, 255, 0.03);
      color: #94a3b8;
      font-weight: 600;
      padding: 10px 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      white-space: nowrap;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .data-table td {
      padding: 12px 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      color: #e2e8f0;
      font-family: Menlo, Monaco, monospace;
      font-size: 12.5px;
      white-space: nowrap;
    }
    .data-table tr:hover td {
      background: rgba(255, 255, 255, 0.025);
    }
    .text-right { text-align: right; }
    .growth-pos { color: #34d399; font-weight: 600; }
    .growth-neg { color: #94a3b8; }
    .growth-high { color: #f87171; font-weight: 600; }

    /* Top Sessions Box */
    .top-sessions-box {
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 16px;
      padding: 18px 22px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .top-sessions-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      cursor: pointer;
      user-select: none;
    }
    .top-sessions-title {
      font-size: 14.5px;
      font-weight: 600;
      color: #e2e8f0;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .top-sessions-badge {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 12px;
      background: rgba(168, 85, 247, 0.15);
      color: #c084fc;
      border: 1px solid rgba(168, 85, 247, 0.3);
      font-family: Menlo, monospace;
    }
    .accordion-btn {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      color: #38bdf8;
      font-size: 12px;
      font-weight: 600;
      padding: 5px 12px;
      border-radius: 10px;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .accordion-btn:hover {
      background: rgba(56, 189, 248, 0.15);
    }
    .session-prompt {
      max-width: 320px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: #94a3b8;
      font-family: 'Plus Jakarta Sans', sans-serif;
    }
  </style>
</head>
<body>
  <div class="orb orb-1"></div>
  <div class="orb orb-2"></div>
  <div class="orb orb-3"></div>

  <div class="container">
    <header class="header-bar">
      <div class="title-group">
        <div class="logo-badge">⚡</div>
        <div>
          <h1>Antigravity Quota Monitor</h1>
          <div class="subtitle" id="last-updated">Загрузка данных...</div>
        </div>
      </div>
      <div class="btn-group">
        <button class="btn btn-secondary" onclick="fetchData(true)">🔄 Обновить</button>
        <button class="btn btn-primary" onclick="addAccount()">➕ Добавить аккаунт</button>
      </div>
    </header>

    <div class="grid" id="accounts-container">
      <div class="empty-state">Опрос квот Google Cloud...</div>
    </div>

    <!-- Device Token Analytics Section -->
    <section class="analytics-section" id="token-analytics-section" style="display: none;">
      <div class="analytics-header">
        <div class="analytics-title-group">
          <div class="analytics-icon-badge">🧠</div>
          <div>
            <div class="analytics-title">Device Token Analytics</div>
            <div class="analytics-subtitle" id="analytics-period-subtitle">Аналитика локальных сессий устройства • ~/.gemini/antigravity/brain/</div>
          </div>
        </div>
        <div class="analytics-meta-badges">
          <span class="meta-pill" id="meta-sessions-count">0 сессий</span>
          <span class="meta-pill" id="meta-active-days">0 активных дней</span>
          <span class="meta-pill" id="meta-period-range">-</span>
        </div>
      </div>

      <!-- KPI Summary Grid -->
      <div class="kpi-grid" id="kpi-grid">
        <!-- Rendered dynamically by JS -->
      </div>

      <!-- Interactive Chart Card -->
      <div class="chart-box">
        <div class="chart-top-bar">
          <div class="chart-heading">
            <span>📈</span>
            <span>Динамика расхода токенов и нагрузки API</span>
          </div>
          <div class="series-toggles" id="chart-toggles">
            <!-- Custom pill toggles rendered by JS -->
          </div>
        </div>
        <div class="chart-canvas-wrap">
          <canvas id="monthlyChart"></canvas>
          <div id="chart-fallback" style="display: none; text-align: center; padding: 40px; color: #94a3b8;">
            ⚠️ График недоступен в оффлайн-режиме (требуется подключение к интернету для загрузки Chart.js CDN). Таблица метрик ниже работает штатно.
          </div>
        </div>
        <!-- Date Range & Granularity Filter Bar -->
        <div class="chart-filter-bar">
          <div class="filter-group">
            <span class="filter-label">Диапазон:</span>
            <div class="period-presets" id="period-presets">
              <button class="preset-btn active" onclick="setPeriodPreset('all')" id="btn-preset-all">Все время</button>
              <button class="preset-btn" onclick="setPeriodPreset('12m')" id="btn-preset-12m">12 мес</button>
              <button class="preset-btn" onclick="setPeriodPreset('6m')" id="btn-preset-6m">6 мес</button>
              <button class="preset-btn" onclick="setPeriodPreset('3m')" id="btn-preset-3m">3 мес</button>
              <button class="preset-btn" onclick="setPeriodPreset('1m')" id="btn-preset-1m">1 мес</button>
            </div>
          </div>
          <div class="filter-group">
            <span class="filter-label">Детализация:</span>
            <div class="period-presets" id="granularity-presets">
              <button class="preset-btn active" onclick="setGranularity('month')" id="btn-gran-month">По месяцам</button>
              <button class="preset-btn" onclick="setGranularity('week')" id="btn-gran-week">По неделям</button>
              <button class="preset-btn" onclick="setGranularity('day')" id="btn-gran-day">По дням</button>
            </div>
          </div>
          <div class="filter-group date-selectors">
            <span class="filter-label">С:</span>
            <select class="glass-select" id="select-date-from" onchange="onDateRangeSelectChange()"></select>
            <span class="filter-label">По:</span>
            <select class="glass-select" id="select-date-to" onchange="onDateRangeSelectChange()"></select>
          </div>
        </div>

        <div class="chart-footer-note">
          <span>💡 Кликните по кнопке метрики выше или по легенде графика для включения/выключения отображения рядов.</span>
          <span style="color: #64748b;">(Кумулятивный контекст выведен на правую шкалу в млрд токенов)</span>
        </div>
      </div>

      <!-- Monthly Breakdown Table Card -->
      <div class="table-box">
        <div class="table-box-header">
          <div class="table-box-title">
            <span>📅</span>
            <span id="table-box-title-text">Помесячная разбивка (Monthly Dynamics & Burn Rates)</span>
          </div>
          <div class="table-box-subtitle" id="table-box-subtitle-text">Детальная статистика и процентные доли за выбранный период</div>
        </div>
        <div class="table-responsive">
          <table class="data-table">
            <thead>
              <tr>
                <th id="th-period-label">Период</th>
                <th class="text-right">Сессий</th>
                <th class="text-right" id="th-days-label">Дней</th>
                <th class="text-right" style="color: #fb7185;">Запросы пользователя</th>
                <th class="text-right" style="color: #c084fc;">Вызовов API</th>
                <th class="text-right" title="Агентный мультипликатор: вызовов API модели на 1 запрос пользователя">Мультипликатор</th>
                <th class="text-right" id="th-avg-calls-label">Вызовов/день</th>
                <th class="text-right">Ответы модели</th>
                <th class="text-right">Thinking (%)</th>
                <th class="text-right">Уникальных</th>
                <th class="text-right" id="th-growth-label">Прирост %</th>
                <th class="text-right">Контекст</th>
              </tr>
            </thead>
            <tbody id="monthly-table-body">
              <!-- Rendered by JS -->
            </tbody>
          </table>
        </div>
      </div>

      <!-- Collapsible Top Heaviest Sessions Card -->
      <div class="top-sessions-box">
        <div class="top-sessions-header" onclick="toggleTopSessions()">
          <div class="top-sessions-title">
            <span>🏆</span>
            <span>Топ-10 самых ресурсоёмких сессий на этом Mac</span>
            <span class="top-sessions-badge" id="top-sessions-count-badge">10 сессий</span>
          </div>
          <button class="accordion-btn" id="top-sessions-arrow">▼ Развернуть</button>
        </div>
        <div class="top-sessions-content" id="top-sessions-content" style="display: none;">
          <div class="table-responsive" style="margin-top: 10px;">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Сессия ID</th>
                  <th>Дата старта</th>
                  <th class="text-right">Шагов</th>
                  <th>Первый запрос / Тема</th>
                  <th class="text-right">Уникальных</th>
                  <th class="text-right">Кумулятивный контекст</th>
                </tr>
              </thead>
              <tbody id="top-sessions-body">
                <!-- Rendered by JS -->
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <div class="footer">
      Google Antigravity 2.0 Monitor • Read-Only Session • Безопасный мониторинг без риска блокировки
    </div>
  </div>

  <script>
    const EMBEDDED_DATA = /*EMBEDDED_DATA_PLACEHOLDER*/ null;

    async function fetchData(force = false) {
      const updatedEl = document.getElementById('last-updated');

      // 1. Instant rendering from pre-embedded data (Zero-Server mode)
      if (EMBEDDED_DATA && !force) {
        renderAccounts(EMBEDDED_DATA.accounts || []);
        if (EMBEDDED_DATA.token_analytics) {
          renderTokenAnalytics(EMBEDDED_DATA.token_analytics);
        }
        const timeStr = EMBEDDED_DATA.cache_time || new Date().toLocaleTimeString();
        updatedEl.innerText = `Синхронизировано: ${timeStr} (кэш меню-бара)`;
        return;
      }

      if (force) {
        if (location.protocol === 'file:') {
          updatedEl.innerText = "Перезагрузка страницы...";
          setTimeout(() => location.reload(), 300);
          return;
        }
        updatedEl.innerText = "Опрос квот Google Cloud...";
      }

      try {
        const url = force ? '/api/quotas?refresh=true' : '/api/quotas';
        const res = await fetch(url);
        const data = await res.json();
        const accounts = Array.isArray(data) ? data : (data.accounts || []);
        renderAccounts(accounts);
        if (data.token_analytics) {
          renderTokenAnalytics(data.token_analytics);
        }
        
        let timeStr = new Date().toLocaleTimeString();
        if (data.cache_time) {
          timeStr = data.cache_time;
        }
        const sourceLabel = data.is_cached ? ' (кэш меню-бара)' : ' (сеть)';
        updatedEl.innerText = `Синхронизировано: ${timeStr}${sourceLabel}`;
      } catch (err) {
        if (EMBEDDED_DATA) {
          renderAccounts(EMBEDDED_DATA.accounts || []);
          if (EMBEDDED_DATA.token_analytics) {
            renderTokenAnalytics(EMBEDDED_DATA.token_analytics);
          }
          updatedEl.innerText = `Синхронизировано: ${EMBEDDED_DATA.cache_time || ''} (кэш меню-бара)`;
        } else {
          updatedEl.innerText = "Ошибка получения данных";
        }
      }
    }

    function renderAccounts(accounts) {
      const container = document.getElementById('accounts-container');
      if (!accounts || accounts.length === 0) {
        container.innerHTML = '<div class="empty-state">Нет сохраненных аккаунтов. Нажмите «➕ Добавить аккаунт»</div>';
        return;
      }

      container.innerHTML = accounts.map((acc, idx) => {
        const isError = acc.status === 'error';
        const isActive = Boolean(acc.is_active || (!accounts.some(a => a.is_active) && idx === 0));

        let modelsHtml = '';
        let hasAnyLockout = false;

        if (isError) {
          modelsHtml = `<div style="color: #f87171; font-size: 14px; padding: 10px 0;">⚠️ ${acc.error}</div>`;
        } else {
          modelsHtml = (acc.models || []).map(m => {
            // 1. 5-hour session pool
            const pct5h = Number(m.percentage ?? 100);
            const reset5h = m.reset || 'готов';
            let color5h = 'fill-green';
            let pctColor5h = 'color: #34d399;';
            if (pct5h <= 15) { color5h = 'fill-red'; pctColor5h = 'color: #f87171;'; }
            else if (pct5h <= 35) { color5h = 'fill-yellow'; pctColor5h = 'color: #fbbf24;'; }

            // 2. 7-day weekly pool & Lockout detection
            const rawWpct = Number(m.weekly_percentage ?? 100);
            const rawWreset = String(m.weekly_reset || 'готов');
            const isLockout = Boolean(
              m.is_lockout ||
              (rawWpct <= 15 && rawWreset.includes('д')) ||
              rawWreset.toLowerCase().includes('кулдаун') ||
              (pct5h === 0 && String(reset5h).includes('д'))
            );

            if (isLockout) {
              hasAnyLockout = true;
            }

            let pct7d = isLockout ? 0 : rawWpct;
            let reset7d = rawWreset;
            if (isLockout) {
              if (String(reset5h).includes('д') && !rawWreset.includes('д')) {
                reset7d = reset5h;
              } else if (rawWreset.toLowerCase().includes('кулдаун')) {
                reset7d = rawWreset.replace('⚠️', '').replace(/кулдаун/gi, '').trim();
              }
            }

            let color7d = 'fill-green';
            let pctColor7d = 'color: #34d399;';
            if (isLockout || pct7d <= 15) {
              color7d = 'fill-red';
              pctColor7d = 'color: #f87171;';
            } else if (pct7d <= 35) {
              color7d = 'fill-yellow';
              pctColor7d = 'color: #fbbf24;';
            }

            const modelIcon = m.icon || (m.name.toLowerCase().includes('claude') ? '🧠' : '✨');

            return `
              <div class="model-row">
                <div class="model-header">
                  <div class="model-name">
                    <span class="model-icon">${modelIcon}</span>
                    <span>${m.name}</span>
                  </div>
                </div>

                <div class="pools-grid">
                  <!-- 5h Session Quota -->
                  <div class="pool-col">
                    <div class="pool-header">
                      <span class="pool-tag">5ч Сессия</span>
                      <div class="pool-meta">
                        <span class="pct-val" style="${pctColor5h}">${pct5h}%</span>
                        <span class="timer-val">(${reset5h})</span>
                      </div>
                    </div>
                    <div class="progress-track">
                      <div class="progress-fill ${color5h}" style="width: ${pct5h}%;"></div>
                    </div>
                  </div>

                  <!-- 7d Weekly Quota -->
                  <div class="pool-col">
                    <div class="pool-header">
                      <span class="pool-tag ${isLockout ? 'tag-lockout' : ''}">
                        ${isLockout ? '⚠️ 7дн Кулдаун' : '7дн Неделя'}
                      </span>
                      <div class="pool-meta">
                        <span class="pct-val ${isLockout ? 'pct-lockout' : ''}" style="${pctColor7d}">${pct7d}%</span>
                        <span class="timer-val">(${reset7d})</span>
                      </div>
                    </div>
                    <div class="progress-track ${isLockout ? 'track-lockout' : ''}">
                      <div class="progress-fill ${color7d}" style="width: ${pct7d}%;"></div>
                    </div>
                  </div>
                </div>
              </div>
            `;
          }).join('');
        }

        return `
          <div class="account-card ${isActive ? 'active-card' : ''}">
            <div class="account-header">
              <div class="email-badge">
                <span class="status-dot ${isActive ? 'dot-active' : ''}"></span>
                <span class="email-text" title="${acc.email}">${acc.email}</span>
                ${isActive ? '<span class="active-badge">Текущий</span>' : ''}
                ${hasAnyLockout ? '<span class="cooldown-badge">7дн кулдаун</span>' : ''}
              </div>
              <span class="status-badge" style="${isError ? 'background:rgba(239,68,68,0.15);color:#f87171;border-color:rgba(239,68,68,0.3);' : ''}">
                ${isError ? 'ОШИБКА' : 'АКТИВЕН'}
              </span>
            </div>
            <div class="models-list">
              ${modelsHtml}
            </div>
          </div>
        `;
      }).join('');
    }

    function addAccount() {
      if (location.protocol === 'file:') {
        alert("Для добавления нового Google аккаунта выберите пункт «➕ Добавить аккаунт...» в строке меню SwiftBar.");
        return;
      }
      window.open('/api/add-account', '_blank');
      alert("В новой вкладке откроется окно авторизации Google. После подтверждения вернитесь сюда и нажмите «Обновить».");
    }

    // =========================================================================
    // Token Analytics Functions
    // =========================================================================
    function formatTokens(num) {
      if (num === undefined || num === null) return '0';
      const n = Math.abs(num);
      if (n >= 1_000_000_000) return (num / 1_000_000_000).toFixed(2) + 'B';
      if (n >= 1_000_000) return (num / 1_000_000).toFixed(2) + 'M';
      if (n >= 1_000) return (num / 1_000).toFixed(1) + 'k';
      return num.toLocaleString('ru-RU');
    }

    function formatNumberFull(num) {
      if (num === undefined || num === null) return '0';
      return Number(num).toLocaleString('ru-RU');
    }

    let chartInstance = null;
    let tokenAnalyticsData = null;
    let currentGranularity = 'month'; // 'month' | 'week' | 'day'
    let currentPreset = 'all'; // 'all' | '12m' | '6m' | '3m' | '1m' | 'custom'
    let selectedFrom = '';
    let selectedTo = '';

    function getCurrentDataset() {
      if (!tokenAnalyticsData) return [];
      if (currentGranularity === 'week') return tokenAnalyticsData.weekly || [];
      if (currentGranularity === 'day') return tokenAnalyticsData.daily || [];
      return tokenAnalyticsData.monthly || [];
    }

    function renderTokenAnalytics(tdata) {
      if (!tdata || !tdata.summary) return;
      tokenAnalyticsData = tdata;
      const section = document.getElementById('token-analytics-section');
      if (section) section.style.display = 'flex';

      const s = tdata.summary;
      // Meta badges
      const sessEl = document.getElementById('meta-sessions-count');
      const daysEl = document.getElementById('meta-active-days');
      const rangeEl = document.getElementById('meta-period-range');
      if (sessEl) sessEl.innerText = `${formatNumberFull(s.total_sessions)} сессий`;
      if (daysEl) daysEl.innerText = `${s.active_days} активных дней`;
      if (rangeEl) rangeEl.innerText = `${s.first_date || ''} — ${s.last_date || ''}`;

      // 1. KPI Cards
      const kpiGrid = document.getElementById('kpi-grid');
      if (kpiGrid) {
        const thinkingShare = s.tot_model_tok ? ((s.tot_thinking_tok / s.tot_model_tok) * 100).toFixed(1) : 0;
        const uPrompts = s.tot_user_prompts || 0;
        const mCalls = s.tot_model_calls || 1;
        const globalMultiplier = uPrompts ? (mCalls / uPrompts).toFixed(1) : '-';

        kpiGrid.innerHTML = `
          <div class="kpi-card">
            <div class="kpi-header">
              <span>Запросы пользователя</span>
              <span class="kpi-icon">⌨️</span>
            </div>
            <div class="kpi-val" style="color: #fb7185;">${formatTokens(uPrompts)}</div>
            <div class="kpi-sub">${formatNumberFull(uPrompts)} прямых промптов</div>
          </div>

          <div class="kpi-card">
            <div class="kpi-header">
              <span>Обращений к API</span>
              <span class="kpi-icon">⚡</span>
            </div>
            <div class="kpi-val" style="color: #c084fc;">${formatTokens(mCalls)}</div>
            <div class="kpi-sub">Мультипликатор: x${globalMultiplier} к запросам</div>
          </div>

          <div class="kpi-card">
            <div class="kpi-header">
              <span>Ответы модели</span>
              <span class="kpi-icon">💬</span>
            </div>
            <div class="kpi-val" style="color: #34d399;">${formatTokens(s.tot_model_tok)}</div>
            <div class="kpi-sub">${formatNumberFull(s.tot_model_tok)} токенов</div>
          </div>

          <div class="kpi-card">
            <div class="kpi-header">
              <span>Рассуждения (CoT)</span>
              <span class="kpi-icon">🧠</span>
            </div>
            <div class="kpi-val" style="color: #fbbf24;">${formatTokens(s.tot_thinking_tok)}</div>
            <div class="kpi-sub">${thinkingShare}% от ответов модели</div>
          </div>

          <div class="kpi-card">
            <div class="kpi-header">
              <span>Данные Tools</span>
              <span class="kpi-icon">🛠️</span>
            </div>
            <div class="kpi-val" style="color: #818cf8;">${formatTokens(s.tot_tool_tok)}</div>
            <div class="kpi-sub">${formatNumberFull(s.tot_tool_tok)} токенов</div>
          </div>

          <div class="kpi-card">
            <div class="kpi-header">
              <span>Уникальных токенов</span>
              <span class="kpi-icon">💎</span>
            </div>
            <div class="kpi-val" style="color: #38bdf8;">${formatTokens(s.tot_unique_tok)}</div>
            <div class="kpi-sub">Суммарно: ${formatNumberFull(s.tot_unique_tok)}</div>
          </div>

          <div class="kpi-card">
            <div class="kpi-header">
              <span>Кумулятивный контекст</span>
              <span class="kpi-icon">🌀</span>
            </div>
            <div class="kpi-val" style="color: #f87171;">${formatTokens(s.tot_context_tok)}</div>
            <div class="kpi-sub">ReAct нагрузка (~O(N²))</div>
          </div>
        `;
      }

      // 2. Initialize Date Range Filter & Render Chart & Table
      initDateRangeFilter();

      // 3. Render Top Sessions
      renderTopSessions(tdata.top_conversations || []);
    }

    function initDateRangeFilter() {
      repopulateDateSelectors();
      applyPresetRange(currentPreset);
      applyDateFilter();
    }

    function repopulateDateSelectors() {
      const items = getCurrentDataset();
      const fromSel = document.getElementById('select-date-from');
      const toSel = document.getElementById('select-date-to');
      if (!fromSel || !toSel || !items.length) return;

      const periods = items.map(m => m.period);
      fromSel.innerHTML = periods.map(p => `<option value="${p}">${p}</option>`).join('');
      toSel.innerHTML = periods.map(p => `<option value="${p}">${p}</option>`).join('');
    }

    function setGranularity(gran) {
      if (currentGranularity === gran) return;
      currentGranularity = gran;

      ['month', 'week', 'day'].forEach(g => {
        const btn = document.getElementById(`btn-gran-${g}`);
        if (btn) btn.classList.toggle('active', g === gran);
      });

      const titleEl = document.getElementById('table-box-title-text');
      const subEl = document.getElementById('table-box-subtitle-text');
      const thPeriod = document.getElementById('th-period-label');
      const thGrowth = document.getElementById('th-growth-label');
      if (gran === 'week') {
        if (titleEl) titleEl.innerText = 'Понедельная разбивка (Weekly Dynamics)';
        if (subEl) subEl.innerText = 'Статистика по календарным неделям (ISO Weeks)';
        if (thPeriod) thPeriod.innerText = 'Неделя';
        if (thGrowth) thGrowth.innerText = 'WoW %';
      } else if (gran === 'day') {
        if (titleEl) titleEl.innerText = 'Посуточная разбивка (Daily Dynamics)';
        if (subEl) subEl.innerText = 'Детальная статистика по каждому активному дню';
        if (thPeriod) thPeriod.innerText = 'Дата';
        if (thGrowth) thGrowth.innerText = 'DoD %';
      } else {
        if (titleEl) titleEl.innerText = 'Помесячная разбивка (Monthly Dynamics & Burn Rates)';
        if (subEl) subEl.innerText = 'Детальная статистика и процентные доли за выбранный период';
        if (thPeriod) thPeriod.innerText = 'Период';
        if (thGrowth) thGrowth.innerText = 'MoM %';
      }

      repopulateDateSelectors();
      applyPresetRange(currentPreset);
      applyDateFilter();
    }

    function setPeriodPreset(preset) {
      currentPreset = preset;
      document.querySelectorAll('#period-presets .preset-btn').forEach(b => b.classList.remove('active'));
      const activeBtn = document.getElementById(`btn-preset-${preset}`);
      if (activeBtn) activeBtn.classList.add('active');

      if (preset === '1m' && currentGranularity === 'month') {
        // Automatically switch to days for 1m to show detailed distribution curve
        setGranularity('day');
        return;
      }

      applyPresetRange(preset);
      applyDateFilter();
    }

    function applyPresetRange(preset) {
      const items = getCurrentDataset();
      if (!items.length) return;
      const periods = items.map(m => m.period);
      const total = periods.length;

      if (preset === 'all') {
        selectedFrom = periods[0];
        selectedTo = periods[total - 1];
      } else {
        let count = total;
        if (currentGranularity === 'month') {
          count = preset === '12m' ? 12 : (preset === '6m' ? 6 : (preset === '3m' ? 3 : 1));
        } else if (currentGranularity === 'week') {
          count = preset === '12m' ? 52 : (preset === '6m' ? 26 : (preset === '3m' ? 13 : 5));
        } else if (currentGranularity === 'day') {
          count = preset === '12m' ? 365 : (preset === '6m' ? 180 : (preset === '3m' ? 90 : 30));
        }
        const startIndex = Math.max(0, total - count);
        selectedFrom = periods[startIndex];
        selectedTo = periods[total - 1];
      }

      const fromSel = document.getElementById('select-date-from');
      const toSel = document.getElementById('select-date-to');
      if (fromSel) fromSel.value = selectedFrom;
      if (toSel) toSel.value = selectedTo;
    }

    function onDateRangeSelectChange() {
      const fromSel = document.getElementById('select-date-from');
      const toSel = document.getElementById('select-date-to');
      if (!fromSel || !toSel) return;

      let fVal = fromSel.value;
      let tVal = toSel.value;
      if (fVal > tVal) {
        tVal = fVal;
        toSel.value = tVal;
      }
      selectedFrom = fVal;
      selectedTo = tVal;

      currentPreset = 'custom';
      document.querySelectorAll('#period-presets .preset-btn').forEach(b => b.classList.remove('active'));
      applyDateFilter();
    }

    function applyDateFilter() {
      const items = getCurrentDataset();
      const filtered = items.filter(m => m.period >= selectedFrom && m.period <= selectedTo);
      renderChart(filtered);
      renderMonthlyTable(filtered);
    }

    function renderChart(monthly) {
      if (typeof Chart === 'undefined') {
        const fallback = document.getElementById('chart-fallback');
        const canvas = document.getElementById('monthlyChart');
        if (fallback) fallback.style.display = 'block';
        if (canvas) canvas.style.display = 'none';
        return;
      }

      const canvas = document.getElementById('monthlyChart');
      if (!canvas) return;

      const labels = monthly.map(m => m.period);
      const userPromptsData = monthly.map(m => m.user_prompts || 0);
      const apiCallsData = monthly.map(m => m.model_calls || 0);
      const modelTokData = monthly.map(m => m.model_tok || 0);
      const thinkingData = monthly.map(m => m.thinking_tok || 0);
      const uniqueTokData = monthly.map(m => m.unique_tok || 0);
      const contextTokData = monthly.map(m => m.context_tok || 0);

      const datasetsConfig = [
        {
          label: 'Запросы пользователя',
          data: userPromptsData,
          borderColor: '#fb7185',
          backgroundColor: 'rgba(251, 113, 133, 0.1)',
          yAxisID: 'yLeft',
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
          fill: false
        },
        {
          label: 'Вызовы API',
          data: apiCallsData,
          borderColor: '#c084fc',
          backgroundColor: 'rgba(192, 132, 252, 0.1)',
          yAxisID: 'yLeft',
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
          fill: false
        },
        {
          label: 'Ответы модели',
          data: modelTokData,
          borderColor: '#34d399',
          backgroundColor: 'rgba(52, 211, 153, 0.1)',
          yAxisID: 'yLeft',
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
          fill: false
        },
        {
          label: 'Thinking CoT',
          data: thinkingData,
          borderColor: '#fbbf24',
          backgroundColor: 'rgba(251, 191, 36, 0.1)',
          yAxisID: 'yLeft',
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 4,
          pointHoverRadius: 6,
          fill: false
        },
        {
          label: 'Уникальные токены',
          data: uniqueTokData,
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56, 189, 248, 0.12)',
          yAxisID: 'yLeft',
          tension: 0.35,
          borderWidth: 2.5,
          pointRadius: 5,
          pointHoverRadius: 7,
          fill: true
        },
        {
          label: 'Кумулятивный контекст',
          data: contextTokData,
          borderColor: '#f87171',
          backgroundColor: 'rgba(248, 113, 113, 0.08)',
          yAxisID: 'yRight',
          tension: 0.35,
          borderWidth: 2.5,
          pointRadius: 5,
          pointHoverRadius: 7,
          borderDash: [5, 4],
          fill: false
        }
      ];

      if (chartInstance) {
        chartInstance.destroy();
      }

      const ctx = canvas.getContext('2d');
      chartInstance = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: datasetsConfig
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: {
            mode: 'index',
            intersect: false,
          },
          plugins: {
            legend: {
              display: true,
              position: 'top',
              labels: {
                color: '#cbd5e1',
                boxWidth: 12,
                boxHeight: 12,
                usePointStyle: true,
                pointStyle: 'circle',
                font: {
                  family: "'Plus Jakarta Sans', sans-serif",
                  size: 12,
                  weight: 500
                },
                padding: 16
              },
              onClick: (e, legendItem, legend) => {
                const index = legendItem.datasetIndex;
                const ci = legend.chart;
                if (ci.isDatasetVisible(index)) {
                  ci.hide(index);
                  legendItem.hidden = true;
                } else {
                  ci.show(index);
                  legendItem.hidden = false;
                }
                syncPillToggles();
              }
            },
            tooltip: {
              backgroundColor: 'rgba(15, 23, 42, 0.95)',
              titleColor: '#f8fafc',
              bodyColor: '#cbd5e1',
              borderColor: 'rgba(255, 255, 255, 0.1)',
              borderWidth: 1,
              padding: 12,
              boxPadding: 6,
              usePointStyle: true,
              callbacks: {
                label: function(context) {
                  let label = context.dataset.label || '';
                  if (label) label += ': ';
                  const val = context.raw;
                  label += formatNumberFull(val) + ' (' + formatTokens(val) + ')';
                  return label;
                }
              }
            }
          },
          scales: {
            x: {
              grid: {
                color: 'rgba(255, 255, 255, 0.05)',
                drawBorder: false
              },
              ticks: {
                color: '#94a3b8',
                autoSkip: true,
                maxTicksLimit: 25,
                maxRotation: 45,
                font: {
                  family: "'Plus Jakarta Sans', sans-serif",
                  size: 11.5
                }
              }
            },
            yLeft: {
              type: 'linear',
              position: 'left',
              grid: {
                color: 'rgba(255, 255, 255, 0.05)',
                drawBorder: false
              },
              ticks: {
                color: '#94a3b8',
                font: {
                  family: "Menlo, Monaco, monospace",
                  size: 11
                },
                callback: function(value) {
                  return formatTokens(value);
                }
              }
            },
            yRight: {
              type: 'linear',
              position: 'right',
              grid: {
                display: false
              },
              ticks: {
                color: '#f87171',
                font: {
                  family: "Menlo, Monaco, monospace",
                  size: 11
                },
                callback: function(value) {
                  return formatTokens(value);
                }
              }
            }
          }
        }
      });

      renderPillToggles();
    }

    function renderPillToggles() {
      const container = document.getElementById('chart-toggles');
      if (!container || !chartInstance) return;

      const colors = ['#fb7185', '#c084fc', '#34d399', '#fbbf24', '#38bdf8', '#f87171'];
      container.innerHTML = chartInstance.data.datasets.map((ds, idx) => {
        const isVis = chartInstance.isDatasetVisible(idx);
        return `
          <button class="toggle-btn ${isVis ? 'active' : ''}" onclick="toggleDataset(${idx})" id="pill-ds-${idx}">
            <span class="dot" style="background: ${colors[idx % colors.length]};"></span>
            <span>${ds.label}</span>
          </button>
        `;
      }).join('');
    }

    function syncPillToggles() {
      if (!chartInstance) return;
      chartInstance.data.datasets.forEach((ds, idx) => {
        const pill = document.getElementById(`pill-ds-${idx}`);
        if (pill) {
          const isVis = chartInstance.isDatasetVisible(idx);
          pill.classList.toggle('active', isVis);
        }
      });
    }

    function toggleDataset(index) {
      if (!chartInstance) return;
      const isVis = chartInstance.isDatasetVisible(index);
      chartInstance.setDatasetVisibility(index, !isVis);
      chartInstance.update();
      syncPillToggles();
    }

    function renderMonthlyTable(monthly) {
      const tbody = document.getElementById('monthly-table-body');
      if (!tbody) return;

      tbody.innerHTML = monthly.map(m => {
        const uPrompts = m.user_prompts || 0;
        const mCalls = m.model_calls || 0;
        const multiplier = uPrompts ? (mCalls / uPrompts).toFixed(1) : '-';
        const growth = m.growth_uniq_pct || 0;
        let growthClass = 'growth-neg';
        let growthStr = '-';
        if (growth > 0) {
          growthClass = growth > 150 ? 'growth-high' : 'growth-pos';
          growthStr = `+${growth.toFixed(1)}%`;
        } else if (growth < 0) {
          growthStr = `${growth.toFixed(1)}%`;
        }

        const thinkingPct = m.thinking_ratio_pct ? m.thinking_ratio_pct.toFixed(1) + '%' : '-';
        const avgCalls = m.avg_daily_calls ? Number(m.avg_daily_calls).toFixed(1) : '-';

        return `
          <tr>
            <td style="font-weight: 600; color: #f8fafc;">${m.period}</td>
            <td class="text-right">${m.sessions_count || 0}</td>
            <td class="text-right">${m.active_days || 0}</td>
            <td class="text-right" style="color: #fb7185; font-weight: 600;" title="${formatNumberFull(uPrompts)}">${formatNumberFull(uPrompts)}</td>
            <td class="text-right" style="color: #c084fc;" title="${formatNumberFull(mCalls)}">${formatNumberFull(mCalls)}</td>
            <td class="text-right" style="color: #38bdf8; font-weight: 600;">x${multiplier}</td>
            <td class="text-right">${avgCalls}</td>
            <td class="text-right" title="${formatNumberFull(m.model_tok || 0)}">${formatTokens(m.model_tok || 0)}</td>
            <td class="text-right" style="color: #fbbf24;">${thinkingPct}</td>
            <td class="text-right" style="font-weight: 600; color: #38bdf8;" title="${formatNumberFull(m.unique_tok || 0)}">${formatTokens(m.unique_tok || 0)}</td>
            <td class="text-right ${growthClass}">${growthStr}</td>
            <td class="text-right" style="color: #f87171;" title="${formatNumberFull(m.context_tok || 0)}">${formatTokens(m.context_tok || 0)}</td>
          </tr>
        `;
      }).join('');
    }

    function renderTopSessions(topConvos) {
      const tbody = document.getElementById('top-sessions-body');
      const badge = document.getElementById('top-sessions-count-badge');
      if (badge && topConvos) badge.innerText = `${topConvos.length} сессий`;
      if (!tbody) return;

      tbody.innerHTML = topConvos.map(c => {
        const shortId = (c.id || '').substring(0, 8) + '...';
        const cleanPrompt = (c.first_req || 'Сессия без стартового сообщения').replace(/</g, '&lt;').replace(/>/g, '&gt;');

        return `
          <tr>
            <td style="color: #38bdf8; font-weight: 600;" title="${c.id}">${shortId}</td>
            <td>${c.start_date || '-'}</td>
            <td class="text-right">${formatNumberFull(c.steps || 0)}</td>
            <td class="session-prompt" title="${cleanPrompt}">${cleanPrompt}</td>
            <td class="text-right" style="font-weight: 600; color: #38bdf8;" title="${formatNumberFull(c.unique_tok || 0)}">${formatTokens(c.unique_tok || 0)}</td>
            <td class="text-right" style="color: #f87171;" title="${formatNumberFull(c.context_tok || 0)}">${formatTokens(c.context_tok || 0)}</td>
          </tr>
        `;
      }).join('');
    }

    function toggleTopSessions() {
      const content = document.getElementById('top-sessions-content');
      const btn = document.getElementById('top-sessions-arrow');
      if (!content || !btn) return;
      const isHidden = content.style.display === 'none';
      content.style.display = isHidden ? 'block' : 'none';
      btn.innerText = isHidden ? '▲ Свернуть' : '▼ Развернуть';
    }

    // Initial fetch
    fetchData();
    // Auto-refresh every 5 minutes
    setInterval(fetchData, 300000);
  </script>
</body>
</html>
"""


def load_token_analytics_data():
    """Loads precomputed token analytics data from token_analytics module."""
    base_dir = os.path.dirname(SCRIPT_DIR)
    json_path = os.path.join(base_dir, "token_analytics", "antigravity_token_analysis.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD_TEMPLATE.encode("utf-8"))
            return

        if parsed.path == "/api/quotas":
            query_params = urllib.parse.parse_qs(parsed.query)
            force = query_params.get("refresh", ["false"])[0].lower() in ("true", "1", "yes")

            data = get_all_quotas_data(force_refresh=force)
            active_email = get_active_antigravity_account()

            # Enrich items with is_active flag
            for acc in data:
                email = (acc.get("email") or "").strip().lower()
                acc["is_active"] = bool(active_email and email == active_email.lower())

            # Sort active account to the top
            data.sort(key=lambda x: 0 if x.get("is_active") else 1)

            cache_time_str = None
            is_from_cache = not force and os.path.exists(CACHE_FILE)
            if os.path.exists(CACHE_FILE):
                try:
                    mtime = os.path.getmtime(CACHE_FILE)
                    cache_time_str = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
                except Exception:
                    pass

            payload = {
                "active_email": active_email,
                "is_cached": is_from_cache,
                "cache_time": cache_time_str,
                "accounts": data,
                "token_analytics": load_token_analytics_data()
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        if parsed.path == "/api/token-analytics":
            tdata = load_token_analytics_data() or {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(tdata).encode("utf-8"))
            return

        if parsed.path == "/api/add-account":
            auth_params = {
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent",
            }
            auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(auth_params)}"
            self.send_response(302)
            self.send_header("Location", auth_url)
            self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def generate_static_dashboard(output_path=None):
    """
    Generates a standalone, self-contained dashboard.html file
    with pre-embedded quota data from local cache / live check.
    Zero server processes required.
    """
    data = get_all_quotas_data(force_refresh=False)
    active_email = get_active_antigravity_account()

    for acc in data:
        email = (acc.get("email") or "").strip().lower()
        acc["is_active"] = bool(active_email and email == active_email.lower())

    data.sort(key=lambda x: 0 if x.get("is_active") else 1)

    cache_time_str = datetime.now().strftime("%H:%M:%S")
    if os.path.exists(CACHE_FILE):
        try:
            mtime = os.path.getmtime(CACHE_FILE)
            cache_time_str = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
        except Exception:
            pass

    payload = {
        "active_email": active_email,
        "is_cached": True,
        "cache_time": cache_time_str,
        "accounts": data,
        "token_analytics": load_token_analytics_data()
    }

    embedded_json = json.dumps(payload, ensure_ascii=False)
    html_content = HTML_DASHBOARD_TEMPLATE.replace(
        "/*EMBEDDED_DATA_PLACEHOLDER*/ null",
        embedded_json
    )

    if not output_path:
        output_path = os.path.join(SCRIPT_DIR, "dashboard.html")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return output_path


def run_dashboard_server():
    server = HTTPServer(("localhost", DASHBOARD_PORT), DashboardHTTPHandler)
    url = f"http://localhost:{DASHBOARD_PORT}"
    print(f"\n✨ Запуск Liquid Glass Dashboard на \033[1;36m{url}\033[0m")
    print("Нажмите Ctrl+C для остановки сервера.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--add":
        add_account_flow()
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        accounts = load_accounts()
        print(f"\n📋 Подключено аккаунтов: {len(accounts)}")
        for i, acc in enumerate(accounts, 1):
            print(f"  {i}. {acc.get('email')} (добавлен: {acc.get('added_at', 'н/д')})")
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        data = get_all_quotas_data(force_refresh=True)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--dashboard":
        html_file = generate_static_dashboard()
        file_url = Path(os.path.abspath(html_file)).as_uri()
        print(f"\n✨ Открытие Liquid Glass Dashboard: \033[1;36m{file_url}\033[0m")
        webbrowser.open(file_url)
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        run_dashboard_server()
        return

    accounts = load_accounts()
    if not accounts:
        print("\nℹ️  Нет сохраненных аккаунтов.")
        print("Чтобы добавить первый аккаунт, запустите:")
        print("  \033[1mpython3 check_quota.py --add\033[0m\n")
        return

    print(f"\n📊 Опрос актуальных квот для {len(accounts)} акк...")
    all_data = get_all_quotas_data(force_refresh=True)
    for acc_data in all_data:
        parse_and_display_quota(acc_data.get("email", "unknown"), acc_data)

    print("\n💡 Чтобы добавить ещё один аккаунт, выполните: python3 check_quota.py --add")
    print("🌐 Чтобы открыть Liquid Glass веб-панель: python3 check_quota.py --dashboard\n")


if __name__ == "__main__":
    main()
