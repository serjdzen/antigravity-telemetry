"""
Fixture factory for Antigravity Account Switcher tests.
Provides isolated filesystem environments and mock datasets adhering to Requirement R3.
"""

import os
import json
import base64
import sqlite3
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

FIXTURES_DIR = Path(__file__).resolve().parent

def build_mock_keychain_payload(
    email: str,
    access_token: str = "ya29.mock_access_token",
    refresh_token: str = "mock_refresh_token",
    expiry: str = "2026-11-07T16:00:00.000000Z"
) -> str:
    """Builds go-keyring-base64:<Base64(JSON)> payload for macOS Keychain."""
    data = {
        "token": {
            "access_token": access_token,
            "token_type": "Bearer",
            "refresh_token": refresh_token,
            "expiry": expiry,
            "expires_in": 3599,
        },
        "auth_method": "consumer",
        "id_token_email": email,
    }
    encoded = base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8")
    return f"go-keyring-base64:{encoded}"

def build_mock_uss_token_payload(email: str, access_token: str = "ya29.mock_access_token") -> str:
    """
    Builds a simulated Protobuf USS topic for antigravityUnifiedStateSync.oauthToken.
    Contains authStateWithContextSentinelKey and oauthTokenInfoSentinelKey.
    """
    inner_token = {
        "accessToken": access_token,
        "tokenType": "Bearer",
        "expiry": 1794060000,
        "email": email,
    }
    inner_b64 = base64.b64encode(json.dumps(inner_token).encode("utf-8")).decode("utf-8")
    uss_container = {
        "authStateWithContextSentinelKey": json.dumps({
            "state": "signedIn",
            "context": {"project": "", "showProjectError": False}
        }),
        "oauthTokenInfoSentinelKey": inner_b64,
        "_email_tag": email
    }
    return base64.b64encode(json.dumps(uss_container).encode("utf-8")).decode("utf-8")

def create_sqlite_state_db(db_path: Path, email: str = "active@example.com") -> None:
    """Creates a mock state.vscdb with ItemTable schema and baseline records."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        
        # Baseline records
        auth_status = json.dumps({
            "name": email.split("@")[0],
            "email": email,
            "apiKey": f"ya29.mock_key_for_{email}"
        })
        uss_payload = build_mock_uss_token_payload(email)
        
        records = [
            ("antigravityAuthStatus", auth_status),
            ("antigravityOnboarding", "true"),
            ("antigravityUnifiedStateSync.oauthToken", uss_payload),
            ("jetskiStateSync.agentManagerInitState", "stale_agent_session_123"),
            ("antigravityUnifiedStateSync.userStatus", json.dumps({"quota": 100, "models": ["gemini-pro"]})),
            ("antigravity.mockExtraSetting", "preserved_setting_value"),
        ]
        cursor.executemany("INSERT INTO ItemTable (key, value) VALUES (?, ?)", records)
        conn.commit()
    finally:
        conn.close()

def create_isolated_environment(
    base_dir: Path,
    active_email: str = "active@example.com"
) -> Dict[str, Any]:
    """
    Creates a full mock Antigravity directory hierarchy in base_dir.
    Sets up all 4 stores:
      1. state.vscdb (SQLite ItemTable)
      2. mock_keychain.json (MockKeychainStore)
      3. credential-user-scope (17-byte session hash)
      4. app_storage.json (username & GCP TOS)
      5. HTTPStorages/com.google.antigravity
      6. accounts.json roster
    """
    test_root = base_dir / "AntigravityHome"
    user_global_storage = test_root / "User" / "globalStorage"
    user_global_storage.mkdir(parents=True, exist_ok=True)

    db_path = user_global_storage / "state.vscdb"
    create_sqlite_state_db(db_path, email=active_email)

    scope_path = test_root / "credential-user-scope"
    scope_path.write_text("ua268fd8d78746986", encoding="utf-8")

    app_storage_path = test_root / "app_storage.json"
    app_storage_data = {
        "telemetry.sqm.machineId": "mock-machine-id-1234567890",
        "theme": "vs-dark",
        "agy.onboarding.lastLoginUsername": active_email,
        "agy.onboarding.lastLoginIsGcpTos": "false",
        "window.zoomLevel": 0
    }
    app_storage_path.write_text(json.dumps(app_storage_data, indent=2), encoding="utf-8")

    # Network cache directory
    http_storage_path = test_root / "HTTPStorages" / "com.google.antigravity"
    http_storage_path.mkdir(parents=True, exist_ok=True)
    (http_storage_path / "mock_http2_session.bin").write_bytes(b"MOCK_HTTP2_TICKETS")

    # Mock Keychain file
    keychain_file = test_root / "mock_keychain.json"
    payload = build_mock_keychain_payload(active_email)
    keychain_data = {
        "gemini:antigravity": payload,
        "antigravity:antigravity": payload
    }
    keychain_file.write_text(json.dumps(keychain_data, indent=2), encoding="utf-8")

    # Mock accounts roster
    accounts_file = test_root / "accounts.json"
    source_accounts = FIXTURES_DIR / "accounts.json"
    if source_accounts.exists():
        shutil.copy2(source_accounts, accounts_file)
    else:
        accounts_file.write_text(json.dumps([
            {"email": "active@example.com", "refresh_token": "mock_rt_active", "added_at": "2026-10-01T10:00:00"},
            {"email": "target@example.com", "refresh_token": "mock_rt_target", "added_at": "2026-10-01T11:00:00"},
            {"email": "expired@example.com", "refresh_token": "mock_invalid_grant_token", "added_at": "2026-10-01T12:00:00"},
        ], indent=2), encoding="utf-8")

    return {
        "root": test_root,
        "db_path": db_path,
        "scope_path": scope_path,
        "app_storage_path": app_storage_path,
        "http_storage_path": http_storage_path,
        "keychain_file": keychain_file,
        "accounts_file": accounts_file,
        "active_email": active_email,
    }
