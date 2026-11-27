import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import codex_scanner, scanner, aggregator
import sync_cache


class CodexScannerTests(unittest.TestCase):
    def _write_jsonl(self, path: Path, events: list[dict]):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events) + "\n",
            encoding="utf-8",
        )

    def test_parses_tokens_dedupes_and_filters_system_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_dir = root / ".codex"
            cache_path = root / "codex_sessions_cache.json"
            sess_file = codex_dir / "sessions" / "2026" / "11" / "27" / "rollout-2026-11-27-abc123.jsonl"

            self._write_jsonl(sess_file, [
                {
                    "timestamp": "2026-11-27T10:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "abc123",
                        "cwd": "/Users/demo/work/QuotaAnalytics",
                        "source": "vscode",
                    },
                },
                {
                    "timestamp": "2026-11-27T10:00:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "<environment_context><cwd>/tmp</cwd></environment_context>"}],
                    },
                },
                {
                    "timestamp": "2026-11-27T10:00:02Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "<INSTRUCTIONS>Do not leak</INSTRUCTIONS>"}],
                    },
                },
                {
                    "timestamp": "2026-11-27T10:00:03Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Добавь поддержку Codex в дашборд"}],
                    },
                },
                {
                    "timestamp": "2026-11-27T10:00:04Z",
                    "type": "event_msg",
                    "payload": {"type": "token_count", "info": None},
                },
                {
                    "timestamp": "2026-11-27T10:00:05Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 16230,
                                "cached_input_tokens": 12000,
                                "output_tokens": 200,
                                "reasoning_output_tokens": 80,
                            },
                            "total_token_usage": {"input_tokens": 16230, "output_tokens": 200},
                        },
                    },
                },
                # Consecutive duplicate total_token_usage should be skipped
                {
                    "timestamp": "2026-11-27T10:00:06Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 16230,
                                "cached_input_tokens": 12000,
                                "output_tokens": 200,
                                "reasoning_output_tokens": 80,
                            },
                            "total_token_usage": {"input_tokens": 16230, "output_tokens": 200},
                        },
                    },
                },
                {
                    "timestamp": "2026-11-27T10:00:07Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "output": "Command output " * 20,
                    },
                },
            ])

            results, cached, updated = codex_scanner.scan_codex_sessions(
                codex_dir=str(codex_dir),
                cache_path=str(cache_path),
                force_refresh=True,
            )

            self.assertEqual(cached, 0)
            self.assertEqual(updated, 1)
            entry = results["codex:rollout-2026-11-27-abc123"]
            self.assertEqual(entry["id"], "abc123")
            self.assertEqual(entry["provider"], "codex")
            self.assertFalse(entry["is_subagent"])
            self.assertEqual(entry["project"], "QuotaAnalytics")
            self.assertEqual(entry["title"], "Добавь поддержку Codex в дашборд")

            day = entry["by_date"]["2026-11-27"]
            self.assertEqual(day["user_prompts"], 1)
            self.assertEqual(day["model_calls"], 1)
            self.assertEqual(day["context_tok"], 16230)
            self.assertEqual(day["thinking_tok"], 80)
            self.assertEqual(day["model_tok"], 120)
            self.assertGreater(day["user_tok"], 0)
            self.assertGreater(day["tool_tok"], 0)

    def test_subagent_session_has_zero_user_prompts_and_counts_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_dir = root / ".codex"
            cache_path = root / "codex_sessions_cache.json"
            sess_file = codex_dir / "archived_sessions" / "rollout-sub-1.jsonl"

            self._write_jsonl(sess_file, [
                {
                    "timestamp": "2026-11-27T11:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "sub-1",
                        "cwd": "/Users/demo/work/MyApp",
                        "source": {"subagent": {"other": "guardian"}},
                    },
                },
                {
                    "timestamp": "2026-11-27T11:00:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Review security policy"}],
                    },
                },
                {
                    "timestamp": "2026-11-27T11:00:02Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 5000,
                                "output_tokens": 100,
                                "reasoning_output_tokens": 40,
                            },
                            "total_token_usage": {"input_tokens": 5000, "output_tokens": 100},
                        },
                    },
                },
            ])

            results, _, _ = codex_scanner.scan_codex_sessions(
                codex_dir=str(codex_dir),
                cache_path=str(cache_path),
            )
            entry = results["codex:rollout-sub-1"]
            self.assertTrue(entry["is_subagent"])
            self.assertTrue(entry["title"].startswith("[subagent: guardian]"))
            day = entry["by_date"]["2026-11-27"]
            self.assertEqual(day["user_prompts"], 0)
            self.assertEqual(day["model_calls"], 1)
            self.assertEqual(day["context_tok"], 5000)

    def test_isolated_caches_and_unified_export_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = root / "brain"
            logs_dir = brain / "ag-1" / ".system_generated" / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl(logs_dir / "transcript_full.jsonl", [
                {
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "created_at": "2026-11-27T09:00:00Z",
                    "content": "AGY prompt",
                },
                {
                    "step_index": 1,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "created_at": "2026-11-27T09:00:01Z",
                    "input_tokens": 1000,
                    "cache_read_tokens": 500,
                    "output_tokens": 150,
                    "content": "Done",
                },
            ])

            codex_dir = root / ".codex"
            cdx_file = codex_dir / "sessions" / "rollout-cdx-1.jsonl"
            self._write_jsonl(cdx_file, [
                {
                    "timestamp": "2026-11-27T10:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "cdx-1", "cwd": "/tmp/Proj", "source": "cli"},
                },
                {
                    "timestamp": "2026-11-27T10:00:01Z",
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Codex prompt"}]},
                },
                {
                    "timestamp": "2026-11-27T10:00:02Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {"input_tokens": 3000, "output_tokens": 300, "reasoning_output_tokens": 100},
                            "total_token_usage": {"input_tokens": 3000, "output_tokens": 300},
                        },
                    },
                },
            ])

            ag_cache = root / "sessions_cache.json"
            cdx_cache = root / "codex_sessions_cache.json"

            with patch.object(scanner, "get_tokenizer", return_value=None), \
                 patch.object(scanner, "load_conversation_metadata", return_value=({}, {})):
                combined, cached, updated = scanner.scan_all_sessions(
                    brain_dir=str(brain),
                    codex_dir=str(codex_dir),
                    cache_path=str(ag_cache),
                    codex_cache_path=str(cdx_cache),
                )
                self.assertEqual(len(combined), 2)
                self.assertEqual(updated, 2)
                self.assertFalse(combined["ag-1"].get("deleted_from_disk"))
                self.assertFalse(combined["codex:rollout-cdx-1"].get("deleted_from_disk"))

                # Second run hits both caches independently
                combined2, cached2, updated2 = scanner.scan_all_sessions(
                    brain_dir=str(brain),
                    codex_dir=str(codex_dir),
                    cache_path=str(ag_cache),
                    codex_cache_path=str(cdx_cache),
                )
                self.assertEqual(cached2, 2)
                self.assertEqual(updated2, 0)
                self.assertFalse(combined2["codex:rollout-cdx-1"].get("deleted_from_disk"))

            # Delete Codex file from disk -> retained with deleted_from_disk=True
            cdx_file.unlink()
            with patch.object(scanner, "get_tokenizer", return_value=None), \
                 patch.object(scanner, "load_conversation_metadata", return_value=({}, {})):
                combined3, _, _ = scanner.scan_all_sessions(
                    brain_dir=str(brain),
                    codex_dir=str(codex_dir),
                    cache_path=str(ag_cache),
                    codex_cache_path=str(cdx_cache),
                )
            self.assertTrue(combined3["codex:rollout-cdx-1"]["deleted_from_disk"])

            payload = sync_cache.build_export_payload(combined, top_limit=10)
            self.assertEqual(payload["summary"]["total_sessions"], 2)
            self.assertEqual(payload["summary"]["tot_context_tok"], 4500)
            self.assertIn("by_provider", payload)
            self.assertEqual(payload["by_provider"]["antigravity"]["summary"]["total_sessions"], 1)
            self.assertEqual(payload["by_provider"]["antigravity"]["summary"]["tot_context_tok"], 1500)
            self.assertEqual(payload["by_provider"]["codex"]["summary"]["total_sessions"], 1)
            self.assertEqual(payload["by_provider"]["codex"]["summary"]["tot_context_tok"], 3000)

            top_providers = {item["provider"] for item in payload["top_conversations"]}
            self.assertEqual(top_providers, {"antigravity", "codex"})

    def test_tool_output_ignores_base64_input_image_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_dir = root / ".codex"
            cache_path = root / "codex_sessions_cache.json"
            sess_file = codex_dir / "sessions" / "rollout-img-1.jsonl"

            self._write_jsonl(sess_file, [
                {
                    "timestamp": "2026-11-27T12:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "img-1", "cwd": "/tmp/Proj", "source": "cli"},
                },
                {
                    "timestamp": "2026-11-27T12:00:01Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "output": [
                            {"type": "input_text", "text": "PDF page 1"},
                            {"type": "input_image", "image_url": "data:image/png;base64," + ("A" * 100000)},
                        ],
                    },
                },
            ])

            results, _, _ = codex_scanner.scan_codex_sessions(
                codex_dir=str(codex_dir),
                cache_path=str(cache_path),
            )
            day = results["codex:rollout-img-1"]["by_date"]["2026-11-27"]
            self.assertLess(day["tool_tok"], 50)
            self.assertGreater(day["tool_tok"], 0)


if __name__ == "__main__":
    unittest.main()
