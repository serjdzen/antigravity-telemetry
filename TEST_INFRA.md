# Test Infrastructure Specification: Antigravity Account Switcher

> **Project:** QuotaAnalytics / Antigravity Native Account Switcher  
> **Status:** Authoritative Test Architecture Document  
> **Test Framework:** Python standard library `unittest` (Zero external pip dependencies)  
> **Isolation Guarantee:** 100% Sandbox Isolation via Temporary Mock Fixtures (Requirement R3)  

---

## 1. Test Philosophy & Architecture

The testing infrastructure for the Antigravity Account Switcher is built upon two core pillars:
1. **100% Isolation & Zero Disruption (Requirement R3):**
   Tests must NEVER mutate the host's actual Google Antigravity IDE configuration, Keychain credentials, SQLite databases, or running processes. Any accidental path leak to `~/Library/Application Support/Antigravity` triggers an immediate `SafetyViolationError` via the runtime safety assertion gate.
2. **Popperian Falsification Methodology (Requirement R4 & `/check`):**
   Rather than merely checking "happy paths", tests actively attempt to falsify the stability of the system under extreme stress, resource exhaustion, network failures, corrupt data, and process deadlocks.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        4-Tier Test Architecture                        │
├────────────────────────────────┬───────────────────────────────────────┤
│ Tier 1: Feature Coverage       │ 22 features tested in isolation       │
│ Tier 2: Boundary & Corner      │ Expired tokens, malformed JSON, locks │
│ Tier 3: Cross-Feature Combos   │ Refresh + Sockets + Atomic rollback   │
│ Tier 4: Real-World Scenarios   │ Full E2E roundtrip workflow (A->B->A) │
├────────────────────────────────┴───────────────────────────────────────┤
│                   Popperian Falsification Suite                        │
│ Probes FP-1.1 through FP-4.3 across all 4 switching phases             │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Safety & Dry-Run Environment Controls

The test infrastructure enforces isolated execution via four environment variables and a runtime invariant checker:

| Environment Variable | Allowed Values | Purpose in Test Suite |
| :--- | :--- | :--- |
| `ANTIGRAVITY_TEST_ROOT` | `/path/to/tmp_dir` | Overrides the root directory for all Antigravity files (`state.vscdb`, `app_storage.json`, `credential-user-scope`, `HTTPStorages`). |
| `ANTIGRAVITY_DRY_RUN` | `1` / `0` | When `1`, forces dry-run simulation mode where mutations are logged and executed against simulated stores. |
| `KEYCHAIN_MOCK` | `1` / `0` | When `1`, redirects all macOS Keychain reads/writes to `MockKeychainStore` instead of `/usr/bin/security`. |
| `KEYCHAIN_MOCK_FILE` | `/path/to/mock_keychain.json` | Specifies the JSON backing file for the mock keychain data store. |

### Runtime Safety Assertion Gate:
Every filesystem and Keychain modification is gated by `assert_environment_safety(target_path, is_test=True, dry_run=True)`. If a resolved target path equals or resides within `~/Library/Application Support/Antigravity`, a `SafetyViolationError` is raised synchronously.

---

## 3. Fixtures Directory Hierarchy & Schemas

The directory `account_switcher/tests/fixtures/` contains baseline mock artifacts and dynamic generators:

```
account_switcher/tests/fixtures/
├── __init__.py
├── fixture_factory.py            # Dynamic generator for temp isolation trees
├── accounts.json                 # Multi-account reference roster for testing
├── app_storage.json              # Mock Electron app storage JSON
├── credential-user-scope         # 17-byte mock session scope hash
├── state.vscdb                   # Pre-initialized SQLite 3 database with ItemTable
└── mock_keychain.json            # Mock generic-password store for gemini:antigravity
```

### Schema Definitions:

1. **SQLite Database (`state.vscdb`):**
   * Table: `ItemTable (key TEXT PRIMARY KEY, value TEXT)`
   * Baseline rows:
     * `antigravityAuthStatus`: JSON `{"name":"TestUser","email":"user2@gmail.com","apiKey":"ya29.test"}`
     * `antigravityOnboarding`: `"true"`
     * `antigravityUnifiedStateSync.oauthToken`: Base64 USS Protobuf containing `authStateWithContextSentinelKey` and `oauthTokenInfoSentinelKey`
     * `jetskiStateSync.agentManagerInitState`: `"stale_session_context"`
     * `antigravityUnifiedStateSync.userStatus`: `"stale_quota_status"`

