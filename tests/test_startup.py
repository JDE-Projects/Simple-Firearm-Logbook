"""Tests for launch-time SQLite recovery and checks."""
import sqlite3
import subprocess
import sys
import time

import pytest

from sfl import config, platform_win, startup
from sfl.db import open_db
from sfl.services import launch_backup, launch_restore


class FakeApi:
    def __init__(self):
        self.messages = []

    def _log(self, message):
        self.messages.append(message)


def _prepare(path):
    api = FakeApi()
    result = startup.prepare_database(api, str(path), config.SCHEMA_VERSION)
    return api, result


def test_missing_database_is_created_and_ready(tmp_path):
    path = tmp_path / config.DB_FILENAME

    api, result = _prepare(path)

    assert result.outcome == "ready"
    assert result.conn is not None
    assert path.exists()
    assert not (tmp_path / config.BACKUPS_DIRNAME).exists()
    assert result.conn.execute("SELECT name FROM sqlite_master WHERE name='firearms'").fetchone()
    assert api.messages[-1].startswith("Launch database ready:")
    result.conn.close()


def test_failed_first_run_creation_is_unavailable(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    monkeypatch.setattr(startup.db, "open_db", lambda *args: (_ for _ in ()).throw(PermissionError()))

    _, result = _prepare(path)

    assert result.outcome == "unavailable"
    assert result.conn is None


def test_zero_byte_database_is_initialized(tmp_path):
    path = tmp_path / config.DB_FILENAME
    path.touch()

    _, result = _prepare(path)

    assert result.outcome == "ready"
    assert result.conn.execute("SELECT name FROM sqlite_master WHERE name='firearms'").fetchone()
    assert not (tmp_path / config.BACKUPS_DIRNAME).exists()
    result.conn.close()


def test_schema_empty_database_is_initialized(tmp_path):
    path = tmp_path / config.DB_FILENAME
    sqlite3.connect(path).close()

    _, result = _prepare(path)

    assert result.outcome == "ready"
    assert result.conn.execute("SELECT name FROM sqlite_master WHERE name='firearms'").fetchone()
    assert not (tmp_path / config.BACKUPS_DIRNAME).exists()
    result.conn.close()


def test_valid_empty_logbook_stays_in_the_same_file(tmp_path):
    path = tmp_path / config.DB_FILENAME
    conn = open_db(str(path))
    conn.close()
    before = path.stat().st_ino

    _, result = _prepare(path)

    assert result.outcome == "ready"
    assert path.stat().st_ino == before
    assert result.conn.execute("SELECT COUNT(*) FROM firearms").fetchone()[0] == 0
    result.conn.close()


def test_encrypted_header_is_refused(tmp_path):
    path = tmp_path / config.DB_FILENAME
    path.write_bytes(config.PRO_ENCRYPTED_MAGIC + b"encrypted data")

    _, result = _prepare(path)

    assert result.outcome == "encrypted"
    assert result.conn is None


def test_pro_stamped_database_is_refused(tmp_path):
    path = tmp_path / config.DB_FILENAME
    conn = open_db(str(path))
    conn.execute(f"PRAGMA application_id = {config.PRO_APPLICATION_ID}")
    conn.commit()
    conn.close()

    _, result = _prepare(path)

    assert result.outcome == "pro_stamped"
    assert result.conn is None


def test_pro_stamped_populated_database_is_ready_when_accepted(tmp_path):
    path = tmp_path / config.DB_FILENAME
    _add_firearm(path)
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA application_id = {config.PRO_APPLICATION_ID}")
    conn.commit()
    conn.close()

    _, result = _prepare_with_options(path, accept_pro=True)

    assert result.outcome == "ready"
    assert result.conn.execute("SELECT COUNT(*) FROM firearms").fetchone()[0] == 1
    result.conn.close()


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / config.DB_FILENAME
    conn = open_db(str(path))
    conn.execute(f"PRAGMA user_version = {config.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    _, result = _prepare(path)

    assert result.outcome == "newer"
    assert result.conn is None


def test_garbage_database_is_damaged_without_changes(tmp_path):
    path = tmp_path / config.DB_FILENAME
    original = b"this is not a SQLite database"
    path.write_bytes(original)

    _, result = _prepare(path)

    assert result.outcome == "damaged_no_backup"
    assert result.conn is None
    assert path.read_bytes() == original


def test_exclusive_database_lock_is_unavailable(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    conn = open_db(str(path))
    conn.execute("BEGIN EXCLUSIVE")
    monkeypatch.setattr(
        startup,
        "_readwrite_connection",
        lambda value: sqlite3.connect(
            startup._readwrite_uri(value),
            uri=True,
            timeout=0.05,
            check_same_thread=False,
        ),
    )

    started = time.monotonic()
    _, result = _prepare(path)

    assert result.outcome == "unavailable"
    assert time.monotonic() - started < 1
    conn.rollback()
    conn.close()


def test_hot_rollback_journal_is_recovered(tmp_path):
    path = tmp_path / config.DB_FILENAME
    conn = open_db(str(path))
    conn.execute(
        "INSERT INTO firearms (log_number, make, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("00001", "Before", "Crash", "now", "now"),
    )
    conn.commit()
    conn.close()
    script = (
        "import os, sqlite3, sys; "
        "c=sqlite3.connect(sys.argv[1]); "
        "c.execute('PRAGMA journal_mode=DELETE'); "
        "c.execute('PRAGMA cache_size=1'); "
        "c.execute('BEGIN IMMEDIATE'); "
        "c.executemany(\"INSERT INTO firearms (log_number, make, model, created_at, updated_at) "
        "VALUES (?, 'Uncommitted', 'Crash', 'now', 'now')\", "
        "[(f'{n:05d}',) for n in range(2, 1000)]); "
        "os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", script, str(path)], check=True)
    assert path.with_name(path.name + "-journal").exists()

    _, result = _prepare(path)

    assert result.outcome == "ready"
    rows = result.conn.execute("SELECT log_number FROM firearms ORDER BY log_number").fetchall()
    assert [row[0] for row in rows] == ["00001"]
    result.conn.close()
    assert not path.with_name(path.name + "-journal").exists()


def test_readwrite_uri_keeps_network_server_and_drive_letter():
    assert startup._readwrite_uri(r"\\server\share\logbook.db") == "file:////server/share/logbook.db?mode=rw"
    assert startup._readwrite_uri(r"Z:\Logbook\logbook.db") == "file:Z%3A/Logbook/logbook.db?mode=rw"


def test_readwrite_uri_opens_a_path_with_spaces_and_hash(tmp_path):
    folder = tmp_path / "my logbook #1"
    folder.mkdir()
    path = folder / config.DB_FILENAME
    open_db(str(path)).close()

    _, result = _prepare(path)

    assert result.outcome == "ready"
    result.conn.close()


def test_readwrite_uri_never_creates_a_missing_file(tmp_path):
    path = tmp_path / config.DB_FILENAME

    try:
        sqlite3.connect(startup._readwrite_uri(str(path)), uri=True).close()
    except sqlite3.OperationalError:
        pass

    assert not path.exists()


def test_ready_launch_sweeps_stale_staging(tmp_path, monkeypatch):
    swept = []
    monkeypatch.setattr(startup.restore_service, "sweep_stale_staging", lambda log: swept.append(log))
    path = tmp_path / config.DB_FILENAME
    open_db(str(path)).close()

    api, result = _prepare(path)

    assert result.outcome == "ready"
    assert swept == [api._log]
    result.conn.close()


def test_damaged_launch_does_not_sweep(tmp_path, monkeypatch):
    swept = []
    monkeypatch.setattr(startup.restore_service, "sweep_stale_staging", lambda log: swept.append(log))
    path = tmp_path / config.DB_FILENAME
    path.write_bytes(b"this is not a SQLite database")

    _, result = _prepare(path)

    assert result.outcome == "damaged_no_backup"
    assert swept == []


def test_folder_lock_refuses_a_second_handle(tmp_path):
    platform_win._folder_lock_handle = None
    try:
        assert platform_win._acquire_folder_lock(str(tmp_path)) is True
        assert platform_win._acquire_folder_lock(str(tmp_path)) is False
    finally:
        handle = platform_win._folder_lock_handle
        platform_win._folder_lock_handle = None
        if handle is not None:
            msvcrt = __import__("msvcrt")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            handle.close()


def test_app_window_class_matches_qt_windows_only():
    assert platform_win._is_app_window_class("Qt6112QWindowIcon") is True
    assert platform_win._is_app_window_class("Qt5QWindowIcon") is True
    assert platform_win._is_app_window_class("CabinetWClass") is False   # File Explorer
    assert platform_win._is_app_window_class("Chrome_WidgetWin_1") is False
    assert platform_win._is_app_window_class("Qt6112QWindowToolSaveBits") is False
    assert platform_win._is_app_window_class("") is False


def _backups(tmp_path):
    folder = tmp_path / config.BACKUPS_DIRNAME
    return sorted(p.name for p in folder.glob("*.db")) if folder.exists() else []


def _add_firearm(path):
    conn = open_db(str(path))
    conn.execute(
        "INSERT INTO firearms (log_number, make, model, created_at, updated_at) "
        "VALUES ('0001', 'Make', 'Model', 'now', 'now')"
    )
    conn.execute("UPDATE counters SET next_log_number=2 WHERE id=1")
    conn.commit()
    conn.close()


def _prepare_with_options(path, **options):
    api = FakeApi()
    result = startup.prepare_database(api, str(path), config.SCHEMA_VERSION, **options)
    return api, result


@pytest.mark.parametrize(
    "path_kind",
    ["resumed_restore", "missing", "zero_byte", "schema_empty", "restored_damaged", "normal"],
)
def test_finish_runs_once_for_each_ready_path(tmp_path, monkeypatch, path_kind):
    path = tmp_path / config.DB_FILENAME
    if path_kind == "resumed_restore":
        restored = open_db(str(path))
        monkeypatch.setattr(
            startup.launch_restore, "recover",
            lambda *_args: launch_restore.RestoreResult("ready", restored, "restored"),
        )
    elif path_kind == "zero_byte":
        path.touch()
    elif path_kind == "schema_empty":
        sqlite3.connect(path).close()
    elif path_kind == "restored_damaged":
        backup_folder = tmp_path / config.BACKUPS_DIRNAME
        backup_folder.mkdir()
        open_db(str(backup_folder / "simple_firearm_logbook-20261026-090500-000000.db")).close()
        path.write_bytes(b"not SQLite")
    elif path_kind == "normal":
        open_db(str(path)).close()

    finished = []
    _, result = _prepare_with_options(path, finish=finished.append)

    assert result.outcome == "ready"
    assert finished == [result.conn]
    result.conn.close()


def test_failing_finish_closes_restored_connection_and_keeps_restore_record(tmp_path):
    path = tmp_path / config.DB_FILENAME
    backup_folder = tmp_path / config.BACKUPS_DIRNAME
    backup_folder.mkdir()
    open_db(str(backup_folder / "simple_firearm_logbook-20261026-090500-000000.db")).close()
    path.write_bytes(b"not SQLite")
    seen = []

    def broken_finish(conn):
        seen.append(conn)
        raise RuntimeError("finish failed")

    _, result = _prepare_with_options(path, finish=broken_finish)

    assert result.outcome == "unavailable"
    assert result.conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        seen[0].execute("SELECT 1")
    assert (tmp_path / launch_restore.RECORD_FILENAME).exists()


@pytest.mark.parametrize(
    "edition_check",
    [lambda _conn: True, lambda _conn: (_ for _ in ()).throw(RuntimeError("broken check"))],
)
def test_edition_backup_check_takes_backup_for_empty_logbook(tmp_path, edition_check):
    path = tmp_path / config.DB_FILENAME
    open_db(str(path)).close()

    _, result = _prepare_with_options(path, needs_backup=edition_check)

    assert result.outcome == "ready"
    assert len(_backups(tmp_path)) == 1
    result.conn.close()


@pytest.mark.parametrize(
    "edition_check",
    [lambda _conn: True, lambda _conn: (_ for _ in ()).throw(RuntimeError("broken check"))],
)
def test_edition_schema_change_blocks_when_launch_backup_fails(tmp_path, monkeypatch, edition_check):
    path = tmp_path / config.DB_FILENAME
    open_db(str(path)).close()
    monkeypatch.setattr(
        startup.launch_backup, "create_launch_backup",
        lambda *_args: launch_backup.LaunchBackup(False, str(tmp_path / config.BACKUPS_DIRNAME), "backup failed"),
    )

    _, result = _prepare_with_options(path, needs_backup=lambda _conn: True, would_change=edition_check)

    assert result.outcome == "upgrade_blocked"
    assert result.conn is None


@pytest.mark.parametrize("message", ["Open this edition instead.", None])
def test_failure_message_is_attached_to_non_ready_result(tmp_path, message):
    path = tmp_path / config.DB_FILENAME
    path.write_bytes(config.PRO_ENCRYPTED_MAGIC + b"encrypted data")

    _, result = _prepare_with_options(path, failure_message=lambda outcome: message if outcome == "encrypted" else None)

    assert result.outcome == "encrypted"
    assert result.message == message


def test_empty_logbook_skips_launch_backup(tmp_path):
    path = tmp_path / config.DB_FILENAME
    open_db(str(path)).close()

    api, result = _prepare(path)

    assert result.outcome == "ready"
    assert result.notices == ()
    assert _backups(tmp_path) == []
    assert "Launch backup skipped: no records yet" in api.messages
    result.conn.close()


def test_one_firearm_takes_launch_backup(tmp_path):
    path = tmp_path / config.DB_FILENAME
    _add_firearm(path)

    api, result = _prepare(path)

    assert result.outcome == "ready"
    assert len(_backups(tmp_path)) == 1
    assert "Launch backup saved" in api.messages
    result.conn.close()


def test_emptied_logbook_that_issued_numbers_takes_launch_backup(tmp_path):
    path = tmp_path / config.DB_FILENAME
    _add_firearm(path)
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM firearms")
    conn.commit()
    conn.close()

    _, result = _prepare(path)

    assert result.outcome == "ready"
    assert len(_backups(tmp_path)) == 1
    result.conn.close()


def test_old_version_empty_logbook_upgrades_without_backup(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    open_db(str(path)).close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    def no_backup(*_args):
        raise AssertionError("an empty logbook must not be backed up")

    monkeypatch.setattr(startup.launch_backup, "create_launch_backup", no_backup)

    _, result = _prepare(path)

    assert result.outcome == "ready"
    assert result.notices == ()
    assert result.conn.execute("PRAGMA user_version").fetchone()[0] == config.SCHEMA_VERSION
    result.conn.close()


def test_failed_record_check_takes_launch_backup(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    open_db(str(path)).close()

    def broken_check(_conn):
        raise sqlite3.OperationalError("no such table")

    monkeypatch.setattr(startup.db, "has_user_records", broken_check)

    api, result = _prepare(path)

    assert result.outcome == "ready"
    assert len(_backups(tmp_path)) == 1
    assert "Launch backup record check failed (OperationalError)" in api.messages
    result.conn.close()
