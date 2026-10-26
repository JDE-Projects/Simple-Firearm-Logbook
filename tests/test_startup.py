"""Tests for launch-time SQLite recovery and checks."""
import sqlite3
import subprocess
import sys
import time

from sfl import config, platform_win, startup
from sfl.db import open_db


class FakeApi:
    def __init__(self):
        self.messages = []

    def log(self, message):
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
    assert swept == [api.log]
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
