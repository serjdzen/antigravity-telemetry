"""macOS Keychain management for Google Antigravity Account Switcher.

Provides secure credential storage in macOS Keychain via `/usr/bin/security`
and a built-in Mock Keychain provider for dry-run simulation and automated testing.
Zero external dependencies (pure Python standard library).
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone, timedelta
import json
import os
import secrets
import subprocess
from typing import Any, Dict, Optional, Tuple


KEYCHAIN_SERVICE = "gemini"
KEYCHAIN_ACCOUNT = "antigravity"
KEYCHAIN_TIMEOUT_SEC = 5


class KeychainError(Exception):
    """Raised when macOS Keychain operations fail."""
    pass


# Global in-memory store for mock operations (KEYCHAIN_MOCK=1 or dry_run=True)
_MOCK_KEYCHAIN_STORE: Dict[Tuple[str, str], str] = {}


def is_mock_enabled(dry_run: bool = False) -> bool:
    """Uses in-memory mock store during dry-run or test isolation."""
    return (
        dry_run
        or os.environ.get("ANTIGRAVITY_DRY_RUN") == "1"
        or os.environ.get("KEYCHAIN_MOCK") == "1"
        or os.environ.get("ANTIGRAVITY_TEST_ROOT") is not None
    )


def clear_mock_keychain() -> None:
    """Clears all entries in the mock keychain store."""
    _MOCK_KEYCHAIN_STORE.clear()


def get_mock_keychain_store() -> Dict[Tuple[str, str], str]:
    """Returns a shallow copy of the current mock keychain store."""
    return dict(_MOCK_KEYCHAIN_STORE)


def set_mock_keychain_item(service: str, account: str, payload: str) -> None:
    """Directly sets an item in the mock keychain store."""
    _MOCK_KEYCHAIN_STORE[(service, account)] = payload


def format_keychain_payload(
    access_token: str,
    refresh_token: str,
    email: str,
    expires_in: int = 3599,
    expiry_iso: Optional[str] = None,
    writer_id: Optional[str] = None,
) -> str:
    """Formats credentials into the exact go-keyring-base64 JSON structure expected by Antigravity 2.0.

    Schema:
    go-keyring-base64:<Base64(JSON)>
    where JSON is:
    {
        "token": {
            "access_token": "ya29...",
            "token_type": "Bearer",
            "refresh_token": "1//...",
            "expiry": "2026-11-21T20:25:54.061099+05:00",
            "expires_in": 3599
        },
        "auth_method": "consumer",
        "id_token_email": "target@gmail.com",
        "writer_id": "<43-char urlsafe id>"
    }
    """
    if not expiry_iso:
        # Match Go time.RFC3339Nano local-offset format written by Antigravity 2.0
        future = datetime.now(timezone.utc).astimezone() + timedelta(seconds=expires_in)
        expiry_iso = future.isoformat()

    if not writer_id:
        # 32 random bytes -> 43-char base64url string matching language_server writer_id
        writer_id = secrets.token_urlsafe(32)

    token_obj = {
        "access_token": access_token,
        "token_type": "Bearer",
        "refresh_token": refresh_token,
        "expiry": expiry_iso,
        "expires_in": expires_in,
    }

    payload_dict = {
        "token": token_obj,
        "auth_method": "consumer",
        "id_token_email": email,
        "writer_id": writer_id,
    }

    json_str = json.dumps(payload_dict, separators=(",", ":"))
    b64_str = base64.b64encode(json_str.encode("utf-8")).decode("ascii")
    return f"go-keyring-base64:{b64_str}"


def parse_keychain_payload(raw_payload: str) -> Dict[str, Any]:
    """Parses a `go-keyring-base64:...` payload back into a Python dictionary."""
    if not raw_payload.startswith("go-keyring-base64:"):
        raise KeychainError("Invalid payload prefix: expected 'go-keyring-base64:'")

    b64_part = raw_payload[len("go-keyring-base64:"):]
    try:
        decoded_bytes = base64.b64decode(b64_part)
        return json.loads(decoded_bytes.decode("utf-8"))
    except Exception as exc:
        raise KeychainError(f"Failed to decode keychain payload: {exc}") from exc


def update_generic_password(
    service: str,
    account: str,
    payload: str,
    dry_run: bool = False,
    security_bin: str = "/usr/bin/security",
) -> bool:
    """Updates or adds a generic password in macOS Keychain strictly WITHOUT `-A`.

    Passing `-A` attempts to modify the item's ACL (`change_acl`), which triggers
    macOS Keychain modal prompts when the item already exists. Matching `go-keyring`,
    we invoke `add-generic-password -U -s <service> -a <account> -w <payload>` once
    with a short timeout and no retries.

    When `dry_run=True` or `KEYCHAIN_MOCK=1`, stores in memory without touching macOS Keychain.
    """
    if is_mock_enabled(dry_run):
        _MOCK_KEYCHAIN_STORE[(service, account)] = payload
        return True

    cmd = [
        security_bin,
        "add-generic-password",
        "-U",
        "-s",
        service,
        "-a",
        account,
        "-w",
        payload,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=KEYCHAIN_TIMEOUT_SEC,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or f"exit code {proc.returncode}"
            raise KeychainError(
                f"Failed to update Keychain password for service '{service}', account '{account}': {err_msg}"
            )
        return True
    except subprocess.SubprocessError as exc:
        raise KeychainError(f"Subprocess error executing security CLI: {exc}") from exc


def find_generic_password(
    service: str,
    account: str,
    dry_run: bool = False,
    security_bin: str = "/usr/bin/security",
) -> Optional[str]:
    """Retrieves generic password from macOS Keychain.

    Returns the password payload string, or None if item not found (exit code 44).
    """
    if is_mock_enabled(dry_run):
        return _MOCK_KEYCHAIN_STORE.get((service, account))

    cmd = [
        security_bin,
        "find-generic-password",
        "-s",
        service,
        "-a",
        account,
        "-w",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        # Exit code 44 = errSecItemNotFound
        if proc.returncode == 44:
            return None
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or f"exit code {proc.returncode}"
            raise KeychainError(
                f"Error retrieving generic password for service '{service}', account '{account}': {err_msg}"
            )
        return proc.stdout.strip()
    except subprocess.SubprocessError as exc:
        raise KeychainError(f"Subprocess error executing security CLI: {exc}") from exc


def delete_generic_password(
    service: str,
    account: str,
    dry_run: bool = False,
    security_bin: str = "/usr/bin/security",
) -> bool:
    """Deletes generic password from macOS Keychain.

    Returns True if deleted, False if item did not exist.
    """
    if is_mock_enabled(dry_run):
        if (service, account) in _MOCK_KEYCHAIN_STORE:
            del _MOCK_KEYCHAIN_STORE[(service, account)]
            return True
        return False

    cmd = [
        security_bin,
        "delete-generic-password",
        "-s",
        service,
        "-a",
        account,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if proc.returncode == 44:  # item not found
            return False
        if proc.returncode != 0:
            err_msg = proc.stderr.strip() or f"exit code {proc.returncode}"
            raise KeychainError(
                f"Error deleting generic password for service '{service}', account '{account}': {err_msg}"
            )
        return True
    except subprocess.SubprocessError as exc:
        raise KeychainError(f"Subprocess error executing security CLI: {exc}") from exc


def update_antigravity_keychain(
    access_token: str,
    refresh_token: str,
    email: str,
    expires_in: int = 3599,
    dry_run: bool = False,
    writer_id: Optional[str] = None,
) -> Dict[str, bool]:
    """Updates Antigravity 2.0 credentials in Keychain (`service='gemini', account='antigravity'`).

    Antigravity 2.0 no longer uses `service='antigravity'`; writing only to `gemini`
    avoids creating stray Keychain entries with mismatched ACLs.
    """
    payload = format_keychain_payload(
        access_token=access_token,
        refresh_token=refresh_token,
        email=email,
        expires_in=expires_in,
        writer_id=writer_id,
    )

    success = update_generic_password(
        service=KEYCHAIN_SERVICE,
        account=KEYCHAIN_ACCOUNT,
        payload=payload,
        dry_run=dry_run,
    )
    return {KEYCHAIN_SERVICE: success}


class KeychainManager:
    """Object-oriented interface for Keychain operations with dry-run support."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

    def update_credentials(
        self,
        access_token: str,
        refresh_token: str,
        email: str,
        expires_in: int = 3599,
        writer_id: Optional[str] = None,
    ) -> Dict[str, bool]:
        return update_antigravity_keychain(
            access_token=access_token,
            refresh_token=refresh_token,
            email=email,
            expires_in=expires_in,
            dry_run=self.dry_run,
            writer_id=writer_id,
        )

    def get_credentials(self, service: str = KEYCHAIN_SERVICE) -> Optional[Dict[str, Any]]:
        raw = find_generic_password(service=service, account=KEYCHAIN_ACCOUNT, dry_run=self.dry_run)
        if not raw:
            return None
        return parse_keychain_payload(raw)
