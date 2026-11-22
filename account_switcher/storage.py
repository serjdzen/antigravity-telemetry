"""Antigravity Multi-Store Storage Coordinator and Protobuf USS Serializer.

Manages state synchronization across all 4 local storage layers on macOS:
1. SQLite Database: `~/Library/Application Support/Antigravity/User/globalStorage/state.vscdb`
   - Protobuf USS Topic: `antigravityUnifiedStateSync.oauthToken`
   - Auth Status: `antigravityAuthStatus`
   - Onboarding Status: `antigravityOnboarding`
   - Stale key eviction: `jetskiStateSync.agentManagerInitState`, `antigravityUnifiedStateSync.userStatus`
2. App Storage JSON: `~/Library/Application Support/Antigravity/app_storage.json`
   - `agy.onboarding.lastLoginUsername`, `agy.onboarding.lastLoginIsGcpTos`
3. Session Scope Cache: `~/Library/Application Support/Antigravity/credential-user-scope` (Deleted)
4. Network Connection Pool: `~/Library/HTTPStorages/com.google.antigravity` (Deleted)

Enforces fail-safe assertion `assert_environment_safety()` preventing test/dry-run mutation of live user paths.
Pure Python standard library (zero pip dependencies).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple, Union


class SafetyViolationError(Exception):
    """Raised when an operation attempts to mutate live production paths in test/simulation mode."""
    pass


# Production default paths on macOS
LIVE_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Antigravity"
LIVE_HTTP_STORAGES = Path.home() / "Library" / "HTTPStorages" / "com.google.antigravity"


@dataclass
class AntigravityPaths:
    """Resolved file paths for Antigravity configuration and storage."""
    root: Path
    state_vscdb: Path
    app_storage: Path
    credential_scope: Path
    http_storages: Path


class AntigravityPathResolver:
    """Resolves Antigravity paths respecting test overrides and environment variables."""

    @staticmethod
    def resolve(test_root: Optional[Union[str, Path]] = None) -> AntigravityPaths:
        env_root = os.environ.get("ANTIGRAVITY_TEST_ROOT")
        effective_root = test_root or env_root

        if effective_root:
            root_path = Path(effective_root).expanduser().resolve()
            # If test fixture has a nested Antigravity folder, use it
            if (root_path / "Antigravity").is_dir():
                app_dir = root_path / "Antigravity"
                http_dir = root_path / "HTTPStorages" / "com.google.antigravity"
            else:
                app_dir = root_path
                http_dir = root_path.parent / "HTTPStorages" / "com.google.antigravity"

            return AntigravityPaths(
                root=app_dir,
                state_vscdb=app_dir / "User" / "globalStorage" / "state.vscdb",
                app_storage=app_dir / "app_storage.json",
                credential_scope=app_dir / "credential-user-scope",
                http_storages=http_dir,
            )

        return AntigravityPaths(
            root=LIVE_APP_SUPPORT,
            state_vscdb=LIVE_APP_SUPPORT / "User" / "globalStorage" / "state.vscdb",
            app_storage=LIVE_APP_SUPPORT / "app_storage.json",
            credential_scope=LIVE_APP_SUPPORT / "credential-user-scope",
            http_storages=LIVE_HTTP_STORAGES,
        )


def assert_environment_safety(
    target_path: Union[str, Path],
    dry_run: bool = False,
    test_root: Optional[Union[str, Path]] = None,
) -> None:
    """Enforces fail-safe invariant preventing mutation of live user paths during simulation or testing.

    When `dry_run=True`, allows inspecting and simulating operations without raising
    `SafetyViolationError` on live paths as long as no real filesystem write is performed.

    Raises `SafetyViolationError` if `target_path` resolves to live user paths while running
    in simulation or test mode (`ANTIGRAVITY_DRY_RUN=1`, `ANTIGRAVITY_TEST_ROOT`, or `test_root` provided)
    and `dry_run` is False.
    """
    if dry_run:
        return

    is_simulation = (
        os.environ.get("ANTIGRAVITY_DRY_RUN") == "1"
        or os.environ.get("ANTIGRAVITY_TEST_ROOT") is not None
        or test_root is not None
    )

    if not is_simulation:
        return

    try:
        target = Path(target_path).expanduser().resolve()
        live_app = LIVE_APP_SUPPORT.expanduser().resolve()
        live_http = LIVE_HTTP_STORAGES.expanduser().resolve()
    except Exception:
        return

    # Check whether target is within live production paths
    if target == live_app or live_app in target.parents:
        raise SafetyViolationError(
            f"Safety Violation: Attempted mutation of live production path '{target}' "
            f"while running in test/simulation mode! (dry_run={dry_run}, test_root={test_root})"
        )

    if target == live_http or live_http in target.parents:
        raise SafetyViolationError(
            f"Safety Violation: Attempted mutation of live HTTPStorages path '{target}' "
            f"while running in test/simulation mode! (dry_run={dry_run}, test_root={test_root})"
        )


# ==============================================================================
# Pure Python Protobuf Wire-Format Serializer for Unified State Sync (USS)
# ==============================================================================

def encode_varint(value: int) -> bytes:
    """Encodes an integer into Protobuf varint format."""
    if value < 0:
        # 64-bit two's complement for negative integers
        value = (1 << 64) + value
    bits = value & 0x7F
    value >>= 7
    result = bytearray()
    while value:
        result.append(0x80 | bits)
        bits = value & 0x7F
        value >>= 7
    result.append(bits)
    return bytes(result)


def decode_varint(data: bytes, offset: int = 0) -> Tuple[int, int]:
    """Decodes a Protobuf varint from data at given offset. Returns (value, new_offset)."""
    result = 0
    shift = 0
    idx = offset
    while idx < len(data):
        b = data[idx]
        idx += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, idx
        shift += 7
        if shift >= 70:
            raise ValueError("Varint too long")
    raise ValueError("Unexpected end of data while decoding varint")


def encode_length_delimited(field_number: int, payload: bytes) -> bytes:
    """Encodes wire type 2 (length-delimited): tag + varint(len) + bytes."""
    tag = (field_number << 3) | 2
    return encode_varint(tag) + encode_varint(len(payload)) + payload


def encode_varint_field(field_number: int, value: int) -> bytes:
    """Encodes wire type 0 (varint): tag + varint(value)."""
    tag = (field_number << 3) | 0
    return encode_varint(tag) + encode_varint(value)


def encode_oauth_token_info(
    access_token: str,
    token_type: str,
    refresh_token: str,
    expiry_unix: int,
) -> bytes:
    """Serializes binary Protobuf message `OAuthTokenInfo`:
    - Field 1 (string): access_token
    - Field 2 (string): token_type ("Bearer")
    - Field 3 (string): refresh_token
    - Field 4 (varint / int64): expiry (unix epoch seconds)
    """
    parts = [
        encode_length_delimited(1, access_token.encode("utf-8")),
        encode_length_delimited(2, token_type.encode("utf-8")),
        encode_length_delimited(3, refresh_token.encode("utf-8")),
        encode_varint_field(4, int(expiry_unix)),
    ]
    return b"".join(parts)


def decode_oauth_token_info(raw_bytes: bytes) -> Dict[str, Any]:
    """Parses binary Protobuf message `OAuthTokenInfo` into a dictionary."""
    offset = 0
    res: Dict[str, Any] = {}
    while offset < len(raw_bytes):
        tag, offset = decode_varint(raw_bytes, offset)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            val, offset = decode_varint(raw_bytes, offset)
            if field_num == 4:
                res["expiry"] = val
        elif wire_type == 2:
            length, offset = decode_varint(raw_bytes, offset)
            data = raw_bytes[offset: offset + length]
            offset += length
            if field_num == 1:
                res["access_token"] = data.decode("utf-8", errors="replace")
            elif field_num == 2:
                res["token_type"] = data.decode("utf-8", errors="replace")
            elif field_num == 3:
                res["refresh_token"] = data.decode("utf-8", errors="replace")
        else:
            raise ValueError(f"Unsupported wire type {wire_type} in OAuthTokenInfo")
    return res


def encode_uss_topic_payload(
    access_token: str,
    refresh_token: str,
    expiry_unix: int,
    project: str = "",
) -> str:
    """Encodes the full Unified State Sync (USS) Protobuf payload for `antigravityUnifiedStateSync.oauthToken`.

    Contains two key-value entries:
    1. `authStateWithContextSentinelKey`:
       JSON `{"state":"signedIn","context":{"project":"","showProjectError":false}}`
    2. `oauthTokenInfoSentinelKey`:
       Base64 of nested binary `OAuthTokenInfo` protobuf.

    Returns the Base64-encoded string representation stored in `state.vscdb` ItemTable.
    """
    # 1. Nested binary Protobuf OAuthTokenInfo
    oauth_token_bytes = encode_oauth_token_info(
        access_token=access_token,
        token_type="Bearer",
        refresh_token=refresh_token,
        expiry_unix=expiry_unix,
    )
    oauth_token_b64 = base64.b64encode(oauth_token_bytes).decode("ascii")

    # 2. JSON authStateWithContextSentinelKey
    auth_state_json = json.dumps(
        {
            "state": "signedIn",
            "context": {
                "project": project,
                "showProjectError": False,
            },
        },
        separators=(",", ":"),
    )

    # 3. Build Protobuf map entries (field 1 repeated)
    # Entry schema: field 1 = string key, field 2 = string value
    def make_entry(key: str, val: str) -> bytes:
        k_bytes = key.encode("utf-8")
        v_bytes = val.encode("utf-8")
        entry_payload = (
            encode_length_delimited(1, k_bytes) +
            encode_length_delimited(2, v_bytes)
        )
        return encode_length_delimited(1, entry_payload)

    uss_bytes = (
        make_entry("authStateWithContextSentinelKey", auth_state_json) +
        make_entry("oauthTokenInfoSentinelKey", oauth_token_b64)
    )

    return base64.b64encode(uss_bytes).decode("ascii")


def decode_uss_topic_payload(b64_payload: str) -> Dict[str, str]:
    """Decodes a Base64-encoded USS topic protobuf payload into its key-value map."""
    raw_bytes = base64.b64decode(b64_payload)
    offset = 0
    entries: Dict[str, str] = {}
    while offset < len(raw_bytes):
        tag, offset = decode_varint(raw_bytes, offset)
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 2:
            entry_len, offset = decode_varint(raw_bytes, offset)
            entry_bytes = raw_bytes[offset: offset + entry_len]
            offset += entry_len

            # Decode entry (field 1: key, field 2: value)
            e_offset = 0
            e_key = ""
            e_val = ""
            while e_offset < len(entry_bytes):
                e_tag, e_offset = decode_varint(entry_bytes, e_offset)
                e_fn = e_tag >> 3
                e_wt = e_tag & 0x07
                if e_wt == 2:
                    flen, e_offset = decode_varint(entry_bytes, e_offset)
                    fbytes = entry_bytes[e_offset: e_offset + flen]
                    e_offset += flen
                    if e_fn == 1:
                        e_key = fbytes.decode("utf-8", errors="replace")
                    elif e_fn == 2:
                        e_val = fbytes.decode("utf-8", errors="replace")
                else:
                    break
            if e_key:
                entries[e_key] = e_val
        else:
            break
    return entries


# ==============================================================================
# Storage Operations: SQLite state.vscdb, app_storage.json, and cache files
# ==============================================================================

def backup_vscdb(
    vscdb_path: Path,
    dry_run: bool = False,
    test_root: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """Creates a `.bak` copy of `state.vscdb` before any mutations occur.

    Returns the backup path if created, or None if the source database does not exist.
    """
    if dry_run:
        return vscdb_path.with_suffix(".vscdb.bak")

    if not vscdb_path.exists():
        return None

    backup_path = vscdb_path.with_suffix(".vscdb.bak")
    assert_environment_safety(backup_path, dry_run=dry_run, test_root=test_root)
    shutil.copy2(vscdb_path, backup_path)
    return backup_path


def update_vscdb_item_table(
    vscdb_path: Path,
    email: str,
    access_token: str,
    refresh_token: str,
    expires_in: int = 3599,
    dry_run: bool = False,
    test_root: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Updates `state.vscdb` ItemTable:
    1. Backs up database to `.bak`.
    2. Writes USS topic `antigravityUnifiedStateSync.oauthToken`.
    3. Writes `antigravityAuthStatus` with email and token.
    4. Writes `antigravityOnboarding` = "true".
    5. Deletes stale keys `jetskiStateSync.agentManagerInitState` and `antigravityUnifiedStateSync.userStatus`.

    Returns dictionary summarizing executed operations.
    """
    assert_environment_safety(vscdb_path, dry_run=dry_run, test_root=test_root)

    expiry_unix = int(time.time()) + expires_in
    uss_payload = encode_uss_topic_payload(
        access_token=access_token,
        refresh_token=refresh_token,
        expiry_unix=expiry_unix,
    )

    username = email.split("@")[0] if "@" in email else email
    auth_status_json = json.dumps(
        {
            "name": username,
            "email": email,
            "apiKey": access_token,
        },
        separators=(",", ":"),
    )

    updates = {
        "antigravityUnifiedStateSync.oauthToken": uss_payload,
        "antigravityAuthStatus": auth_status_json,
        "antigravityOnboarding": "true",
    }
    deletions = [
        "jetskiStateSync.agentManagerInitState",
        "antigravityUnifiedStateSync.userStatus",
    ]

    if dry_run:
        return {
            "backup_created": False,
            "dry_run": True,
            "keys_updated": list(updates.keys()),
            "keys_deleted": deletions,
        }

    # Ensure parent directory exists
    vscdb_path.parent.mkdir(parents=True, exist_ok=True)

    # Perform backup
    backup_file = backup_vscdb(vscdb_path, dry_run=dry_run, test_root=test_root)

    # SQLite transaction
    conn = sqlite3.connect(str(vscdb_path))
    try:
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS ItemTable (key TEXT PRIMARY KEY, value TEXT);")

        for key, val in updates.items():
            cursor.execute("INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?);", (key, val))

        for del_key in deletions:
            cursor.execute("DELETE FROM ItemTable WHERE key = ?;", (del_key,))

        conn.commit()
    finally:
        conn.close()

    return {
        "backup_created": backup_file is not None,
        "backup_path": str(backup_file) if backup_file else None,
        "dry_run": False,
        "keys_updated": list(updates.keys()),
        "keys_deleted": deletions,
    }


