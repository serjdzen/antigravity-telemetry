import contextlib
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import check_quota


PLUGIN_PATH = ROOT / "plugins" / "antigravity_bar.5m.py"
PLUGIN_SPEC = importlib.util.spec_from_file_location("quota_bar_plugin", PLUGIN_PATH)
quota_bar = importlib.util.module_from_spec(PLUGIN_SPEC)
_prev_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    PLUGIN_SPEC.loader.exec_module(quota_bar)
finally:
    sys.dont_write_bytecode = _prev_dont_write_bytecode

FIRST = "primary@GMAIL.COM"
SECOND = "work@example.com"


def quota(email, percentage=43):
    return {
        "email": email, "status": "ok", "models": [{
            "name": "Gemini", "pool_id": "gemini", "percentage": percentage,
            "reset": "1ч", "fraction": percentage / 100,
        }],
    }


class AccountManagementTest(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = directory / "accounts.json"
        self.backup = directory / "global.json"
        self.cache = directory / ".quota_cache.json"
        self.dashboard = directory / "dashboard.html"
        self.enterContext(patch.multiple(
            check_quota, CONFIG_FILE=str(self.config), GLOBAL_CONFIG_FILE=str(self.backup),
            CACHE_FILE=str(self.cache), SCRIPT_DIR=str(directory),
        ))
        self.active = self.enterContext(patch.object(
            check_quota, "get_active_antigravity_account", return_value=None,
        ))
        self.enterContext(patch.object(check_quota, "load_token_analytics_data", return_value=None))
        self.enterContext(patch.object(quota_bar, "trigger_background_token_sync"))
        self.config.write_text(json.dumps([
            {"email": FIRST, "refresh_token": "fixture-one"},
            {"email": SECOND, "refresh_token": "fixture-two"},
        ]))
        self.cache.write_text(json.dumps([quota(FIRST), quota(SECOND, 79)]))

    def plugin_output(self):
        output = io.StringIO()
        with patch.object(check_quota, "fetch_account_quota", side_effect=lambda row: quota(
            row["email"], 43 if row["email"] == FIRST else 79,
        )), contextlib.redirect_stdout(output):
            quota_bar.main()
        return output.getvalue()

    def request(self, path, body=None, *, origin=None):
        host = f"127.0.0.1:{check_quota.DASHBOARD_PORT}"
        encoded = json.dumps(body).encode() if body is not None else b""
        method = "POST" if body is not None else "GET"
        raw = (f"{method} {path} HTTP/1.0\r\nHost: {host}\r\n"
               f"Origin: {origin or 'http://' + host}\r\nContent-Type: application/json\r\n"
               f"Content-Length: {len(encoded)}\r\n\r\n").encode() + encoded

        class Connection:
            def __init__(self):
                self.output = bytearray()

            def makefile(self, *_args):
                return io.BytesIO(raw)

            def sendall(self, data):
                self.output.extend(data)

        connection = Connection()
        check_quota.DashboardHTTPHandler(connection, ("127.0.0.1", 0), None)
        headers, payload = connection.output.split(b"\r\n\r\n", 1)
        return int(headers.split(b" ", 2)[1]), payload.decode()

    def test_gmail_shortens_only_in_dropdown(self):
        output = self.plugin_output()
        self.assertIn("\nprimary | font=", output)
        self.assertNotIn(FIRST, output)
        self.assertIn(f"\n{SECOND} | font=", output)
        self.assertIn(FIRST, self.dashboard.read_text())

    def test_note_is_saved_and_displayed_as_text_in_both_views(self):
        note = 'подписка | "Jio" </script>'
        status, payload = self.request("/api/account/preferences", {
            "email": FIRST.lower(), "note": note,
        })
        self.assertEqual(status, 200)
        self.assertEqual(check_quota.load_accounts()[0]["note"], note)
        self.assertEqual(json.loads(payload)["accounts"][0]["note"], note)
        self.assertIn('primary (подписка ｜ "Jio" </script>) | font=', self.plugin_output())
        html = self.dashboard.read_text()
        self.assertNotIn('fixture-one', html + payload)
        self.assertNotIn('Jio\\" </script>', html)
        self.assertEqual(self.request("/api/account/preferences", {"email": FIRST, "note": ""})[0], 200)
        self.assertEqual(check_quota.load_accounts()[0].get("note", ""), "")

    def test_hidden_active_stays_first_and_supplies_menu_bar_quota(self):
        self.active.return_value = FIRST.lower()
        status, _ = self.request("/api/account/preferences", {
            "email": FIRST, "show_in_swiftbar": False,
        })
        self.assertEqual(status, 200)
        output = self.plugin_output()
        self.assertTrue(output.startswith("43%"))
        self.assertLess(output.index("primary (Активен)"), output.index(SECOND))
        self.active.return_value = SECOND
        output = self.plugin_output()
        self.assertTrue(output.startswith("79%"))
        self.assertNotIn("primary", output)
        self.assertIn(FIRST, self.dashboard.read_text())

    def test_rejects_cross_origin_account_mutation(self):
        status, _ = self.request("/api/account/delete", {"email": FIRST}, origin="https://external.example")
        self.assertEqual(status, 403)
        self.assertEqual(len(check_quota.load_accounts()), 2)

    def test_dashboard_add_launches_existing_oauth_callback_flow(self):
        with patch.object(check_quota.subprocess, "Popen") as launch:
            status, _ = self.request("/api/add-account", {})
        self.assertEqual(status, 202)
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0][-1], "--add")

    def test_parallel_edits_and_reauthorization_preserve_both_settings(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            note = pool.submit(self.request, "/api/account/preferences", {
                "email": FIRST, "note": "До 31 мая",
            })
            visibility = pool.submit(self.request, "/api/account/preferences", {
                "email": FIRST, "show_in_swiftbar": False,
            })
            refresh = pool.submit(check_quota.register_account, FIRST.lower(), "new-fixture")
            self.assertEqual(note.result()[0], 200)
            self.assertEqual(visibility.result()[0], 200)
            refresh.result()
        account = check_quota.load_accounts()[0]
        self.assertEqual(account["note"], "До 31 мая")
        self.assertFalse(account["show_in_swiftbar"])
        self.assertEqual(account["refresh_token"], "new-fixture")
        self.assertEqual(len(check_quota.load_accounts()), 2)

    def test_uses_first_visible_quota_when_active_account_is_unknown(self):
        self.assertEqual(self.request("/api/account/preferences", {
            "email": FIRST, "show_in_swiftbar": False,
        })[0], 200)
        self.assertTrue(self.plugin_output().startswith("79%"))
        self.assertEqual(self.request("/api/account/preferences", {
            "email": SECOND, "show_in_swiftbar": False,
        })[0], 200)
        output = self.plugin_output()
        self.assertTrue(output.startswith("Antigravity"))
        self.assertIn("Все аккаунты скрыты", output)
        self.assertIn("Открыть Liquid Glass Dashboard", output)

    def test_delete_filters_caches_and_global_list_even_for_last_account(self):
        self.config.replace(self.backup)
        status, payload = self.request("/api/account/delete", {"email": FIRST.lower()})
        self.assertEqual(status, 200)
        self.assertEqual([row["email"] for row in json.loads(payload)["accounts"]], [SECOND])
        self.assertNotIn(FIRST, self.cache.read_text() + self.dashboard.read_text())
        self.assertEqual(self.request("/api/account/delete", {"email": SECOND})[0], 200)
        self.assertEqual(check_quota.get_all_quotas_data(), [])
        self.assertNotIn(FIRST, self.dashboard.read_text())
        self.assertIn("Добавить", self.plugin_output())

    def test_in_flight_poll_cannot_restore_deleted_or_replaced_account(self):
        started = threading.Event()
        deleted = threading.Event()

        def fetch(row):
            if row["email"] == FIRST:
                started.set()
                if not deleted.wait(5):
                    raise AssertionError("Delete was blocked by quota polling")
            return quota(row["email"])

        output = []
        with patch.object(check_quota, "fetch_account_quota", side_effect=fetch):
            thread = threading.Thread(target=lambda: output.extend(check_quota.get_all_quotas_data(True)))
            thread.start()
            try:
                self.assertTrue(started.wait(3))
                self.assertEqual(self.request("/api/account/delete", {"email": FIRST})[0], 200)
                check_quota.register_account(FIRST.lower(), "fixture-new")
            finally:
                deleted.set()
                thread.join(5)

        self.assertFalse(thread.is_alive())
        self.assertEqual([row["email"] for row in output if row["status"] == "ok"], [SECOND])
        self.assertNotIn(FIRST, self.cache.read_text())

    def test_generated_dashboard_avoids_heavy_blur_and_blocking_chart_script(self):
        self.plugin_output()
        html = self.dashboard.read_text(encoding="utf-8")
        self.assertNotIn("filter: blur(100px)", html)
        self.assertNotIn('<div class="orb ', html)
        self.assertIn("chart.umd.min.js\" async", html)
        card_css = html.split(".account-card {", 1)[1].split("}", 1)[0]
        analytics_css = html.split(".analytics-section {", 1)[1].split("}", 1)[0]
        self.assertNotIn("backdrop-filter", card_css)
        self.assertNotIn("backdrop-filter", analytics_css)

    def test_mru_rotation_orders_swiftbar_and_dashboard_by_last_used(self):
        third = "third@example.com"
        accounts = json.loads(self.config.read_text())
        accounts.append({"email": third, "refresh_token": "fixture-three"})
        self.config.write_text(json.dumps(accounts))
        self.cache.write_text(json.dumps([quota(FIRST), quota(SECOND, 79), quota(third, 55)]))

        # Rotate active account: FIRST -> third -> SECOND
        for active_email in (FIRST, third, SECOND):
            self.active.return_value = active_email
            self.plugin_output()

        # Verify SwiftBar dropdown order: SECOND (active), third (prev), FIRST (oldest)
        output = self.plugin_output()
        idx_second = output.index(f"{SECOND} (Активен)")
        idx_third = output.index(third)
        idx_first = output.index("\nprimary |")
        self.assertLess(idx_second, idx_third)
        self.assertLess(idx_third, idx_first)

        # Verify Dashboard (/api/quotas and static dashboard.html) shares the exact MRU order
        _, payload = self.request("/api/quotas")
        api_emails = [row["email"] for row in json.loads(payload)["accounts"]]
        self.assertEqual(api_emails, [SECOND, third, FIRST])

        html = self.dashboard.read_text(encoding="utf-8")
        marker = "const EMBEDDED_DATA = "
        embedded_line = next(line for line in html.splitlines() if marker in line)
        embedded_json = embedded_line.split(marker, 1)[1].rstrip(";")
        static_emails = [row["email"] for row in json.loads(embedded_json)["accounts"]]
        self.assertEqual(static_emails, [SECOND, third, FIRST])

        # Hiding 'third' removes it from SwiftBar, preserves MRU position in Dashboard and when re-enabled
        self.assertEqual(self.request("/api/account/preferences", {"email": third, "show_in_swiftbar": False})[0], 200)
        hidden_output = self.plugin_output()
        self.assertNotIn(third, hidden_output)
        self.assertLess(hidden_output.index(f"{SECOND} (Активен)"), hidden_output.index("\nprimary |"))

        self.assertEqual(self.request("/api/account/preferences", {"email": third, "show_in_swiftbar": True})[0], 200)
        restored_output = self.plugin_output()
        self.assertLess(restored_output.index(f"{SECOND} (Активен)"), restored_output.index(third))
        self.assertLess(restored_output.index(third), restored_output.index("\nprimary |"))

    def test_argon_is_excluded_from_parsing_cache_swiftbar_and_dashboard(self):
        parsed = check_quota.parse_quota_groups([
            {"displayName": "Gemini Models", "buckets": [{"displayName": "Five Hour Limit Remaining", "remainingFraction": 0.9}]},
            {"displayName": "Claude and GPT models", "buckets": [{"displayName": "Five Hour Limit Remaining", "remainingFraction": 1.0}]},
            {"displayName": "Gemini 4 Argon", "buckets": [{"displayName": "Gemini 4 Argon Weekly Limit", "remainingFraction": 0.22}]},
        ])
        self.assertEqual([m["pool_id"] for m in parsed], ["gemini", "claude"])

        stale_argon = {"name": "Gemini 4 Argon", "pool_id": "argon", "percentage": 22, "weekly_percentage": 22, "weekly_reset": "5д"}
        row_with_argon = quota(FIRST)
        row_with_argon["models"].append(stale_argon)
        self.cache.write_text(json.dumps([row_with_argon, quota(SECOND, 79)]))

        merged = check_quota.merge_quota_results([quota(SECOND, 79)], [row_with_argon])
        self.assertFalse(any(m.get("pool_id") == "argon" for m in merged[0]["models"]))

        _, payload = self.request("/api/quotas")
        api_models = json.loads(payload)["accounts"][0]["models"]
        self.assertFalse(any(m.get("pool_id") == "argon" for m in api_models))

        output = self.plugin_output()
        self.assertNotIn("Argon", output)
        html = self.dashboard.read_text(encoding="utf-8")
        marker = "const EMBEDDED_DATA = "
        embedded_line = next(line for line in html.splitlines() if marker in line)
        embedded_json = embedded_line.split(marker, 1)[1].rstrip(";")
        static_models = [m for acc in json.loads(embedded_json)["accounts"] for m in acc.get("models", [])]
        self.assertFalse(any(m.get("pool_id") == "argon" for m in static_models))


if __name__ == "__main__":
    unittest.main()
