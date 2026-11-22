# TEST_READY: Antigravity Account Switcher Test Suite

> **Project:** QuotaAnalytics / Native Antigravity Account Switcher  
> **Track:** E2E Testing & Popperian Falsification Quality Gate  
> **Author:** Test Writer and Test Track Lead (`worker_e2e_tests`)  
> **Date:** 2026-11-07  
> **Status:** READY & VERIFIED (100% PASS RATE)  

---

## 1. Executive Summary

The comprehensive automated test suite for the **Antigravity Native Account Switcher** has been fully authored, verified, and published. It satisfies all requirements defined in `ORIGINAL_REQUEST.md`, `PROJECT.md`, and `TEST_INFRA.md`.

* **Total Automated Tests:** 53 tests
* **Execution Time:** ~0.125 seconds
* **Framework:** Standard Library `unittest` (Zero external pip dependencies)
* **Isolation Compliance (Requirement R3):** 100% Sandbox Isolation via dynamic temporary directory fixtures (`ANTIGRAVITY_TEST_ROOT`, `ANTIGRAVITY_DRY_RUN=1`, `KEYCHAIN_MOCK=1`, `PROCESS_MOCK=1`). Host production paths (`~/Library/Application Support/Antigravity`), production macOS Keychain (`/usr/bin/security`), and running IDE processes were completely untouched.
* **Popperian Falsification (Requirement R4):** 14 specialized stress failure probes covering all 4 switching phases (FP-1.1 through FP-4.3).

---

## 2. Test Runner Commands

### Standard Discovery Runner (Runs all 53 tests):
```bash
python3 -m unittest discover -s account_switcher/tests -p 'test_*.py' -v
```

### Run 4-Tier Test Suite Individually (39 tests):
```bash
python3 -m unittest account_switcher.tests.test_switcher -v
```

### Run Popperian Falsification Suite Individually (14 tests):
```bash
python3 -m unittest account_switcher.tests.test_popper_falsification -v
```

---

## 3. Test Coverage Breakdown

### Tier 1: Feature Coverage (All 22 Features in Isolation)
| Feature ID | Feature Name | Test Method | Result |
| :--- | :--- | :--- | :---: |
| **F-01** | Account Enumeration & Validation | `test_f01_account_enumeration_and_validation` | PASS |
| **F-02** | Pre-flight Token Refresh | `test_f02_preflight_token_refresh` | PASS |
| **F-03** | Active Streaming Detection | `test_f03_active_streaming_detection` | PASS |
| **F-04** | Socket Busy Abort / Retry | `test_f04_socket_busy_abort_and_retry` | PASS |
| **F-05** | SQLite State Backup (`.bak`) | `test_f05_sqlite_state_backup` | PASS |
| **F-06** | SQLite USS Topic Injection | `test_f06_sqlite_uss_topic_injection` | PASS |
| **F-07** | SQLite Stale Key Eviction | `test_f07_sqlite_stale_key_eviction` | PASS |
| **F-08** | macOS Keychain Commit (`-U -A`) | `test_f08_macos_keychain_commit` | PASS |
| **F-09** | Credential Scope Invalidation | `test_f09_credential_scope_invalidation` | PASS |
| **F-10** | HTTPStorages Cache Purge | `test_f10_http_storages_cache_purge` | PASS |
| **F-11** | App Storage Update | `test_f11_app_storage_update` | PASS |
| **F-12** | Process Graceful Shutdown | `test_f12_process_graceful_shutdown` | PASS |
| **F-13** | Process Relaunch Supervision | `test_f13_process_relaunch_supervision` | PASS |
| **F-14** | Dry-Run Simulation Mode | `test_f14_dry_run_simulation_mode` | PASS |
| **F-15** | SwiftBar Zero-Button Click UX | `test_f15_swiftbar_zero_button_ux_directive` | PASS |
| **F-16** | SwiftBar AppleScript Dialog | `test_f16_swiftbar_applescript_dialog_generation` | PASS |
| **F-17** | SwiftBar Active Account Badge | `test_f17_swiftbar_active_account_badge_and_alert` | PASS |
| **F-18** | CLI Switch Arguments | `test_f18_cli_switch_arguments_parsing` | PASS |
| **F-19** | CLI Active Profile Indicator | `test_f19_cli_active_profile_indicator` | PASS |
| **F-20** | Dashboard POST /api/switch | `test_f20_dashboard_post_api_switch_endpoint` | PASS |
| **F-21** | Dashboard Glass Modal & Button | `test_f21_dashboard_glass_modal_and_button` | PASS |
| **F-22** | Popperian Suite Linkage | `test_f22_popperian_falsification_suite_linkage` | PASS |

