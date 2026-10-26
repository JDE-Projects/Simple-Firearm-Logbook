"""Checked SQLite snapshots made before launch-time schema preparation."""
import datetime
import os
import re
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass

from sfl import config

BACKUP_TIME_LIMIT_S = 30
BACKUP_KEEP = 5
BACKUP_FILENAME_RE = re.compile(r"^simple_firearm_logbook-\d{8}-\d{6}-\d{6}\.db\Z", re.ASCII)
BACKUP_TMP_FILENAME_RE = re.compile(
    r"^simple_firearm_logbook-\d{8}-\d{6}-\d{6}\.db\.tmp\Z", re.ASCII
)


@dataclass(frozen=True)
class BackupCheck:
    ok: bool
    newer: bool
    detail: str


@dataclass(frozen=True)
class LaunchBackup:
    ok: bool
    folder: str
    detail: str
    prune_failed: bool = False


def database_uri(path: str, mode: str) -> str:
    """Build a SQLite file URI without losing a network-path server name."""
    path = os.path.abspath(path).replace("\\", "/")
    if path.startswith("//"):
        path = "//" + path
    return "file:" + urllib.parse.quote(path) + f"?mode={mode}"


def _backup_names(folder: str, pattern: re.Pattern[str]) -> list[str]:
    return sorted(name for name in os.listdir(folder) if pattern.fullmatch(name))


def _new_backup_paths(folder: str) -> tuple[str, str]:
    while True:
        name = f"simple_firearm_logbook-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.db"
        final = os.path.join(folder, name)
        temporary = final + ".tmp"
        if not os.path.exists(final) and not os.path.exists(temporary):
            return final, temporary


def _remove_stale_temps(folder: str) -> None:
    # Best effort: a leftover held open by another program (an antivirus scan)
    # must not fail every later backup. It never matches a final backup name.
    for name in _backup_names(folder, BACKUP_TMP_FILENAME_RE):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def check_backup(path: str, schema_version: int) -> BackupCheck:
    """Check a backup without modifying it; newer schemas need integrity only."""
    conn = None
    try:
        conn = sqlite3.connect(database_uri(path, "ro"), uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        if len(integrity) != 1 or integrity[0][0] != "ok":
            return BackupCheck(False, False, "integrity check failed")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > schema_version:
            return BackupCheck(True, True, "newer schema passed integrity check")
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = {"firearms", "photos", "attachments", "counters"} - tables
        if missing:
            return BackupCheck(False, False, f"missing table {sorted(missing)[0]}")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            return BackupCheck(False, False, "foreign key check failed")
        return BackupCheck(True, False, "backup passed checks")
    except Exception as error:
        return BackupCheck(False, False, f"backup could not be checked ({type(error).__name__})")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _prune(folder: str) -> None:
    names = _backup_names(folder, BACKUP_FILENAME_RE)
    for name in names[:-BACKUP_KEEP]:
        os.remove(os.path.join(folder, name))


def create_launch_backup(conn: sqlite3.Connection, db_path: str, schema_version: int) -> LaunchBackup:
    """Publish a checked snapshot, then retain only the five newest names."""
    folder = os.path.join(os.path.dirname(db_path), config.BACKUPS_DIRNAME)
    temporary = None
    target = None
    try:
        os.makedirs(folder, exist_ok=True)
        _remove_stale_temps(folder)
        target, temporary = _new_backup_paths(folder)
        deadline = time.monotonic() + BACKUP_TIME_LIMIT_S

        def progress(_status, _remaining, _total):
            if time.monotonic() >= deadline:
                raise TimeoutError("backup time limit exceeded")

        destination = sqlite3.connect(temporary, timeout=5.0)
        try:
            conn.backup(destination, pages=100, progress=progress)
        finally:
            destination.close()
        checked = check_backup(temporary, schema_version)
        if not checked.ok:
            _remove_file(temporary)
            return LaunchBackup(False, folder, checked.detail)
        os.replace(temporary, target)
    except Exception as error:
        if temporary is not None:
            _remove_file(temporary)
        return LaunchBackup(False, folder, f"backup could not be saved ({type(error).__name__})")
    try:
        _prune(folder)
    except Exception as error:
        return LaunchBackup(True, folder, f"backup saved but prune failed ({type(error).__name__})", True)
    return LaunchBackup(True, folder, "backup saved")
