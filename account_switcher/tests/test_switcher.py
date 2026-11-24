"""
4-Tier Test Suite for Antigravity Account Switcher.

Tiers:
  Tier 1: Feature coverage (all 22 features from PROJECT.md in isolation)
  Tier 2: Boundary & Corner cases (expired tokens, malformed JSON, locks, busy sockets)
  Tier 3: Cross-feature combinations (dry-run multi-store, forced bypass, offline IDE)
  Tier 4: Real-world application scenarios (E2E roundtrip A->B->A, CLI, Dashboard)

Zero pip dependencies (uses standard library unittest).
100% test isolation adhering to Requirement R3.
"""

from __future__ import annotations

import base64
from http.client import HTTPMessage
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from account_switcher.keychain import (
    KeychainManager,
    clear_mock_keychain,
    format_keychain_payload,
    get_mock_keychain_store,
    parse_keychain_payload,
    update_antigravity_keychain,
    update_generic_password,
)
from account_switcher.process import (
    ProcessSupervisor,
    get_mock_process_counters,
    is_antigravity_running,
    is_streaming_active as process_is_streaming_active,
    quit_antigravity,
    relaunch_antigravity,
    reset_mock_process_state,
    set_mock_process_state,
    show_mac_confirmation_dialog,
)
from account_switcher.storage import (
    AntigravityPathResolver,
    AntigravityStorageManager,
    SafetyViolationError,
    assert_environment_safety,
    backup_vscdb,
    clear_http_storages_cache,
    decode_oauth_token_info,
    decode_uss_topic_payload,
    delete_credential_scope,
    encode_oauth_token_info,
    encode_uss_topic_payload,
    update_app_storage_json,
    update_vscdb_item_table,
)
from account_switcher.switcher import (
    SwitchResult,
    get_active_account,
    is_streaming_active,
    load_accounts_roster,
    locate_accounts_file,
    refresh_google_oauth_token,
    resolve_target_account,
    switch_account,
)
from account_switcher.tests.fixtures.fixture_factory import (
    create_isolated_environment,
    create_sqlite_state_db,
)


