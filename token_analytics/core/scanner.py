import os
import json
import sqlite3
from datetime import datetime
from collections import defaultdict
from tokenizers import Tokenizer

SYSTEM_PROMPT_ESTIMATE = 3500  # Overhead on each model turn (system prompt, tools, schemas)

def get_tokenizer():
    return Tokenizer.from_pretrained('Qwen/Qwen2.5-7B')

import urllib.parse

def load_conversation_metadata(db_path=None):
    """
    Reads actual human-readable conversation titles and workspace project names
    from Antigravity conversation_summaries.db.
    Returns:
        tuple: (title_map, project_map)
    """
    if db_path is None:
        db_path = os.path.expanduser('~/.gemini/antigravity/conversation_summaries.db')
    if not os.path.exists(db_path):
        return {}, {}
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT conversation_id, title, preview, workspace_uris FROM conversation_summaries")
        title_map = {}
        project_map = {}
        for cid, t, p, ws in cur.fetchall():
            # 1. Human-readable Title
            t = (t or "").strip()
            p = (p or "").strip()
            best = t if t else p
            if best:
                first_line = best.split("\n")[0].strip()
                title_map[cid] = first_line[:120]

            # 2. Project Name from Workspace URIs
            if ws:
                try:
                    uris = json.loads(ws)
                    for u in uris:
                        unquoted = urllib.parse.unquote(u)
                        clean_path = unquoted.replace("file://", "").rstrip("/")
                        bname = os.path.basename(clean_path)
                        if bname and not bname.startswith("subagent-"):
                            project_map[cid] = bname
                            break
                        elif bname and cid not in project_map:
                            project_map[cid] = bname
                except Exception:
                    pass
        conn.close()
        return title_map, project_map
    except Exception:
        return {}, {}

import re

def infer_project_name(text):
    """
    Fallback extractor for project name from conversation title or initial user request.
    Handles historical sessions where sqlite summary row was deleted.
    """
    if not text:
        return None
    stopwords = {'из', 'есть', 'в', 'на', 'с', 'по', 'этого', 'нашего', 'этом', 'проекте', 'проекта'}
    m = re.search(r'проект[ауе]?\s+([A-Za-zА-Яа-я0-9_-]+)', text, re.I)
    if m:
        cand = m.group(1).strip()
        if cand.lower() in stopwords:
            return None
        if cand.lower() == 'seo':
            return 'SEO'
        if cand.lower() == 'dialoganalytics':
            return 'DialogAnalytics'
        if cand.lower() == 'джойтиш':
            return 'Джойтиш'
        return cand
    return None

