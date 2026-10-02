from collections import defaultdict
from datetime import datetime

def _init_bucket():
    return {
        'sessions': set(),
        'active_dates': set(),
        'user_tok': 0,
        'model_tok': 0,
        'thinking_tok': 0,
        'tool_tok': 0,
        'unique_tok': 0,
        'context_tok': 0,
        'model_calls': 0,
        'steps': 0,
        'user_prompts': 0
    }

def aggregate_by_period(sessions, period_type='month'):
    """
    Aggregates sessions data by period: 'month' (YYYY-MM), 'week' (YYYY-Www), or 'day' (YYYY-MM-DD).
    Returns a sorted dict of period -> metrics dictionary.
    """
    buckets = defaultdict(_init_bucket)

    for c_id, s_data in sessions.items():
        by_date = s_data.get('by_date', {})
        for d_str, d_metrics in by_date.items():
            if period_type == 'month':
                key = d_str[:7]  # YYYY-MM
            elif period_type == 'week':
                try:
                    dt = datetime.strptime(d_str, '%Y-%m-%d')
                    key = dt.strftime('%Y-W%U')
                except Exception:
                    key = 'unknown'
            else:  # 'day'
                key = d_str

            b = buckets[key]
            b['sessions'].add(c_id)
            b['active_dates'].add(d_str)
            b['user_tok'] += d_metrics.get('user_tok', 0)
            b['model_tok'] += d_metrics.get('model_tok', 0)
            b['thinking_tok'] += d_metrics.get('thinking_tok', 0)
            b['tool_tok'] += d_metrics.get('tool_tok', 0)
            b['context_tok'] += d_metrics.get('context_tok', 0)
            b['model_calls'] += d_metrics.get('model_calls', 0)
            b['steps'] += d_metrics.get('steps', 0)
            b['user_prompts'] += d_metrics.get('user_prompts', 0)

    # Finalize totals
    sorted_keys = sorted([k for k in buckets.keys() if k != 'unknown'])
    result = {}
    for k in sorted_keys:
        b = buckets[k]
        u = b['user_tok']
        m = b['model_tok']
        th = b['thinking_tok']
        tl = b['tool_tok']
        uniq = u + m + th + tl
        result[k] = {
            'period': k,
            'sessions_count': len(b['sessions']),
            'active_days_count': len(b['active_dates']),
            'user_tok': u,
            'model_tok': m,
            'thinking_tok': th,
            'tool_tok': tl,
            'unique_tok': uniq,
            'context_tok': b['context_tok'],
            'model_calls': b['model_calls'],
            'steps': b['steps'],
            'user_prompts': b['user_prompts']
        }

    return result

def get_overall_summary(sessions):
    """
    Computes grand totals across the entire history.
    """
    tot_sessions = len(sessions)
    all_dates = set()
    tot_user = 0
    tot_model = 0
    tot_thinking = 0
    tot_tool = 0
    tot_context = 0
    tot_calls = 0
    tot_steps = 0
    tot_prompts = 0

    for c_id, s_data in sessions.items():
        by_date = s_data.get('by_date', {})
        for d_str, d_metrics in by_date.items():
            all_dates.add(d_str)
            tot_user += d_metrics.get('user_tok', 0)
            tot_model += d_metrics.get('model_tok', 0)
            tot_thinking += d_metrics.get('thinking_tok', 0)
            tot_tool += d_metrics.get('tool_tok', 0)
            tot_context += d_metrics.get('context_tok', 0)
            tot_calls += d_metrics.get('model_calls', 0)
            tot_steps += d_metrics.get('steps', 0)
            tot_prompts += d_metrics.get('user_prompts', 0)

    sorted_dates = sorted(all_dates)
    tot_unique = tot_user + tot_model + tot_thinking + tot_tool

    return {
        'total_sessions': tot_sessions,
        'active_days': len(all_dates),
        'first_date': sorted_dates[0] if sorted_dates else None,
        'last_date': sorted_dates[-1] if sorted_dates else None,
        'tot_user_tok': tot_user,
        'tot_model_tok': tot_model,
        'tot_thinking_tok': tot_thinking,
        'tot_tool_tok': tot_tool,
        'tot_unique_tok': tot_unique,
        'tot_context_tok': tot_context,
        'tot_model_calls': tot_calls,
        'tot_steps': tot_steps,
        'tot_user_prompts': tot_prompts
    }

def get_top_conversations(sessions, limit=15, sort_by='unique_tok'):
    """
    Returns top conversations sorted by unique tokens or context.
    """
    conv_list = []
    for c_id, s_data in sessions.items():
        by_date = s_data.get('by_date', {})
        dates = sorted(by_date.keys())
        u = sum(m.get('user_tok', 0) for m in by_date.values())
        m_tok = sum(m.get('model_tok', 0) for m in by_date.values())
        th = sum(m.get('thinking_tok', 0) for m in by_date.values())
        tl = sum(m.get('tool_tok', 0) for m in by_date.values())
        ctx = sum(m.get('context_tok', 0) for m in by_date.values())
        steps = sum(m.get('steps', 0) for m in by_date.values())
        uniq = u + m_tok + th + tl

        conv_list.append({
            'id': c_id,
            'start_date': dates[0] if dates else 'unknown',
            'first_req': s_data.get('first_req', '(нет текста)'),
            'user_tok': u,
            'model_tok': m_tok,
            'thinking_tok': th,
            'tool_tok': tl,
            'unique_tok': uniq,
            'context_tok': ctx,
            'steps': steps
        })

    conv_list.sort(key=lambda x: x.get(sort_by, 0), reverse=True)
    return conv_list[:limit]
