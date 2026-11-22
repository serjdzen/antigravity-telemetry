"""Storage primitives shared by the dashboard, OAuth CLI, and quota poller."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile


def email_key(email):
    return email.strip().lower()


def read_accounts(config_file, fallback_file, *, strict=False):
    for path in (config_file, fallback_file):
        try:
            with open(path, encoding="utf-8") as source:
                accounts = json.load(source)
            if not isinstance(accounts, list) or any(
                not isinstance(account, dict)
                or not isinstance(account.get("email"), str)
                or not email_key(account["email"])
                for account in accounts
            ):
                raise ValueError("Invalid account list")
            return accounts
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            if strict:
                raise
    return []


@contextmanager
def roster_lock(config_file):
    # Keep the lock file in place so all processes lock the same inode.
    path = Path(config_file).with_name(".accounts.lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "r+b") as lock:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_write(path, content):
    destination = Path(path)
    temporary = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, suffix=".tmp", delete=False,
    )
    try:
        with temporary:
            temporary.write(content)
        os.replace(temporary.name, destination)
    finally:
        Path(temporary.name).unlink(missing_ok=True)
