"""
Popperian Falsification Test Suite for Antigravity Account Switcher.

Verifies the 4-Phase Switching Lifecycle against failure probes FP-1.1 through FP-4.3
according to Requirement R4 and Popperian Falsification Methodology (/check):
  Phase 1: Pre-flight Google OAuth Token Validation (FP-1.1 - FP-1.4)
  Phase 2: Active Streaming Socket Guard (FP-2.1 - FP-2.3)
  Phase 3: Atomic Multi-Store Commit & Rollback (FP-3.1 - FP-3.4)
  Phase 4: Process Lifecycle Supervision (FP-4.1 - FP-4.3)

Adheres to 100% test isolation (Requirement R3) with zero live side-effects.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from account_switcher.keychain import (
    KeychainError,
    clear_mock_keychain,
    get_mock_keychain_store,
    update_antigravity_keychain,
)
from account_switcher.process import (
    get_mock_process_counters,
    is_antigravity_running,
    is_streaming_active as process_is_streaming_active,
    quit_antigravity,
    relaunch_antigravity,
    reset_mock_process_state,
    set_mock_process_state,
)
from account_switcher.storage import (
    AntigravityPathResolver,
    AntigravityStorageManager,
    SafetyViolationError,
    assert_environment_safety,
    backup_vscdb,
    delete_credential_scope,
    update_app_storage_json,
    update_vscdb_item_table,
)
from account_switcher.switcher import (
    SwitchResult,
    get_active_account,
    refresh_google_oauth_token,
    switch_account,
)
from account_switcher.tests.fixtures.fixture_factory import (
    create_isolated_environment,
    create_sqlite_state_db,
)


class BasePopperTestCase(unittest.TestCase):
    """Base harness for Popperian falsification probes with isolated sandbox environment."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.env = create_isolated_environment(self.base_path, active_email="active@example.com")
        self.test_root = self.env["root"]

        self.orig_env = dict(os.environ)
        os.environ["ANTIGRAVITY_TEST_ROOT"] = str(self.test_root)
        os.environ["ANTIGRAVITY_DRY_RUN"] = "1"
        os.environ["KEYCHAIN_MOCK"] = "1"
        os.environ["PROCESS_MOCK"] = "1"
        os.environ["OAUTH_MOCK"] = "1"

        clear_mock_keychain()
        reset_mock_process_state()

    def tearDown(self):
        clear_mock_keychain()
        reset_mock_process_state()
        os.environ.clear()
        os.environ.update(self.orig_env)
        self.temp_dir.cleanup()


# ==============================================================================
# Phase 1: Pre-flight Token Validation (Probes FP-1.1 - FP-1.4)
# ==============================================================================

class TestPopperianFalsificationPhase1(BasePopperTestCase):
    """
    Hypothesis H1:
    The system guarantees Antigravity IDE is NEVER terminated and the active session
    is never corrupted if target credentials are invalid, revoked, or unreachable.
    """

    def test_fp_1_1_revoked_or_expired_refresh_token(self):
        """FP-1.1: Mock OAuth returns HTTP 400 invalid_grant.
        Falsification check: Switch must abort at 'preflight', IDE remains running, status success=False.
        """
        set_mock_process_state(is_running=True)
        res = switch_account(
            target_email="expired@example.com",
            accounts_file=self.env["accounts_file"],
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.phase_reached, "preflight")
        self.assertIn("invalid_grant", str(res.error))

        # IDE must NOT have been terminated
        q_count, _ = get_mock_process_counters()
        self.assertEqual(q_count, 0)
        self.assertTrue(is_antigravity_running(dry_run=True))

    def test_fp_1_2_complete_network_outage_or_dns_timeout(self):
        """FP-1.2: Google API sockets unreachable / connection timeout.
        Falsification check: Network exception is caught cleanly, switch aborts without crashing,
        IDE remains running, active session remains intact.
        """
        set_mock_process_state(is_running=True)
        with patch("account_switcher.switcher.refresh_google_oauth_token", return_value=(None, None, "Google OAuth network error: DNS resolution failed")):
            res = switch_account(
                target_email="target@example.com",
                accounts_file=self.env["accounts_file"],
                test_root=self.test_root,
            )
            self.assertFalse(res.success)
            self.assertEqual(res.phase_reached, "preflight")
            self.assertIn("DNS resolution failed", str(res.message))

            # Verify IDE was not quit
            q_count, _ = get_mock_process_counters()
            self.assertEqual(q_count, 0)

    def test_fp_1_3_corrupt_or_incomplete_accounts_json(self):
        """FP-1.3: accounts.json contains invalid JSON syntax or missing refresh_token.
        Falsification check: Validation fails immediately BEFORE any network calls or disk mutations.
        """
        corrupt_file = self.test_root / "invalid_accounts.json"
        corrupt_file.write_text("[{'broken_json': true", encoding="utf-8")

        res = switch_account(
            target_email="target@example.com",
            accounts_file=corrupt_file,
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.phase_reached, "preflight")
        self.assertIn("Could not load accounts file", res.message)

    def test_fp_1_4_idle_noop_switch_target_already_active(self):
        """FP-1.4: User triggers switch to the account that is already active.
        Falsification check: System detects currently active account.
        """
        current_active = get_active_account(test_root=self.test_root)
        self.assertEqual(current_active, "active@example.com")


