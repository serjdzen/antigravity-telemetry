def compute_period_comparisons(aggregated_dict, total_summary):
    """
    Computes comparative statistics:
    - % share of total history
    - Growth rate over previous period (MoM / WoW)
    - Normalized metrics (tokens/active day, tokens/session, calls/day)
    - Thinking CoT ratio
    """
    tot_unique = total_summary.get('tot_unique_tok', 1) or 1
    tot_context = total_summary.get('tot_context_tok', 1) or 1

    sorted_keys = sorted(aggregated_dict.keys())
    results = []

    prev_item = None

    for k in sorted_keys:
        item = aggregated_dict[k]
        u = item['user_tok']
        m = item['model_tok']
        th = item['thinking_tok']
        tl = item['tool_tok']
        uniq = item['unique_tok']
        ctx = item['context_tok']
        sess_cnt = item['sessions_count']
        act_days = item['active_days_count'] or 1
        calls = item['model_calls']

        share_uniq = (uniq / tot_unique) * 100.0
        share_ctx = (ctx / tot_context) * 100.0

        if prev_item and prev_item['unique_tok'] > 0:
            growth_uniq = ((uniq - prev_item['unique_tok']) / prev_item['unique_tok']) * 100.0
        else:
            growth_uniq = 0.0

        if prev_item and prev_item['context_tok'] > 0:
            growth_ctx = ((ctx - prev_item['context_tok']) / prev_item['context_tok']) * 100.0
        else:
            growth_ctx = 0.0

        avg_daily_uniq = uniq / act_days
        avg_daily_ctx = ctx / act_days
        avg_daily_calls = calls / act_days
        avg_sess_uniq = uniq / (sess_cnt or 1)
        
        tot_out = m + th
        th_ratio = (th / tot_out * 100.0) if tot_out > 0 else 0.0

        comp_entry = {
            'period': k,
            'sessions_count': sess_cnt,
            'active_days': act_days,
            'unique_tok': uniq,
            'unique_share_pct': share_uniq,
            'growth_uniq_pct': growth_uniq,
            'context_tok': ctx,
            'context_share_pct': share_ctx,
            'growth_ctx_pct': growth_ctx,
            'avg_daily_uniq': avg_daily_uniq,
            'avg_daily_ctx': avg_daily_ctx,
            'avg_daily_calls': avg_daily_calls,
            'avg_sess_uniq': avg_sess_uniq,
            'thinking_tok': th,
            'thinking_ratio_pct': th_ratio,
            'model_calls': calls,
            'user_prompts': item.get('user_prompts', 0),
            'avg_daily_prompts': item.get('user_prompts', 0) / act_days,
            'user_tok': u,
            'model_tok': m,
            'tool_tok': tl
        }

        results.append(comp_entry)
        prev_item = item

    return results

def evaluate_quota_hypothesis(monthly_comparisons):
    """
    Evaluates whether recent account burnout is due to:
    1) Massive volume increase by user
    2) Google quota restrictions / model thinking overhead
    """
    if len(monthly_comparisons) < 2:
        return "Недостаточно данных для сравнения периодов."

    # Look at latest month (e.g. September) vs previous months
    latest = monthly_comparisons[-1]
    prev = monthly_comparisons[-2]
    
    # Calculate baseline from prior months (excluding latest)
    prior_months = monthly_comparisons[:-1]
    avg_prior_daily_uniq = sum(m['avg_daily_uniq'] for m in prior_months) / len(prior_months)
    avg_prior_daily_calls = sum(m['avg_daily_calls'] for m in prior_months) / len(prior_months)
    avg_prior_daily_ctx = sum(m['avg_daily_ctx'] for m in prior_months) / len(prior_months)
    avg_prior_th_ratio = sum(m['thinking_ratio_pct'] for m in prior_months) / len(prior_months)

    daily_burn_diff_pct = ((latest['avg_daily_uniq'] - avg_prior_daily_uniq) / avg_prior_daily_uniq) * 100.0
    daily_calls_diff_pct = ((latest['avg_daily_calls'] - avg_prior_daily_calls) / avg_prior_daily_calls) * 100.0
    daily_ctx_diff_pct = ((latest['avg_daily_ctx'] - avg_prior_daily_ctx) / avg_prior_daily_ctx) * 100.0
    th_ratio_diff = latest['thinking_ratio_pct'] - avg_prior_th_ratio

    insights = {
        'latest_month': latest['period'],
        'latest_daily_uniq': latest['avg_daily_uniq'],
        'prior_daily_uniq_avg': avg_prior_daily_uniq,
        'daily_burn_diff_pct': daily_burn_diff_pct,
        'daily_calls_diff_pct': daily_calls_diff_pct,
        'daily_ctx_diff_pct': daily_ctx_diff_pct,
        'latest_th_ratio': latest['thinking_ratio_pct'],
        'prior_th_ratio': avg_prior_th_ratio,
        'th_ratio_diff': th_ratio_diff
    }

    # Hypothesis verdict
    if daily_burn_diff_pct > 60.0:
        verdict = (
            f"🔥 ГИПОТЕЗА 1 (РЕАЛЬНЫЙ РОСТ НАГРУЗКИ): Подтверждена!\n"
            f"В месяце {latest['period']} среднесуточный расход уникальных токенов вырос на {daily_burn_diff_pct:+.1f}% "
            f"по сравнению со средней нормой предыдущих месяцев (с {avg_prior_daily_uniq:,.0f} до {latest['avg_daily_uniq']:,.0f} токенов/день). "
            f"Число вызовов к API в день изменилось на {daily_calls_diff_pct:+.1f}%. "
            f"Аккаунты сгорают прежде всего из-за кратно возросшего физического объема генераций."
        )
    elif daily_burn_diff_pct < 20.0 and latest['growth_uniq_pct'] < 20.0:
        verdict = (
            f"🔒 ГИПОТЕЗА 2 (УЖЕСТОЧЕНИЕ ЛИМИТОВ GOOGLE): Подтверждена!\n"
            f"В месяце {latest['period']} среднесуточный расход токенов остался на базовом уровне ({daily_burn_diff_pct:+.1f}% к предыдущим месяцам). "
            f"Однако если при сопоставимой нагрузке аккаунты начали отлетать чаще — Google урезал лимиты квот (RPM/TPM/Daily Quota) "
            f"или снизил пороги антифрод-фильтров на бесплатные/триальные аккаунты."
        )
    else:
        verdict = (
            f"⚖️ СМЕШАННЫЙ ФАКТОР:\n"
            f"Расход уникальных токенов вырос умеренно ({daily_burn_diff_pct:+.1f}%), "
            f"но при этом кумулятивный контекст изменился на {daily_ctx_diff_pct:+.1f}%, "
            f"а доля Thinking CoT изменилась на {th_ratio_diff:+.1f}%. "
            f"Отлеты аккаунтов вызваны комбинацией роста длины агентных сессий и обновленной политикой Google."
        )

    insights['verdict_text'] = verdict
    return insights
