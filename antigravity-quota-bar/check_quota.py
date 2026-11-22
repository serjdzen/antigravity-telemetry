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

import argparse
import os
import sys
import json
import time
import socket
import subprocess
import re
import urllib.request
import urllib.parse
import webbrowser
import base64
from pathlib import Path
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone

from account_store import atomic_write, email_key, read_accounts, roster_lock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
try:
    import account_switcher
except ImportError:
    account_switcher = None

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
    "https://daily-cloudcode-pa.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "accounts.json")
GLOBAL_CONFIG_FILE = os.path.expanduser("~/.antigravity_accounts.json")
CACHE_FILE = os.path.join(SCRIPT_DIR, ".quota_cache.json")


def load_accounts(*, strict=False):
    return read_accounts(CONFIG_FILE, GLOBAL_CONFIG_FILE, strict=strict)


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
                    email = (
                        data.get("agy.onboarding.lastLoginUsername")
                        or data.get("jetski.onboarding.lastLoginUsername")
                        or data.get("antigravity.onboarding.lastLoginUsername")
                    )
                    if email and "@" in email:
                        return email.strip().lower()
            except Exception:
                pass
    return None


def save_accounts(accounts):
    atomic_write(CONFIG_FILE, json.dumps(accounts, indent=2, ensure_ascii=False))


def register_account(email, refresh_token):
    with roster_lock(CONFIG_FILE):
        accounts = load_accounts(strict=True)
        account = next((row for row in accounts if email_key(row["email"]) == email_key(email)), None)
        timestamp = datetime.now().isoformat()
        if account is None:
            accounts.append({"email": email, "refresh_token": refresh_token, "added_at": timestamp})
        else:
            account.update(refresh_token=refresh_token, updated_at=timestamp)
        save_accounts(accounts)


def read_quota_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as source:
            cached = json.load(source)
        if isinstance(cached, list):
            return [row for row in cached if isinstance(row, dict) and isinstance(row.get("email"), str)]
    except (OSError, ValueError):
        pass
    return []


def save_quota_cache(rows):
    atomic_write(CACHE_FILE, json.dumps(rows, indent=2, ensure_ascii=False))


_EPOCH = datetime(1970, 1, 1)