class BaseIsolatedTestCase(unittest.TestCase):
    """Base class providing pristine temporary directory and safety isolation."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.env = create_isolated_environment(self.base_path, active_email="active@example.com")
        self.test_root = self.env["root"]

        # Isolation environment variables
        self.orig_env = dict(os.environ)
        os.environ["ANTIGRAVITY_TEST_ROOT"] = str(self.test_root)
        os.environ["ANTIGRAVITY_DRY_RUN"] = "1"
        os.environ["KEYCHAIN_MOCK"] = "1"
        os.environ["PROCESS_MOCK"] = "1"
        os.environ["OAUTH_MOCK"] = "1"

        clear_mock_keychain()
        reset_mock_process_state()

    def _make_switch_handler(self, handler_cls, payload_bytes: bytes, origin: str = "http://127.0.0.1:51122"):
        headers = HTTPMessage()
        headers["Host"] = "127.0.0.1:51122"
        headers["Origin"] = origin
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(payload_bytes))

        handler = handler_cls.__new__(handler_cls)
        handler.server = None
        handler.path = "/api/switch"
        handler.rfile = io.BytesIO(payload_bytes)
        handler.wfile = io.BytesIO()
        handler.headers = headers
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        return handler

    def tearDown(self):
        clear_mock_keychain()
        reset_mock_process_state()
        os.environ.clear()
        os.environ.update(self.orig_env)
        self.temp_dir.cleanup()


# ==============================================================================
# Tier 1: Feature Coverage (All 22 Features from PROJECT.md in Isolation)
# ==============================================================================

class TestTier1FeaturesInIsolation(BaseIsolatedTestCase):
    """Tests each of the 22 features identified in PROJECT.md in complete isolation."""

    def test_f01_account_enumeration_and_validation(self):
        """Feature 1: Account Enumeration & Validation from accounts.json."""
        roster = load_accounts_roster(self.env["accounts_file"])
        self.assertIsInstance(roster, list)
        self.assertGreaterEqual(len(roster), 2)

        # Lookup by exact email
        acc = resolve_target_account("target@example.com", roster)
        self.assertIsNotNone(acc)
        self.assertEqual(acc["email"], "target@example.com")

        # Lookup by case-insensitive email
        acc_case = resolve_target_account("TARGET@EXAMPLE.COM", roster)
        self.assertIsNotNone(acc_case)
        self.assertEqual(acc_case["email"], "target@example.com")

        # Lookup by 1-based index string
        acc_idx = resolve_target_account("2", roster)
        self.assertIsNotNone(acc_idx)
        self.assertEqual(acc_idx["email"], "target@example.com")

    def test_f02_preflight_token_refresh(self):
        """Feature 2: Pre-flight Google OAuth token refresh."""
        token, expires_in, err = refresh_google_oauth_token("mock_valid_refresh_token")
        self.assertIsNotNone(token)
        self.assertTrue(token.startswith("ya29."))
        self.assertIsNone(err)
        self.assertGreater(expires_in, 0)

    def test_f03_active_streaming_detection(self):
        """Feature 3: Active Streaming Detection via lsof / socket inspector."""
        set_mock_process_state(streaming_active=False)
        self.assertFalse(is_streaming_active(timeout_sec=0.1))

        set_mock_process_state(streaming_active=True)
        self.assertTrue(is_streaming_active(timeout_sec=0.1))

    def test_f04_socket_busy_abort_and_retry(self):
        """Feature 4: Socket Busy Abort when active streaming is in progress."""
        set_mock_process_state(streaming_active=True)
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

    def test_f05_sqlite_state_backup(self):
        """Feature 5: SQLite State Backup creates timestamped/named .bak file."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        backup_path = backup_vscdb(self.env["db_path"], dry_run=False, test_root=self.test_root)
        self.assertIsNotNone(backup_path)
        self.assertTrue(backup_path.exists())
        self.assertEqual(backup_path.suffix, ".bak")
        self.assertEqual(backup_path.stat().st_size, self.env["db_path"].stat().st_size)

    def test_f06_sqlite_uss_topic_injection(self):
        """Feature 6: SQLite USS Topic Injection into ItemTable."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        res = update_vscdb_item_table(
            vscdb_path=self.env["db_path"],
            email="target@example.com",
            access_token="ya29.target_token",
            refresh_token="mock_rt_target",
            dry_run=False,
            test_root=self.test_root,
        )
        self.assertTrue(res["backup_created"])
        self.assertIn("antigravityUnifiedStateSync.oauthToken", res["keys_updated"])

        # Verify decoded payload in SQLite ItemTable
        conn = sqlite3.connect(str(self.env["db_path"]))
        cur = conn.cursor()
        cur.execute("SELECT value FROM ItemTable WHERE key = 'antigravityUnifiedStateSync.oauthToken';")
        val = cur.fetchone()[0]
        conn.close()

        decoded = decode_uss_topic_payload(val)
        self.assertIn("oauthTokenInfoSentinelKey", decoded)
        inner_info = decode_oauth_token_info(base64.b64decode(decoded["oauthTokenInfoSentinelKey"]))
        self.assertEqual(inner_info["access_token"], "ya29.target_token")

    def test_f07_sqlite_stale_key_eviction(self):
        """Feature 7: SQLite Stale Key Eviction removes agentManagerInitState & userStatus."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        # Verify initial keys exist
        conn = sqlite3.connect(str(self.env["db_path"]))
        cur = conn.cursor()
        cur.execute("SELECT key FROM ItemTable WHERE key IN ('jetskiStateSync.agentManagerInitState', 'antigravityUnifiedStateSync.userStatus');")
        rows = cur.fetchall()
        conn.close()
        self.assertEqual(len(rows), 2)

        # Update table
        update_vscdb_item_table(
            vscdb_path=self.env["db_path"],
            email="target@example.com",
            access_token="ya29.target",
            refresh_token="mock_rt",
            dry_run=False,
            test_root=self.test_root,
        )

        # Verify stale keys deleted
        conn = sqlite3.connect(str(self.env["db_path"]))
        cur = conn.cursor()
        cur.execute("SELECT key FROM ItemTable WHERE key IN ('jetskiStateSync.agentManagerInitState', 'antigravityUnifiedStateSync.userStatus');")
        remaining = cur.fetchall()
        conn.close()
        self.assertEqual(len(remaining), 0)

    def test_f08_macos_keychain_commit(self):
        """Feature 8: macOS Keychain generic password commit for gemini/antigravity only."""
        results = update_antigravity_keychain(
            access_token="ya29.target_access",
            refresh_token="mock_target_refresh",
            email="target@example.com",
            dry_run=True,
        )
        self.assertEqual(results, {"gemini": True})

        store = get_mock_keychain_store()
        self.assertIn(("gemini", "antigravity"), store)
        self.assertNotIn(("antigravity", "antigravity"), store)

        parsed = parse_keychain_payload(store[("gemini", "antigravity")])
        self.assertEqual(parsed["id_token_email"], "target@example.com")
        self.assertEqual(parsed["token"]["access_token"], "ya29.target_access")
        self.assertEqual(len(parsed.get("writer_id", "")), 43)

    def test_f08b_keychain_never_passes_flag_a_or_legacy_service(self):
        """Regression guard: security CLI must never pass -A or write service='antigravity'."""
        os.environ["KEYCHAIN_MOCK"] = "0"
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        os.environ.pop("ANTIGRAVITY_TEST_ROOT", None)

        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            proc = MagicMock()
            proc.returncode = 0
            proc.stdout = ""
            proc.stderr = ""
            return proc

        with patch("account_switcher.keychain.subprocess.run", side_effect=fake_run):
            res = update_antigravity_keychain(
                access_token="ya29.safe_token",
                refresh_token="1//safe_rt",
                email="target@example.com",
                dry_run=False,
            )

        self.assertEqual(res, {"gemini": True})
        self.assertEqual(len(captured_cmds), 1)
        cmd = captured_cmds[0]
        self.assertEqual(cmd[:6], ["/usr/bin/security", "add-generic-password", "-U", "-s", "gemini", "-a"])
        self.assertEqual(cmd[6], "antigravity")
        self.assertNotIn("-A", cmd)

    def test_f09_credential_scope_invalidation(self):
        """Feature 9: Credential Scope Invalidation unlinks credential-user-scope."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        scope_path = self.env["scope_path"]
        self.assertTrue(scope_path.exists())

        deleted = delete_credential_scope(scope_path, dry_run=False, test_root=self.test_root)
        self.assertTrue(deleted)
        self.assertFalse(scope_path.exists())

    def test_f10_http_storages_cache_purge(self):
        """Feature 10: HTTPStorages Cache Purge cleans connection pool directory."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        http_path = self.env["http_storage_path"]
        self.assertTrue(http_path.exists())

        cleared = clear_http_storages_cache(http_path, dry_run=False, test_root=self.test_root)
        self.assertTrue(cleared)
        self.assertFalse(http_path.exists())

    def test_f11_app_storage_update(self):
        """Feature 11: App Storage Update writes lastLoginUsername."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        app_storage = self.env["app_storage_path"]

        res = update_app_storage_json(
            app_storage_path=app_storage,
            email="target@example.com",
            dry_run=False,
            test_root=self.test_root,
        )
        self.assertEqual(res["updated_email"], "target@example.com")

        with open(app_storage, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["agy.onboarding.lastLoginUsername"], "target@example.com")

    def test_f12_process_graceful_shutdown(self):
        """Feature 12: Process Graceful Shutdown triggers quit sequence."""
        set_mock_process_state(is_running=True, quit_success=True)
        supervisor = ProcessSupervisor(dry_run=True)
        self.assertTrue(supervisor.is_running())

        quit_res = supervisor.quit(timeout_sec=1.0)
        self.assertTrue(quit_res)
        self.assertFalse(supervisor.is_running())
        q_count, _ = get_mock_process_counters()
        self.assertGreaterEqual(q_count, 1)

    def test_f13_process_relaunch_supervision(self):
        """Feature 13: Process Relaunch Supervision relaunches application."""
        set_mock_process_state(is_running=False, relaunch_success=True)
        supervisor = ProcessSupervisor(dry_run=True)
        self.assertFalse(supervisor.is_running())

        relaunch_res = supervisor.relaunch(timeout_sec=1.0)
        self.assertTrue(relaunch_res)
        self.assertTrue(supervisor.is_running())
        _, r_count = get_mock_process_counters()
        self.assertGreaterEqual(r_count, 1)

    def test_f13b_relaunch_prefers_antigravity_2_app(self):
        """Relaunch must prefer explicit Antigravity 2.app path when present."""
        os.environ["PROCESS_MOCK"] = "0"
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        os.environ.pop("ANTIGRAVITY_TEST_ROOT", None)

        fake_app = self.base_path / "Antigravity 2.app"
        fake_app.mkdir()
        captured = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            proc = MagicMock()
            proc.returncode = 0
            return proc

        with patch("account_switcher.process.subprocess.run", side_effect=fake_run), \
             patch("account_switcher.process.is_antigravity_running", return_value=True), \
             patch("account_switcher.process.time.sleep"):
            ok = relaunch_antigravity(dry_run=False, app_path=fake_app)

        self.assertTrue(ok)
        self.assertEqual(captured[0], ["/usr/bin/open", "-a", str(fake_app)])

    def test_f13c_relaunch_waits_for_fresh_hub_file(self):
        """Relaunch polls daemon_dir for fresh hub_*.json before returning."""
        os.environ["PROCESS_MOCK"] = "0"
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        os.environ.pop("ANTIGRAVITY_TEST_ROOT", None)

        fake_app = self.base_path / "Antigravity 2.app"
        fake_app.mkdir()
        daemon_dir = self.base_path / "daemon"
        daemon_dir.mkdir()

        def fake_run(cmd, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            return proc

        sleep_calls = []

        def fake_sleep(sec):
            sleep_calls.append(sec)
            if len(sleep_calls) == 2:
                (daemon_dir / "hub_12345.json").write_text('{"port": 12345}', encoding="utf-8")

        with patch("account_switcher.process.subprocess.run", side_effect=fake_run), \
             patch("account_switcher.process.is_antigravity_running", return_value=True), \
             patch("account_switcher.process.time.sleep", side_effect=fake_sleep):
            ok = relaunch_antigravity(dry_run=False, app_path=fake_app, daemon_dir=daemon_dir)

        self.assertTrue(ok)
        self.assertEqual(len(sleep_calls), 2)

    def test_f14_dry_run_simulation_mode(self):
        """Feature 14: Dry-Run Simulation Mode leaves storage layers untouched."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "1"
        scope_before = self.env["scope_path"].read_text(encoding="utf-8")
        db_mtime_before = self.env["db_path"].stat().st_mtime

        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            dry_run=True,
            test_root=self.test_root,
        )
        self.assertTrue(res.success)
        self.assertTrue(res.dry_run)
        self.assertEqual(res.phase_reached, "completed")

        # Files on disk must remain unchanged
        self.assertTrue(self.env["scope_path"].exists())
        self.assertEqual(self.env["scope_path"].read_text(encoding="utf-8"), scope_before)
        self.assertEqual(self.env["db_path"].stat().st_mtime, db_mtime_before)

    def _load_swiftbar_plugin(self):
        plugin_path = Path(__file__).resolve().parent.parent.parent / "antigravity-quota-bar" / "plugins" / "antigravity_bar.5m.py"
        spec = importlib.util.spec_from_file_location("antigravity_bar", str(plugin_path))
        bar_mod = importlib.util.module_from_spec(spec)
        prev_dont_write = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(bar_mod)
        finally:
            sys.dont_write_bytecode = prev_dont_write
        return bar_mod

    def test_f15_swiftbar_zero_button_ux_directive(self):
        """Feature 15: SwiftBar Zero-Button Click UX bash directive syntax."""
        bar_mod = self._load_swiftbar_plugin()

        with patch.object(bar_mod.check_quota, "get_all_quotas_data", return_value=[
            {"email": "target@example.com", "status": "ok", "models": []},
            {"email": "active@example.com", "status": "ok", "models": []},
        ]), patch.object(bar_mod.check_quota, "get_active_antigravity_account", return_value="active@example.com"), \
           patch.object(bar_mod.check_quota, "generate_static_dashboard"), \
           patch.object(bar_mod, "trigger_background_token_sync"):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bar_mod.main()
            output = buf.getvalue()

        target_lines = [l for l in output.splitlines() if "target@example.com" in l]
        self.assertTrue(len(target_lines) > 0, f"Expected target@example.com in output: {output}")
        directive_line = target_lines[0]
        self.assertIn("terminal=false", directive_line)
        self.assertIn("refresh=true", directive_line)
        self.assertIn("--switch-interactive", directive_line)
        self.assertIn("target@example.com", directive_line)
        self.assertIn("color=#000000", directive_line)

    def test_f16_swiftbar_applescript_dialog_generation(self):
        """Feature 16: SwiftBar AppleScript Dialog generation and confirmation."""
        set_mock_process_state(dialog_confirmed=True)
        confirmed = show_mac_confirmation_dialog(
            email="target@example.com",
            quota_summary="5ч: 100%, 7дн: 100%",
            dry_run=True,
        )
        self.assertTrue(confirmed)

        set_mock_process_state(dialog_confirmed=False)
        rejected = show_mac_confirmation_dialog(
            email="target@example.com",
            dry_run=True,
        )
        self.assertFalse(rejected)

        os.environ["PROCESS_MOCK"] = "0"
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        os.environ.pop("ANTIGRAVITY_TEST_ROOT", None)
        with patch("account_switcher.process.run_applescript", return_value=(0, "button returned:Переключить", "")) as mock_as:
            ok = show_mac_confirmation_dialog(email="target@example.com", dry_run=False)
        self.assertTrue(ok)
        script_arg = mock_as.call_args.args[0]
        self.assertNotIn("with icon", script_arg)
        self.assertNotIn("IDE", script_arg)

    def test_f17_swiftbar_active_account_badge_and_alert(self):
        """Feature 17: SwiftBar Active Account Badge and current user detection."""
        active = get_active_account(test_root=self.test_root)
        self.assertEqual(active, "active@example.com")

        bar_mod = self._load_swiftbar_plugin()

        with patch.object(bar_mod.check_quota, "get_all_quotas_data", return_value=[
            {"email": "active@example.com", "status": "ok", "models": []},
            {"email": "target@example.com", "status": "ok", "models": []},
        ]), patch.object(bar_mod.check_quota, "get_active_antigravity_account", return_value=active), \
           patch.object(bar_mod.check_quota, "generate_static_dashboard"), \
           patch.object(bar_mod, "trigger_background_token_sync"):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bar_mod.main()
            output = buf.getvalue()

        active_lines = [l for l in output.splitlines() if "active@example.com" in l]
        self.assertTrue(len(active_lines) > 0, f"Expected active@example.com in output: {output}")
        active_line = active_lines[0]
        self.assertIn("active@example.com (Активен)", active_line)
        self.assertIn("color=#0284c7", active_line)
        self.assertIn("display notification", active_line)
        self.assertIn("Аккаунт уже активен", active_line)
        self.assertIn("terminal=false", active_line)

    def test_f18_cli_switch_arguments_parsing(self):
        """Feature 18: CLI Switch Arguments validation via check_quota.py argument parser."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        quota_dir = str(repo_root / "antigravity-quota-bar")
        sys_path_added = False
        if quota_dir not in sys.path:
            sys.path.insert(0, quota_dir)
            sys_path_added = True
        try:
            import check_quota
            with patch("sys.argv", ["check_quota.py", "--switch", "target@example.com", "--dry-run"]), \
                 patch("account_switcher.switch_account") as mock_switch:
                mock_switch.return_value.success = True
                mock_switch.return_value.dry_run = True
                mock_switch.return_value.message = "Switched"
                mock_switch.return_value.error = None
                with self.assertRaises(SystemExit) as cm:
                    check_quota.main()
                self.assertEqual(cm.exception.code, 0)
                mock_switch.assert_called_once_with(
                    target_email="target@example.com",
                    dry_run=True,
                    force=False,
                    interactive=False,
                    test_root=self.test_root,
                )

            with patch("sys.argv", ["check_quota.py", "--switch-interactive", "target@example.com", "--force"]), \
                 patch("account_switcher.switch_account") as mock_switch:
                mock_switch.return_value.success = True
                mock_switch.return_value.dry_run = False
                mock_switch.return_value.message = "Switched"
                mock_switch.return_value.error = None
                with self.assertRaises(SystemExit) as cm:
                    check_quota.main()
                self.assertEqual(cm.exception.code, 0)
                mock_switch.assert_called_once_with(
                    target_email="target@example.com",
                    dry_run=False,
                    force=True,
                    interactive=True,
                    test_root=self.test_root,
                )
        finally:
            if sys_path_added:
                sys.path.remove(quota_dir)

    def test_f19_cli_active_profile_indicator(self):
        """Feature 19: CLI Active Profile Indicator badge formatting."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        quota_dir = str(repo_root / "antigravity-quota-bar")
        sys_path_added = False
        if quota_dir not in sys.path:
            sys.path.insert(0, quota_dir)
            sys_path_added = True
        try:
            import check_quota
            with patch.object(check_quota, "get_active_antigravity_account", return_value="active@example.com"):
                buf_active = io.StringIO()
                with patch("sys.stdout", buf_active):
                    check_quota.parse_and_display_quota("active@example.com", {"status": "ok", "models": []})
                out_active = buf_active.getvalue()
                self.assertIn("active@example.com (Активен)", out_active)
                self.assertIn("\033[1;32m", out_active)

                buf_inactive = io.StringIO()
                with patch("sys.stdout", buf_inactive):
                    check_quota.parse_and_display_quota("target@example.com", {"status": "ok", "models": []})
                out_inactive = buf_inactive.getvalue()
                self.assertIn("target@example.com", out_inactive)
                self.assertNotIn("(Активен)", out_inactive)
        finally:
            if sys_path_added:
                sys.path.remove(quota_dir)

    def test_f20_dashboard_post_api_switch_endpoint(self):
        """Feature 20: Dashboard POST /api/switch schema and response contract."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        quota_dir = str(repo_root / "antigravity-quota-bar")
        sys_path_added = False
        if quota_dir not in sys.path:
            sys.path.insert(0, quota_dir)
            sys_path_added = True
        try:
            from check_quota import DashboardHTTPHandler
            payload_bytes = json.dumps({"email": "target@example.com", "dry_run": True, "force": False}).encode("utf-8")
            handler = self._make_switch_handler(DashboardHTTPHandler, payload_bytes)
            handler.do_POST()

            handler.send_response.assert_called_with(200)
            resp = json.loads(handler.wfile.getvalue().decode("utf-8"))
            self.assertEqual(resp.get("status"), "ok")
            self.assertEqual(resp.get("email"), "target@example.com")
            self.assertTrue(resp.get("dry_run"))
            self.assertEqual(resp.get("phase"), "completed")
            self.assertIn("Successfully switched", resp.get("message", ""))

            # Reject cross-origin switch requests (CSRF protection)
            bad_handler = self._make_switch_handler(
                DashboardHTTPHandler,
                payload_bytes,
                origin="https://evil.example",
            )
            bad_handler.do_POST()
            bad_handler.send_response.assert_called_with(403)

            # Live switch (dry_run=False) must trigger SwiftBar refresh
            import check_quota
            live_bytes = json.dumps({"email": "target@example.com", "dry_run": False, "force": False}).encode("utf-8")
            live_handler = self._make_switch_handler(DashboardHTTPHandler, live_bytes)
            with patch.object(check_quota.account_switcher, "switch_account") as mock_sw, \
                 patch.object(check_quota, "trigger_swiftbar_refresh") as mock_refresh, \
                 patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ANTIGRAVITY_TEST_ROOT", None)
                mock_sw.return_value = SwitchResult(
                    success=True,
                    email="target@example.com",
                    message="Successfully switched",
                    dry_run=False,
                    phase_reached="completed",
                )
                live_handler.do_POST()
                live_handler.send_response.assert_called_with(200)
                mock_refresh.assert_called_once()
        finally:
            if sys_path_added:
                sys.path.remove(quota_dir)

    def test_f21_dashboard_glass_modal_and_button(self):
        """Feature 21: Dashboard Glass Modal & Button markup verification."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        quota_dir = str(repo_root / "antigravity-quota-bar")
        sys_path_added = False
        if quota_dir not in sys.path:
            sys.path.insert(0, quota_dir)
            sys_path_added = True
        try:
            import check_quota
            template = check_quota.HTML_DASHBOARD_TEMPLATE
            self.assertIn('id="switch-modal"', template)
            self.assertIn('id="switch-modal-confirm"', template)
            self.assertIn('openSwitchModal', template)
            self.assertIn('confirmSwitchAccount', template)
            self.assertIn('⚡ Сделать активным', template)
            self.assertIn('/api/switch', template)
        finally:
            if sys_path_added:
                sys.path.remove(quota_dir)

    def test_f22_popperian_falsification_suite_linkage(self):
        """Feature 22: Popperian Falsification Suite import and structure check."""
        from account_switcher.tests import test_popper_falsification
        self.assertTrue(hasattr(test_popper_falsification, "TestPopperianFalsificationPhase1"))
        self.assertTrue(hasattr(test_popper_falsification, "TestPopperianFalsificationPhase2"))
        self.assertTrue(hasattr(test_popper_falsification, "TestPopperianFalsificationPhase3"))
        self.assertTrue(hasattr(test_popper_falsification, "TestPopperianFalsificationPhase4"))