2. **App Storage (`app_storage.json`):**
   ```json
   {
     "agy.onboarding.lastLoginUsername": "user2@gmail.com",
     "agy.onboarding.lastLoginIsGcpTos": "false"
   }
   ```

3. **Credential User Scope (`credential-user-scope`):**
   * Size: 17 bytes
   * Content: `u0000000000000000` (unlinked during switch)

4. **Accounts Roster (`accounts.json`):**
   ```json
   [
     {
       "email": "active@example.com",
       "refresh_token": "mock_refresh_token_active",
       "added_at": "2026-10-01T10:00:00.000000"
     },
     {
       "email": "target@example.com",
       "refresh_token": "mock_refresh_token_target",
       "added_at": "2026-10-01T11:00:00.000000"
     },
     {
       "email": "expired@example.com",
       "refresh_token": "mock_refresh_token_expired",
       "added_at": "2026-10-01T12:00:00.000000"
     }
   ]
   ```

---

## 4. Four-Tier Test Suite Specification

### Tier 1: Feature Coverage (22 Features in Isolation)
* **F-01: Account Enumeration & Validation:** Parse accounts from valid/invalid JSON rosters.
* **F-02: Pre-flight Token Refresh:** Verify OAuth refresh call before any mutation.
* **F-03: Active Streaming Detection:** Verify detection of active TCP 443 connections via `lsof`.
* **F-04: Socket Busy Abort / Retry:** Verify abortion when streaming is active and retry logic.
* **F-05: SQLite State Backup:** Verify creation of `.bak` timestamped/suffix backup before write.
* **F-06: SQLite USS Topic Injection:** Verify writing `antigravityUnifiedStateSync.oauthToken`.
* **F-07: SQLite Stale Key Eviction:** Verify deletion of `jetskiStateSync.agentManagerInitState` and `antigravityUnifiedStateSync.userStatus`.
* **F-08: macOS Keychain Commit:** Verify updating passwords for `gemini` and `antigravity` with `-U -A`.
* **F-09: Credential Scope Invalidation:** Verify unlinking `credential-user-scope`.
* **F-10: HTTPStorages Cache Purge:** Verify removing HTTP connection cache directory.
* **F-11: App Storage Update:** Verify updating `agy.onboarding.lastLoginUsername` in `app_storage.json`.
* **F-12: Process Graceful Shutdown:** Verify AppleScript `tell application id "com.google.antigravity" to quit`.
* **F-13: Process Relaunch Supervision:** Verify relaunch via `open -b com.google.antigravity`.
* **F-14: Dry-Run Simulation Mode:** Verify all 4 layers log proposed mutations without modifying disk.
* **F-15: SwiftBar Zero-Button Click UX:** Verify bash directive string syntax for menu bar.
* **F-16: SwiftBar AppleScript Dialog:** Verify generation of native macOS modal confirmation dialog.
* **F-17: SwiftBar Active Account Badge:** Verify `(Активен)` badge assignment and no-op alert.
* **F-18: CLI Switch Arguments:** Verify flags `--switch`, `--switch-interactive`, `--dry-run`, `--force`.
* **F-19: CLI Active Profile Indicator:** Verify visual styling for active profile in terminal tables.
* **F-20: Dashboard POST /api/switch:** Verify REST handler accepting switch request with valid CORS.
* **F-21: Dashboard Glass Modal & Button:** Verify HTML/JS template contains modal and activation hooks.
* **F-22: Popperian Falsification Suite:** Integrated runner for Popperian failure probes.

### Tier 2: Boundary & Corner Cases
* **B-01: Revoked Refresh Token:** Google OAuth returns HTTP 400 `invalid_grant`.
* **B-02: Network Outage:** Urllib raises `URLError` / socket timeout during token refresh.
* **B-03: Malformed JSON:** `accounts.json` contains invalid syntax or missing required fields.
* **B-04: Dangling Symlink SingletonLock:** `SingletonLock` points to nonexistent process PID.
* **B-05: Missing Database:** `state.vscdb` file is missing or contains an empty schema.
* **B-06: Read-Only Scope File:** `credential-user-scope` has permissions `0o444`.
* **B-07: Busy Sockets:** Active socket connection present during unforced switch request.
* **B-08: Target Not Found:** User requests email not present in accounts roster.
* **B-09: No-op Switch:** User requests account that is already active.
* **B-10: Keychain CLI Error:** `/usr/bin/security` returns non-zero error code (e.g., 36, 44).

