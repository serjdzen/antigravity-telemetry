#!/usr/bin/env python3
import os
import sys
import json
import argparse
from tabulate import tabulate

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add current directory to path for clean imports
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from core.scanner import scan_conversations
from core.aggregator import aggregate_by_period, get_overall_summary, get_top_conversations
from core.comparator import compute_period_comparisons, evaluate_quota_hypothesis

def format_tokens(num):
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.2f}B"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}k"
    return str(num)

def main():
    parser = argparse.ArgumentParser(description="Antigravity 2.0 Modular Token Analytics & Audit Engine")
    parser.add_argument('--period', choices=['month', 'week', 'day'], default='month',
                        help="Aggregation period: month (default), week, or day")
    parser.add_argument('--compare', action='store_true', default=True,
                        help="Include percentage shares, MoM/WoW growth, and normalized daily metrics")
    parser.add_argument('--limit', type=int, default=None,
                        help="Limit output rows (e.g. last 14 days)")
    parser.add_argument('--refresh', action='store_true',
                        help="Force full re-scan of brain/ bypassing the incremental cache")
    parser.add_argument('--top', type=int, default=10,
                        help="Display top N largest sessions (default: 10)")
    parser.add_argument('--json-out', type=str, default=None,
                        help="Output path for JSON report (default: token_analytics/antigravity_token_analysis.json)")

    args = parser.parse_args()

    print(f"\n================================================================================")
    print(f"            ANTIGRAVITY 2.0 TOKEN ANALYTICS & AUDIT ENGINE                     ")
    print(f"================================================================================")
    print("Scanning sessions and applying incremental cache...")

    sessions, cached_cnt, updated_cnt = scan_conversations(force_refresh=args.refresh)
    print(f"✓ Total sessions processed: {len(sessions)} (Cached: {cached_cnt}, Re-scanned: {updated_cnt})\n")

    summary = get_overall_summary(sessions)
    aggregated = aggregate_by_period(sessions, period_type=args.period)
    comparisons = compute_period_comparisons(aggregated, summary)

    # 1. Overall Summary Banner
    print("--------------------------------------------------------------------------------")
    print("📊 ИТОГОВАЯ СВОДКА ЗА ВСЮ ИСТОРИЮ:")
    print(f"  • Активный период:            {summary['first_date']} — {summary['last_date']} ({summary['active_days']} активных дней)")
    print(f"  • Всего сессий диалогов:      {summary['total_sessions']:,}")
    print(f"  • Всего шагов (сообщений):    {summary['tot_steps']:,}")
    print(f"  • Всего обращений к API:      {summary['tot_model_calls']:,} вызовов")
    print(f"  • Входящие токены (User):     {summary['tot_user_tok']:>12,} ({format_tokens(summary['tot_user_tok'])})")
    print(f"  • Ответы модели (Model):      {summary['tot_model_tok']:>12,} ({format_tokens(summary['tot_model_tok'])})")
    print(f"  • Рассуждения (Thinking CoT): {summary['tot_thinking_tok']:>12,} ({format_tokens(summary['tot_thinking_tok'])})")
    print(f"  • Данные инструментов (Tools):{summary['tot_tool_tok']:>12,} ({format_tokens(summary['tot_tool_tok'])})")
    print(f"  • СУММАРНО УНИКАЛЬНЫХ:        {summary['tot_unique_tok']:>12,} ({format_tokens(summary['tot_unique_tok'])})")
    print(f"  • КУМУЛЯТИВНЫЙ КОНТЕКСТ:      {summary['tot_context_tok']:>12,} ({format_tokens(summary['tot_context_tok'])})")
    print("--------------------------------------------------------------------------------\n")

    # 2. Period Table
    table_data = []
    display_items = comparisons[-args.limit:] if args.limit else comparisons

    for c in display_items:
        growth_str = f"{c['growth_uniq_pct']:+.1f}%" if c['growth_uniq_pct'] != 0 else "-"
        table_data.append([
            c['period'],
            c['sessions_count'],
            c['active_days'],
            f"{c['unique_tok']:,}",
            f"{c['unique_share_pct']:.1f}%",
            growth_str,
            f"{c['avg_daily_uniq']:,.0f}",
            f"{c['thinking_ratio_pct']:.1f}%",
            f"{c['context_tok']:,}",
            f"{c['avg_daily_calls']:.1f}"
        ])

    period_title = {
        'month': "ПОМЕСЯЧНОЕ СРАВНЕНИЕ (ДЕТАЛЬНАЯ ДИНАМИКА И ДОЛИ)",
        'week': "ПОНЕДЕЛЬНЫЙ СРЕЗ (WEEKS)",
        'day': "ПОСУТОЧНЫЙ СРЕЗ (DAYS)"
    }.get(args.period, "СРЕЗ ПО ПЕРИОДАМ")

    headers = [
        "Период", "Сессий", "Дней", "Уникальных", "Доля %",
        "Прирост", "Токенов/день", "Thinking %", "Контекст (Всего)", "Вызовов/день"
    ]

    print(f"📅 {period_title}:")
    print(tabulate(table_data, headers=headers, tablefmt="github"))
    print("\n")

    # 3. Anomaly Analysis & Quota Hypothesis (for monthly breakdown)
    if args.period == 'month' and len(comparisons) >= 2:
        hypothesis_result = evaluate_quota_hypothesis(comparisons)
        print("================================================================================")
        print("🔍 ДИАГНОСТИЧЕСКИЙ АУДИТ ГИПОТЕЗЫ: «ПЕРЕРАСХОД vs ЛИМИТЫ GOOGLE»")
        print("================================================================================")
        print(hypothesis_result['verdict_text'])
        print(f"\nДетальные метрики последнего месяца ({hypothesis_result['latest_month']}):")
        print(f"  • Среднесуточный расход: {hypothesis_result['latest_daily_uniq']:,.0f} токенов/день (база прошлых месяцев: {hypothesis_result['prior_daily_uniq_avg']:,.0f}, {hypothesis_result['daily_burn_diff_pct']:+.1f}%)")
        print(f"  • Доля Thinking CoT:     {hypothesis_result['latest_th_ratio']:.1f}% (база прошлых месяцев: {hypothesis_result['prior_th_ratio']:.1f}%, сдвиг: {hypothesis_result['th_ratio_diff']:+.1f}%)")
        print(f"  • Кумулятивный контекст: изменение среднесуточного объема на {hypothesis_result['daily_ctx_diff_pct']:+.1f}%")
        print("================================================================================\n")

    # 4. Top Conversations
    if args.top and args.top > 0:
        top_convos = get_top_conversations(sessions, limit=args.top)
        top_table = []
        for cv in top_convos:
            top_table.append([
                cv['id'][:8] + "...",
                cv['start_date'],
                cv['steps'],
                cv['first_req'],
                f"{cv['unique_tok']:,}",
                f"{cv['context_tok']:,}"
            ])
        print(f"🏆 ТОП-{args.top} САМЫХ БОЛЬШИХ СЕССИЙ:")
        c_headers = ["ID", "Дата", "Шагов", "Первый запрос / Тема", "Уникальных", "Контекст"]
        print(tabulate(top_table, headers=c_headers, tablefmt="github"))
        print("\n")

    # 5. Export JSON
    out_path = args.json_out
    if not out_path:
        out_path = os.path.join(current_dir, 'antigravity_token_analysis.json')

    export_payload = {
        'summary': summary,
        'monthly': comparisons if args.period == 'month' else compute_period_comparisons(aggregate_by_period(sessions, 'month'), summary),
        'weekly': comparisons if args.period == 'week' else compute_period_comparisons(aggregate_by_period(sessions, 'week'), summary),
        'daily': comparisons if args.period == 'day' else compute_period_comparisons(aggregate_by_period(sessions, 'day'), summary),
        'top_conversations': get_top_conversations(sessions, limit=20)
    }

    try:
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(export_payload, f, ensure_ascii=False, indent=2)
        print(f"💾 Данные успешно сохранены в: {out_path}\n")
    except Exception as e:
        print(f"Warning: Failed to export json: {e}")

if __name__ == '__main__':
    main()