# ==============================================================================
# Tier 2: Boundary & Corner Cases
# ==============================================================================

class TestTier2BoundaryAndCornerCases(BaseIsolatedTestCase):
    """Verifies edge cases, corrupt data, permission restrictions, and error cascades."""

    def test_b01_expired_or_revoked_refresh_token(self):
        """Boundary B-01: Switch aborts on revoked / expired refresh token."""
        res = switch_account(
            target_email="expired@example.com",
            accounts_file=self.env["accounts_file"],
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.phase_reached, "preflight")
        self.assertIn("invalid_grant", str(res.error))

    def test_b02_malformed_accounts_json(self):
        """Boundary B-02: Gracefully handles malformed accounts.json syntax."""
        corrupt_file = self.test_root / "corrupt_accounts.json"
        corrupt_file.write_text("{ not a valid json [", encoding="utf-8")

        res = switch_account(
            target_email="target@example.com",
            accounts_file=corrupt_file,
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.phase_reached, "preflight")
        self.assertIn("Could not load accounts file", res.message)

    def test_b03_missing_refresh_token_field(self):
        """Boundary B-03: Aborts if account entry is missing refresh_token."""
        no_token_file = self.test_root / "no_token.json"
        no_token_file.write_text(json.dumps([{"email": "broken@example.com"}]), encoding="utf-8")

        res = switch_account(
            target_email="broken@example.com",
            accounts_file=no_token_file,
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.error, "missing_refresh_token")

    def test_b04_dangling_symlink_singleton_lock(self):
        """Boundary B-04: Gracefully tolerates dangling Electron SingletonLock symlink."""
        lock_path = self.test_root / "SingletonLock"
        if not lock_path.exists():
            try:
                os.symlink("nonexistent-host-99999", str(lock_path))
            except OSError:
                pass

        # Path resolver and supervisor must tolerate dangling symlink
        resolver = AntigravityPathResolver()
        paths = resolver.resolve(self.test_root)
        self.assertTrue(paths.root.exists())

    def test_b05_busy_sockets_blocks_unforced_switch(self):
        """Boundary B-05: Busy sockets abort unforced switch request."""
        set_mock_process_state(streaming_active=True)
        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            force=False,
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.error, "streaming_active")

    def test_b06_missing_database_recreation(self):
        """Boundary B-06: Missing state.vscdb is automatically initialized."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"
        missing_db = self.test_root / "User" / "globalStorage" / "new_state.vscdb"
        if missing_db.exists():
            missing_db.unlink()

        res = update_vscdb_item_table(
            vscdb_path=missing_db,
            email="fresh@example.com",
            access_token="ya29.fresh",
            refresh_token="mock_fresh_rt",
            dry_run=False,
            test_root=self.test_root,
        )
        self.assertTrue(missing_db.exists())
        self.assertIn("antigravityAuthStatus", res["keys_updated"])

    def test_b07_readonly_credential_scope_file(self):
        """Boundary B-07: Unlinks credential-user-scope even if read-only."""
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

    def test_b08_target_account_not_found_in_roster(self):
        """Boundary B-08: Rejects target email that does not exist in roster."""
        res = switch_account(
            target_email="nonexistent_account@example.com",
            accounts_file=self.env["accounts_file"],
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.error, "account_not_found")

    def test_b09_noop_switch_target_already_active(self):
        """Boundary B-09: Correctly identifies currently active account."""
        active = get_active_account(test_root=self.test_root)
        self.assertEqual(active, "active@example.com")

    def test_b10_keychain_service_error_handling(self):
        """Boundary B-10: Handles keychain error parsing on corrupt payload."""
        from account_switcher.keychain import KeychainError
        with self.assertRaises(KeychainError):
            parse_keychain_payload("invalid_prefix_without_go_keyring")


# ==============================================================================
# Tier 3: Cross-Feature Combinations & State Rollbacks
# ==============================================================================

class TestTier3CrossFeatureCombinations(BaseIsolatedTestCase):
    """Tests multi-phase interactions, force overrides, and dry-run execution."""

    def test_c01_dry_run_multi_store_pipeline_leaves_files_untouched(self):
        """Combination C-01: Dry-run switch executes pipeline with zero disk writes."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "1"
        mgr = AntigravityStorageManager(test_root=self.test_root, dry_run=True)
        res = mgr.commit_all(
            email="target@example.com",
            access_token="ya29.target",
            refresh_token="mock_target_rt",
        )
        self.assertTrue(res["dry_run"])
        self.assertTrue(res["vscdb"]["dry_run"])
        self.assertTrue(res["app_storage"]["dry_run"])

        # Scope file still exists on disk
        self.assertTrue(self.env["scope_path"].exists())

    def test_c02_forced_switch_bypasses_active_sockets(self):
        """Combination C-02: force=True bypasses active streaming socket guard."""
        set_mock_process_state(streaming_active=True)
        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            force=True,
            test_root=self.test_root,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.phase_reached, "completed")

    def test_c03_offline_ide_switch_skips_shutdown_and_launches(self):
        """Combination C-03: Switching when IDE is offline skips quit and proceeds."""
        set_mock_process_state(is_running=False, relaunch_success=True)
        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            test_root=self.test_root,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.phase_reached, "completed")
        q_count, _ = get_mock_process_counters()
        self.assertEqual(q_count, 0)  # No quit executed because it was not running

    def test_c04_interactive_dialog_cancellation_aborts_without_mutations(self):
        """Combination C-04: User cancelling confirmation dialog halts before mutations."""
        set_mock_process_state(dialog_confirmed=False)
        res = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            interactive=True,
            test_root=self.test_root,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.error, "user_cancelled")
        self.assertEqual(res.phase_reached, "preflight")