### Tier 2: Boundary & Corner Cases (10 Tests)
* `test_b01_expired_or_revoked_refresh_token`: OAuth `invalid_grant` aborts switch with clean error code.
* `test_b02_malformed_accounts_json`: Syntax errors in `accounts.json` handled safely without crash.
* `test_b03_missing_refresh_token_field`: Missing token triggers structured schema validation error.
* `test_b04_dangling_symlink_singleton_lock`: Dangling symlinks tolerated by path resolver without `FileNotFoundError`.
* `test_b05_busy_sockets_blocks_unforced_switch`: Active sockets block unforced switch attempts.
* `test_b06_missing_database_recreation`: Missing `state.vscdb` is automatically created with valid schema.
* `test_b07_readonly_credential_scope_file`: Read-only permissions (`0444`) on scope file are resolved and unlinked.
* `test_b08_target_account_not_found_in_roster`: Non-existent target email rejected with structured failure.
* `test_b09_noop_switch_target_already_active`: Active profile correctly resolved across stores.
* `test_b10_keychain_service_error_handling`: Malformed Keychain payload raises structured `KeychainError`.

### Tier 3: Cross-Feature Combinations & State Rollbacks (4 Tests)
* `test_c01_dry_run_multi_store_pipeline_leaves_files_untouched`: Complete multi-store pipeline executes with zero disk mutations.
* `test_c02_forced_switch_bypasses_active_sockets`: `--force` flag overrides streaming socket guard.
* `test_c03_offline_ide_switch_skips_shutdown_and_launches`: Offline IDE switch skips shutdown phase and starts application cleanly.
* `test_c04_interactive_dialog_cancellation_aborts_without_mutations`: User cancellation in native dialog halts switch with zero changes.

### Tier 4: Real-World Application Scenarios (3 Tests)
* `test_w01_e2e_dual_switch_roundtrip_consistency`: Full roundtrip A -> B -> A verifies complete multi-store consistency.
* `test_w02_e2e_cli_invocation_simulation`: Simulated CLI invocation validating output formatting.
* `test_w03_e2e_dashboard_http_server_switch_simulation`: Simulated Dashboard HTTP REST call validating JSON schema.

### Popperian Falsification Suite (14 Tests across 4 Phases)
* **Phase 1 (Pre-flight Token Validation):**
  * `FP-1.1`: Revoked or expired refresh token (HTTP 400 `invalid_grant` -> abort, IDE untouched, exit != 0)
  * `FP-1.2`: Network outage / DNS timeout (socket error -> abort, warning emitted, state untouched)
  * `FP-1.3`: Corrupt / incomplete accounts JSON (schema failure -> halt before network/disk)
  * `FP-1.4`: Idle switch / No-op switch (active == target -> clean status, no restart)
* **Phase 2 (Active Streaming Sockets):**
  * `FP-2.1`: Active streaming socket detected (lsof ESTABLISHED -> abort, alert emitted, IDE preserved)
  * `FP-2.2`: Forced user override (`--force` -> log warning, proceed with switch)
  * `FP-2.3`: `lsof` command hang / timeout (subprocess timeout 2.0s -> handled safely without lockup)
* **Phase 3 (Atomic Multi-Store State Write):**
  * `FP-3.1`: SQLite database locked (`SQLITE_BUSY` -> database integrity verified, DB unharmed)
  * `FP-3.2`: Disk full error (`ENOSPC` -> atomic temp write fails, `.bak` preserved intact)
  * `FP-3.3`: Mid-chain Keychain failure -> storage failure reported, ghost session prevented
  * `FP-3.4`: Read-only scope file permissions -> reset chmod, unlink file cleanly
* **Phase 4 (Process Lifecycle):**
  * `FP-4.1`: IDE locked by unsaved file modal -> graceful abort, no blind `kill -9`, report quit_timeout
  * `FP-4.2`: IDE initially closed -> quit skipped, stores updated, app launched
  * `FP-4.3`: Relaunch command failure -> credentials switched, relaunch_failed reported cleanly

---

## 4. Test Fixtures

Located in `account_switcher/tests/fixtures/`:
1. `fixture_factory.py` — Dynamic sandbox factory creating temporary Antigravity trees.
2. `state.vscdb` — Real SQLite 3 database with `ItemTable` schema and baseline keys.
3. `app_storage.json` — Electron application storage mock.
4. `credential-user-scope` — 17-byte session scope hash file.
5. `accounts.json` — Multi-account reference roster for tests.
6. `mock_keychain.json` — Backing store for `MockKeychainStore`.

---

## 5. Auditor Verification Checklist

- [x] All 53 tests pass with exit code `0`.
- [x] Zero external dependencies (`pip list` not required).
- [x] Zero mutation of live host user files (`~/Library/Application Support/Antigravity`).
- [x] Runtime safety guard `assert_environment_safety()` actively blocks live writes.
- [x] All 4 tiers and 14 Popper probes executed cleanly.