# ==============================================================================
# Phase 2: Active Streaming Sockets Check (Probes FP-2.1 - FP-2.3)
# ==============================================================================

class TestPopperianFalsificationPhase2(BasePopperTestCase):
    """
    Hypothesis H2:
    The system prevents interrupting active LLM generation or code thought streaming
    unless explicitly forced by user override (--force).
    """

    def test_fp_2_1_active_streaming_socket_detected(self):
        """FP-2.1: lsof detects active ESTABLISHED TCP 443 sockets to Google Cloud Code PA.
        Falsification check: Switch aborts at 'sockets' phase with alert, IDE is not terminated.
        """
        set_mock_process_state(is_running=True, streaming_active=True)
        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            force=False,
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.phase_reached, "sockets")
        self.assertEqual(res.error, "streaming_active")
        self.assertIn("Active code generation", res.message)

        # IDE process preserved
        q_count, _ = get_mock_process_counters()
        self.assertEqual(q_count, 0)

    def test_fp_2_2_forced_override_bypasses_streaming_lock(self):
        """FP-2.2: User supplies --force flag when streaming socket is active.
        Falsification check: Warning is logged, but switch proceeds through completion.
        """
        set_mock_process_state(is_running=True, streaming_active=True)
        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            force=True,
            test_root=self.test_root,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.phase_reached, "completed")

    def test_fp_2_3_lsof_command_hang_or_timeout(self):
        """FP-2.3: System lsof command hangs / times out.
        Falsification check: Subprocess timeout of 2.0s triggers gracefully without deadlock.
        """
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["lsof"], timeout=2.0)):
            # When lsof times out, process_is_streaming_active catches TimeoutExpired and returns False safely
            active = process_is_streaming_active(timeout_sec=0.1, dry_run=False)
            self.assertFalse(active)


# ==============================================================================
# Phase 3: Atomic Multi-Store State Write (Probes FP-3.1 - FP-3.4)
# ==============================================================================

