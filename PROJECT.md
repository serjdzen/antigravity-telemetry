# Project: Native Antigravity Account Switcher for QuotaAnalytics

## Architecture
The Antigravity Account Switcher provides reliable, zero-downtime, and safe account switching between multiple Google Antigravity accounts configured in `antigravity-quota-bar/accounts.json`. It coordinates across 4 local storage layers and 2 desktop processes:

```
                      ┌────────────────────────────────────────┐
                      │ User Entry Points:                     │
                      │ 1. SwiftBar (Zero-Button UX Menu)      │
                      │ 2. CLI: check_quota.py --switch        │
                      │ 3. Liquid Glass Dashboard (POST /api)  │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │ Core Engine: account_switcher/switcher │
                      ├────────────────────────────────────────┤
                      │ Phase 1: Pre-flight Token Validation   │
                      │ Phase 2: Streaming Socket Guard (lsof) │
                      │ Phase 3: Atomic Multi-Store Commit     │
                      │ Phase 4: Process Supervision & Restart │
                      └───────────────────┬────────────────────┘
                                          │
         ┌───────────────────┬────────────┴───────┬──────────────────┐
         ▼                   ▼                    ▼                  ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌────────────────┐
│ SQLite Database │ │ macOS Keychain  │ │ app_storage.json│ │ User Scope     │
│  state.vscdb    │ │  security CLI   │ │ lastLoginUser   │ │ delete hash &  │
│  ItemTable USS  │ │ gemini:antigrav │ │ lastLoginGcpTos │ │ HTTPStorages   │
└─────────────────┘ └─────────────────┘ └─────────────────┘ └────────────────┘
```

### Safety & Dry-Run Architecture (Requirement R3)
- **Environment Variables**:
  - `ANTIGRAVITY_DRY_RUN=1`: Activates dry-run simulation mode.
  - `ANTIGRAVITY_TEST_ROOT=<path>`: Redirects all filesystem operations (`state.vscdb`, `app_storage.json`, `credential-user-scope`, `HTTPStorages`) to an isolated sandbox fixture.
  - `KEYCHAIN_MOCK=1`: Intercepts macOS Keychain calls and stores passwords in an in-memory or fixture-backed store.
- **Fail-Safe Invariant**:
  - Core engine enforces `assert_environment_safety()` before any disk write or process signal: if live production paths (`~/Library/Application Support/Antigravity`) are targeted during tests/simulations, a `SafetyViolationError` is immediately raised.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Account Enumeration & Validation | Load accounts from `accounts.json`, parse refresh token, client ID/secret | M1 | survey_spec |
| 2 | Pre-flight Token Refresh | Refresh access token with Google OAuth 2.0 API before any mutation | M1 | survey_spec |
| 3 | Active Streaming Detection | Detect active LLM streaming sockets via `lsof -a -c agy -c Antigravity -iTCP:443 -sTCP:ESTABLISHED` | M1 | survey_environment |
| 4 | Socket Busy Abort / Retry | Abort switch or wait with retry counter if prompt streaming is active | M1 | survey_spec |
| 5 | SQLite State Backup | Create timestamped `.bak` copy of `state.vscdb` before modifying `ItemTable` | M1 | survey_spec |
| 6 | SQLite USS Topic Injection | Write `antigravityUnifiedStateSync.oauthToken` Protobuf USS payload into `ItemTable` | M1 | survey_spec |
| 7 | SQLite Stale Key Eviction | Delete `jetskiStateSync.agentManagerInitState` & `antigravityUnifiedStateSync.userStatus` | M1 | survey_spec |
| 8 | macOS Keychain Commit | Update generic password for services `gemini` and `antigravity` with `-U -A` | M1 | survey_environment |
| 9 | Credential Scope Invalidation | Unlink `~/Library/Application Support/Antigravity/credential-user-scope` | M1 | survey_spec |
| 10| HTTPStorages Cache Purge | Clear `~/Library/HTTPStorages/com.google.antigravity` connection pool | M1 | survey_spec |
| 11| App Storage Update | Update `agy.onboarding.lastLoginUsername` in `app_storage.json` | M1 | survey_spec |
| 12| Process Graceful Shutdown | Soft quit Antigravity IDE via AppleScript bundle id `com.google.antigravity` | M1 | survey_environment |
| 13| Process Relaunch Supervision | Relaunch IDE via `open -b com.google.antigravity` or command line | M1 | survey_spec |
| 14| Dry-Run Simulation Mode | Full mock fixture redirection and dry-run execution without mutating live state | M1 | survey_environment |
| 15| SwiftBar Zero-Button Click UX | Clickable account entries in `antigravity_bar.5m.py` with `bash` directives | M2 | survey_codebase |
| 16| SwiftBar AppleScript Dialog | Native macOS confirmation popup (`display dialog`) before executing switch | M2 | survey_codebase |
| 17| SwiftBar Active Account Badge | Visual styling and `(Активен)` badge with info alert on active click | M2 | survey_codebase |
| 18| CLI Switch Arguments | Support `--switch <email>`, `--switch-interactive <email>`, `--dry-run`, `--force` | M3 | survey_codebase |
| 19| CLI Active Profile Indicator | Visual badge `(Активен)` and styling in terminal quota output | M3 | survey_codebase |
| 20| Dashboard POST /api/switch | HTTP endpoint in `DashboardHTTPHandler` accepting switch requests with CORS | M3 | survey_codebase |
| 21| Dashboard Glass Modal & Button | Interactive `⚡ Сделать активным` button & Liquid Glass confirmation modal | M3 | survey_codebase |
| 22| Popperian Falsification Suite | Comprehensive stress and failure tests across all 4 switching phases | M4 / E2E | survey_environment |

