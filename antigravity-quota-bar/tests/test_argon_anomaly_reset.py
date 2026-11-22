from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import check_quota


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pool(
    pool_id: str,
    name: str,
    *,
    pct_5h: int = 100,
    frac_5h: float | None = None,
    reset_5h: datetime | None = None,
    pct_7d: int = 100,
    frac_7d: float | None = None,
    reset_7d: datetime | None = None,
    include_weekly_iso: bool = True,
    include_summary_flags: bool = True,
) -> dict:
    f5 = (pct_5h / 100.0) if frac_5h is None else frac_5h
    f7 = (pct_7d / 100.0) if frac_7d is None else frac_7d
    r5_iso = _iso(reset_5h) if reset_5h is not None else None
    r7_iso = (_iso(reset_7d) if reset_7d is not None else None) if include_weekly_iso else None
    model = {
        "name": name,
        "pool_id": pool_id,
        "fraction": f5 if pool_id != "argon" else f7,
        "percentage": pct_5h if pool_id != "argon" else pct_7d,
        "reset": "1ч" if r5_iso else "готов",
        "weekly_fraction": f7,
        "weekly_percentage": pct_7d,
        "weekly_reset": "5д" if r7_iso else "готов",
    }
    if include_summary_flags:
        model["has_5h"] = pool_id != "argon"
        model["has_weekly"] = True
        model["reset_iso"] = r5_iso if pool_id != "argon" else r7_iso
    if include_weekly_iso:
        model["weekly_reset_iso"] = r7_iso
    return model


def _account(email: str, models: list[dict], status: str = "ok", **extra) -> dict:
    return {"email": email, "status": status, "models": models, **extra}


class ArgonAnomalyResetTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.active_patch = patch.object(check_quota, "get_active_antigravity_account", return_value=None)
        self.active_patch.start()
        self.addCleanup(self.active_patch.stop)

    def test_early_weekly_reset_of_gemini_resets_cached_argon(self):
        prev = [_account("u1@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=60, reset_5h=self.now + timedelta(hours=3), pct_7d=40, reset_7d=self.now + timedelta(days=5)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("argon", "Gemini 4 Argon", pct_7d=22, reset_7d=self.now + timedelta(days=5)),
        ])]
        fresh = [_account("u1@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 100)
        self.assertEqual(argon["percentage"], 100)
        self.assertEqual(argon["weekly_reset"], "готов")
        self.assertIsNone(argon["weekly_reset_iso"])
        self.assertIsNone(argon["reset_iso"])

    def test_early_weekly_reset_of_claude_resets_cached_argon(self):
        prev = [_account("u2@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("claude", "Claude", pct_5h=80, reset_5h=self.now + timedelta(hours=4), pct_7d=55, reset_7d=self.now + timedelta(days=4)),
            _pool("argon", "Gemini 4 Argon", pct_7d=15, reset_7d=self.now + timedelta(days=4)),
        ])]
        fresh = [_account("u2@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 100)
        self.assertEqual(argon["weekly_reset"], "готов")
        self.assertIsNone(argon["weekly_reset_iso"])

    def test_early_5h_reset_with_shifted_weekly_window_resets_argon(self):
        prev = [_account("u3@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=60, reset_5h=self.now + timedelta(hours=3), pct_7d=100, frac_7d=0.9995, reset_7d=self.now + timedelta(days=5)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("argon", "Gemini 4 Argon", pct_7d=30, reset_7d=self.now + timedelta(days=5)),
        ])]
        fresh = [_account("u3@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 100)
        self.assertIsNone(argon["weekly_reset_iso"])

    def test_normal_5h_expiration_does_not_reset_argon(self):
        argon_reset = self.now + timedelta(days=5)
        prev = [_account("u4@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=40, reset_5h=self.now - timedelta(minutes=1), pct_7d=85, reset_7d=self.now + timedelta(days=6)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("argon", "Gemini 4 Argon", pct_7d=22, reset_7d=argon_reset),
        ])]
        fresh = [_account("u4@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=85, reset_7d=self.now + timedelta(days=6)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 22)
        self.assertEqual(argon["weekly_reset_iso"], _iso(argon_reset))

    def test_natural_7d_expiration_of_gemini_with_later_argon_does_not_force_reset(self):
        argon_reset = self.now + timedelta(days=2)
        prev = [_account("u5@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=50, reset_5h=self.now - timedelta(minutes=5), pct_7d=50, reset_7d=self.now - timedelta(minutes=5)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("argon", "Gemini 4 Argon", pct_7d=22, reset_7d=argon_reset),
        ])]
        fresh = [_account("u5@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 22)
        self.assertEqual(argon["weekly_reset_iso"], _iso(argon_reset))

    def test_cached_fallback_does_not_trigger_argon_reset(self):
        argon_reset = self.now + timedelta(days=4)
        prev = [_account("u6@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=70, reset_5h=self.now + timedelta(hours=3), pct_7d=70, reset_7d=self.now + timedelta(days=5)),
            _pool("argon", "Gemini 4 Argon", pct_7d=25, reset_7d=argon_reset),
        ])]
        fresh = [{"email": "u6@gmail.com", "status": "error", "error_type": "network_error", "error": "timeout", "models": []}]

        merged = check_quota.merge_quota_results(fresh, prev)
        self.assertTrue(merged[0].get("cached_fallback"))
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 25)
        self.assertEqual(argon["weekly_reset_iso"], _iso(argon_reset))

    def test_active_account_fresh_argon_from_ls_is_not_overwritten(self):
        prev = [_account("u7@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=50, reset_5h=self.now + timedelta(hours=2), pct_7d=50, reset_7d=self.now + timedelta(days=4)),
            _pool("argon", "Gemini 4 Argon", pct_7d=10, reset_7d=self.now + timedelta(days=4)),
        ])]
        fresh = [_account("u7@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("argon", "Gemini 4 Argon", pct_7d=88, reset_7d=self.now + timedelta(days=6)),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 88)

    def test_only_argon_spent_while_gemini_and_claude_stay_100_keeps_argon(self):
        argon_reset = self.now + timedelta(days=5)
        prev = [_account("u8@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=4, minutes=50), pct_7d=100, reset_7d=self.now + timedelta(days=6, hours=23)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=4, minutes=50), pct_7d=100, reset_7d=self.now + timedelta(days=6, hours=23)),
            _pool("argon", "Gemini 4 Argon", pct_7d=22, reset_7d=argon_reset),
        ])]
        fresh = [_account("u8@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("claude", "Claude", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 22)
        self.assertEqual(argon["weekly_reset_iso"], _iso(argon_reset))

    def test_fetch_available_models_fallback_does_not_trigger_argon_reset(self):
        argon_reset = self.now + timedelta(days=4)
        prev = [_account("u9@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("claude", "Claude", pct_5h=60, reset_5h=self.now + timedelta(hours=3), pct_7d=40, reset_7d=self.now + timedelta(days=4)),
            _pool("argon", "Gemini 4 Argon", pct_7d=22, reset_7d=argon_reset),
        ])]
        # Simulates real fallback from fetchAvailableModels (no has_weekly / weekly_reset_iso)
        fresh = [_account("u9@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, pct_7d=100, include_weekly_iso=False, include_summary_flags=False),
            _pool("claude", "Claude", pct_5h=100, pct_7d=100, include_weekly_iso=False, include_summary_flags=False),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 22)
        self.assertEqual(argon["weekly_reset_iso"], _iso(argon_reset))

    def test_early_gemini_reset_works_when_claude_omits_reset_time(self):
        # Simulates live API shape where 3p-weekly omits resetTime at 100%
        prev = [_account("u11@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=60, reset_5h=self.now + timedelta(hours=3), pct_7d=40, reset_7d=self.now + timedelta(days=5)),
            _pool("claude", "Claude", pct_5h=100, pct_7d=100, reset_7d=None),
            _pool("argon", "Gemini 4 Argon", pct_7d=22, reset_7d=self.now + timedelta(days=5)),
        ])]
        fresh = [_account("u11@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("claude", "Claude", pct_5h=100, pct_7d=100, reset_7d=None),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 100)
        self.assertEqual(argon["weekly_reset"], "готов")
        self.assertIsNone(argon["weekly_reset_iso"])

    def test_partial_reset_while_other_primary_pool_below_100_keeps_argon(self):
        argon_reset = self.now + timedelta(days=5)
        prev = [_account("u10@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=50, reset_5h=self.now + timedelta(hours=3), pct_7d=50, reset_7d=self.now + timedelta(days=5)),
            _pool("claude", "Claude", pct_5h=40, reset_5h=self.now + timedelta(hours=3), pct_7d=40, reset_7d=self.now + timedelta(days=5)),
            _pool("argon", "Gemini 4 Argon", pct_7d=22, reset_7d=argon_reset),
        ])]
        fresh = [_account("u10@gmail.com", [
            _pool("gemini", "Gemini", pct_5h=100, reset_5h=self.now + timedelta(hours=5), pct_7d=100, reset_7d=self.now + timedelta(days=7)),
            _pool("claude", "Claude", pct_5h=40, reset_5h=self.now + timedelta(hours=3), pct_7d=40, reset_7d=self.now + timedelta(days=5)),
        ])]

        merged = check_quota.merge_quota_results(fresh, prev)
        argon = next(m for m in merged[0]["models"] if m["pool_id"] == "argon")
        self.assertEqual(argon["weekly_percentage"], 22)
        self.assertEqual(argon["weekly_reset_iso"], _iso(argon_reset))


if __name__ == "__main__":
    unittest.main()