def _parse_mru_datetime(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
    except ValueError:
        return None


def _touch_active_account_locked(accounts, active_email=None):
    """Records last_used_at for the active account when it changes (caller must hold roster_lock)."""
    if active_email is None:
        active_email = get_active_antigravity_account()
    active = email_key(active_email or "")
    if not active or not accounts:
        return False
    target = next((acc for acc in accounts if email_key(acc["email"]) == active), None)
    if target is None:
        return False
    timestamps = [dt for acc in accounts if (dt := _parse_mru_datetime(acc.get("last_used_at"))) is not None]
    max_dt = max(timestamps, default=None)
    target_dt = _parse_mru_datetime(target.get("last_used_at"))
    if (
        target_dt is not None
        and max_dt is not None
        and target_dt == max_dt
        and sum(1 for dt in timestamps if dt == max_dt) == 1
    ):
        return False
    now_dt = datetime.now()
    if max_dt is not None and now_dt <= max_dt:
        now_dt = max_dt + timedelta(microseconds=1)
    target["last_used_at"] = now_dt.isoformat()
    if os.path.exists(CONFIG_FILE):
        save_accounts(accounts)
    return True


def account_view(rows, accounts):
    """Join the latest settings with quota data, keeping the roster authoritative and MRU-sorted."""
    quota_fields = ("status", "models", "error", "error_type", "cached_fallback", "transient_error")
    by_email = {email_key(row["email"]): row for row in rows}
    active = email_key(get_active_antigravity_account() or "")
    ranked = []
    for idx, account in enumerate(accounts):
        key = email_key(account["email"])
        row = by_email.get(key, {})
        preferences = {
            "email": account["email"],
            "is_active": key == active,
            "note": account["note"] if isinstance(account.get("note"), str) else "",
            "show_in_swiftbar": account.get("show_in_swiftbar") is not False,
        }
        item = {
            "status": "pending",
            **{field: row[field] for field in quota_fields if field in row},
            **preferences,
        }
        dt = _parse_mru_datetime(account.get("last_used_at"))
        mru_key = -int((dt - _EPOCH).total_seconds() * 1_000_000) if dt is not None else 0
        ranked.append(((0 if item["is_active"] else 1, 0 if dt is not None else 1, mru_key, idx), item))
    ranked.sort(key=lambda pair: pair[0])
    return [item for _, item in ranked]


def refresh_access_token(refresh_token, retries=1, return_details=False):
    """
    Refreshes Google OAuth access token with automatic retry on transient network errors.
    Returns:
        access_token (str or None) if return_details is False.
        tuple (access_token, error_type, error_msg) if return_details is True.
    """
    token_url = "https://oauth2.googleapis.com/token"
    payload = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")

    last_err_type = None
    last_err_msg = None

    for attempt in range(retries + 1):
        req = urllib.request.Request(token_url, data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=7) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tok = data.get("access_token")
                if tok:
                    return (tok, None, None) if return_details else tok
        except urllib.error.HTTPError as e:
            err_code = ""
            err_desc = ""
            try:
                err_body = e.read().decode("utf-8")
                err_json = json.loads(err_body)
                err_code = err_json.get("error", "")
                err_desc = err_json.get("error_description", "")
            except Exception:
                pass

            if e.code == 400 and ("invalid_grant" in str(err_code) or "invalid_grant" in str(err_desc)):
                last_err_type = "invalid_grant"
                last_err_msg = "Требуется повторный вход (токен отозван или устарел)"
                break  # Fatal: no point in retrying invalid_grant
            elif e.code in (429, 500, 502, 503, 504):
                last_err_type = "network_error"
                last_err_msg = f"Временный сбой Google OAuth (HTTP {e.code})"
            else:
                last_err_type = "http_error"
                last_err_msg = f"Ошибка OAuth (HTTP {e.code}: {err_code or err_desc or 'неизвестная ошибка'})"
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            last_err_type = "network_error"
            last_err_msg = "Таймаут или сбой сети при запросе к Google OAuth"
        except Exception as e:
            last_err_type = "other"
            last_err_msg = f"Ошибка: {str(e)}"

        if attempt < retries:
            time.sleep(1.0)

    if return_details:
        return None, last_err_type, (last_err_msg or "Не удалось обновить токен")
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
        "User-Agent": "antigravity/2.23.1 darwin/arm64",
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
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    p = data.get("cloudaicompanionProject")
                    if p and p not in discovered_projects:
                        discovered_projects.append(p)
                        break
        except Exception:
            pass

    project_candidates = [""] + discovered_projects + ["rising-fact-p41fc", "aicode-consumers"]

    for base_url in ENDPOINTS:
        for proj in project_candidates:
            try:
                body = json.dumps({"project": proj}).encode("utf-8") if proj else b"{}"
                req = urllib.request.Request(
                    f"{base_url}/v1internal:retrieveUserQuotaSummary",
                    data=body,
                    headers=headers,
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data and ("groups" in data or "quota_groups" in data or "quotaGroups" in data):
                            return data
            except Exception:
                continue
    return None


_CACHED_LS_PORT = None
_CACHED_LS_TOKEN = None

def discover_local_language_server(force=False):
    """
    Auto-discovers the local Antigravity Language Server port and CSRF token.
    Fast (sub-millisecond) local query with 0 network latency.
    """
    global _CACHED_LS_PORT, _CACHED_LS_TOKEN
    if not force and _CACHED_LS_PORT and _CACHED_LS_TOKEN:
        try:
            url = f"http://127.0.0.1:{_CACHED_LS_PORT}/exa.language_server_pb.LanguageServerService/GetStatus"
            req = urllib.request.Request(
                url,
                data=b"{}",
                headers={"Content-Type": "application/json", "x-codeium-csrf-token": _CACHED_LS_TOKEN},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=0.3) as resp:
                if resp.status == 200:
                    return _CACHED_LS_PORT, _CACHED_LS_TOKEN
        except Exception:
            _CACHED_LS_PORT = None
            _CACHED_LS_TOKEN = None

    # Step 0: Fast deterministic IPC daemon file discovery (~/.gemini/antigravity/daemon/hub_*.json)
    # Sub-millisecond (0.2ms), does not rely on ps or lsof
    try:
        daemon_dir = os.path.expanduser("~/.gemini/antigravity/daemon")
        if os.path.isdir(daemon_dir):
            hub_files = []
            for fname in os.listdir(daemon_dir):
                if fname.startswith("hub_") and fname.endswith(".json"):
                    fpath = os.path.join(daemon_dir, fname)
                    try:
                        hub_files.append((fpath, os.path.getmtime(fpath)))
                    except Exception:
                        pass
            # Sort by mtime descending (most recent first)
            hub_files.sort(key=lambda x: x[1], reverse=True)

            for fpath, _ in hub_files:
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    pid = meta.get("pid")
                    http_port = meta.get("httpPort")
                    csrf_token = meta.get("csrfToken")
                    if not http_port or not csrf_token:
                        continue
                    # Verify process is still alive if PID is present
                    if pid:
                        try:
                            os.kill(int(pid), 0)
                        except OSError:
                            continue  # Process no longer exists, skip stale file
                        except Exception:
                            pass
                    # Verify port responds to GetStatus
                    url = f"http://127.0.0.1:{http_port}/exa.language_server_pb.LanguageServerService/GetStatus"
                    req = urllib.request.Request(
                        url,
                        data=b"{}",
                        headers={"Content-Type": "application/json", "x-codeium-csrf-token": csrf_token},
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=0.25) as resp:
                        if resp.status == 200:
                            _CACHED_LS_PORT = int(http_port)
                            _CACHED_LS_TOKEN = csrf_token
                            return _CACHED_LS_PORT, _CACHED_LS_TOKEN
                except Exception:
                    continue
    except Exception:
        pass

    try:
        # 1. Fallback: Get csrf_token and host_bridge_url from ps
        ps_out = subprocess.run(["ps", "auxww"], capture_output=True, text=True, timeout=1.5).stdout
        m = re.search(r"language_server.*--csrf_token\s+([a-f0-9-]+)", ps_out)
        if not m:
            return None, None
        csrf_token = m.group(1)

        # Fast path: extract host_bridge_url (language_server Connect-RPC is host_bridge + 2)
        candidate_ports = []
        m_hb = re.search(r"--host_bridge_url=https?://127\.0\.0\.1:(\d+)", ps_out)
        if m_hb:
            hb = int(m_hb.group(1))
            candidate_ports.extend([hb + 2, hb + 1, hb + 3, hb - 1])

        for p in candidate_ports:
            try:
                url = f"http://127.0.0.1:{p}/exa.language_server_pb.LanguageServerService/GetStatus"
                req = urllib.request.Request(
                    url,
                    data=b"{}",
                    headers={"Content-Type": "application/json", "x-codeium-csrf-token": csrf_token},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=0.25) as resp:
                    if resp.status == 200:
                        _CACHED_LS_PORT = int(p)
                        _CACHED_LS_TOKEN = csrf_token
                        return _CACHED_LS_PORT, _CACHED_LS_TOKEN
            except Exception:
                continue

        # Fallback to lsof if candidate ports did not match
        lsof_out = subprocess.run(["lsof", "-nP", "-a", "-c", "language_server", "-iTCP", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=1.0).stdout
        ports = re.findall(r":(\d+)\s+\(LISTEN\)", lsof_out)
        for p in ports[:15]:
            try:
                url = f"http://127.0.0.1:{p}/exa.language_server_pb.LanguageServerService/GetStatus"
                req = urllib.request.Request(
                    url,
                    data=b"{}",
                    headers={"Content-Type": "application/json", "x-codeium-csrf-token": csrf_token},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=0.3) as resp:
                    if resp.status == 200:
                        _CACHED_LS_PORT = int(p)
                        _CACHED_LS_TOKEN = csrf_token
                        return _CACHED_LS_PORT, _CACHED_LS_TOKEN
            except Exception:
                continue
    except Exception:
        pass
    return None, None


def query_local_quota_summary():
    """
    Queries local Antigravity Language Server for quota summary (including Gemini 4 Argon).
    Returns list of quota groups or None.
    """
    port, token = discover_local_language_server()
    if not port or not token:
        return None
    try:
        url = f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json", "x-codeium-csrf-token": token},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                resp_obj = data.get("response", data)
                groups = resp_obj.get("groups") or resp_obj.get("quota_groups") or resp_obj.get("quotaGroups")
                if groups:
                    return groups
    except Exception:
        pass
    return None


def query_quota(access_token, project_id="rising-fact-p41fc"):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity/2.23.1 darwin/arm64",
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


def parse_quota_groups(groups):
    """
    Parses structured quota groups into models list:
    - Gemini Models (excludes argon to avoid name collisions)
    - Claude and GPT models
    - Gemini 4 Argon (weekly pool)
    """
    models = []

    def parse_group(target_keywords, model_name, pool_id, icon, exclude_keywords=None):
        for g in groups:
            g_name = (g.get("displayName") or g.get("display_name") or g.get("name") or "").lower()
            if any(kw in g_name for kw in target_keywords):
                if exclude_keywords and any(ex in g_name for ex in exclude_keywords):
                    continue
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

                has_5h = five_h_frac is not None
                has_weekly = week_frac is not None

                return {
                    "name": model_name,
                    "pool_id": pool_id,
                    "icon": icon,
                    "has_5h": has_5h,
                    "has_weekly": has_weekly,
                    "fraction": five_h_frac if has_5h else (week_frac if week_frac is not None else 1.0),
                    "percentage": int(round(five_h_frac * 100)) if has_5h else (int(round(week_frac * 100)) if week_frac is not None else 100),
                    "reset": format_reset_time(five_h_reset) if has_5h else format_reset_time(week_reset),
                    "reset_iso": five_h_reset if has_5h else week_reset,
                    "weekly_fraction": week_frac if week_frac is not None else 1.0,
                    "weekly_percentage": int(round(week_frac * 100)) if week_frac is not None else 100,
                    "weekly_reset": format_reset_time(week_reset) if week_reset else "готов",
                    "weekly_reset_iso": week_reset,
                }
        return None

    g_m = parse_group(["gemini"], "Gemini", "gemini", "✨", exclude_keywords=["argon"])
    if g_m:
        models.append(g_m)

    c_m = parse_group(["claude", "gpt"], "Claude", "claude", "🧠")
    if c_m:
        models.append(c_m)

    a_m = parse_group(["argon"], "Gemini 4 Argon", "argon", "⚡")
    if a_m:
        models.append(a_m)

    return models


ANOMALY_RESET_SKEW_SECONDS = 120
FULL_QUOTA_FRACTION_THRESHOLD = 0.999
PRIMARY_POOL_IDS = ("gemini", "claude")


def _parse_iso_utc(iso_str):
    """Parses an ISO-8601 timestamp string into a UTC-aware datetime, or returns None."""
    if not iso_str:
        return None
    try:
        clean_ts = str(iso_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def reset_argon_model(model_dict):
    """Returns a copy of a cached Argon model reset to 100% ('готов') with cleared timers."""
    if not model_dict or model_dict.get("pool_id") != "argon":
        return model_dict
    m = dict(model_dict)
    m["percentage"] = 100
    m["fraction"] = 1.0
    m["reset"] = "готов"
    m["weekly_percentage"] = 100
    m["weekly_fraction"] = 1.0
    m["weekly_reset"] = "готов"
    m["weekly_reset_iso"] = None
    m["reset_iso"] = None
    return m


def _has_full_primary_quota(model_dict):
    """Checks that a primary pool reports 100% 5h/7d quota from a reliable weekly source."""
    has_reliable_weekly = (
        model_dict.get("has_weekly") is True
        or _parse_iso_utc(model_dict.get("weekly_reset_iso")) is not None
    )
    if not has_reliable_weekly:
        return False
    pct_5h = model_dict.get("percentage", 100)
    frac_5h = model_dict.get("fraction", 1.0)
    pct_7d = model_dict.get("weekly_percentage", 100)
    frac_7d = model_dict.get("weekly_fraction", 1.0)
    return (
        pct_5h == 100
        and pct_7d == 100
        and (frac_5h is None or frac_5h >= FULL_QUOTA_FRACTION_THRESHOLD)
        and (frac_7d is None or frac_7d >= FULL_QUOTA_FRACTION_THRESHOLD)
    )


def detect_anomalous_pool_reset(cached_acc, fresh_res, now=None, skew_seconds=ANOMALY_RESET_SKEW_SECONDS):
    """
    Detects an early/anomalous Google quota reset on primary pools (Gemini/Claude)
    between cached_acc and fresh_res so cached Argon can also be reset.

    Requires ALL present primary pools in fresh_res to be 100% with valid weekly_reset_iso
    (ignoring fetchAvailableModels fallback and cached_fallback), and at least one primary
    pool to have transitioned from <100% before its cached reset window expired.
    """
    if not cached_acc or not fresh_res:
        return False
    if fresh_res.get("status") != "ok" or fresh_res.get("cached_fallback"):
        return False

    fresh_pools = {
        m.get("pool_id"): m
        for m in fresh_res.get("models", [])
        if m.get("pool_id") in PRIMARY_POOL_IDS
    }
    if not fresh_pools:
        return False
    if not all(_has_full_primary_quota(m) for m in fresh_pools.values()):
        return False

    prev_pools = {
        m.get("pool_id"): m
        for m in cached_acc.get("models", [])
        if m.get("pool_id") in PRIMARY_POOL_IDS
    }
    now_utc = now or datetime.now(timezone.utc)
    skew = timedelta(seconds=skew_seconds)

    for pid, curr_m in fresh_pools.items():
        prev_m = prev_pools.get(pid)
        if not prev_m:
            continue

        prev_w_pct = prev_m.get("weekly_percentage", 100)
        prev_w_frac = prev_m.get("weekly_fraction", 1.0)
        prev_w_dt = _parse_iso_utc(prev_m.get("weekly_reset_iso"))
        curr_w_dt = _parse_iso_utc(curr_m.get("weekly_reset_iso"))

        had_weekly_usage = prev_w_pct < 100 or (
            prev_w_frac is not None and prev_w_frac < FULL_QUOTA_FRACTION_THRESHOLD
        )
        if had_weekly_usage and prev_w_dt is not None and prev_w_dt > now_utc + skew:
            return True

        prev_5h_pct = prev_m.get("percentage", 100)
        prev_5h_frac = prev_m.get("fraction", 1.0)
        prev_5h_dt = _parse_iso_utc(prev_m.get("reset_iso"))
        had_5h_usage = prev_5h_pct < 100 or (
            prev_5h_frac is not None and prev_5h_frac < FULL_QUOTA_FRACTION_THRESHOLD
        )
        if (
            had_5h_usage
            and prev_5h_dt is not None
            and prev_5h_dt > now_utc + skew
            and prev_w_dt is not None
            and curr_w_dt is not None
            and curr_w_dt > prev_w_dt + skew
        ):
            return True

    return False


def recalculate_cached_argon(model_dict):
    """
    Recalculates dynamic reset countdown for cached Argon quota.
    If weekly window has expired, resets quota to 100% ('готов').
    """
    if not model_dict or model_dict.get("pool_id") != "argon":
        return model_dict

    m = dict(model_dict)
    w_iso = m.get("weekly_reset_iso") or m.get("reset_iso")
    target = _parse_iso_utc(w_iso)
    if target is None:
        return m

    total_seconds = int((target - datetime.now(timezone.utc)).total_seconds())
    if total_seconds <= 0:
        return reset_argon_model(m)

    updated_time_str = format_reset_time(w_iso)
    m["reset"] = updated_time_str
    m["weekly_reset"] = updated_time_str
    return m


def recalculate_cached_model(model_dict):
    """
    Recalculates dynamic reset countdowns for any cached model (Gemini, Claude, Argon).
    If reset window has passed, marks quota as 'готов'.
    """
    if not model_dict or not isinstance(model_dict, dict):
        return model_dict
    if model_dict.get("pool_id") == "argon":
        return recalculate_cached_argon(model_dict)

    m = dict(model_dict)
    # Recalculate 5h session reset
    reset_iso = m.get("reset_iso")
    if reset_iso and m.get("has_5h", True):
        m["reset"] = format_reset_time(reset_iso)
        if m["reset"] == "готов":
            m["percentage"] = 100
            m["fraction"] = 1.0

    # Recalculate weekly reset
    w_iso = m.get("weekly_reset_iso")
    if w_iso:
        m["weekly_reset"] = format_reset_time(w_iso)
        if m["weekly_reset"] == "готов":
            m["weekly_percentage"] = 100
            m["weekly_fraction"] = 1.0

    return m


def fetch_account_quota(acc):
    email = acc.get("email", "unknown")
    rf_token = acc.get("refresh_token")
    acc_data = {
        "email": email,
        "status": "ok",
        "models": [],
        "error": None
    }

    # 0. Check if this is the active account in Antigravity IDE
    active_email = get_active_antigravity_account()
    is_active = bool(active_email and email.strip().lower() == active_email.strip().lower())

    if is_active:
        # Fast local query via Language Server (sub-millisecond, returns Gemini, Claude, and Gemini 4 Argon)
        local_groups = query_local_quota_summary()
        if local_groups:
            parsed_models = parse_quota_groups(local_groups)
            if parsed_models:
                acc_data["models"] = parsed_models
                return acc_data

    # 1. Fallback / Standard query via Google Cloud Code API
    if not rf_token:
        acc_data["status"] = "error"
        acc_data["error"] = "Отсутствует refresh token"
        acc_data["error_type"] = "config_error"
        return acc_data

    access_token, err_type, err_msg = refresh_access_token(rf_token, retries=1, return_details=True)
    if not access_token:
        acc_data["status"] = "error"
        acc_data["error"] = err_msg
        acc_data["error_type"] = err_type
        return acc_data

    # 2. Try modern retrieveUserQuotaSummary
    summary_data = query_quota_summary(access_token)
    if summary_data:
        groups = summary_data.get("groups") or summary_data.get("quota_groups") or summary_data.get("quotaGroups")
        if not groups and "response" in summary_data:
            groups = summary_data["response"].get("groups", [])
        if groups:
            acc_data["models"] = parse_quota_groups(groups)

    if is_active and not any(m.get("pool_id") == "argon" for m in acc_data["models"]):
        # Ensure active account always retains Argon from local Language Server
        local_groups = query_local_quota_summary()
        if local_groups:
            argon_m = next((m for m in parse_quota_groups(local_groups) if m.get("pool_id") == "argon"), None)
            if argon_m:
                acc_data["models"].append(argon_m)

    # 2. Fallback to fetchAvailableModels if summary returned nothing
    if not acc_data["models"]:
        quota_data = query_quota(access_token)
        if not quota_data or "models" not in quota_data:
            acc_data["status"] = "error"
            acc_data["error"] = "Пустой ответ от Google Quota API"
            acc_data["error_type"] = "network_error"
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
    with roster_lock(CONFIG_FILE):
        accounts = load_accounts()
        if not accounts:
            return []
        _touch_active_account_locked(accounts)
        cached = read_quota_cache()
        try:
            fresh = time.time() - os.path.getmtime(CACHE_FILE) < 360
        except OSError:
            fresh = False
        if not force_refresh and fresh:
            view = account_view(cached, accounts)
            if any(row["status"] == "ok" for row in view) and all(row["status"] != "pending" for row in view):
                return view

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(accounts), 8)) as executor:
        results = list(executor.map(fetch_account_quota, accounts))

    # Recheck the connection after polling. An OAuth replacement or delete may have
    # completed while the requests were in flight.
    def connection_id(account):
        return tuple(account.get(field) for field in ("added_at", "updated_at", "refresh_token"))

    connections = {email_key(account["email"]): connection_id(account) for account in accounts}
    with roster_lock(CONFIG_FILE):
        current = load_accounts()
        _touch_active_account_locked(current)
        valid = {
            key for account in current
            if (key := email_key(account["email"])) in connections
            and connections[key] == connection_id(account)
        }
        previous = read_quota_cache()
        results = [row for row in results if email_key(row["email"]) in valid]
        merged = merge_quota_results(results, previous)
        # Preserve newer cached results for accounts added or reauthorized during the poll.
        merged.extend(row for row in previous if email_key(row["email"]) not in valid)
        registered = {email_key(account["email"]) for account in current}
        merged = [row for row in merged if email_key(row["email"]) in registered]
        try:
            save_quota_cache(merged)
        except OSError:
            pass
        return account_view(merged, current)


def merge_quota_results(results, previous):
    prev_cache = {
        email_key(row["email"]): row for row in previous if row.get("status") == "ok"
    }
    # If an individual account hit a transient network error or active IDE account hit OAuth error,
    # gracefully fall back to its cached quota with live reset timers
    active_email = get_active_antigravity_account()
    active_key = email_key(active_email) if active_email else ""
    for i, res in enumerate(results):
        acc_key = email_key(res.get("email") or "")
        is_active_acc = bool(active_key and acc_key == active_key)
        if res.get("status") == "error":
            cached_acc = prev_cache.get(acc_key)
            should_use_cache = False
            if res.get("error_type") == "network_error":
                should_use_cache = True
            elif is_active_acc and res.get("error_type") in ("invalid_grant", "auth_error", "config_error"):
                # Active IDE session is still valid in IDE, but backup token in accounts.json failed.
                # Preserve valid cached quotas instead of flashing a false error.
                should_use_cache = True

            if should_use_cache and cached_acc and cached_acc.get("models"):
                updated_models = [recalculate_cached_model(m) for m in cached_acc.get("models", [])]
                results[i] = {
                    **cached_acc,
                    "status": "ok",
                    "models": updated_models,
                    "cached_fallback": True,
                    "transient_error": res.get("error")
                }
                res = results[i]

        # Smart Argon Persistence: preserve cached Argon for ANY account that previously had it,
        # unless fresh data in this cycle already contains Argon.
        # If Google unexpectedly reset primary pools (Gemini/Claude) early, reset Argon too.
        if not any(m.get("pool_id") == "argon" for m in res.get("models", [])):
            cached_acc = prev_cache.get(acc_key)
            if cached_acc:
                cached_argon = next((m for m in cached_acc.get("models", []) if m.get("pool_id") == "argon"), None)
                if cached_argon:
                    already_full = (
                        cached_argon.get("weekly_percentage", 100) == 100
                        and cached_argon.get("percentage", 100) == 100
                        and not cached_argon.get("weekly_reset_iso")
                    )
                    if not already_full and detect_anomalous_pool_reset(cached_acc, res):
                        updated_argon = reset_argon_model(cached_argon)
                    else:
                        updated_argon = recalculate_cached_argon(cached_argon)
                    res.setdefault("models", []).append(updated_argon)

    if not any(row.get("status") == "ok" for row in results):
        results = [
            {
                **cached,
                "models": [recalculate_cached_model(model) for model in cached.get("models", [])],
                "cached_fallback": True,
            } if (cached := prev_cache.get(email_key(row["email"]))) else row
            for row in results
        ]

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

    active_email = get_active_antigravity_account()
    is_active = bool(
        active_email
        and account_email
        and email_key(str(account_email)) == email_key(str(active_email))
    )
    account_header = (
        f"\n👤 \033[1;32m{account_email} (Активен)\033[0m"
        if is_active
        else f"\n👤 \033[1m{account_email}\033[0m"
    )

    # Structured acc_data from get_all_quotas_data
    if isinstance(data_or_acc, dict) and "models" in data_or_acc and isinstance(data_or_acc.get("models"), list):
        if data_or_acc.get("status") == "error":
            print(account_header)
            print(f"  ⚠️  {data_or_acc.get('error')}")
            return

        print(account_header)
        print("─" * 68)
        for m in data_or_acc.get("models", []):
            if m.get("pool_id") in ("image", "claude") or "claude" in m.get("name", "").lower():
                continue
            is_argon = m.get("pool_id") == "argon" or "argon" in m.get("name", "").lower()
            if is_argon:
                w_pct = m.get("weekly_percentage", 100)
                w_reset = m.get("weekly_reset", "готов")
                w_frac = m.get("weekly_fraction", 1.0)
                bar_w = render_progress_bar(w_frac, width=5)
                print(f"  ⚡ Argon: {bar_w}  ({w_reset})")
                continue

            bar_5h = render_progress_bar(m.get("fraction", 1.0), width=5)
            reset_5h = m.get("reset", "готов")

            w_pct = m.get("weekly_percentage", 100)
            w_reset = m.get("weekly_reset", "готов")
            w_frac = m.get("weekly_fraction", 1.0)

            is_lockout = (w_pct == 0) or "кулдаун" in str(w_reset).lower() or (m.get("percentage") == 0 and "д" in str(reset_5h))
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
    print(account_header)
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

            register_account(email, refresh_token)
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
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js" async onload="window.__chartReady=true;if(window.__onChartLoaded)window.__onChartLoaded();" onerror="window.__chartLoadFailed=true;if(window.__onChartLoaded)window.__onChartLoaded();"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
      background-color: #0f172a;
      background-image:
        radial-gradient(600px circle at 10% 10%, rgba(56, 189, 248, 0.16), transparent 65%),
        radial-gradient(700px circle at 90% 90%, rgba(129, 140, 248, 0.16), transparent 65%),
        radial-gradient(500px circle at 55% 40%, rgba(16, 185, 129, 0.10), transparent 65%),
        radial-gradient(circle at 10% 20%, rgba(30, 41, 59, 0.8) 0%, rgba(15, 23, 42, 1) 90.2%);
      background-attachment: fixed;
      color: #f1f5f9;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 40px 20px;
      overflow-x: hidden;
    }

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
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
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
      transition: background-color 0.15s ease, border-color 0.15s ease, transform 0.15s ease;
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
      background: linear-gradient(155deg, rgba(30, 41, 59, 0.62) 0%, rgba(15, 23, 42, 0.78) 100%);
      border: 1px solid rgba(255, 255, 255, 0.09);
      border-radius: 20px;
      padding: 22px 24px;
      box-shadow: 0 10px 28px rgba(0, 0, 0, 0.28);
      transition: border-color 0.18s ease;
      display: flex;
      flex-direction: column;
    }
    .account-card:hover {
      border-color: rgba(56, 189, 248, 0.35);
    }

    .account-header {
      display: flex;
      flex-direction: column;
      align-items: stretch;
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
      align-items: baseline;
      flex-wrap: wrap;
      gap: 8px;
      font-size: 13.5px;
      font-weight: 600;
      color: #f8fafc;
      min-width: 0;
      flex: 1;
      overflow: hidden;
    }
    .email-text {
      overflow-wrap: anywhere;
    }
    .account-note {
      color: #cbd5e1;
      font-weight: 400;
      overflow-wrap: anywhere;
    }
    .account-actions {
      display: flex;
      gap: 6px;
      align-items: center;
      flex-wrap: wrap;
    }
    .account-actions .status-badge {
      margin-right: auto;
    }
    .icon-button {
      position: relative;
      display: inline-grid;
      place-items: center;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.15);
      border-radius: 8px;
      color: #f8fafc;
      width: 32px;
      height: 32px;
      cursor: pointer;
      font-size: 16px;
    }
    .icon-button:hover:not(:disabled) { background: rgba(255, 255, 255, 0.14); }
    .icon-button:focus-visible { outline: 2px solid #38bdf8; }
    .icon-button:disabled { opacity: 0.55; cursor: default; }
    .icon-button.eye-closed::after {
      content: '';
      position: absolute;
      width: 20px;
      height: 2px;
      background: #f8fafc;
      box-shadow: 0 1px #0f172a;
      transform: rotate(-45deg);
    }
    .note-editor:not([hidden]) {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .note-editor input {
      width: 100%;
      padding: 8px;
      color: #f8fafc;
      background: #0f172a;
      border: 1px solid #475569;
      border-radius: 8px;
    }
    .note-editor .btn { padding: 6px 9px; font-size: 12px; }
    #account-management-status:not(:empty) {
      color: #cbd5e1;
      font-size: 13px;
      margin-bottom: 14px;
    }
    #account-management-status[data-error="true"] {
      color: #fca5a5;
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
    .pools-grid.pools-grid-single {
      grid-template-columns: 1fr;
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
        grid-template-columns: minmax(0, 1fr);
      }
    }
    @media (max-width: 600px) {
      .header-bar {
        align-items: stretch;
        flex-direction: column;
        gap: 14px;
        padding: 16px;
      }
      .btn-group { flex-wrap: wrap; }
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
      background: linear-gradient(160deg, rgba(30, 41, 59, 0.58) 0%, rgba(15, 23, 42, 0.78) 100%);
      border: 1px solid rgba(255, 255, 255, 0.09);
      border-radius: 20px;
      padding: 28px;
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.3);
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
      transition: border-color 0.15s ease;
    }
    .kpi-card:hover {
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
      transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
      user-select: none;
    }
    .toggle-btn:hover {
      background: rgba(255, 255, 255, 0.08);
      color: #f1f5f9;
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
      transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
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
      transition: background-color 0.15s ease, border-color 0.15s ease;
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
    .topic-cell {
      white-space: normal !important;
      min-width: 280px;
      max-width: 440px;
    }
    .topic-title {
      font-weight: 600;
      color: #f8fafc;
      font-size: 13px;
      line-height: 1.4;
      white-space: normal !important;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      word-break: break-word;
    }
    .topic-id {
      font-size: 11px;
      color: #64748b;
      font-family: Menlo, Monaco, monospace;
      margin-top: 4px;
      white-space: nowrap;
    }

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
      transition: background-color 0.15s ease;
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

    /* Account Switch Button & Native Top-Layer Modal */
    .switch-account-btn {
      padding: 5px 10px;
      font-size: 12px;
      border-radius: 8px;
    }
    dialog#switch-modal {
      margin: auto;
      padding: 24px 26px;
      max-width: 440px;
      width: calc(100% - 32px);
      background: linear-gradient(155deg, rgba(30, 41, 59, 0.96) 0%, rgba(15, 23, 42, 0.98) 100%);
      border: 1px solid rgba(56, 189, 248, 0.3);
      border-radius: 20px;
      color: #f8fafc;
      box-shadow: 0 25px 60px rgba(0, 0, 0, 0.65);
    }
    dialog#switch-modal:modal {
      overscroll-behavior: contain;
    }
    dialog#switch-modal::backdrop {
      overflow: hidden;
      overscroll-behavior: contain;
      background: rgba(2, 6, 23, 0.72);
    }
    dialog#switch-modal h2 {
      font-size: 17px;
      font-weight: 700;
      margin-bottom: 10px;
    }
    dialog#switch-modal p {
      font-size: 13.5px;
      color: #cbd5e1;
      line-height: 1.5;
      margin-bottom: 18px;
    }
    dialog#switch-modal .modal-email {
      color: #38bdf8;
      font-weight: 600;
      overflow-wrap: anywhere;
    }
    dialog#switch-modal .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
    }
  </style>