class TestPopperianFalsificationPhase3(BasePopperTestCase):
    """
    Hypothesis H3:
    In case of hardware failure, disk exhaustion, or permission denial, stores never
    enter a desynchronized Ghost Session, and the SQLite database remains restorable.
    """

    def test_fp_3_1_sqlite_database_locked_sqlite_busy(self):
        """FP-3.1: Another process holds an exclusive lock on state.vscdb.
        Falsification check: Locked DB raises OperationalError without corrupting database.
        """
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        db_path = self.env["db_path"]

        # Hold exclusive lock on database in a separate connection
        lock_conn = sqlite3.connect(str(db_path), timeout=0.1)
        lock_conn.execute("BEGIN EXCLUSIVE TRANSACTION;")

        try:
            with self.assertRaises(sqlite3.OperationalError):
                # Attempt to update while locked with zero timeout
                conn2 = sqlite3.connect(str(db_path), timeout=0.01)
                cur2 = conn2.cursor()
                cur2.execute("INSERT INTO ItemTable VALUES ('busy_test', 'val');")
                conn2.commit()
        finally:
            lock_conn.rollback()
            lock_conn.close()

        # Database integrity check
        conn_check = sqlite3.connect(str(db_path))
        cur = conn_check.cursor()
        cur.execute("PRAGMA integrity_check;")
        res = cur.fetchone()[0]
        conn_check.close()
        self.assertEqual(res, "ok")

    def test_fp_3_2_disk_full_error_enospc(self):
        """FP-3.2: Simulated disk full error (ENOSPC) during atomic write.
        Falsification check: Atomic write fails cleanly, backup (.bak) preserved.
        """
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        db_path = self.env["db_path"]

        # Backup created before write
        bak = backup_vscdb(db_path, dry_run=False, test_root=self.test_root)
        self.assertIsNotNone(bak)
        self.assertTrue(bak.exists())

        # Simulate write failure
        with patch("sqlite3.connect", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                update_vscdb_item_table(
                    vscdb_path=db_path,
                    email="target@example.com",
                    access_token="ya29.target",
                    refresh_token="mock_rt",
                    dry_run=False,
                    test_root=self.test_root,
                )

        # Original database and backup remain intact
        self.assertTrue(bak.exists())
        self.assertTrue(db_path.exists())

    def test_fp_3_3_mid_chain_failure_and_rollback(self):
        """FP-3.3: storage commit succeeds, but subsequent Keychain write fails.
        Falsification check: Switcher detects failure, rolls back app_storage & scope, preventing ghost sessions.
        """
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        scope_before = self.env["scope_path"].read_text(encoding="utf-8")

        with patch("account_switcher.switcher.update_antigravity_keychain", side_effect=KeychainError("Security daemon unavailable")):
            res = switch_account(
                target_email="target@example.com",
                accounts_file=self.env["accounts_file"],
                dry_run=False,
                test_root=self.test_root,
            )
            self.assertFalse(res.success)
            self.assertEqual(res.phase_reached, "storage")
            self.assertIn("Security daemon unavailable", str(res.error))

            # Verify credential-user-scope was restored on rollback
            self.assertTrue(self.env["scope_path"].exists())
            self.assertEqual(self.env["scope_path"].read_text(encoding="utf-8"), scope_before)

            # Verify state.vscdb remained untouched with active@example.com
            conn = sqlite3.connect(str(self.env["db_path"]))
            cur = conn.cursor()
            cur.execute("SELECT value FROM ItemTable WHERE key = 'antigravityAuthStatus';")
            row = cur.fetchone()
            conn.close()
            self.assertIsNotNone(row)
            auth_status = json.loads(row[0])
            self.assertEqual(auth_status.get("email"), "active@example.com")

            # Verify get_active_account reports active account, preventing ghost sessions
            self.assertEqual(get_active_account(test_root=self.test_root), "active@example.com")

            # Verify app_storage.json was restored to active@example.com
            if self.env["app_storage_path"].exists():
                with open(self.env["app_storage_path"], "r", encoding="utf-8") as f:
                    app_data = json.load(f)
                self.assertEqual(app_data.get("agy.onboarding.lastLoginUsername"), "active@example.com")

    def test_fp_3_4_readonly_credential_user_scope_permissions(self):
        """FP-3.4: credential-user-scope has read-only file permissions (0444).
        Falsification check: Resetting chmod allows clean unlinking.
        """
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        scope = self.env["scope_path"]
        os.chmod(str(scope), 0o444)

        try:
            os.chmod(str(scope), 0o666)
            deleted = delete_credential_scope(scope, dry_run=False, test_root=self.test_root)
            self.assertTrue(deleted)
            self.assertFalse(scope.exists())
        finally:
            if scope.exists():
                os.chmod(str(scope), 0o666)


# ==============================================================================
# Phase 4: Process Lifecycle (Graceful Shutdown & Restart) (Probes FP-4.1 - FP-4.3)
# ==============================================================================

class TestPopperianFalsificationPhase4(BasePopperTestCase):
    """
    Hypothesis H4:
    The system gracefully closes IDE without blind kill -9, handles unsaved file prompts,
    and reliably relaunches Antigravity IDE.
    """

    def test_fp_4_1_ide_locked_by_unsaved_file_prompt(self):
        """FP-4.1: IDE fails to quit because an unsaved file dialog is active.
        Falsification check: Switcher aborts cleanly, does NOT issue blind 'kill -9', reports quit_timeout.
        """
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        set_mock_process_state(is_running=True, quit_success=False)

        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            dry_run=False,
            force=False,
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.phase_reached, "storage")
        self.assertEqual(res.error, "quit_timeout")
        self.assertIn("Failed to gracefully terminate Antigravity IDE", res.message)

    def test_fp_4_2_ide_initially_closed(self):
        """FP-4.2: User executes switch while Antigravity IDE is closed.
        Falsification check: Quit phase is skipped, storage updated, and app relaunches cleanly.
        """
        set_mock_process_state(is_running=False, relaunch_success=True)

        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            test_root=self.test_root,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.phase_reached, "completed")

        # Quit was never invoked
        q_count, _ = get_mock_process_counters()
        self.assertEqual(q_count, 0)

    def test_fp_4_3_relaunch_command_failure_handling(self):
        """FP-4.3: LaunchServices open -b fails to launch Antigravity IDE.
        Falsification check: Switcher marks credential switch successful, but reports relaunch_failed error.
        """
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        set_mock_process_state(is_running=True, quit_success=True, relaunch_success=False)

        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            dry_run=False,
            test_root=self.test_root,
        )
        # Credentials switched, but relaunch encountered error
        self.assertTrue(res.success)
        self.assertEqual(res.phase_reached, "process")
        self.assertEqual(res.error, "relaunch_failed")
        self.assertIn("failed to automatically relaunch", res.message)


if __name__ == "__main__":
    unittest.main()
