"""Tests for crash-safe automatic launch restore."""
import json
import os
import sqlite3
import threading

import pytest

from sfl import config, db, startup
from sfl.services import launch_backup, launch_restore


class FakeApi:
    def __init__(self):
        self.messages = []

    def _log(self, message):
        self.messages.append(message)


def _good(path, marker=""):
    conn = db.open_db(str(path), config.SCHEMA_VERSION)
    if marker:
        conn.execute(
            "INSERT INTO firearms (log_number, make, model, created_at, updated_at) VALUES (?, 'Make', 'Model', 'now', 'now')",
            (marker,),
        )
        conn.commit()
    conn.close()


def _backup(folder, stamp, marker=""):
    folder.mkdir(exist_ok=True)
    path = folder / f"simple_firearm_logbook-{stamp}-000000.db"
    _good(path, marker)
    return path


def _record(path):
    return path.parent / launch_restore.RECORD_FILENAME


def _damaged_with_backup(tmp_path):
    live = tmp_path / config.DB_FILENAME
    _good(live, "old")
    _backup(tmp_path / config.BACKUPS_DIRNAME, "20261026-090500", "backup")
    live.write_bytes(b"not SQLite")
    return live, b"not SQLite"


def test_damaged_database_restores_newest_and_keeps_original(tmp_path):
    live, original = _damaged_with_backup(tmp_path)
    _backup(tmp_path / config.BACKUPS_DIRNAME, "20261027-090500", "newest")

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    assert "October 27, 2026 at 9:05 AM" in result.restore_notice
    kept = next(tmp_path.glob("simple_firearm_logbook.damaged-*.db"))
    assert kept.read_bytes() == original
    assert str(kept) in result.restore_notice
    assert result.conn.execute("SELECT log_number FROM firearms").fetchone()[0] == "newest"
    assert _record(live).exists()
    result.conn.close()


def test_restored_connection_is_usable_from_another_thread(tmp_path):
    live, _ = _damaged_with_backup(tmp_path)
    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)
    values = []

    worker = threading.Thread(
        target=lambda: values.append(result.conn.execute("SELECT count(*) FROM firearms").fetchone()[0])
    )
    worker.start()
    worker.join()

    assert values == [1]
    result.conn.close()


