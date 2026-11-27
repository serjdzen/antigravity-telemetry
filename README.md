# ⚡ Antigravity Telemetry

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Antigravity 2.0](https://img.shields.io/badge/Google-Antigravity%202.0-4285F4.svg)]()
[![OpenAI Codex CLI](https://img.shields.io/badge/OpenAI-Codex%20CLI-10a37f.svg)]()
[![macOS SwiftBar](https://img.shields.io/badge/macOS-SwiftBar-orange.svg)](https://github.com/swiftbar/SwiftBar)
[![1-Click Switcher](https://img.shields.io/badge/macOS-1--Click%20Account%20Switcher-purple.svg)]()
[![Privacy: 100% Local](https://img.shields.io/badge/privacy-100%25%20Local--First-success.svg)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Multi-account quota monitor, 1-click macOS account switcher, and unified local token/context analytics for Google Antigravity 2.0 and OpenAI Codex CLI.**  
> Track live 5-hour and 7-day quota pools in the macOS Menu Bar, switch Antigravity accounts in one click without Keychain popup storms, and audit real AI agent token burn across both **Antigravity** and **OpenAI Codex** in an instant Liquid Glass Dashboard.

---

## 📌 What Problem Does It Solve?

| Developer Pain Point | How Antigravity Telemetry Solves It |
|---|---|
| **Multi-account quota blind spot** — IDE UI only shows the active profile and hides the true 7-day weekly cooldown pool. | **Live Quota Bar (`antigravity-quota-bar/`)** polls all accounts in parallel (~1.5s), displaying both **5h** and **7d** pools with MRU rotation in SwiftBar and the web dashboard. |
| **Painful account switching & macOS Keychain popup storms** when rotating profiles in Antigravity 2.0. | **Native Account Switcher (`account_switcher/`)** updates `gemini/antigravity` Keychain without `-A`, validates OAuth pre-flight, checks active streaming sockets, and relaunches `Antigravity 2.app` cleanly (**0 Keychain dialogs**). |
| **Hidden token burn across multiple AI agents** — long ReAct sessions expand quadratically ($\sim O(N^2)$) across both Antigravity and Codex CLI. | **Unified Local Token Analytics (`token_analytics/`)** scans local Antigravity (`brain/`) and OpenAI Codex CLI (`~/.codex/sessions/`) logs with **zero Codex OAuth**, showing combined & per-agent KPIs, charts, and Top-20 heaviest sessions. |
| **Headless Linux VPS agents hitting limits silently** during cron or background runs. | **Server Watchdog (`server-watchdog/`)** monitors `agy` CLI quotas on headless servers and sends Telegram alerts at $\le 20\%$ and $\le 5\%$. |

---

## 🧠 Why It Matters: The $O(N^2)$ ReAct Context Trap

Developers actively building with **Google Antigravity 2.0** and **OpenAI Codex CLI** regularly hit two hidden bottlenecks:

1. **The ReAct Context Explosion ($\sim O(N^2)$):**  
   Autonomous AI coding agents transmit the *entire accumulated session history*—system prompts, MCP tool schemas, tool outputs, terminal logs, and reasoning chains—on every step $N$:
   $$\text{Context}_N = \text{System Prompt} + \text{MCP Schema} + \sum_{i=1}^{N-1}\text{Step}_i + \text{Step}_N$$
   A single 2,000–3,500-step session can consume **hundreds of millions to billions of cumulative context tokens**, draining a 7-day quota pool in hours.
2. **User Prompts vs. Model Calls (`~x11.4` Multiplier):**  
   Antigravity Telemetry separates your actual typing (**User Prompts**, genuine Enter keypresses) from internal agentic operations (**Model API Calls**), exposing the real **~x11.4 agentic multiplier** (often spiking to x16–x18 with subagents and heavy MCP setups).

---

## 🏗 Architecture Overview

```text
                                 Antigravity Telemetry
                                           │
       ┌───────────────────┬───────────────┴───────────────┬───────────────────┐
       ▼                   ▼                               ▼                   ▼
[1. Live Quota Bar]  [2. Account Switcher]       [3. Token Analytics]   [4. Server Watchdog]
(antigravity-        (account_switcher/)         (token_analytics/)     (server-watchdog/)
 quota-bar/)               │                               │                   │
       │             • Antigravity 2.0 macOS     • Antigravity brain/    • Headless VPS Monitor
• Multi-Account      • Keychain (no -A flag)     • Codex CLI sessions/   • Telegram Alerts
  OAuth + Local IPC  • Pre-flight OAuth Check    • Isolated Caches         (≤20% & ≤5%)
• 5h & 7d Pools      • Active Socket Guard       • Native API Counters   • Cron-Ready Daemon
• MRU Rotation       • SwiftBar / Dashboard      • Combined + Per-Agent
• SwiftBar & UI        / CLI (--switch)            KPIs & Top-20
```

---

## 🖥 Key Components

### 1. ⚡ Live Quota Bar & SwiftBar Widget (`antigravity-quota-bar/`)

- **Multi-Account Concurrent Checking:** Polls all configured Google profiles in parallel via `ThreadPoolExecutor` in **~1.5 seconds**.
- **Dual Limits & Local IPC Discovery:** Reads live 5-hour and 7-day quota pools for **Gemini** and **Claude** via Google Cloud Code endpoints and deterministic local Language Server IPC (`~/.gemini/antigravity/daemon/hub_*.json` with live PID check).
- **Antigravity 2.0 Active Account & MRU Rotation:** Detects the active profile via `app_storage.json` (`agy.onboarding.lastLoginUsername`), pins it first, and orders remaining profiles by Most Recently Used (`last_used_at`).
- **Menu Hygiene:** Shortens `@gmail.com` suffixes in the SwiftBar dropdown while preserving full emails for identity, supports custom profile notes, and lets you hide/show secondary accounts from the menu bar.
- **Zero Background Overhead & Anti-Freeze:** `socket.setdefaulttimeout(8)`, graceful cache fallback (`recalculate_cached_model`), and `sys.dont_write_bytecode = True` to prevent `[?]` SwiftBar artifacts.

#### Menu Bar Preview (SwiftBar):
```text
✨ 94% · 4h 7m
─────────────────────────────────────────────
user1 (Active)
   5h:  [████████]  94%  (4h 7m)
   7d:  [████████]  99%  (6d 23h)
user2 (Pro through May 31)
   5h:  [░░░░░░░░]   5%  (1h 23m)
   7d:  [███████░]  84%  (6d 15h)
user3
   5h:  [████████] 100%  (4h 59m)
   7d:  [░░░░░░░░]   0%  (2d 1h)  <-- 7-day cooldown highlighted
─────────────────────────────────────────────
🔄 Refresh Now
➕ Add Account...
🌐 Open Liquid Glass Dashboard
```

---

### 2. 🔄 Native 1-Click Account Switcher (`account_switcher/`)

Built specifically for **Antigravity 2.0 on macOS** with full safety guards and rollback support:
- **Zero Keychain Dialog Storms:** Updates `service="gemini", account="antigravity"` via `/usr/bin/security` strictly without `-A` (matching the IDE's own `go-keyring` contract).
- **Safe Transaction Pipeline:** Runs pre-flight OAuth token refresh, guards against interrupting active streaming TCP 443 sockets (`--force` to override), updates `app_storage.json` + removes `credential-user-scope` (leaving dead `state.vscdb` untouched), and cleanly relaunches `Antigravity 2.app` while waiting for a fresh `daemon/hub_*.json`.
- **Switch Anywhere:** Click an inactive profile in SwiftBar (with native AppleScript confirmation), click **Make Active** in the Liquid Glass Dashboard (`POST /api/switch` + modal), or run CLI `check_quota.py --switch <email>` (`--dry-run` supported).

---

### 3. 🪟 Liquid Glass Dashboard

An instant local web dashboard (`127.0.0.1:51122`, with standalone `dashboard.html` snapshot fallback):
- **Account Cards (2x3 Grid):** Side-by-side 5h & 7d gauges for Gemini and Claude, custom notes, SwiftBar visibility toggles, account add/remove, and 1-click profile switching.
- **Unified Device Token Analytics:**
  - **Agent Provider Switcher:** Toggle seamlessly between **`All Agents`**, **`Antigravity`**, and **`Codex`**.
  - **Combined & Split KPIs:** In `All Agents` mode, primary KPI cards display combined totals alongside `AGY · Codex` breakdowns.
  - **Interactive Dual Y-Axis Chart:** Cumulative ReAct Context (billions, right axis) vs. User Prompts, Model Calls, Model Output, Thinking CoT, and Unique Tokens (left axis), with presets (`All Time`, `12M`, `6M`, `3M`, `1M`), granularity (`Month` / `Week` / `Day`), and custom date range selectors.
  - **Top-20 Heaviest Sessions:** Filterable by `All`, `Active on Disk`, or `Deleted from UI`, showing `AGY` / `Codex` badges, project tags, user prompts, steps, unique tokens, and cumulative context.

---

### 4. 📊 Unified Local Token & Context Analytics (`token_analytics/`)

- **Multi-Agent Local Scanners (Zero Network / Zero Codex OAuth):**
  - **Antigravity (`core/scanner.py`):** Parses `~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript*.jsonl` and `conversation_summaries.db` (project & title metadata).
  - **OpenAI Codex CLI (`core/codex_scanner.py`):** Recursively parses `~/.codex/sessions/**/*.jsonl` and `archived_sessions/**/*.jsonl`, reading native `event_msg: token_count` events (`input_tokens`, `output_tokens`, `reasoning_output_tokens`) with `total_token_usage` deduplication, skips base64 `input_image` blobs in tool outputs, filters system XML wrappers from `user_prompts`, and tracks subagent sessions (`user_prompts = 0`, full token/call accounting).
- **Isolated Incremental Caches:** `data/sessions_cache.json` (Antigravity) and `data/codex_sessions_cache.json` (Codex) store `(mtime, size)` fingerprints and retain deleted sessions (`deleted_from_disk: True`) without cross-contamination.
- **Background Sync (`sync_cache.py`):** Triggered asynchronously by SwiftBar every 5 minutes, exporting unified `antigravity_token_analysis.json` (`all` + `by_provider`).
- **CLI Audit Engine (`report.py`):** Supports `--provider [all|antigravity|codex]`, `--period [month|week|day]`, `--top`, and `--limit`.

---

### 5. 🤖 Headless Server Quota Watchdog (`server-watchdog/`)

For developers running autonomous AI agents, cron jobs, or headless coding workflows on remote Linux servers / VPS:
- **CLI Quota Inspector:** Direct integration with the `agy` binary (`agy --quota --json`) to monitor sliding 5-hour and 7-day limits in headless environments.
- **Proactive Telegram Notifications:**
  - ⚠️ **Warning Alert:** Triggered at $\le 20\%$ remaining capacity with exact reset countdown.
  - 🚨 **Critical Alert:** Triggered at $\le 5\%$ to avoid sudden pipeline disruptions.
  - ✅ **Recovery Notification:** Dispatched when quotas return to normal ($> 20\%$).
- **Anti-Spam State Tracking:** State is preserved in `.agy_quota_state.json` to prevent repetitive messaging across scheduled cron executions.
- **Cron-Ready:** Seamless 1-line setup in `crontab` for automated 3-hour polling.

---

## 🚀 Quick Start

### 💻 Platform Compatibility & Prerequisites
- **Cross-Platform (Windows, macOS, Linux):**
  - **Python 3.9+** (Standard library only for Quota Bar & Dashboard; optional `tabulate` and `tokenizers` for deep analytics).
  - All core features run natively across Windows (PowerShell/CMD), Linux, and macOS:
    - Multi-account OAuth pairing: `python antigravity-quota-bar/check_quota.py --add`
    - Live terminal quota tables: `python antigravity-quota-bar/check_quota.py`
    - Liquid Glass Web Dashboard: `python antigravity-quota-bar/check_quota.py --dashboard` (opens in your default browser)
    - Historical Token & Context Analytics: `python token_analytics/report.py`
- **macOS Exclusive Feature:**
  - **[SwiftBar](https://swiftbar.app/)** menu bar widget (`antigravity_bar.5m.py`) is designed specifically for the native macOS Menu Bar:
    ```bash
    brew install --cask swiftbar
    ```
    *(Or download directly from [swiftbar.app](https://swiftbar.app/) or [GitHub Releases](https://github.com/swiftbar/SwiftBar/releases))*

### 1. Clone & Setup
```bash
git clone https://github.com/serjdzen/antigravity-telemetry.git
cd antigravity-telemetry
```

Install optional dependencies for deep token analytics (CLI tables and BPE tokenizer):
```bash
pip install -r requirements.txt
# Or manually:
pip install tabulate tokenizers
```

### 2. Connect Your Google Accounts
To authorize an Antigravity account via official Google OAuth:
```bash
python3 antigravity-quota-bar/check_quota.py --add
```
A local server will temporarily listen on port `51121`, open Google's OAuth consent screen in your default browser, capture the refresh token, and securely save it to `antigravity-quota-bar/accounts.json` (which is `.gitignore`d). Repeat for all your Google accounts.

### 3. Check Live Quotas via CLI
```bash
python3 antigravity-quota-bar/check_quota.py
```

### 4. Open Liquid Glass Dashboard
```bash
python3 antigravity-quota-bar/check_quota.py --dashboard
```

### 5. Switch Active Antigravity Account (macOS)
```bash
# Dry-run simulation (no files or IDE processes touched)
python3 antigravity-quota-bar/check_quota.py --switch user@example.com --dry-run

# Live switch (or click any inactive profile in SwiftBar / Dashboard)
python3 antigravity-quota-bar/check_quota.py --switch user@example.com
```

### 6. Install SwiftBar Menu Bar Widget
[SwiftBar](https://swiftbar.app/) is an open-source macOS utility that displays shell script output directly in your macOS Menu Bar.

**Option A: Set Plugin Folder directly (Recommended)**
1. Launch SwiftBar.
2. In SwiftBar's initial setup prompt (or via `Preferences` ⌘, → `Set Plugin Directory...`), select:
   ```text
   /path/to/antigravity-telemetry/antigravity-quota-bar/plugins
   ```
3. The live quota widget will immediately appear in your macOS status bar.

**Option B: Symlink into your existing SwiftBar directory**
If you already maintain a centralized SwiftBar plugins directory:
```bash
ln -s "/path/to/antigravity-telemetry/antigravity-quota-bar/plugins/antigravity_bar.5m.py" ~/path/to/your/swiftbar/plugins/
```

**Customizing Refresh Rate:**
SwiftBar calculates polling frequency directly from the filename:
- `antigravity_bar.5m.py` ➔ Every 5 minutes (default).
- `antigravity_bar.1m.py` ➔ Every 1 minute.
- `antigravity_bar.15m.py` ➔ Every 15 minutes.
Simply rename the file in `plugins/` to adjust your preferred update interval.

### 7. Run Unified Historical Token Analytics (Antigravity + Codex)
Generate a full breakdown of token consumption across local Antigravity and OpenAI Codex CLI sessions:
```bash
python3 token_analytics/report.py --period month
```

Options:
```bash
# Filter by provider: all (default), antigravity, or codex
python3 token_analytics/report.py --provider codex --period month

# Weekly ISO aggregation
python3 token_analytics/report.py --period week --limit 12

# Daily breakdown
python3 token_analytics/report.py --period day --limit 30

# Inspect the 20 heaviest sessions across agents
python3 token_analytics/report.py --top 20

# Export structured JSON data (unified all + by_provider)
python3 token_analytics/sync_cache.py
```

### 8. Run Headless Server Watchdog (Optional)
On remote Linux servers running `agy` CLI agents:
```bash
cd server-watchdog
cp .env.example .env
# Edit .env with your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

# Check current balances:
python3 agy_quota_watchdog.py --status

# Test Telegram alert:
python3 agy_quota_watchdog.py --test-alert
```

---

## 💡 Token Hygiene & Best Practices

Based on empirical data from hundreds of analyzed developer sessions:

1. **Beware the $O(N^2)$ ReAct Loop:**  
   Because the agent sends the entire dialogue on every step, a 2,000-step conversation consumes exponentially more quota than forty 50-step conversations.
2. **Rotate Chats Frequently:**  
   Once a subtask, refactoring phase, or feature milestone is reached, start a **new clean chat**. Pass architectural state forward via artifacts or files (e.g. `walkthrough.md`, `memory-bank`), not by keeping a single conversation open for weeks.
3. **Curate MCP Tool Definitions:**  
   Every enabled MCP tool injects its JSON schema into the system prompt on *every single API invocation*. Disable unused MCP servers to save thousands of tokens per prompt.

---

## 🔒 Security & Privacy

- **100% Local-First:** No accounts, prompts, or session logs are ever transmitted to third-party servers. Antigravity quota checks talk directly to official Google Cloud endpoints (`cloudcode-pa.googleapis.com` / `daily-cloudcode-pa.googleapis.com`), while **OpenAI Codex CLI analytics operates 100% offline** by reading local `~/.codex/sessions/` JSONL files without touching Codex OAuth credentials.
- **No Secret Leakage:** `accounts.json`, `.quota_cache.json`, session caches, and generated dashboards are strictly ignored by `.gitignore`. A safe [accounts.example.json](antigravity-quota-bar/accounts.example.json) is provided as a reference.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
