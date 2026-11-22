import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import scanner


class ScannerTests(unittest.TestCase):
    def _make_convo(self, brain_dir: Path, cid: str, steps: list[dict]):
        logs_dir = brain_dir / cid / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        tpath = logs_dir / "transcript_full.jsonl"
        tpath.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in steps) + "\n", encoding="utf-8")
        return tpath

    def test_native_api_tokens_and_fallback_without_tokenizers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            cache_path = root / "sessions_cache.json"

            self._make_convo(brain, "sess-native", [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "created_at": "2026-11-18T10:00:00Z",
                    "content": "Привет, проверь дашборд",
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "created_at": "2026-11-18T10:00:02Z",
                    "input_tokens": 22000,
                    "cache_read_tokens": 0,
                    "output_tokens": 300,
                    "thinking": "Думаю",
                    "tool_calls": [{"name": "read_file"}],
                },
                {
                    "step_index": 2,
                    "source": "MODEL",
                    "type": "GENERIC",
                    "created_at": "2026-11-18T10:00:03Z",
                    "content": "ok " * 40,
                },
                {
                    "step_index": 3,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "created_at": "2026-11-18T10:00:04Z",
                    "input_tokens": 1500,
                    "cache_read_tokens": 22300,
                    "output_tokens": 500,
                    "content": "Готово",
                },
            ])

            with patch.object(scanner, "get_tokenizer", return_value=None), \
                 patch.object(scanner, "load_conversation_metadata", return_value=({}, {})):
                results, cached, updated = scanner.scan_conversations(
                    brain_dir=str(brain),
                    cache_path=str(cache_path),
                    force_refresh=True,
                )

            self.assertEqual(cached, 0)
            self.assertEqual(updated, 1)
            day = results["sess-native"]["by_date"]["2026-11-18"]
            self.assertEqual(day["user_prompts"], 1)
            self.assertEqual(day["model_calls"], 2)
            # Context is exact sum of input_tokens + cache_read_tokens across both model calls
            self.assertEqual(day["context_tok"], 22000 + (1500 + 22300))
            # Output tokens are split between model_tok and thinking_tok without double-counting
            self.assertEqual(day["model_tok"] + day["thinking_tok"], 300 + 500)
            self.assertGreater(day["thinking_tok"], 0)
            self.assertGreater(day["user_tok"], 0)
            self.assertGreater(day["tool_tok"], 0)

    def test_legacy_steps_without_api_tokens_use_cumulative_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            cache_path = root / "sessions_cache.json"

            self._make_convo(brain, "sess-legacy", [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "created_at": "2026-10-10T10:00:00Z",
                    "content": "Hello world",
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "created_at": "2026-10-10T10:00:02Z",
                    "content": "Response text",
                },
            ])

            with patch.object(scanner, "get_tokenizer", return_value=None), \
                 patch.object(scanner, "load_conversation_metadata", return_value=({}, {})):
                results, _, _ = scanner.scan_conversations(
                    brain_dir=str(brain),
                    cache_path=str(cache_path),
                    force_refresh=True,
                )

            day = results["sess-legacy"]["by_date"]["2026-10-10"]
            self.assertGreater(day["context_tok"], scanner.SYSTEM_PROMPT_ESTIMATE)
            self.assertGreater(day["model_tok"], 0)

    def test_retains_deleted_sessions_and_incremental_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            cache_path = root / "sessions_cache.json"

            self._make_convo(brain, "sess-old", [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "created_at": "2026-11-01T10:00:00Z",
                    "content": "Old prompt",
                },
            ])

            with patch.object(scanner, "get_tokenizer", return_value=None), \
                 patch.object(scanner, "load_conversation_metadata", return_value=({}, {})):
                scanner.scan_conversations(brain_dir=str(brain), cache_path=str(cache_path))
                # Second run hits cache
                res2, cached2, updated2 = scanner.scan_conversations(brain_dir=str(brain), cache_path=str(cache_path))
                self.assertEqual(cached2, 1)
                self.assertEqual(updated2, 0)
                # Remove from disk -> retained with deleted_from_disk=True
                for p in sorted((brain / "sess-old").rglob("*"), reverse=True):
                    p.unlink() if p.is_file() else p.rmdir()
                (brain / "sess-old").rmdir()
                res3, _, _ = scanner.scan_conversations(brain_dir=str(brain), cache_path=str(cache_path))
                self.assertTrue(res3["sess-old"]["deleted_from_disk"])


if __name__ == "__main__":
    unittest.main()
