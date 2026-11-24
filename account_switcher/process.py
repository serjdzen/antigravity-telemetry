"""Process supervision and active streaming socket detection for Antigravity IDE on macOS.

Supervises desktop IDE lifecycle using LaunchServices AppleScript Bundle ID `com.google.antigravity`
(bypassing macOS SIP / sandbox restrictions on `ps` and `pgrep`), and guards against corrupting
active agent code generation using BSD `lsof` TCP 443 socket inspection.

Includes full mock supervisor for simulation / dry-run testing (Requirement R3).
Zero external dependencies (pure Python standard library).
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Optional, Tuple


ANTIGRAVITY_BUNDLE_ID = "com.google.antigravity"
ANTIGRAVITY_APP_PATH = Path("/Applications/Antigravity 2.app")
DEFAULT_DAEMON_DIR = Path.home() / ".gemini" / "antigravity" / "daemon"


class ProcessSupervisorError(Exception):
    """Raised when process supervision or signal operations fail."""
    pass


# ==============================================================================
# In-Memory Mock State for Simulation and Automated Testing
# ==============================================================================

class _MockSupervisorState:
    def __init__(self):
        self.is_running: bool = False
        self.streaming_active: bool = False
        self.dialog_confirmed: bool = True
        self.quit_success: bool = True
        self.relaunch_success: bool = True
        self.quit_count: int = 0
        self.relaunch_count: int = 0


_MOCK_STATE = _MockSupervisorState()


def is_mock_process_enabled(dry_run: bool = False) -> bool:
    """Checks whether mock process supervisor should be active."""
    return (
        dry_run
        or os.environ.get("ANTIGRAVITY_DRY_RUN") == "1"
        or os.environ.get("PROCESS_MOCK") == "1"
        or os.environ.get("ANTIGRAVITY_TEST_ROOT") is not None
    )


def set_mock_process_state(
    is_running: Optional[bool] = None,
    streaming_active: Optional[bool] = None,
    dialog_confirmed: Optional[bool] = None,
    quit_success: Optional[bool] = None,
    relaunch_success: Optional[bool] = None,
) -> None:
    """Configures the mock process supervisor state for testing."""
    if is_running is not None:
        _MOCK_STATE.is_running = is_running
    if streaming_active is not None:
        _MOCK_STATE.streaming_active = streaming_active
    if dialog_confirmed is not None:
        _MOCK_STATE.dialog_confirmed = dialog_confirmed
    if quit_success is not None:
        _MOCK_STATE.quit_success = quit_success
    if relaunch_success is not None:
        _MOCK_STATE.relaunch_success = relaunch_success


def reset_mock_process_state() -> None:
    """Resets mock supervisor state to defaults."""
    global _MOCK_STATE
    _MOCK_STATE = _MockSupervisorState()


def get_mock_process_counters() -> Tuple[int, int]:
    """Returns (quit_count, relaunch_count) recorded during mock runs."""
    return _MOCK_STATE.quit_count, _MOCK_STATE.relaunch_count


# ==============================================================================
# AppleScript & CLI Process Supervisions
# ==============================================================================

def run_applescript(script: str, timeout_sec: float = 5.0) -> Tuple[int, str, str]:
    """Executes an AppleScript command via `/usr/bin/osascript`.

    Returns (returncode, stdout, stderr).
    """
    cmd = ["/usr/bin/osascript", "-e", script]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"AppleScript timed out after {timeout_sec}s"
    except Exception as exc:
        return -1, "", str(exc)


def _has_live_antigravity_processes() -> Optional[bool]:
    """Checks `/bin/ps` for live Antigravity IDE or `language_server` processes.

    Returns True if any matching process is alive, False if none, or None if `ps` fails.
    """
    try:
        proc = subprocess.run(
            ["/bin/ps", "-ww", "-axo", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
        if proc.returncode != 0:
            return None
        for line in proc.stdout.splitlines():
            line_s = line.strip()
            if not line_s:
                continue
            if "/Applications/Antigravity 2.app/" in line_s or "/Applications/Antigravity.app/" in line_s:
                return True
            if "language_server" in line_s and "--app_data_dir antigravity" in line_s:
                return True
        return False
    except Exception:
        return None


def is_antigravity_running(
    bundle_id: str = ANTIGRAVITY_BUNDLE_ID,
    dry_run: bool = False,
) -> bool:
    """Checks whether Antigravity IDE is currently running via LaunchServices bundle identifier.

    AppleScript: `application id "com.google.antigravity" is running`
    Returns True if running, False otherwise.
    """
    if is_mock_process_enabled(dry_run):
        return _MOCK_STATE.is_running

    script = f'application id "{bundle_id}" is running'
    code, out, _ = run_applescript(script, timeout_sec=3.0)
    return code == 0 and out.lower() == "true"


def quit_antigravity(
    bundle_id: str = ANTIGRAVITY_BUNDLE_ID,
    timeout_sec: float = 5.0,
    dry_run: bool = False,
) -> bool:
    """Gracefully terminates Antigravity IDE using AppleScript:
    `tell application id "com.google.antigravity" to quit`

    Waits up to `timeout_sec` for both the main app and its `language_server`
    to exit cleanly (no blind `kill -9`).
    """
    if is_mock_process_enabled(dry_run):
        _MOCK_STATE.quit_count += 1
        if _MOCK_STATE.quit_success:
            _MOCK_STATE.is_running = False
            return True
        return False

    if not is_antigravity_running(bundle_id=bundle_id, dry_run=dry_run):
        ps_alive = _has_live_antigravity_processes()
        if ps_alive is not True:
            return True

    script = f'tell application id "{bundle_id}" to quit'
    run_applescript(script, timeout_sec=timeout_sec)

    # Poll until both AppleScript and ps report all Antigravity/language_server processes exited
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        app_running = is_antigravity_running(bundle_id=bundle_id, dry_run=False)
        ps_alive = _has_live_antigravity_processes()
        if not app_running and ps_alive is not True:
            return True
        time.sleep(0.25)

    return (not is_antigravity_running(bundle_id=bundle_id, dry_run=False)) and (_has_live_antigravity_processes() is not True)


def _has_fresh_hub_file(daemon_dir: Path, since_ts: float) -> bool:
    """Checks whether `language_server` has published a fresh `hub_*.json` since `since_ts`."""
    try:
        for hub_file in daemon_dir.glob("hub_*.json"):
            if hub_file.stat().st_mtime >= since_ts - 0.5:
                return True
    except OSError:
        return False
    return False


def relaunch_antigravity(
    bundle_id: str = ANTIGRAVITY_BUNDLE_ID,
    timeout_sec: float = 5.0,
    dry_run: bool = False,
    app_path: Optional[Path] = ANTIGRAVITY_APP_PATH,
    daemon_dir: Optional[Path] = DEFAULT_DAEMON_DIR,
) -> bool:
    """Relaunches Antigravity 2.0 IDE and waits briefly for `language_server` hub IPC.

    Prefers explicit `/Applications/Antigravity 2.app` when present so LaunchServices
    cannot accidentally pick a legacy `Antigravity.app` sharing the same bundle ID.
    """
    if is_mock_process_enabled(dry_run):
        _MOCK_STATE.relaunch_count += 1
        if _MOCK_STATE.relaunch_success:
            _MOCK_STATE.is_running = True
            return True
        return False

    if app_path is not None and app_path.exists():
        cmd = ["/usr/bin/open", "-a", str(app_path)]
    else:
        cmd = ["/usr/bin/open", "-b", bundle_id]

    effective_daemon_dir = daemon_dir
    if app_path not in (None, ANTIGRAVITY_APP_PATH) and daemon_dir == DEFAULT_DAEMON_DIR:
        effective_daemon_dir = None

    launch_ts = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
        if proc.returncode != 0:
            return False

        time.sleep(0.5)
        if effective_daemon_dir is not None and effective_daemon_dir.is_dir():
            hub_deadline = launch_ts + min(float(timeout_sec), 3.0)
            while time.time() < hub_deadline:
                if (
                    is_antigravity_running(bundle_id=bundle_id, dry_run=False)
                    and _has_fresh_hub_file(effective_daemon_dir, launch_ts)
                ):
                    return True
                time.sleep(0.25)

        return is_antigravity_running(bundle_id=bundle_id, dry_run=False)
    except Exception:
        return False


# ==============================================================================
# Active Streaming Socket Guard (BSD lsof)
# ==============================================================================

def is_streaming_active(timeout_sec: float = 2.0, dry_run: bool = False) -> bool:
    """Inspects active established TCP:443 sockets to Google Cloud Code PA.

    Uses BSD lsof command:
    `lsof -a -c agy -c Antigravity -iTCP:443 -sTCP:ESTABLISHED`

    Returns True if active prompt streaming/code generation is in progress, False if idle.
    """
    if is_mock_process_enabled(dry_run):
        return _MOCK_STATE.streaming_active

    cmd = [
        "lsof",
        "-a",
        "-c",
        "agy",
        "-c",
        "Antigravity",
        "-iTCP:443",
        "-sTCP:ESTABLISHED",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
        # lsof exits with code 0 if matching sockets are found, code 1 if none found
        if proc.returncode == 0:
            lines = [line.strip() for line in proc.stdout.strip().splitlines() if line.strip()]
            # First line is usually header: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
            # Active connections are lines containing ESTABLISHED
            socket_lines = [l for l in lines if "ESTABLISHED" in l]
            return len(socket_lines) > 0
        return False
    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError:
        # lsof not found in environment
        return False
    except Exception:
        return False


# ==============================================================================
# Native macOS UI Integration (Dialog & Notifications)
# ==============================================================================

def show_mac_confirmation_dialog(
    email: str,
    quota_summary: str = "",
    timeout_sec: float = 30.0,
    dry_run: bool = False,
) -> bool:
    """Displays a native modal confirmation dialog with AppleScript `display dialog`.

    Buttons: [ Отмена ]   [ Переключить ]
    Default button: "Переключить"

    Returns True if user confirmed, False if user canceled or pressed Esc.
    """
    if is_mock_process_enabled(dry_run):
        return _MOCK_STATE.dialog_confirmed

    body_text = f"Переключить Antigravity на:\n{email}?"
    if quota_summary:
        body_text += f"\n\nТекущие лимиты профиля:\n{quota_summary}"
    body_text += "\n\nAntigravity будет безопасно перезапущен."

    # Escape quotes for AppleScript string literal
    safe_body = body_text.replace('\\', '\\\\').replace('"', '\\"')

    script = (
        f'display dialog "{safe_body}" '
        f'with title "Переключение аккаунта" '
        f'buttons {{"Отмена", "Переключить"}} '
        f'default button "Переключить" '
        f'cancel button "Отмена"'
    )

    code, out, _ = run_applescript(script, timeout_sec=timeout_sec)
    # AppleScript returns "button returned:Переключить" on success
    # If user clicks "Отмена" or presses Esc, code is non-zero (exit code 1, user canceled)
    return code == 0 and "button returned:Переключить" in out


def send_mac_notification(
    title: str,
    message: str,
    dry_run: bool = False,
) -> bool:
    """Delivers a native macOS banner notification using AppleScript `display notification`."""
    if is_mock_process_enabled(dry_run):
        return True

    safe_title = title.replace('\\', '\\\\').replace('"', '\\"')
    safe_msg = message.replace('\\', '\\\\').replace('"', '\\"')
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    code, _, _ = run_applescript(script, timeout_sec=3.0)
    return code == 0


class ProcessSupervisor:
    """Class wrapper providing process supervisor interface with dry-run support."""

    def __init__(self, dry_run: bool = False, bundle_id: str = ANTIGRAVITY_BUNDLE_ID):
        self.dry_run = dry_run
        self.bundle_id = bundle_id

    def is_running(self) -> bool:
        return is_antigravity_running(bundle_id=self.bundle_id, dry_run=self.dry_run)

    def quit(self, timeout_sec: float = 5.0) -> bool:
        return quit_antigravity(bundle_id=self.bundle_id, timeout_sec=timeout_sec, dry_run=self.dry_run)

    def relaunch(self, timeout_sec: float = 5.0) -> bool:
        return relaunch_antigravity(bundle_id=self.bundle_id, timeout_sec=timeout_sec, dry_run=self.dry_run)

    def is_streaming(self, timeout_sec: float = 2.0) -> bool:
        return is_streaming_active(timeout_sec=timeout_sec, dry_run=self.dry_run)

    def confirm(self, email: str, quota_summary: str = "") -> bool:
        return show_mac_confirmation_dialog(
            email=email,
            quota_summary=quota_summary,
            dry_run=self.dry_run,
        )

    def notify(self, title: str, message: str) -> bool:
        return send_mac_notification(title=title, message=message, dry_run=self.dry_run)