---

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| E2E | E2E Testing Track | Test infra, mock fixtures, Tiers 1-4 test runner, Popperian stress tests | none | DONE |
| M1 | Core Switcher Engine | `account_switcher/switcher.py` (4 phases, storage layers, dry-run mode) | none | DONE |
| M2 | SwiftBar Integration | `antigravity-quota-bar/plugins/antigravity_bar.5m.py` (Zero-Button UX, AppleScript dialog) | M1 | DONE |
| M3 | CLI & Dashboard Integrations | `check_quota.py` CLI flags & `DashboardHTTPHandler` + HTML template | M1 | DONE |
| M4 | Final E2E Verification & Hardening | Pass 100% E2E tests (Tiers 1-4), Tier 5 adversarial hardening, Forensic Audit | E2E, M1, M2, M3 | DONE |

---

## Interface Contracts

### 1. `account_switcher/switcher.py` ↔ Consumers (CLI, SwiftBar, Dashboard)
```python
class SwitchResult:
    success: bool
    email: str
    message: str
    dry_run: bool
    phase_reached: str  # 'preflight', 'sockets', 'storage', 'process', 'completed'
    error: Optional[str] = None

def switch_account(
    target_email: str,
    accounts_file: Optional[Path] = None,
    dry_run: bool = False,
    force: bool = False,
    interactive: bool = False,
    test_root: Optional[Path] = None
) -> SwitchResult:
    ...

def get_active_account(test_root: Optional[Path] = None) -> Optional[str]:
    ...

def is_streaming_active(timeout_sec: float = 2.0) -> bool:
    ...
```

### 2. SwiftBar ↔ CLI
- SwiftBar invokes CLI via standard SwiftBar bash directive:
  ```text
  <email> | bash="{python3}" param1="{check_quota.py}" param2="--switch-interactive" param3="{email}" terminal=false refresh=true
  ```

### 3. Dashboard Web ↔ Server
- **Endpoint**: `POST /api/switch`
- **Request**: `{"email": "user@example.com", "dry_run": false, "force": false}`
- **Response**: `{"status": "ok"|"error", "email": "...", "message": "...", "dry_run": bool}`

---

## Code Layout
- `account_switcher/`
  - `__init__.py`
  - `switcher.py` — Core 4-phase switcher implementation and dry-run engine
  - `keychain.py` — Native macOS Keychain provider with standard library `/usr/bin/security`
  - `storage.py` — Multi-store coordinator (`state.vscdb`, `app_storage.json`, `credential-user-scope`)
  - `process.py` — Process supervisor (AppleScript bundle ID management, streaming socket detection)
  - `tests/` — Test suites and mock fixtures
    - `__init__.py`
    - `test_switcher.py` — Unit and integration tests
    - `test_popper_falsification.py` — Popperian falsification tests (Requirement R4)
    - `fixtures/` — Isolated mock data for state.vscdb, app_storage.json, accounts.json
- `antigravity-quota-bar/`
  - `check_quota.py` — CLI entrypoint and DashboardHTTPHandler
  - `plugins/antigravity_bar.5m.py` — SwiftBar menu bar plugin
  - `accounts.json` — Reference account roster