### Tier 3: Cross-Feature Combinations & State Rollbacks
* **C-01: Dry-Run Multi-Store Pipeline:** Full sequence (token refresh -> socket check -> storage dry-run -> mock process) executes without filesystem changes.
* **C-02: Partial Write Rollback:** Keychain failure after SQLite write restores SQLite from `.bak`.
* **C-03: Offline IDE Switch:** Switch executed when IDE is closed (skips graceful shutdown, updates stores, launches IDE).
* **C-04: Force Switch with Active Sockets:** User supplies `--force`, bypassing streaming socket lock.

### Tier 4: Real-World Application Scenarios
* **W-01: E2E Dual-Switch Roundtrip:** Switch from Account A to Account B, verify all 4 stores, then switch back from B to A, verifying complete consistency.
* **W-02: CLI Integration Simulation:** Execute CLI `--switch` and verify exit codes and output messages.
* **W-03: Dashboard HTTP Switch Simulation:** Perform `POST /api/switch` against `DashboardHTTPHandler` and verify JSON response.

---

## 5. Popperian Falsification Matrix (`test_popper_falsification.py`)

| Probe ID | Switching Phase | Falsification Scenario | Simulated Condition | Expected Popperian Reaction |
| :--- | :--- | :--- | :--- | :--- |
| **FP-1.1** | Phase 1 (Pre-flight) | Revoked / Expired Token | Mock OAuth HTTP 400 `invalid_grant` | Abort immediately; IDE not quit; exit code != 0 |
| **FP-1.2** | Phase 1 (Pre-flight) | Network / DNS Outage | Socket timeout / URLError | Catch exception; abort; IDE kept running |
| **FP-1.3** | Phase 1 (Pre-flight) | Corrupt Accounts File | Malformed JSON / missing fields | Validation fails before network or disk mutation |
| **FP-1.4** | Phase 1 (Pre-flight) | Idle / No-op Switch | Target email == active email | Detect match; return clean status; no restart |
| **FP-2.1** | Phase 2 (Sockets) | Active Streaming Socket | `lsof` returns ESTABLISHED on port 443 | Abort switch; alert prompt; do not kill IDE |
| **FP-2.2** | Phase 2 (Sockets) | Forced Override | Active socket + `--force` flag | Log warning; proceed with switch |
| **FP-2.3** | Phase 2 (Sockets) | Process Check Hang | `lsof` command timeout > 2.0s | Subprocess timeout caught; safe error handling |
| **FP-3.1** | Phase 3 (Storage) | Database Locked (`SQLITE_BUSY`)| SQLite exclusive lock held | Retry with backoff; rollback without DB damage |
| **FP-3.2** | Phase 3 (Storage) | Disk Full (`ENOSPC`) | Simulated disk write failure | Atomic temp file write fails; `.bak` intact |
| **FP-3.3** | Phase 3 (Storage) | Mid-Chain Failure | Keychain write fails after SQLite write | Rollback SQLite from `.bak`; zero ghost session |
| **FP-3.4** | Phase 3 (Storage) | Read-Only Scope File | File has `chmod 0o444` | Reset mode to writable; unlink successfully |
| **FP-4.1** | Phase 4 (Process) | Unsaved File Prompt | IDE fails to quit within timeout | Abort switch; do NOT execute `kill -9` |
| **FP-4.2** | Phase 4 (Process) | IDE Already Closed | `is_running()` returns `False` | Skip quit phase; update stores; launch IDE |
| **FP-4.3** | Phase 4 (Process) | Relaunch Failure | Launch command fails | Report failure cleanly; provide manual start hint |

---

## 6. Execution & Verification Guide

### Running Full Test Suite:
```bash
python3 -m unittest discover -s account_switcher/tests -p 'test_*.py' -v
```

### Running Popperian Falsification Suite Specifically:
```bash
python3 -m unittest account_switcher.tests.test_popper_falsification -v
```

### Running 4-Tier Test Suite Specifically:
```bash
python3 -m unittest account_switcher.tests.test_switcher -v
```

### Invariants Verified on Every Test Run:
1. `~/Library/Application Support/Antigravity` was never read or written to by tests.
2. System Keychain was never queried or altered via `/usr/bin/security`.
3. Running `Antigravity` or `agy` desktop processes were never signaled or terminated.
4. Total execution time of full test suite remains under 10 seconds.