def test_selection_uses_filename_and_skips_newer_or_bad_backups(tmp_path):
    live, _ = _damaged_with_backup(tmp_path)
    folder = tmp_path / config.BACKUPS_DIRNAME
    older = _backup(folder, "20261025-090500", "older")
    newest = _backup(folder, "20261028-090500", "newer")
    conn = sqlite3.connect(newest)
    conn.execute(f"PRAGMA user_version = {config.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    bad = folder / "simple_firearm_logbook-20261027-090500-000000.db"
    bad.write_bytes(b"bad")
    (folder / "simple_firearm_logbook-20261026-090500-000000.db").write_bytes(b"bad")
    os.utime(older, (9999999999, 9999999999))

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    assert result.conn.execute("SELECT log_number FROM firearms").fetchone()[0] == "older"
    assert result.conn is not None
    result.conn.close()


def test_no_usable_backup_leaves_every_file_unchanged(tmp_path):
    live, _ = _damaged_with_backup(tmp_path)
    folder = tmp_path / config.BACKUPS_DIRNAME
    for backup in folder.iterdir():
        backup.write_bytes(b"bad")
    before = {item.name: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    after = {item.name: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    assert result.outcome == "damaged_no_backup"
    assert after == before


def test_broken_record_link_restores_newest_good_backup(tmp_path):
    live = tmp_path / config.DB_FILENAME
    _good(live, "old")
    conn = sqlite3.connect(live)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (99, 'x.jpg', 1, 0)")
    conn.commit()
    conn.close()
    _backup(tmp_path / config.BACKUPS_DIRNAME, "20261027-090500", "backup")

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    assert result.restore_notice is not None
    assert result.conn.execute("SELECT log_number FROM firearms").fetchone()[0] == "backup"
    assert next(tmp_path.glob("simple_firearm_logbook.damaged-*.db")).exists()
    assert result.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    result.conn.close()


def test_broken_record_link_without_backup_changes_nothing(tmp_path):
    live = tmp_path / config.DB_FILENAME
    _good(live, "old")
    conn = sqlite3.connect(live)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (99, 'x.jpg', 1, 0)")
    conn.commit()
    conn.close()
    before = {item.name: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    after = {item.name: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    assert result.outcome == "damaged_no_backup"
    assert after == before


def test_newer_database_with_broken_link_reports_newer(tmp_path):
    live = tmp_path / config.DB_FILENAME
    _good(live, "old")
    conn = sqlite3.connect(live)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (99, 'x.jpg', 1, 0)")
    conn.execute(f"PRAGMA user_version = {config.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "newer"
    assert not list(tmp_path.glob("simple_firearm_logbook.damaged-*.db"))


def test_live_journal_moves_beside_kept_damaged_file(tmp_path, monkeypatch):
    live, _ = _damaged_with_backup(tmp_path)
    journal = live.with_name(live.name + "-journal")
    journal.write_bytes(b"journal")
    monkeypatch.setattr(startup, "_readwrite_connection", lambda _: (_ for _ in ()).throw(sqlite3.DatabaseError("damaged")))
    monkeypatch.setattr(startup, "_database_error_outcome", lambda _: ("damaged", "damaged"))

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    assert not journal.exists()
    kept = next(tmp_path.glob("simple_firearm_logbook.damaged-*.db"))
    assert kept.with_name(kept.name + "-journal").read_bytes() == b"journal"
    result.conn.close()


@pytest.mark.parametrize("stage", ["prepared", "aborting"])
def test_recovery_undoes_pre_swap_restore_then_retries(tmp_path, stage):
    live, original = _damaged_with_backup(tmp_path)
    token = "a1b2c3d4"
    kept = f"simple_firearm_logbook.damaged-20261026-090500-{token}.db"
    staged = f".sfl_launch_restore_stage-{token}.db"
    record = {"stage": stage, "backup_name": "simple_firearm_logbook-20261026-090500-000000.db",
              "backup_timestamp": "20261026-090500", "staged_name": staged, "kept_name": kept,
              "kept_journal_name": kept + "-journal", "kept_temporary_name": f".sfl_launch_restore_keep-{token}.db.tmp"}
    (tmp_path / staged).write_bytes(b"stage")
    (tmp_path / kept).write_bytes(b"copy")
    _record(live).write_text(json.dumps(record), encoding="utf-8")

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    assert next(tmp_path.glob("simple_firearm_logbook.damaged-*.db")).read_bytes() == original
    assert result.restore_notice is not None
    result.conn.close()


def test_recovery_prepared_without_stage_finishes_proven_swap(tmp_path):
    live = tmp_path / config.DB_FILENAME
    _good(live, "restored")
    token = "a1b2c3d4"
    kept = f"simple_firearm_logbook.damaged-20261026-090500-{token}.db"
    record = {"stage": "prepared", "backup_name": "simple_firearm_logbook-20261026-090500-000000.db",
              "backup_timestamp": "20261026-090500", "staged_name": f".sfl_launch_restore_stage-{token}.db",
              "kept_name": kept, "kept_journal_name": kept + "-journal",
              "kept_temporary_name": f".sfl_launch_restore_keep-{token}.db.tmp"}
    _record(live).write_text(json.dumps(record), encoding="utf-8")

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    assert result.restore_notice is not None
    result.conn.close()


def test_recovery_unexpected_error_stops_without_escaping(tmp_path, monkeypatch):
    live, _ = _damaged_with_backup(tmp_path)
    token = "a1b2c3d4"
    record = {"stage": "prepared", "backup_name": "simple_firearm_logbook-20261026-090500-000000.db",
              "backup_timestamp": "20261026-090500", "staged_name": f".sfl_launch_restore_stage-{token}.db",
              "kept_name": f"simple_firearm_logbook.damaged-20261026-090500-{token}.db",
              "kept_journal_name": f"simple_firearm_logbook.damaged-20261026-090500-{token}.db-journal",
              "kept_temporary_name": f".sfl_launch_restore_keep-{token}.db.tmp"}
    (tmp_path / record["staged_name"]).write_bytes(b"stage")
    _record(live).write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(launch_restore, "_write_record", lambda *args: (_ for _ in ()).throw(OSError("disk full")))

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "restore_stopped"
    assert _record(live).exists()


def test_restored_open_ensures_schema_and_sweeps_staging(tmp_path, monkeypatch):
    live, _ = _damaged_with_backup(tmp_path)
    ensured = []
    swept = []
    original_ensure = launch_restore.db.ensure_schema

    def ensure(conn, version):
        ensured.append(conn.execute("PRAGMA database_list").fetchone()[2])
        original_ensure(conn, version)

    monkeypatch.setattr(launch_restore.db, "ensure_schema", ensure)
    monkeypatch.setattr(launch_restore.restore_service, "sweep_stale_staging", lambda log: swept.append(log))

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert str(live) in ensured
    assert swept
    result.conn.close()


@pytest.mark.parametrize("contents", ["not json", json.dumps({"stage": "unknown"})])
def test_bad_or_unreadable_record_stops_without_changing_files(tmp_path, contents):
    live, _ = _damaged_with_backup(tmp_path)
    _record(live).write_text(contents, encoding="utf-8")
    before = {item.name: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "restore_stopped"
    assert {item.name: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()} == before


def test_undo_stops_when_both_journals_exist(tmp_path):
    live, _ = _damaged_with_backup(tmp_path)
    token = "a1b2c3d4"
    kept = f"simple_firearm_logbook.damaged-20261026-090500-{token}.db"
    record = {"stage": "aborting", "backup_name": "simple_firearm_logbook-20261026-090500-000000.db",
              "backup_timestamp": "20261026-090500", "staged_name": f".sfl_launch_restore_stage-{token}.db",
              "kept_name": kept, "kept_journal_name": kept + "-journal",
              "kept_temporary_name": f".sfl_launch_restore_keep-{token}.db.tmp"}
    _record(live).write_text(json.dumps(record), encoding="utf-8")
    (tmp_path / (live.name + "-journal")).write_bytes(b"live")
    (tmp_path / (kept + "-journal")).write_bytes(b"kept")

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "restore_stopped"


def test_restored_launch_skips_launch_backup(tmp_path, monkeypatch):
    live, _ = _damaged_with_backup(tmp_path)
    monkeypatch.setattr(launch_backup, "create_launch_backup", lambda *args: pytest.fail("backup ran"))

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    result.conn.close()


def test_restore_removes_orphaned_staging_without_removing_kept_copy(tmp_path):
    live, _ = _damaged_with_backup(tmp_path)
    stage = tmp_path / ".sfl_launch_restore_stage-a1b2c3d4.db"
    temporary = tmp_path / ".sfl_launch_restore_keep-a1b2c3d4.db.tmp"
    kept = tmp_path / "simple_firearm_logbook.damaged-20261026-090500-a1b2c3d4.db"
    stage.write_bytes(b"stage")
    temporary.write_bytes(b"temporary")
    kept.write_bytes(b"kept")
    for backup in (tmp_path / config.BACKUPS_DIRNAME).iterdir():
        backup.write_bytes(b"bad")

    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "damaged_no_backup"
    assert not stage.exists()
    assert not temporary.exists()
    assert kept.read_bytes() == b"kept"


class _Interruption(BaseException):
    pass


def _assert_interrupted_restore_recovers(tmp_path, live, original):
    result = startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    assert result.restore_notice is not None
    assert result.conn.execute("SELECT log_number FROM firearms").fetchone()[0] == "backup"
    result.conn.close()
    assert any(kept.read_bytes() == original for kept in tmp_path.glob("simple_firearm_logbook.damaged-*.db"))


def test_interruption_during_staging_recovers_on_next_launch(tmp_path, monkeypatch):
    live, original = _damaged_with_backup(tmp_path)
    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            launch_restore, "_copy_durable", lambda *args: (_ for _ in ()).throw(_Interruption())
        )
        with pytest.raises(_Interruption):
            startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    _assert_interrupted_restore_recovers(tmp_path, live, original)


def test_interruption_after_prepared_record_recovers_on_next_launch(tmp_path, monkeypatch):
    live, original = _damaged_with_backup(tmp_path)
    original_copy = launch_restore._copy_durable
    calls = 0

    def copy_then_interrupt(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise _Interruption()
        original_copy(*args)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(launch_restore, "_copy_durable", copy_then_interrupt)
        with pytest.raises(_Interruption):
            startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    _assert_interrupted_restore_recovers(tmp_path, live, original)


def test_interruption_after_kept_copy_recovers_on_next_launch(tmp_path, monkeypatch):
    live, original = _damaged_with_backup(tmp_path)
    original_replace = launch_restore.os.replace

    def replace_then_interrupt(source, destination):
        original_replace(source, destination)
        if os.path.basename(source).startswith(".sfl_launch_restore_keep-"):
            raise _Interruption()

    with monkeypatch.context() as interrupted:
        interrupted.setattr(launch_restore.os, "replace", replace_then_interrupt)
        with pytest.raises(_Interruption):
            startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    _assert_interrupted_restore_recovers(tmp_path, live, original)


def test_interruption_after_journal_move_recovers_on_next_launch(tmp_path, monkeypatch):
    live, original = _damaged_with_backup(tmp_path)
    journal = live.with_name(live.name + "-journal")
    journal.write_bytes(b"journal")
    original_replace = launch_restore.os.replace

    def replace_then_interrupt(source, destination):
        original_replace(source, destination)
        if str(destination).endswith(".db-journal"):
            raise _Interruption()

    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            startup,
            "_readwrite_connection",
            lambda _: (_ for _ in ()).throw(sqlite3.DatabaseError("damaged")),
        )
        interrupted.setattr(startup, "_database_error_outcome", lambda _: ("damaged", "damaged"))
        interrupted.setattr(launch_restore.os, "replace", replace_then_interrupt)
        with pytest.raises(_Interruption):
            startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    _assert_interrupted_restore_recovers(tmp_path, live, original)


def test_interruption_after_swap_before_swapped_record_recovers_on_next_launch(tmp_path, monkeypatch):
    live, original = _damaged_with_backup(tmp_path)
    original_replace = launch_restore.os.replace

    def replace_then_interrupt(source, destination):
        original_replace(source, destination)
        if os.path.basename(source).startswith(".sfl_launch_restore_stage-"):
            raise _Interruption()

    with monkeypatch.context() as interrupted:
        interrupted.setattr(launch_restore.os, "replace", replace_then_interrupt)
        with pytest.raises(_Interruption):
            startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    _assert_interrupted_restore_recovers(tmp_path, live, original)


def test_interruption_after_swapped_record_before_clear_recovers_on_next_launch(tmp_path, monkeypatch):
    live, original = _damaged_with_backup(tmp_path)
    original_write = launch_restore._write_record

    def write_then_interrupt(path, record):
        original_write(path, record)
        if record["stage"] == "swapped":
            raise _Interruption()

    with monkeypatch.context() as interrupted:
        interrupted.setattr(launch_restore, "_write_record", write_then_interrupt)
        with pytest.raises(_Interruption):
            startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    _assert_interrupted_restore_recovers(tmp_path, live, original)


def test_interruption_during_undo_after_aborting_record_recovers_on_next_launch(tmp_path, monkeypatch):
    live, original = _damaged_with_backup(tmp_path)
    original_copy = launch_restore._copy_durable
    calls = 0

    def copy_then_fail(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError()
        original_copy(*args)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(launch_restore, "_undo", lambda *args: (_ for _ in ()).throw(_Interruption()))
        interrupted.setattr(launch_restore, "_copy_durable", copy_then_fail)
        with pytest.raises(_Interruption):
            startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    _assert_interrupted_restore_recovers(tmp_path, live, original)


def test_interruption_partway_through_undo_recovers_on_next_launch(tmp_path, monkeypatch):
    live, original = _damaged_with_backup(tmp_path)
    token = "a1b2c3d4"
    kept = f"simple_firearm_logbook.damaged-20261026-090500-{token}.db"
    record = {"stage": "aborting", "backup_name": "simple_firearm_logbook-20261026-090500-000000.db",
              "backup_timestamp": "20261026-090500", "staged_name": f".sfl_launch_restore_stage-{token}.db",
              "kept_name": kept, "kept_journal_name": kept + "-journal",
              "kept_temporary_name": f".sfl_launch_restore_keep-{token}.db.tmp"}
    (tmp_path / record["staged_name"]).write_bytes(b"stage")
    (tmp_path / kept).write_bytes(b"kept")
    (tmp_path / record["kept_journal_name"]).write_bytes(b"journal")
    _record(live).write_text(json.dumps(record), encoding="utf-8")
    original_remove = launch_restore.os.remove

    def remove_then_interrupt(path):
        if os.path.basename(path) == record["staged_name"]:
            raise _Interruption()
        original_remove(path)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(launch_restore.os, "remove", remove_then_interrupt)
        with pytest.raises(_Interruption):
            startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION)

    _assert_interrupted_restore_recovers(tmp_path, live, original)


@pytest.mark.parametrize("missing", [True, False])
def test_pending_swapped_restore_missing_or_zero_live_stops(tmp_path, missing):
    live = tmp_path / config.DB_FILENAME
    token = "a1b2c3d4"
    kept = f"simple_firearm_logbook.damaged-20261026-090500-{token}.db"
    record = {"stage": "swapped", "backup_name": "simple_firearm_logbook-20261026-090500-000000.db",
              "backup_timestamp": "20261026-090500", "staged_name": f".sfl_launch_restore_stage-{token}.db",
              "kept_name": kept, "kept_journal_name": kept + "-journal",
              "kept_temporary_name": f".sfl_launch_restore_keep-{token}.db.tmp"}
    if not missing:
        live.write_bytes(b"")
    _record(live).write_text(json.dumps(record), encoding="utf-8")

    assert startup.prepare_database(FakeApi(), str(live), config.SCHEMA_VERSION).outcome == "restore_stopped"
    assert not live.exists() if missing else live.stat().st_size == 0
