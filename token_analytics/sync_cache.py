#!/usr/bin/env python3
"""
Lightweight background worker for QuotaAnalytics.
Incrementally scans brain/ sessions, retains historical cache, and exports antigravity_token_analysis.json.
Runs detached from SwiftBar without blocking UI.
"""
import os
import sys
import json

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from core.scanner import scan_conversations
from core.aggregator import aggregate_by_period, get_overall_summary, get_top_conversations
from core.comparator import compute_period_comparisons

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
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(export_payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        pass

if __name__ == '__main__':
    sync()
