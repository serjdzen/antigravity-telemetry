# ⚡ Antigravity Telemetry

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![macOS SwiftBar](https://img.shields.io/badge/macOS-SwiftBar-orange.svg)](https://github.com/swiftbar/SwiftBar)
[![Zero-Server](https://img.shields.io/badge/architecture-Zero--Server-brightgreen.svg)]()
[![Privacy: 100% Local](https://img.shields.io/badge/privacy-100%25%20Local--First-success.svg)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Real-time multi-account quota monitoring and deep token/context analytics for Google Antigravity 2.0.**  
> Monitor live 5-hour and 7-day quota pools in the macOS Menu Bar, inspect detailed consumption in an instant Zero-Server Liquid Glass Dashboard, and mathematically audit token burn rates across your historical ReAct sessions.

---

## 📌 Why Antigravity Telemetry?

Developers actively building with **Google Antigravity 2.0** (Google AI Pro, Ultra, or enterprise Workspaces) regularly face two critical challenges:

1. **The Multi-Account Quota Blind Spot:**  
   The official Antigravity IDE UI only displays status for the currently active account, and standard views only expose the 5-hour rolling window while obscuring the true **7-day weekly quota pool**. Developers juggling multiple accounts are left guessing when limits reset or why an account unexpectedly went into a multi-day lockdown.
2. **The ReAct Context Explosion ($\sim O(N^2)$):**  
   Autonomous AI agents do not simply make isolated API calls. In the ReAct (Reasoning + Acting) loop, the agent transmits the *entire accumulated session history*—including system prompts, MCP tool schemas, tool outputs, terminal logs, and thought chains—at every step $N$:
   $$\text{Context}_N = \text{System Prompt} + \text{MCP Schema} + \sum_{i=1}^{N-1}\text{Step}_i + \text{Step}_N$$
   A single session reaching 2,000–3,500 steps can burn **billions of cumulative context tokens**, consuming a 7-day quota pool in hours. Many developers mistakenly believe Google silently lowered their quotas, when the root cause is quadratic context expansion.
3. **User Prompts vs. Model Calls:**  
   Antigravity Telemetry separates your actual typing (**User Prompts**, direct Enter keypresses) from internal agentic operations (**Model API Calls**), revealing the actual **~x11.4 agentic multiplier** (which can surge to x16–x18 in complex multi-tool subagent workflows).

---

## 🏗 Architecture Overview

```
                               Antigravity Telemetry
                                         │
        ┌────────────────────────────────┼────────────────────────────────┐
        ▼                                ▼                                ▼
[1. Live Quota Bar]           [2. Token Analytics]            [3. Server Watchdog]
(antigravity-quota-bar/)        (token_analytics/)             (server-watchdog/)
        │                                │                                │
• Multi-Account OAuth          • brain/ Transcript Scanner      • Headless VPS & CLI Monitor
• 5h & 7d Cooldown Pools       • BPE Tokenizer (Qwen2.5)        • Proactive Telegram Alerts
• macOS SwiftBar Widget        • Two-Tier ReAct Audit           • Warning (≤20%) & Critical (≤5%)
• Zero-Server Dashboard        • CLI Comparator (report.py)     • Cron-Ready Headless Daemon
```

---

## 🖥 Key Components

### 1. ⚡ Live Quota Bar & Menu Bar Widget (`antigravity-quota-bar`)

- **Multi-Account Concurrent Checking:** Checks 6+ accounts in parallel via `ThreadPoolExecutor` within **~1.5 seconds** (compared to 30–40s sequentially).
- **Dual Limits Support:** Queries internal Google Cloud Code endpoints (`loadCodeAssist` and `retrieveUserQuotaSummary`) to extract both the **5-hour sliding window** and the **7-day weekly quota pool** with exact cooldown reset timers.
- **Dynamic Active Account Detection:** Automatically reads `~/Library/Application Support/Antigravity/app_storage.json` (`jetski.onboarding.lastLoginUsername`) to detect which account is currently active in the IDE, highlighting it at the top of the menu bar.
- **Zero Background Overhead:** Operates with **0% continuous CPU usage**. SwiftBar triggers the script once every 5 minutes; it runs for ~1.5 seconds, updates the local cache `.quota_cache.json`, and completely exits memory.
- **Robust Anti-Freeze Guard:** Enforces `socket.setdefaulttimeout(8)` to eliminate freezes during network switches or macOS sleep/wake cycles, and suppresses Python bytecode generation (`sys.dont_write_bytecode = True`) to prevent stale `[?]` alerts.

#### Menu Bar Preview (SwiftBar):
```text
✨ 94% · 4ч 7м
─────────────────────────────────────────────
user1@gmail.com (Active)
   5h:  [████████]  94%  (4h 7m)
   7d:  [████████]  99%  (6d 23h)
user2@gmail.com
   5h:  [░░░░░░░░]   5%  (1h 23m)
   7d:  [███████░]  84%  (6d 15h)
user3@gmail.com
   5h:  [████████] 100%  (4h 59m)
   7d:  [░░░░░░░░]   0%  (2d 1h)  <-- 7-day cooldown highlighted
─────────────────────────────────────────────
🔄 Refresh Now
➕ Add Account...
🌐 Open Liquid Glass Dashboard
```

---

### 2. 🪟 Zero-Server Liquid Glass Dashboard

An ultra-fast, local web interface displaying all connected profiles and token analytics:
- **Zero-Server Architecture:** Generates a standalone, static `dashboard.html` in **~1 ms** and opens it directly via `open file://`. No persistent Python HTTP daemon, Node.js server, or open terminal window required.
- **Multi-Model Side-by-Side View:** Displays 5h and 7d quota gauges for both **Claude** (Claude 3.7 Sonnet) and **Gemini**.
- **Widescreen 2x3 Grid:** High-contrast responsive cards with real-time status indicators (Active, Available, Warning, Cooldown).
- **Integrated Device Token Analytics:**
  - Dual Y-axis interactive Chart.js visualization: Cumulative ReAct Context (in billions of tokens, right axis) vs. User Prompts & API Calls (left axis).
  - Dynamic time range presets: `[All Time]`, `[12 Months]`, `[6 Months]`, `[3 Months]`, and `[1 Month]` (which automatically unfolds daily granularity).
  - Granularity switcher: `[By Months]`, `[By Weeks]`, `[By Days]`, plus custom `From:` / `To:` date pickers.
  - Expandable accordion ranking the **Top-10 Heaviest Sessions** on your Mac.

---

### 3. 📊 Deep Token & Context Analytics Engine (`token_analytics`)

- **Direct Transcript Scanner:** Inspects session logs at `~/.gemini/antigravity/brain/<id>/.system_generated/logs/transcript.jsonl`.
- **Incremental Cache:** Tracks file modification times (`mtime`) and sizes in `data/sessions_cache.json`. Re-scanning 500+ sessions takes **0.2 seconds**.
- **Two-Tier Metrics Methodology:**
  1. **Raw Unique Delta Tokens:** Measures physical new output generated: User Input + Model Output + Reasoning/Thinking CoT + Tool Calls/Outputs.
  2. **Cumulative ReAct Agent Context:** Measures total contextual tokens transmitted across all steps of the ReAct cycle.
- **CLI Analytics Tool (`report.py`):**
  - Formatted terminal tables with color coding.
  - Comparative analysis: Month-over-Month (`MoM`), Week-over-Week (`WoW`), Day-over-Day (`DoD`).
  - Google Cloud cost simulation (with 90% Prompt Caching adjustment).

---

### 3. 🤖 Headless Server Quota Watchdog (`server-watchdog/`)

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
    - Zero-Server Liquid Glass Web Dashboard: `python antigravity-quota-bar/check_quota.py --dashboard` (automatically opens in your default browser: Edge, Chrome, Firefox)
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

### 5. Install SwiftBar Menu Bar Widget
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

### 6. Run Historical Token Analytics
Generate a full breakdown of token consumption across all historical Antigravity conversations:
```bash
python3 token_analytics/report.py --period month
```

Options:
```bash
# Weekly ISO aggregation
python3 token_analytics/report.py --period week --limit 12

# Daily breakdown
python3 token_analytics/report.py --period day --limit 30

# Inspect the 15 heaviest sessions
python3 token_analytics/report.py --top 15

# Export structured JSON data for custom reporting
python3 token_analytics/report.py --json-out token_analytics/antigravity_token_analysis.json
```

### 7. Run Headless Server Watchdog (Optional)
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

- **100% Local-First:** No accounts, logs, or metrics are ever transmitted to third-party servers. All requests go directly to official Google Cloud endpoints (`cloudcode-pa.googleapis.com`).
- **No Secret Leakage:** `accounts.json`, `.quota_cache.json`, session caches, and generated dashboards are strictly ignored by `.gitignore`. A safe [accounts.example.json](antigravity-quota-bar/accounts.example.json) is provided as a reference.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