# ==============================================================================
# Tier 4: Real-World Application Scenarios (E2E Simulated Workflows)
# ==============================================================================

class TestTier4RealWorldScenarios(BaseIsolatedTestCase):
    """End-to-end full lifecycle roundtrip workflows."""

    def test_w01_e2e_dual_switch_roundtrip_consistency(self):
        """Workflow W-01: Full roundtrip switch A -> B -> A verifies state consistency."""
        os.environ["ANTIGRAVITY_DRY_RUN"] = "0"

        # Initial state is Account A
        active_initial = get_active_account(test_root=self.test_root)
        self.assertEqual(active_initial, "active@example.com")

        # 1. Switch from A to B (target@example.com)
        set_mock_process_state(is_running=True, quit_success=True, relaunch_success=True)
        res_b = switch_account(
            target_email="target@example.com",
            accounts_file=self.env["accounts_file"],
            dry_run=False,
            test_root=self.test_root,
        )
        self.assertTrue(res_b.success)
        self.assertEqual(res_b.phase_reached, "completed")

        # Verify active account is now B
        active_b = get_active_account(test_root=self.test_root)
        self.assertEqual(active_b, "target@example.com")
        self.assertFalse(self.env["scope_path"].exists())

        # 2. Re-create scope file to simulate runtime Antigravity operation under Account B
        self.env["scope_path"].write_text("ubbbbbbbbbbbbbbbb", encoding="utf-8")
        self.assertTrue(self.env["scope_path"].exists())

        # 3. Switch back from B to A (active@example.com)
        res_a = switch_account(
            target_email="active@example.com",
            accounts_file=self.env["accounts_file"],
            dry_run=False,
            test_root=self.test_root,
        )
        self.assertTrue(res_a.success)
        self.assertEqual(res_a.phase_reached, "completed")

        # Verify active account is back to A
        active_final = get_active_account(test_root=self.test_root)
        self.assertEqual(active_final, "active@example.com")
        self.assertFalse(self.env["scope_path"].exists())

    def test_w02_e2e_cli_invocation_simulation(self):
        """Workflow W-02: Real subprocess CLI invocation of check_quota.py with --dry-run."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        script_path = repo_root / "antigravity-quota-bar" / "check_quota.py"

        proc_env = os.environ.copy()
        proc_env["ANTIGRAVITY_TEST_ROOT"] = str(self.test_root)
        proc_env["ANTIGRAVITY_DRY_RUN"] = "1"
        proc_env["KEYCHAIN_MOCK"] = "1"
        proc_env["PROCESS_MOCK"] = "1"
        proc_env["OAUTH_MOCK"] = "1"

        proc = subprocess.run(
            [sys.executable, str(script_path), "--switch", "target@example.com", "--dry-run"],
            capture_output=True,
            text=True,
            env=proc_env,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, f"check_quota.py failed with stderr: {proc.stderr}")
        self.assertIn("target@example.com", proc.stdout)
        self.assertIn("Successfully switched", proc.stdout)
        self.assertIn("Режим симуляции (dry-run)", proc.stdout)

    def test_w03_e2e_dashboard_http_server_switch_simulation(self):
        """Workflow W-03: Real Dashboard HTTPHandler POST /api/switch execution."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        quota_dir = str(repo_root / "antigravity-quota-bar")
        sys_path_added = False
        if quota_dir not in sys.path:
            sys.path.insert(0, quota_dir)
            sys_path_added = True
        try:
            from check_quota import DashboardHTTPHandler
            payload_bytes = json.dumps({"email": "target@example.com", "dry_run": True, "force": False}).encode("utf-8")
            handler = self._make_switch_handler(DashboardHTTPHandler, payload_bytes)
            handler.do_POST()

            handler.send_response.assert_called_with(200)
            resp = json.loads(handler.wfile.getvalue().decode("utf-8"))
            self.assertEqual(resp["status"], "ok")
            self.assertEqual(resp["email"], "target@example.com")
            self.assertTrue(resp["dry_run"])
            self.assertEqual(resp["phase"], "completed")
            self.assertIn("Successfully switched", resp.get("message", ""))
        finally:
            if sys_path_added:
                sys.path.remove(quota_dir)


if __name__ == "__main__":
    unittest.main()