def scan_conversations(brain_dir=None, cache_path=None, force_refresh=False):
    """
    Scans all conversations in Antigravity brain_dir.
    Uses incremental caching via (mtime, size) to avoid re-tokenizing untouched sessions.
    Returns:
        dict: {
            session_id: {
                'id': session_id,
                'first_req': str,
                'file_path': str,
                'mtime': float,
                'size': int,
                'by_date': {
                    'YYYY-MM-DD': {
                        'user_tok': int,
                        'model_tok': int,
                        'thinking_tok': int,
                        'tool_tok': int,
                        'context_tok': int,
                        'model_calls': int,
                        'steps': int
                    }
                }
            }
        }
    """
    if brain_dir is None:
        brain_dir = os.path.expanduser('~/.gemini/antigravity/brain')
    
    if cache_path is None:
        cache_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'sessions_cache.json')

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    cache = {}
    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    if not os.path.exists(brain_dir):
        raise FileNotFoundError(f"Brain directory not found at: {brain_dir}")

    convo_ids = [d for d in os.listdir(brain_dir) if os.path.isdir(os.path.join(brain_dir, d))]
    titles_map, projects_map = load_conversation_metadata()
    
    # We will load the tokenizer only if there are sessions to process
    tok = None

    def count_tok(text):
        nonlocal tok
        if not text:
            return 0
        if not isinstance(text, str):
            text = str(text)
        if tok is None:
            tok = get_tokenizer()
        return len(tok.encode(text, add_special_tokens=False).ids)

    updated_sessions = 0
    cached_sessions = 0

    results = {}

    for c_id in convo_ids:
        logs_dir = os.path.join(brain_dir, c_id, '.system_generated', 'logs')
        tf_path = os.path.join(logs_dir, 'transcript_full.jsonl')
        t_path = os.path.join(logs_dir, 'transcript.jsonl')

        target_file = None
        if os.path.exists(tf_path) and os.path.getsize(tf_path) > 0:
            target_file = tf_path
        elif os.path.exists(t_path) and os.path.getsize(t_path) > 0:
            target_file = t_path

        if not target_file:
            continue

        try:
            st = os.stat(target_file)
            mtime = st.st_mtime
            size = st.st_size
        except OSError:
            continue

        # Check cache validity
        cached_entry = cache.get(c_id)
        if (
            not force_refresh
            and cached_entry
            and cached_entry.get('mtime') == mtime
            and cached_entry.get('size') == size
            and 'by_date' in cached_entry
        ):
            if c_id in titles_map:
                cached_entry['title'] = titles_map[c_id]
            elif 'title' not in cached_entry:
                cached_entry['title'] = cached_entry.get('first_req', '(нет темы)')
            if c_id in projects_map:
                cached_entry['project'] = projects_map[c_id]
            elif not cached_entry.get('project') or cached_entry.get('project') == '—':
                cached_entry['project'] = infer_project_name(cached_entry.get('title')) or infer_project_name(cached_entry.get('first_req')) or '—'
            results[c_id] = cached_entry
            cached_sessions += 1
            continue

        # Need to parse and tokenize
        steps = []
        try:
            with open(target_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            steps.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            continue

        if not steps:
            continue

        by_date = defaultdict(lambda: {
            'user_tok': 0,
            'model_tok': 0,
            'thinking_tok': 0,
            'tool_tok': 0,
            'context_tok': 0,
            'model_calls': 0,
            'steps': 0,
            'user_prompts': 0
        })

        first_req = ""
        current_history = SYSTEM_PROMPT_ESTIMATE
        file_default_date = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')

        for s in steps:
            cat = s.get('created_at', '')
            step_date = cat[:10] if (cat and len(cat) >= 10 and cat[0].isdigit()) else file_default_date

            stype = s.get('type')
            src = s.get('source')
            content = s.get('content', '')
            thinking = s.get('thinking', '')
            tc = s.get('tool_calls', [])

            by_date[step_date]['steps'] += 1

            if src == 'USER_EXPLICIT' or stype == 'USER_INPUT':
                u_cnt = count_tok(content)
                by_date[step_date]['user_tok'] += u_cnt
                by_date[step_date]['user_prompts'] += 1
                current_history += u_cnt

                if not first_req and content:
                    clean = content.replace('<USER_REQUEST>', '').replace('</USER_REQUEST>', '').strip()
                    first_line = clean.split('\n')[0].strip()
                    first_req = first_line[:70]

            elif src == 'MODEL' and stype == 'PLANNER_RESPONSE':
                m_cnt = count_tok(content)
                tc_cnt = count_tok(json.dumps(tc, ensure_ascii=False)) if tc else 0
                th_cnt = count_tok(thinking) if thinking else 0
                out_tot = m_cnt + tc_cnt

                by_date[step_date]['model_tok'] += out_tot
                by_date[step_date]['thinking_tok'] += th_cnt
                by_date[step_date]['model_calls'] += 1
                by_date[step_date]['context_tok'] += current_history

                current_history += (out_tot + th_cnt)
            else:
                # Tool output or system event
                tl_cnt = count_tok(content)
                by_date[step_date]['tool_tok'] += tl_cnt
                current_history += tl_cnt

        proj_val = projects_map.get(c_id) or infer_project_name(titles_map.get(c_id)) or infer_project_name(first_req) or '—'
        entry = {
            'id': c_id,
            'title': titles_map.get(c_id) or first_req or "(нет темы)",
            'project': proj_val,
            'first_req': first_req or "(нет текста запроса)",
            'file_path': target_file,
            'mtime': mtime,
            'size': size,
            'by_date': dict(by_date)
        }

        results[c_id] = entry
        cache[c_id] = entry
        updated_sessions += 1

    # Retain all cached historical sessions that have since been deleted from disk
    # to guarantee that historical token consumption and audits remain 100% complete
    for c_id, cached_entry in cache.items():
        if c_id not in results:
            entry_copy = dict(cached_entry)
            entry_copy['deleted_from_disk'] = True
            if c_id in titles_map:
                entry_copy['title'] = titles_map[c_id]
            elif 'title' not in entry_copy:
                entry_copy['title'] = entry_copy.get('first_req', '(нет темы)')
            if c_id in projects_map:
                entry_copy['project'] = projects_map[c_id]
            elif not entry_copy.get('project') or entry_copy.get('project') == '—':
                entry_copy['project'] = infer_project_name(entry_copy.get('title')) or infer_project_name(entry_copy.get('first_req')) or '—'
            cached_entry['title'] = entry_copy['title']
            cached_entry['project'] = entry_copy['project']
            results[c_id] = entry_copy

    # Save cache back
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Failed to save cache: {e}")

    return results, cached_sessions, updated_sessions
