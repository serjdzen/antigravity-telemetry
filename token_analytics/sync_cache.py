#!/usr/bin/env python3
"""
Lightweight background worker for QuotaAnalytics.
Incrementally scans brain/ sessions, retains historical cache, and exports antigravity_token_analysis.json.
Runs detached from SwiftBar without blocking UI.
"""
import os
import sys
import json
import tempfile
import traceback
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from core.scanner import scan_conversations
from core.aggregator import aggregate_by_period, get_overall_summary, get_top_conversations
from core.comparator import compute_period_comparisons


def _log_error(err_text):
    """Appends sync errors to a bounded log file so background failures are visible."""
    log_path = os.path.join(current_dir, 'sync_cache.log')
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 102400:
            os.replace(log_path, log_path + '.1')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now().isoformat()}] {err_text}\n")
    except Exception:
        pass


def _atomic_write_json(path, payload):
    """Writes JSON atomically via temp file and os.replace."""
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix='.tmp_analysis_', suffix='.json', dir=dir_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def sync():
    try:
        sessions, _, _ = scan_conversations(force_refresh=False)
        summary = get_overall_summary(sessions)
        out_path = os.path.join(current_dir, 'antigravity_token_analysis.json')
        export_payload = {
            'summary': summary,
            'monthly': compute_period_comparisons(aggregate_by_period(sessions, 'month'), summary),
            'weekly': compute_period_comparisons(aggregate_by_period(sessions, 'week'), summary),
            'daily': compute_period_comparisons(aggregate_by_period(sessions, 'day'), summary),
            'top_conversations': get_top_conversations(sessions, limit=50)
        }
        _atomic_write_json(out_path, export_payload)
        return 0
    except Exception:
        _log_error(traceback.format_exc())
        return 1


if __name__ == '__main__':
    sys.exit(sync())
