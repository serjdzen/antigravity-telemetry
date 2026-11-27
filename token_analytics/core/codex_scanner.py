import glob
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime

try:
    from .scanner import _approx_tokens
except ImportError:
    from core.scanner import _approx_tokens

CODEX_CACHE_SCHEMA_VERSION = 2


def _is_system_user_text(text):
    """Filters Codex CLI injected XML/system wrappers from genuine user prompts."""
    if not text or not isinstance(text, str):
        return True
    stripped = text.lstrip()
    if not stripped:
        return True
    if stripped.startswith("<") or stripped.startswith("# AGENTS.md"):
        return True
    return False


def _extract_message_text(content):
    """Extracts readable text from Codex response_item message content."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict):
            txt = item.get("text")
            if isinstance(txt, str) and txt:
                parts.append(txt)
        elif isinstance(item, str) and item:
            parts.append(item)
    return "\n".join(parts)


def _extract_tool_output_text(payload):
    """Extracts readable text from tool outputs while skipping base64 image blobs."""
    out = payload.get("output")
    if out is None:
        return ""
    if isinstance(out, str):
        return out
    image_types = ("input_image", "image", "image_url")
    if isinstance(out, list):
        parts = []
        for item in out:
            if isinstance(item, dict):
                if item.get("type") in image_types:
                    continue
                txt = item.get("text")
                if isinstance(txt, str) and txt:
                    parts.append(txt)
            elif isinstance(item, str) and item:
                parts.append(item)
        return "\n".join(parts)
    if isinstance(out, dict):
        if out.get("type") in image_types:
            return ""
        txt = out.get("text")
        if isinstance(txt, str):
            return txt
    try:
        return json.dumps(out, ensure_ascii=False)
    except Exception:
        return str(out)


def _parse_subagent_info(source):
    """Detects whether a Codex session is a subagent and returns its role label."""
    if isinstance(source, dict) and "subagent" in source:
        sub = source.get("subagent")
        if isinstance(sub, str) and sub.strip():
            return True, sub.strip()
        if isinstance(sub, dict):
            if sub.get("other"):
                return True, str(sub["other"])
            keys = [str(k) for k in sub.keys() if k]
            return True, (", ".join(keys) if keys else "subagent")
        return True, "subagent"
    return False, ""


def _atomic_write_json(path, payload):
    """Writes JSON atomically via temp file + os.replace."""
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_codex_cache_", suffix=".json", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _collect_codex_jsonl_files(codex_dir):
    """Collects rollout JSONL files from sessions/ and archived_sessions/."""
    files = []
    seen = set()
    for subdir in ("sessions", "archived_sessions"):
        base = os.path.join(codex_dir, subdir)
        if not os.path.isdir(base):
            continue
        pattern = os.path.join(base, "**", "*.jsonl")
        for fp in glob.glob(pattern, recursive=True):
            if os.path.isfile(fp):
                real = os.path.abspath(fp)
                if real not in seen:
                    seen.add(real)
                    files.append(real)
    files.sort()
    return files


def scan_codex_sessions(codex_dir=None, cache_path=None, force_refresh=False):
    """
    Incrementally scans local OpenAI Codex CLI JSONL sessions in ~/.codex/sessions/
    and ~/.codex/archived_sessions/ using an isolated codex_sessions_cache.json.
    """
    if codex_dir is None:
        codex_dir = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")

    if cache_path is None:
        cache_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data",
            "codex_sessions_cache.json",
        )

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    cache = {}
    if not force_refresh and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    cache = loaded
        except Exception:
            cache = {}

    jsonl_files = _collect_codex_jsonl_files(codex_dir) if os.path.isdir(codex_dir) else []

    updated_sessions = 0
    cached_sessions = 0
    results = {}

    for target_file in jsonl_files:
        file_stem = os.path.splitext(os.path.basename(target_file))[0]
        cache_key = f"codex:{file_stem}"

        try:
            st = os.stat(target_file)
            mtime = st.st_mtime
            size = st.st_size
        except OSError:
            continue

        if size <= 0:
            continue

        cached_entry = cache.get(cache_key)
        if (
            not force_refresh
            and isinstance(cached_entry, dict)
            and cached_entry.get("mtime") == mtime
            and cached_entry.get("size") == size
            and cached_entry.get("schema_version") == CODEX_CACHE_SCHEMA_VERSION
            and "by_date" in cached_entry
        ):
            entry_copy = dict(cached_entry)
            entry_copy["provider"] = "codex"
            entry_copy["deleted_from_disk"] = False
            results[cache_key] = entry_copy
            cached_sessions += 1
            continue

        by_date = defaultdict(lambda: {
            "user_tok": 0,
            "model_tok": 0,
            "thinking_tok": 0,
            "tool_tok": 0,
            "context_tok": 0,
            "model_calls": 0,
            "steps": 0,
            "user_prompts": 0,
        })

        session_id = ""
        is_subagent = False
        subagent_role = ""
        project_name = "—"
        first_req = ""
        prev_total_usage = None
        file_default_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")

        try:
            with open(target_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue

                    ts = obj.get("timestamp", "")
                    step_date = (
                        ts[:10]
                        if (isinstance(ts, str) and len(ts) >= 10 and ts[0].isdigit())
                        else file_default_date
                    )
                    etype = obj.get("type")
                    payload = obj.get("payload")
                    if not isinstance(payload, dict):
                        payload = {}

                    if etype == "session_meta":
                        if not session_id and payload.get("id"):
                            session_id = str(payload["id"])
                        sub_flag, sub_role = _parse_subagent_info(payload.get("source"))
                        if sub_flag:
                            is_subagent = True
                            subagent_role = sub_role
                        cwd = payload.get("cwd")
                        if isinstance(cwd, str) and cwd.strip():
                            bname = os.path.basename(cwd.rstrip("/\\"))
                            if bname:
                                project_name = bname

                    elif etype == "response_item":
                        ptype = payload.get("type")
                        if ptype == "message" and payload.get("role") == "user":
                            txt = _extract_message_text(payload.get("content"))
                            if not _is_system_user_text(txt):
                                by_date[step_date]["steps"] += 1
                                by_date[step_date]["user_tok"] += _approx_tokens(txt)
                                if not is_subagent:
                                    by_date[step_date]["user_prompts"] += 1
                                if not first_req:
                                    first_line = txt.strip().split("\n")[0].strip()
                                    first_req = first_line[:200]
                        elif ptype in ("function_call_output", "custom_tool_call_output"):
                            out_txt = _extract_tool_output_text(payload)
                            by_date[step_date]["steps"] += 1
                            by_date[step_date]["tool_tok"] += _approx_tokens(out_txt)

                    elif etype == "event_msg" and payload.get("type") == "token_count":
                        info = payload.get("info")
                        if not isinstance(info, dict):
                            continue
                        last_usage = info.get("last_token_usage")
                        if not isinstance(last_usage, dict):
                            continue
                        total_usage = info.get("total_token_usage")
                        if total_usage is not None and total_usage == prev_total_usage:
                            continue
                        prev_total_usage = total_usage

                        inp = max(0, int(last_usage.get("input_tokens") or 0))
                        out = max(0, int(last_usage.get("output_tokens") or 0))
                        reas = max(0, int(last_usage.get("reasoning_output_tokens") or 0))
                        th_cnt = min(out, reas)
                        m_cnt = max(0, out - th_cnt)

                        by_date[step_date]["context_tok"] += inp
                        by_date[step_date]["model_tok"] += m_cnt
                        by_date[step_date]["thinking_tok"] += th_cnt
                        by_date[step_date]["model_calls"] += 1
                        by_date[step_date]["steps"] += 1
        except Exception:
            continue

        if not by_date:
            continue

        clean_req = first_req or "(нет текста запроса)"
        if is_subagent:
            role_tag = subagent_role or "subagent"
            title = f"[subagent: {role_tag}] {first_req}" if first_req else f"[subagent: {role_tag}]"
        else:
            title = first_req or "(Codex session)"

        entry = {
            "id": session_id or file_stem,
            "provider": "codex",
            "is_subagent": is_subagent,
            "title": title,
            "project": project_name,
            "first_req": clean_req,
            "file_path": target_file,
            "mtime": mtime,
            "size": size,
            "schema_version": CODEX_CACHE_SCHEMA_VERSION,
            "deleted_from_disk": False,
            "by_date": dict(by_date),
        }

        results[cache_key] = entry
        cache[cache_key] = entry
        updated_sessions += 1

    for cache_key, cached_entry in cache.items():
        if cache_key not in results and isinstance(cached_entry, dict) and "by_date" in cached_entry:
            entry_copy = dict(cached_entry)
            entry_copy["provider"] = "codex"
            entry_copy["deleted_from_disk"] = True
            cached_entry["provider"] = "codex"
            cached_entry["deleted_from_disk"] = True
            results[cache_key] = entry_copy

    try:
        if cache or os.path.exists(cache_path):
            _atomic_write_json(cache_path, cache)
    except Exception as e:
        print(f"Warning: Failed to save codex cache: {e}")

    return results, cached_sessions, updated_sessions