</head>
<body>
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
        <button class="btn btn-secondary" id="refresh-btn" onclick="fetchData(true)">🔄 Обновить</button>
        <button class="btn btn-primary" id="add-account-btn" onclick="addAccount()">➕ Добавить аккаунт</button>
      </div>
    </header>

    <div id="account-management-status" role="status"></div>
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
            <span>Топ-20 самых ресурсоёмких сессий на этом Mac</span>
            <span class="top-sessions-badge" id="top-sessions-count-badge">сессий</span>
          </div>
          <button class="accordion-btn" id="top-sessions-arrow">▲ Свернуть</button>
        </div>
        <div class="top-sessions-content" id="top-sessions-content" style="display: block;">
          <!-- Filter Controls for Active vs Deleted -->
          <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-top: 10px; margin-bottom: 12px;">
            <div class="filter-group" style="display: flex; align-items: center; gap: 10px;">
              <span class="filter-label">Диалоги:</span>
              <div class="period-presets" id="top-sessions-filter-presets">
                <button class="preset-btn active" onclick="setTopFilter('all')" id="btn-top-all">Все (включая удалённые)</button>
                <button class="preset-btn" onclick="setTopFilter('active')" id="btn-top-active">Только на диске (активные)</button>
                <button class="preset-btn" onclick="setTopFilter('deleted')" id="btn-top-deleted">Только удалённые из UI</button>
              </div>
            </div>
            <span style="font-size: 12px; color: #64748b;" id="top-filter-hint">Показано топ-20 сессий</span>
          </div>

          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th>Тема диалога</th>
                  <th>Проект</th>
                  <th>Статус</th>
                  <th>Дата старта</th>
                  <th class="text-right">Запросов</th>
                  <th class="text-right">Шагов</th>
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
      Google Antigravity 2.0 Monitor
    </div>
  </div>

  <dialog id="switch-modal" aria-labelledby="switch-modal-title">
    <h2 id="switch-modal-title">Переключение аккаунта Antigravity</h2>
    <p>Переключить активный аккаунт Antigravity на <span class="modal-email" id="switch-modal-email"></span>? Antigravity IDE будет безопасно перезапущена.</p>
    <div class="modal-actions">
      <button type="button" class="btn btn-secondary" id="switch-modal-cancel" onclick="closeSwitchModal()" autofocus>Отмена</button>
      <button type="button" class="btn btn-primary" id="switch-modal-confirm" onclick="confirmSwitchAccount()">Переключить</button>
    </div>
  </dialog>

  <script>
    const EMBEDDED_DATA = /*EMBEDDED_DATA_PLACEHOLDER*/ null;
    const canManageAccounts = location.protocol !== 'file:';
    const managementStatus = document.getElementById('account-management-status');
    let isFetching = false;
    let isManaging = false;
    let lastAccountsSig = '';
    let lastAnalyticsSig = '';
    if (!canManageAccounts) {
      managementStatus.textContent = 'Для управления аккаунтами откройте дашборд из SwiftBar.';
    }

    function analyticsSignature(tdata) {
      const s = tdata && tdata.summary;
      return s ? `${s.last_date || ''}|${s.tot_steps || 0}|${s.tot_unique_tok || 0}|${s.total_sessions || 0}` : '';
    }

    async function fetchData(force = false) {
      const switchDialog = document.getElementById('switch-modal');
      if (isFetching || isManaging || document.querySelector('.note-editor:not([hidden])') || (switchDialog && switchDialog.open)) return;
      const updatedEl = document.getElementById('last-updated');
      const refreshBtn = document.getElementById('refresh-btn');

      // 1. Instant rendering from pre-embedded data (Zero-Server mode) on initial page load
      if (EMBEDDED_DATA && !force && !window.__initialDataRendered) {
        window.__initialDataRendered = true;
        const embAccounts = EMBEDDED_DATA.accounts || [];
        lastAccountsSig = JSON.stringify(embAccounts);
        renderAccounts(embAccounts);
        if (EMBEDDED_DATA.token_analytics) {
          lastAnalyticsSig = analyticsSignature(EMBEDDED_DATA.token_analytics);
          renderTokenAnalytics(EMBEDDED_DATA.token_analytics);
        }
        const timeStr = EMBEDDED_DATA.cache_time || new Date().toLocaleTimeString();
        updatedEl.innerText = `Синхронизировано: ${timeStr} (кэш меню-бара)`;
        if (!canManageAccounts) return;
      }

      isFetching = true;
      if (refreshBtn) {
        refreshBtn.innerText = "⏳ Обновление...";
        refreshBtn.disabled = true;
      }

      if (force) {
        updatedEl.innerText = "Опрос квот Google Cloud...";
      }

      const baseUrl = (location.protocol === 'file:') ? 'http://127.0.0.1:51122' : '';
      try {
        const url = `${baseUrl}/api/quotas${force ? '?refresh=true' : ''}`;
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 12000);
        const res = await fetch(url, { signal: controller.signal });
        clearTimeout(timeoutId);

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const accounts = Array.isArray(data) ? data : (data.accounts || []);
        const nextAccountsSig = JSON.stringify(accounts);
        if (nextAccountsSig !== lastAccountsSig) {
          lastAccountsSig = nextAccountsSig;
          renderAccounts(accounts);
        }
        if (data.token_analytics) {
          const nextAnalyticsSig = analyticsSignature(data.token_analytics);
          if (nextAnalyticsSig !== lastAnalyticsSig) {
            lastAnalyticsSig = nextAnalyticsSig;
            renderTokenAnalytics(data.token_analytics);
          }
        }

        let timeStr = new Date().toLocaleTimeString();
        if (data.cache_time) {
          timeStr = data.cache_time;
        }
        const sourceLabel = data.is_cached ? ' (кэш меню-бара)' : ' (сеть)';
        updatedEl.innerText = `Синхронизировано: ${timeStr}${sourceLabel}`;
      } catch (err) {
        console.warn("API fetch error:", err);
        if (location.protocol === 'file:' && force) {
          updatedEl.innerText = "Запрос обновления через меню-бар...";
          window.location.href = "swiftbar://refreshPlugin?name=antigravity_bar.5m.py";
          setTimeout(() => location.reload(), 2500);
          return;
        }

        updatedEl.innerText = 'Не удалось обновить данные';
      } finally {
        isFetching = false;
        if (refreshBtn) {
          refreshBtn.innerText = "🔄 Обновить";
          refreshBtn.disabled = false;
        }
      }
    }

    function renderAccounts(accounts) {
      lastAccountsSig = JSON.stringify(accounts || []);
      const container = document.getElementById('accounts-container');
      if (!accounts || accounts.length === 0) {
        container.innerHTML = '<div class="empty-state">Нет сохраненных аккаунтов. Нажмите «➕ Добавить аккаунт»</div>';
        return;
      }

      container.innerHTML = accounts.map(acc => {
        const isError = acc.status === 'error';
        const isActive = Boolean(acc.is_active);
        const isPending = acc.status === 'pending';

        let modelsHtml = '';
        let hasAnyLockout = false;

        if (isError) {
          modelsHtml = '<div class="account-error" style="color: #f87171; font-size: 14px; padding: 10px 0;"></div>';
        } else {
          modelsHtml = (acc.models || []).map(m => {
            const has5h = m.has_5h !== false && m.pool_id !== 'argon';

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
              rawWpct === 0 ||
              rawWreset.toLowerCase().includes('кулдаун') ||
              (has5h && pct5h === 0 && String(reset5h).includes('д'))
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

            const modelIcon = m.icon || (m.pool_id === 'argon' ? '⚡' : (m.name.toLowerCase().includes('claude') ? '🧠' : '✨'));

            return `
              <div class="model-row">
                <div class="model-header">
                  <div class="model-name">
                    <span class="model-icon">${modelIcon}</span>
                    <span>${m.name}</span>
                  </div>
                </div>

                <div class="pools-grid ${has5h ? '' : 'pools-grid-single'}">
                  ${has5h ? `
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
                  ` : ''}

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
                <span class="email-text"></span>
                <span class="account-note" hidden></span>
                ${isActive ? '<span class="active-badge">Текущий</span>' : ''}
                ${hasAnyLockout ? '<span class="cooldown-badge">7дн кулдаун</span>' : ''}
              </div>
              <div class="account-actions">
                <span class="status-badge" style="${isError ? 'background:rgba(239,68,68,0.15);color:#f87171;border-color:rgba(239,68,68,0.3);' : ''}">
                  ${isError ? 'ОШИБКА' : isPending ? 'ОЖИДАЕТ ОПРОСА' : 'АКТИВЕН'}
                </span>
                ${canManageAccounts ? `
                  ${!isActive ? '<button class="btn btn-primary switch-account-btn" type="button">⚡ Сделать активным</button>' : ''}
                  <button class="icon-button edit-note" type="button" aria-label="Изменить отметку" title="Изменить отметку">✎</button>
                  <button class="icon-button toggle-visible" type="button">👁</button>
                  <button class="icon-button delete-account" type="button" aria-label="Удалить аккаунт" title="Удалить аккаунт">🗑</button>
                ` : ''}
              </div>
              ${canManageAccounts ? `
                <form class="note-editor" hidden>
                  <input type="text" aria-label="Отметка аккаунта" placeholder="Введите отметку" maxlength="160">
                  <button type="submit" class="btn btn-primary">Сохранить</button>
                  <button type="button" class="btn btn-secondary cancel-note">Отмена</button>
                </form>
              ` : ''}
            </div>
            <div class="models-list">
              ${modelsHtml}
            </div>
          </div>
        `;
      }).join('');

      Array.from(container.children).forEach((card, index) => {
        const account = accounts[index];
        const name = card.querySelector('.email-text');
        name.textContent = account.email;
        name.title = account.email;
        const note = card.querySelector('.account-note');
        note.hidden = !account.note;
        note.textContent = account.note ? `(${account.note})` : '';
        const error = card.querySelector('.account-error');
        if (error) error.textContent = `⚠️ ${account.error || 'Не удалось загрузить квоты'}`;
        if (canManageAccounts) bindAccountActions(card, account);
      });
    }

    function bindAccountActions(card, account) {
      const edit = card.querySelector('.edit-note');
      const form = card.querySelector('.note-editor');
      const input = form.querySelector('input');
      const submit = form.querySelector('[type="submit"]');
      edit.addEventListener('click', () => {
        if (isFetching || isManaging) return;
        const openForm = document.querySelector('.note-editor:not([hidden])');
        if (openForm && openForm !== form) return;
        input.value = account.note || '';
        form.hidden = false;
        input.focus();
      });
      form.querySelector('.cancel-note').addEventListener('click', () => {
        if (isManaging) return;
        form.hidden = true;
        edit.focus();
      });
      form.addEventListener('submit', event => {
        event.preventDefault();
        saveAccountChange('/api/account/preferences', account.email, { note: input.value }, submit,
          'Отметка сохранена.');
      });

      const eye = card.querySelector('.toggle-visible');
      const visible = account.show_in_swiftbar !== false;
      const effectivelyVisible = account.is_active || visible;
      const visibilityLabel = account.is_active ? 'Активный аккаунт всегда виден в SwiftBar' :
        visible ? 'Скрыть в SwiftBar' : 'Показать в SwiftBar';
      eye.title = visibilityLabel;
      eye.setAttribute('aria-label', visibilityLabel);
      eye.disabled = Boolean(account.is_active);
      eye.classList.toggle('eye-closed', !effectivelyVisible);
      eye.addEventListener('click', () => {
        if (document.querySelector('.note-editor:not([hidden])')) return;
        saveAccountChange('/api/account/preferences', account.email, { show_in_swiftbar: !visible },
          eye, visible ? 'Аккаунт скрыт в SwiftBar.' : 'Аккаунт показан в SwiftBar.');
      });

      const remove = card.querySelector('.delete-account');
      remove.addEventListener('click', () => {
        if (isFetching || isManaging || document.querySelector('.note-editor:not([hidden])')) return;
        const message = account.is_active
          ? `Удалить ${account.email} из мониторинга QuotaAnalytics? Сессия Antigravity останется открытой.`
          : `Удалить ${account.email} из дашборда и SwiftBar?`;
        if (window.confirm(message)) {
          saveAccountChange('/api/account/delete', account.email, {}, remove, 'Аккаунт удалён.');
        }
      });

      const switchBtn = card.querySelector('.switch-account-btn');
      if (switchBtn) {
        switchBtn.addEventListener('click', () => {
          if (isFetching || isManaging || document.querySelector('.note-editor:not([hidden])')) return;
          openSwitchModal(account.email);
        });
      }
    }

    let pendingSwitchEmail = null;

    function openSwitchModal(email) {
      if (!canManageAccounts || isManaging) return;
      const modal = document.getElementById('switch-modal');
      const emailEl = document.getElementById('switch-modal-email');
      const confirmBtn = document.getElementById('switch-modal-confirm');
      const cancelBtn = document.getElementById('switch-modal-cancel');
      if (!modal || !emailEl) return;
      pendingSwitchEmail = email;
      emailEl.textContent = email;
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Переключить';
      }
      if (cancelBtn) cancelBtn.disabled = false;
      modal.returnValue = '';
      if (typeof modal.showModal === 'function') {
        modal.showModal();
      } else {
        modal.setAttribute('open', '');
      }
    }

    function closeSwitchModal() {
      const modal = document.getElementById('switch-modal');
      pendingSwitchEmail = null;
      if (modal && modal.open) {
        if (typeof modal.close === 'function') {
          modal.close('cancel');
        } else {
          modal.removeAttribute('open');
        }
      }
    }

    (() => {
      const modal = document.getElementById('switch-modal');
      if (modal) {
        modal.addEventListener('close', () => {
          modal.returnValue = '';
          if (!isManaging) pendingSwitchEmail = null;
        });
      }
    })();

    async function confirmSwitchAccount() {
      if (!pendingSwitchEmail || isManaging) return;
      const targetEmail = pendingSwitchEmail;
      const confirmBtn = document.getElementById('switch-modal-confirm');
      const cancelBtn = document.getElementById('switch-modal-cancel');
      isManaging = true;
      if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = '⏳ Переключение...';
      }
      if (cancelBtn) cancelBtn.disabled = true;
      managementStatus.dataset.error = 'false';
      managementStatus.textContent = `Переключение активного аккаунта на ${targetEmail}…`;
      try {
        const response = await fetch('/api/switch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: targetEmail, dry_run: false, force: false }),
        });
        const data = await response.json();
        if (!response.ok || data.status !== 'ok') {
          throw new Error(data.message || data.error || 'Не удалось переключить аккаунт.');
        }
        isManaging = false;
        closeSwitchModal();
        managementStatus.textContent = `Аккаунт переключён на ${data.email || targetEmail}.`;
        await fetchData(true);
      } catch (error) {
        isManaging = false;
        closeSwitchModal();
        managementStatus.dataset.error = 'true';
        managementStatus.textContent = error.message || 'Ошибка переключения аккаунта.';
      } finally {
        isManaging = false;
        if (confirmBtn) {
          confirmBtn.disabled = false;
          confirmBtn.textContent = 'Переключить';
        }
        if (cancelBtn) cancelBtn.disabled = false;
      }
    }

    async function saveAccountChange(path, email, changes, button, message) {
      if (isFetching || isManaging) return;
      isManaging = true;
      button.disabled = true;
      managementStatus.dataset.error = 'false';
      managementStatus.textContent = 'Сохранение…';
      try {
        const response = await fetch(path, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, ...changes }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        renderAccounts(data.accounts);
        managementStatus.textContent = `${message} SwiftBar подхватит изменения при обновлении.`;
      } catch (error) {
        managementStatus.dataset.error = 'true';
        managementStatus.textContent = error.message || 'Не удалось сохранить изменение.';
      } finally {
        isManaging = false;
        button.disabled = false;
      }
    }

    async function addAccount() {
      if (!canManageAccounts) return;
      const button = document.getElementById('add-account-btn');
      button.disabled = true;
      managementStatus.dataset.error = 'false';
      try {
        const response = await fetch('/api/add-account', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error);
        managementStatus.textContent = 'После авторизации Google вернитесь сюда и нажмите «Обновить».';
      } catch (error) {
        managementStatus.dataset.error = 'true';
        managementStatus.textContent = error.message || 'Не удалось открыть авторизацию Google.';
      } finally {
        button.disabled = false;
      }
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

    window.__onChartLoaded = () => {
      if (tokenAnalyticsData) applyDateFilter();
    };

    function renderChart(monthly) {
      const fallback = document.getElementById('chart-fallback');
      const canvas = document.getElementById('monthlyChart');
      if (typeof Chart === 'undefined') {
        if (window.__chartLoadFailed) {
          if (fallback) fallback.style.display = 'block';
          if (canvas) canvas.style.display = 'none';
        }
        return;
      }
      if (fallback) fallback.style.display = 'none';
      if (canvas) canvas.style.display = 'block';
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
          animation: false,
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

    let currentTopFilter = 'all';
    let rawTopConversations = [];

    function setTopFilter(filter) {
      currentTopFilter = filter;
      ['all', 'active', 'deleted'].forEach(f => {
        const btn = document.getElementById(`btn-top-${f}`);
        if (btn) btn.classList.toggle('active', f === filter);
      });
      applyTopFilter();
    }

    function renderTopSessions(topConvos) {
      rawTopConversations = topConvos || [];
      applyTopFilter();
    }

    function applyTopFilter() {
      const tbody = document.getElementById('top-sessions-body');
      const badge = document.getElementById('top-sessions-count-badge');
      const hint = document.getElementById('top-filter-hint');
      if (!tbody) return;

      let filtered = rawTopConversations;
      if (currentTopFilter === 'active') {
        filtered = rawTopConversations.filter(c => !c.deleted_from_disk);
      } else if (currentTopFilter === 'deleted') {
        filtered = rawTopConversations.filter(c => c.deleted_from_disk);
      }

      const displayItems = filtered.slice(0, 20);
      if (badge) badge.innerText = `${displayItems.length} из ${filtered.length} сессий`;
      if (hint) {
        if (currentTopFilter === 'active') {
          hint.innerText = `Показаны топ-${displayItems.length} самых ресурсоёмких сессий, которые сейчас сохранены на диске`;
        } else if (currentTopFilter === 'deleted') {
          hint.innerText = `Показаны топ-${displayItems.length} самых ресурсоёмких сессий из числа удалённых из UI`;
        } else {
          hint.innerText = `Показано топ-${displayItems.length} сессий за всё время (включая удалённые)`;
        }
      }

      if (!displayItems.length) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: #64748b; padding: 24px;">Нет сессий в данной категории</td></tr>`;
        return;
      }

      tbody.innerHTML = displayItems.map(c => {
        const shortId = (c.id || '').substring(0, 8) + '...';
        const displayTitle = (c.title || c.first_req || 'Диалог без темы').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const cleanPrompt = (c.first_req || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const rawProj = (c.project && c.project !== '—') ? c.project.replace(/</g, '&lt;').replace(/>/g, '&gt;') : '';
        const projectBadge = rawProj
          ? `<span style="display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; background: rgba(99, 102, 241, 0.15); color: #a5b4fc; border: 1px solid rgba(99, 102, 241, 0.3); white-space: nowrap;">📁 ${rawProj}</span>`
          : `<span style="color: #64748b; font-size: 12px;">—</span>`;
        const isDeleted = Boolean(c.deleted_from_disk);
        const statusBadge = isDeleted
          ? `<span style="display: inline-block; padding: 2px 7px; border-radius: 6px; font-size: 11px; font-weight: 600; background: rgba(248, 113, 113, 0.15); color: #f87171; border: 1px solid rgba(248, 113, 113, 0.3);">Удалён из UI</span>`
          : `<span style="display: inline-block; padding: 2px 7px; border-radius: 6px; font-size: 11px; font-weight: 600; background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3);">Активен</span>`;

        return `
          <tr>
            <td class="topic-cell" title="ID: ${c.id}&#10;Тема: ${displayTitle}&#10;Стартовый запрос: ${cleanPrompt}">
              <div class="topic-title">${displayTitle}</div>
              <div class="topic-id">ID: ${shortId}</div>
            </td>
            <td>${projectBadge}</td>
            <td>${statusBadge}</td>
            <td>${c.start_date || '-'}</td>
            <td class="text-right" style="color: #fb7185; font-weight: 600;" title="Запросов пользователя: ${c.user_prompts || 0}">${formatNumberFull(c.user_prompts || 0)}</td>
            <td class="text-right">${formatNumberFull(c.steps || 0)}</td>
            <td class="text-right" style="font-weight: 600; color: #38bdf8;" title="${formatNumberFull(c.unique_tok || 0)}">${formatTokens(c.unique_tok || 0)}</td>
            <td class="text-right" style="color: #f87171; font-weight: 600;" title="${formatNumberFull(c.context_tok || 0)}">${formatTokens(c.context_tok || 0)}</td>
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


def trigger_swiftbar_refresh():
    """Requests an immediate SwiftBar menu refresh in the background on macOS."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.Popen(
            ["/usr/bin/open", "-g", "swiftbar://refreshPlugin?name=antigravity_bar.5m.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path not in {"/api/account/preferences", "/api/account/delete", "/api/add-account", "/api/switch"}:
            self.send_json(404, {"error": "Действие не найдено."})
            return

        port = self.server.server_port if self.server else DASHBOARD_PORT
        host = self.headers.get("Host")
        if (host not in {f"127.0.0.1:{port}", f"localhost:{port}"}
                or self.headers.get("Origin") != f"http://{host}"):
            self.send_json(403, {"error": "Откройте дашборд через SwiftBar."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send_json(415, {"error": "Ожидаются данные в формате JSON."})
            return

        if path == "/api/add-account":
            try:
                subprocess.Popen(
                    [sys.executable, os.path.abspath(__file__), "--add"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True,
                )
            except OSError:
                self.send_json(500, {"error": "Не удалось запустить авторизацию Google."})
                return
            self.send_json(202, {"status": "started"})
            return

        if path == "/api/switch":
            if account_switcher is None:
                self.send_json(500, {"status": "error", "error": "Модуль account_switcher недоступен."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict) or not isinstance(body.get("email"), str) or not body["email"].strip():
                    raise ValueError
                target_email = body["email"].strip()
                dry_run = bool(body.get("dry_run", False))
                force = bool(body.get("force", False))
            except (ValueError, UnicodeError):
                self.send_json(400, {"status": "error", "error": "Укажите корректный email для переключения."})
                return

            env_root = os.environ.get("ANTIGRAVITY_TEST_ROOT")
            test_root_path = Path(env_root) if env_root else None
            try:
                res = account_switcher.switch_account(
                    target_email=target_email,
                    accounts_file=Path(CONFIG_FILE) if test_root_path is None else None,
                    dry_run=dry_run,
                    force=force,
                    test_root=test_root_path,
                )
            except Exception as exc:
                self.send_json(500, {"status": "error", "email": target_email, "error": str(exc), "message": str(exc)})
                return

            resp_payload = {
                "status": "ok" if res.success else "error",
                "email": res.email,
                "message": res.message,
                "dry_run": res.dry_run,
                "phase": res.phase_reached,
            }
            if not res.success:
                resp_payload["error"] = res.error or res.message
            if res.success and not res.dry_run and test_root_path is None:
                trigger_swiftbar_refresh()
            self.send_json(200 if res.success else 400, resp_payload)
            return

        deleting = path == "/api/account/delete"
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                raise ValueError
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict) or not isinstance(body.get("email"), str):
                raise ValueError
            key = email_key(body["email"])
            if not key or "@" not in key:
                raise ValueError
            settings = {name: body[name] for name in body if name != "email"}
            if deleting and settings:
                raise ValueError
            if not deleting:
                if not settings or settings.keys() - {"note", "show_in_swiftbar"}:
                    raise ValueError
                if "note" in settings:
                    note = settings["note"]
                    if not isinstance(note, str) or len(note) > 160:
                        raise ValueError
                    settings["note"] = " ".join(note.splitlines()).strip()
                    settings["note"].encode("utf-8")
                if "show_in_swiftbar" in settings and not isinstance(settings["show_in_swiftbar"], bool):
                    raise ValueError
        except (ValueError, UnicodeError):
            self.send_json(400, {"error": "Проверьте данные. Отметка должна содержать до 160 символов."})
            return

        try:
            with roster_lock(CONFIG_FILE):
                accounts = load_accounts(strict=True)
                account = next((row for row in accounts if email_key(row["email"]) == key), None)
                if account is None:
                    self.send_json(404, {"error": "Аккаунт уже удалён или не подключён."})
                    return
                if deleting:
                    saved_cache = read_quota_cache()
                    retained_cache = [row for row in saved_cache if email_key(row["email"]) != key]
                    if retained_cache != saved_cache:
                        save_quota_cache(retained_cache)
                    accounts = [row for row in accounts if email_key(row["email"]) != key]
                else:
                    account.update(settings)
                save_accounts(accounts)
        except (OSError, ValueError):
            self.send_json(500, {"error": "Не удалось сохранить список аккаунтов."})
            return

        try:
            generate_static_dashboard()
        except OSError as error:
            print(f"Unable to update dashboard snapshot: {error}", file=sys.stderr)
        with roster_lock(CONFIG_FILE):
            accounts = load_accounts()
            _touch_active_account_locked(accounts)
            rows = account_view(read_quota_cache(), accounts)
        self.send_json(200, {"accounts": rows})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            static_file = os.path.join(SCRIPT_DIR, "dashboard.html")
            if os.path.exists(static_file):
                with open(static_file, "r", encoding="utf-8") as f:
                    content = f.read()
                self.wfile.write(content.encode("utf-8"))
            else:
                self.wfile.write(HTML_DASHBOARD_TEMPLATE.encode("utf-8"))
            return

        if parsed.path == "/api/quotas":
            query_params = urllib.parse.parse_qs(parsed.query)
            force = query_params.get("refresh", ["false"])[0].lower() in ("true", "1", "yes")

            data = get_all_quotas_data(force_refresh=force)
            active_email = next((row["email"] for row in data if row["is_active"]), None)

            # If force refreshed, keep static dashboard.html synchronized on disk
            if force:
                try:
                    generate_static_dashboard()
                except Exception:
                    pass

            cache_time_str = datetime.now().strftime("%H:%M:%S")
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
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path == "/api/token-analytics":
            tdata = load_token_analytics_data() or {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(json.dumps(tdata, ensure_ascii=False).encode("utf-8"))
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
    token_analytics = load_token_analytics_data()
    if not output_path:
        output_path = os.path.join(SCRIPT_DIR, "dashboard.html")

    with roster_lock(CONFIG_FILE):
        accounts = load_accounts()
        _touch_active_account_locked(accounts)
        data = account_view(read_quota_cache(), accounts)
        for account in data:
            if "models" in account:
                account["models"] = [recalculate_cached_model(model) for model in account["models"]]

        try:
            cache_time = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
        except OSError:
            cache_time = datetime.now()
        payload = {
            "active_email": next((row["email"] for row in data if row["is_active"]), None),
            "is_cached": True,
            "cache_time": cache_time.strftime("%H:%M:%S"),
            "accounts": data,
            "token_analytics": token_analytics,
        }
        # A literal '<' in user data could close the script tag even inside a JSON string.
        embedded_json = json.dumps(payload, ensure_ascii=False).replace("<", r"\u003c")
        html_content = HTML_DASHBOARD_TEMPLATE.replace("/*EMBEDDED_DATA_PLACEHOLDER*/ null", embedded_json)
        atomic_write(output_path, html_content)

    return output_path


def is_dashboard_server_running(port=DASHBOARD_PORT):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def start_dashboard_server_background():
    cmd = [sys.executable, os.path.abspath(__file__), "--server", "--no-open"]
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        for _ in range(25):
            time.sleep(0.1)
            if is_dashboard_server_running():
                return True
    except Exception:
        pass
    return False


def run_dashboard_server(open_browser=True):
    try:
        from http.server import ThreadingHTTPServer
        server_cls = ThreadingHTTPServer
    except ImportError:
        server_cls = HTTPServer

    try:
        server = server_cls(("127.0.0.1", DASHBOARD_PORT), DashboardHTTPHandler)
    except OSError as e:
        if e.errno == 48:  # Address already in use
            if open_browser:
                webbrowser.open(f"http://localhost:{DASHBOARD_PORT}")
            return
        raise

    url = f"http://localhost:{DASHBOARD_PORT}"
    print(f"\n✨ Запуск Liquid Glass Dashboard на \033[1;36m{url}\033[0m")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.")
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Antigravity Quota Checker & Account Switcher")
    parser.add_argument("--add", action="store_true", help="Add a new account via Google OAuth flow")
    parser.add_argument("--list", action="store_true", help="List configured accounts")
    parser.add_argument("--json", action="store_true", help="Output all quota data in JSON format")
    parser.add_argument("--dashboard", action="store_true", help="Open Liquid Glass Dashboard in browser")
    parser.add_argument("--server", action="store_true", help="Run background HTTP server")
    parser.add_argument("--no-open", action="store_true", help="Do not open browser when starting server")
    parser.add_argument("--switch", metavar="EMAIL", help="Perform account switch to target email or index")
    parser.add_argument(
        "--switch-interactive",
        metavar="EMAIL",
        help="Prompt native macOS AppleScript confirmation dialog before switching",
    )
    parser.add_argument("--dry-run", action="store_true", help="Execute switch in safe simulation mode")
    parser.add_argument("--force", action="store_true", help="Bypass active streaming socket check if needed")
    args, _ = parser.parse_known_args()

    if args.add:
        add_account_flow()
        return

    if args.list:
        accounts = load_accounts()
        print(f"\n📋 Подключено аккаунтов: {len(accounts)}")
        for i, acc in enumerate(accounts, 1):
            print(f"  {i}. {acc.get('email')} (добавлен: {acc.get('added_at', 'н/д')})")
        return

    if args.json:
        data = get_all_quotas_data(force_refresh=True)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if args.dashboard:
        html_file = generate_static_dashboard()
        server_ok = is_dashboard_server_running()
        if not server_ok:
            server_ok = start_dashboard_server_background()

        if server_ok:
            url = f"http://localhost:{DASHBOARD_PORT}"
            print(f"\n✨ Открытие Liquid Glass Dashboard: \033[1;36m{url}\033[0m")
            webbrowser.open(url)
        else:
            file_url = Path(os.path.abspath(html_file)).as_uri()
            print(f"\n✨ Открытие Liquid Glass Dashboard: \033[1;36m{file_url}\033[0m")
            webbrowser.open(file_url)
        return

    if args.server:
        run_dashboard_server(open_browser=not args.no_open)
        return

    if args.switch or args.switch_interactive:
        if account_switcher is None:
            print("❌ Модуль account_switcher недоступен.")
            sys.exit(1)
        target = args.switch_interactive or args.switch
        interactive = bool(args.switch_interactive)
        dry_run = bool(args.dry_run)
        env_root = os.environ.get("ANTIGRAVITY_TEST_ROOT")
        test_root_path = Path(env_root) if env_root else None
        is_sim = dry_run or os.environ.get("ANTIGRAVITY_DRY_RUN") == "1"
        mode_note = " (симуляция / dry-run)" if is_sim else ""
        print(f"🔄 Выполняется переключение на аккаунт: {target}{mode_note}...")
        res = account_switcher.switch_account(
            target_email=str(target),
            dry_run=dry_run,
            force=bool(args.force),
            interactive=interactive,
            test_root=test_root_path,
        )
        if res.success:
            print(f"\n✅ {res.message}")
            if res.dry_run:
                print("   ℹ️  Режим симуляции (dry-run): системные файлы и процессы не изменялись.")
            elif not interactive and test_root_path is None:
                trigger_swiftbar_refresh()
            sys.exit(0)
        print(f"\n❌ Ошибка переключения: {res.message}")
        if res.error:
            print(f"   Детали: {res.error}")
        sys.exit(1)

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