def update_app_storage_json(
    app_storage_path: Path,
    email: str,
    dry_run: bool = False,
    test_root: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Updates `app_storage.json`:
    - `agy.onboarding.lastLoginUsername`: target email
    - `agy.onboarding.lastLoginIsGcpTos`: false (or preserve format)

    Safely creates file and parent directories if missing.
    """
    assert_environment_safety(app_storage_path, dry_run=dry_run, test_root=test_root)

    data: Dict[str, Any] = {}
    if app_storage_path.exists():
        try:
            with open(app_storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    prev_email = data.get("agy.onboarding.lastLoginUsername")
    data["agy.onboarding.lastLoginUsername"] = email
    # Keep type consistent: string "false" if previously string, otherwise boolean False
    if isinstance(data.get("agy.onboarding.lastLoginIsGcpTos"), str):
        data["agy.onboarding.lastLoginIsGcpTos"] = "false"
    else:
        data["agy.onboarding.lastLoginIsGcpTos"] = False

    if dry_run:
        return {
            "dry_run": True,
            "previous_email": prev_email,
            "updated_email": email,
        }

    app_storage_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = app_storage_path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp_path.replace(app_storage_path)

    return {
        "dry_run": False,
        "previous_email": prev_email,
        "updated_email": email,
    }


def delete_credential_scope(
    scope_path: Path,
    dry_run: bool = False,
    test_root: Optional[Union[str, Path]] = None,
) -> bool:
    """Unlinks `credential-user-scope` session hash file.

    Returns True if file was deleted (or would be deleted in dry_run), False if not present.
    """
    assert_environment_safety(scope_path, dry_run=dry_run, test_root=test_root)

    if not scope_path.exists():
        return False

    if dry_run:
        return True

    try:
        try:
            scope_path.unlink(missing_ok=True)
        except PermissionError:
            os.chmod(str(scope_path), 0o600)
            scope_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def clear_http_storages_cache(
    http_storages_path: Path,
    dry_run: bool = False,
    test_root: Optional[Union[str, Path]] = None,
) -> bool:
    """Purges `~/Library/HTTPStorages/com.google.antigravity` connection pool cache.

    Returns True if directory was purged (or would be purged in dry_run), False if not present.
    """
    assert_environment_safety(http_storages_path, dry_run=dry_run, test_root=test_root)

    if not http_storages_path.exists():
        return False

    if dry_run:
        return True

    try:
        if http_storages_path.is_dir():
            shutil.rmtree(http_storages_path, ignore_errors=True)
        else:
            http_storages_path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


class AntigravityStorageManager:
    """High-level multi-store coordinator managing Antigravity 2.0 persistence layers."""

    def __init__(self, test_root: Optional[Union[str, Path]] = None, dry_run: bool = False):
        self.test_root = test_root
        self.dry_run = dry_run
        self.paths = AntigravityPathResolver.resolve(test_root)
        self._prev_username: Optional[str] = None
        self._prev_app_storage_bytes: Optional[bytes] = None
        self._prev_scope_bytes: Optional[bytes] = None

    def rollback(self) -> Dict[str, Any]:
        """Rolls back storage layers after a mid-commit failure:
        1. Restores `state.vscdb` from `state.vscdb.bak` if backup exists.
        2. Restores `app_storage.json` from pre-commit snapshot (or previous username).
        3. Restores `credential-user-scope` if it existed before commit.
        """
        if self.dry_run:
            return {"rolled_back": True, "dry_run": True}

        rolled_back_db = False
        rolled_back_app_storage = False
        rolled_back_scope = False

        bak_path = self.paths.state_vscdb.with_suffix(".vscdb.bak")
        if not bak_path.exists():
            alt_bak = self.paths.state_vscdb.parent / (self.paths.state_vscdb.name + ".bak")
            if alt_bak.exists():
                bak_path = alt_bak

        if bak_path.exists():
            assert_environment_safety(self.paths.state_vscdb, dry_run=False, test_root=self.test_root)
            shutil.copy2(bak_path, self.paths.state_vscdb)
            rolled_back_db = True

        if self._prev_app_storage_bytes is not None:
            assert_environment_safety(self.paths.app_storage, dry_run=False, test_root=self.test_root)
            temp_path = self.paths.app_storage.with_suffix(".tmp")
            temp_path.write_bytes(self._prev_app_storage_bytes)
            temp_path.replace(self.paths.app_storage)
            rolled_back_app_storage = True
        elif self._prev_username is not None and self.paths.app_storage.exists():
            assert_environment_safety(self.paths.app_storage, dry_run=False, test_root=self.test_root)
            try:
                with open(self.paths.app_storage, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
            data["agy.onboarding.lastLoginUsername"] = self._prev_username
            temp_path = self.paths.app_storage.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_path.replace(self.paths.app_storage)
            rolled_back_app_storage = True

        if self._prev_scope_bytes is not None:
            assert_environment_safety(self.paths.credential_scope, dry_run=False, test_root=self.test_root)
            self.paths.credential_scope.parent.mkdir(parents=True, exist_ok=True)
            self.paths.credential_scope.write_bytes(self._prev_scope_bytes)
            rolled_back_scope = True

        return {
            "rolled_back": True,
            "database_restored": rolled_back_db,
            "app_storage_restored": rolled_back_app_storage,
            "scope_restored": rolled_back_scope,
        }

    def commit_all(
        self,
        email: str,
        access_token: str,
        refresh_token: str,
        expires_in: int = 3599,
        update_vscdb: bool = False,
        clear_http_cache: bool = False,
    ) -> Dict[str, Any]:
        """Atomically commits active Antigravity 2.0 storage layers:
        1. `app_storage.json` (`agy.onboarding.lastLoginUsername`)
        2. `credential-user-scope` (unlinked so `language_server` recalculates PinnedScope)

        Legacy `state.vscdb` and `HTTPStorages` are skipped by default because Antigravity 2.0
        does not read auth from `state.vscdb` (verified via V3 diff).
        """
        if self.paths.app_storage.exists():
            try:
                self._prev_app_storage_bytes = self.paths.app_storage.read_bytes()
                app_data = json.loads(self._prev_app_storage_bytes.decode("utf-8"))
                self._prev_username = app_data.get("agy.onboarding.lastLoginUsername")
            except Exception:
                self._prev_app_storage_bytes = None
                self._prev_username = None
        else:
            self._prev_app_storage_bytes = None
            self._prev_username = None

        if self.paths.credential_scope.exists():
            try:
                self._prev_scope_bytes = self.paths.credential_scope.read_bytes()
            except Exception:
                self._prev_scope_bytes = None
        else:
            self._prev_scope_bytes = None

        try:
            vscdb_res: Dict[str, Any] = {"skipped": True, "dry_run": self.dry_run}
            if update_vscdb:
                vscdb_res = update_vscdb_item_table(
                    vscdb_path=self.paths.state_vscdb,
                    email=email,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_in=expires_in,
                    dry_run=self.dry_run,
                    test_root=self.test_root,
                )

            app_storage_res = update_app_storage_json(
                app_storage_path=self.paths.app_storage,
                email=email,
                dry_run=self.dry_run,
                test_root=self.test_root,
            )

            scope_deleted = delete_credential_scope(
                scope_path=self.paths.credential_scope,
                dry_run=self.dry_run,
                test_root=self.test_root,
            )

            http_cleared = False
            if clear_http_cache:
                http_cleared = clear_http_storages_cache(
                    http_storages_path=self.paths.http_storages,
                    dry_run=self.dry_run,
                    test_root=self.test_root,
                )

            return {
                "vscdb": vscdb_res,
                "app_storage": app_storage_res,
                "scope_deleted": scope_deleted,
                "http_cache_cleared": http_cleared,
                "dry_run": self.dry_run,
            }
        except Exception:
            self.rollback()
            raise
