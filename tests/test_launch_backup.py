"""Tests for checked, launch-time SQLite backups."""
import os
import sqlite3

from sfl import config, db, startup
from sfl.services import launch_backup


class FakeApi:
    def __init__(self):
        self.messages = []

    def log(self, message):
        self.messages.append(message)


def _prepared(path):
    conn = db.open_db(str(path))
    conn.close()


def _backup_names(path):
    folder = path.parent / config.BACKUPS_DIRNAME
    return sorted(item.name for item in folder.iterdir() if item.suffix == ".db")


def test_check_backup_accepts_good_database(tmp_path):
    path = tmp_path / "good.db"
    _prepared(path)

    checked = launch_backup.check_backup(str(path), config.SCHEMA_VERSION)

    assert checked.ok is True
    assert checked.newer is False


def test_check_backup_rejects_damaged_database(tmp_path):
    path = tmp_path / "damaged.db"
    path.write_bytes(b"not SQLite")

    checked = launch_backup.check_backup(str(path), config.SCHEMA_VERSION)

    assert checked.ok is False


def test_check_backup_rejects_missing_table(tmp_path):
    path = tmp_path / "incomplete.db"
    sqlite3.connect(path).close()

    checked = launch_backup.check_backup(str(path), config.SCHEMA_VERSION)

    assert checked.ok is False
    assert "missing table" in checked.detail


def test_check_backup_rejects_broken_record_link(tmp_path):
    path = tmp_path / "broken-link.db"
    _prepared(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (99, 'x.jpg', 1, 0)")
    conn.commit()
    conn.close()

    checked = launch_backup.check_backup(str(path), config.SCHEMA_VERSION)

    assert checked.ok is False
    assert "foreign key" in checked.detail


def test_check_backup_accepts_newer_database_on_integrity_alone(tmp_path):
    path = tmp_path / "newer.db"
    sqlite3.connect(path).execute(f"PRAGMA user_version = {config.SCHEMA_VERSION + 1}").close()

    checked = launch_backup.check_backup(str(path), config.SCHEMA_VERSION)

    assert checked.ok is True
    assert checked.newer is True


def test_normal_launch_creates_checked_backup(tmp_path):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)

    result = startup.prepare_database(FakeApi(), str(path), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    result.conn.close()
    names = _backup_names(path)
    assert len(names) == 1
    assert launch_backup.BACKUP_FILENAME_RE.fullmatch(names[0])
    assert launch_backup.check_backup(str(path.parent / config.BACKUPS_DIRNAME / names[0]), config.SCHEMA_VERSION).ok


def test_six_launches_keep_newest_five_by_filename_not_modified_time(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)
    names = iter(
        f"simple_firearm_logbook-20260101-00000{number}-000000.db" for number in range(1, 7)
    )

    def paths(folder):
        final = os.path.join(folder, next(names))
        return final, final + ".tmp"

    monkeypatch.setattr(launch_backup, "_new_backup_paths", paths)
    for number in range(6):
        result = startup.prepare_database(FakeApi(), str(path), config.SCHEMA_VERSION)
        result.conn.close()
        backup = path.parent / config.BACKUPS_DIRNAME / f"simple_firearm_logbook-20260101-00000{number + 1}-000000.db"
        os.utime(backup, (1000 + number, 1000 + number))

    assert _backup_names(path) == [
        f"simple_firearm_logbook-20260101-00000{number}-000000.db" for number in range(2, 7)
    ]


def test_rejected_backup_never_prunes_existing_set(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)
    folder = tmp_path / config.BACKUPS_DIRNAME
    folder.mkdir()
    existing = folder / "simple_firearm_logbook-20260101-000000-000000.db"
    existing.write_bytes(b"existing")
    calls = []
    monkeypatch.setattr(launch_backup, "check_backup", lambda *_: launch_backup.BackupCheck(False, False, "rejected"))
    monkeypatch.setattr(launch_backup, "_prune", lambda folder: calls.append(folder))
    conn = sqlite3.connect(path)

    result = launch_backup.create_launch_backup(conn, str(path), config.SCHEMA_VERSION)

    conn.close()
    assert result.ok is False
    assert calls == []
    assert existing.read_bytes() == b"existing"


def test_cleanup_and_prune_never_touch_unmatched_files(tmp_path):
    folder = tmp_path / config.BACKUPS_DIRNAME
    folder.mkdir()
    untouched = folder / "keep-me.db"
    untouched.write_bytes(b"do not touch")
    unmatched_temp = folder / "keep-me.db.tmp"
    unmatched_temp.write_bytes(b"do not touch")
    stale = folder / "simple_firearm_logbook-20260101-000000-000000.db.tmp"
    stale.write_bytes(b"temporary")

    launch_backup._remove_stale_temps(str(folder))
    launch_backup._prune(str(folder))

    assert untouched.read_bytes() == b"do not touch"
    assert unmatched_temp.read_bytes() == b"do not touch"
    assert not stale.exists()


def test_stale_temp_held_open_does_not_fail_the_backup(tmp_path):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)
    folder = tmp_path / config.BACKUPS_DIRNAME
    folder.mkdir()
    stale = folder / "simple_firearm_logbook-20260101-000000-000000.db.tmp"
    stale.write_bytes(b"temporary")
    conn = sqlite3.connect(path)

    with open(stale, "rb"):  # an open handle blocks deletion on Windows
        backup = launch_backup.create_launch_backup(conn, str(path), config.SCHEMA_VERSION)

    conn.close()
    assert backup.ok
    assert len(_backup_names(path)) == 1


def test_backup_timeout_aborts_and_leaves_no_final_file(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)
    conn = sqlite3.connect(path)
    monkeypatch.setattr(launch_backup, "BACKUP_TIME_LIMIT_S", 0)

    result = launch_backup.create_launch_backup(conn, str(path), config.SCHEMA_VERSION)

    conn.close()
    assert result.ok is False
    folder = tmp_path / config.BACKUPS_DIRNAME
    assert not list(folder.glob("*.db"))
    assert not list(folder.glob("*.tmp"))


def test_five_good_backups_survive_a_later_damaged_launch(tmp_path):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)
    for _ in range(5):
        result = startup.prepare_database(FakeApi(), str(path), config.SCHEMA_VERSION)
        result.conn.close()
    before = _backup_names(path)
    path.write_bytes(b"not SQLite")

    result = startup.prepare_database(FakeApi(), str(path), config.SCHEMA_VERSION)

    assert result.outcome == "damaged"
    assert _backup_names(path) == before


def test_failed_backup_before_upgrade_blocks_without_changing_database(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE firearms DROP COLUMN nfa_stamp_date")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    monkeypatch.setattr(
        launch_backup, "create_launch_backup", lambda *args: launch_backup.LaunchBackup(False, "backup-folder", "full")
    )

    result = startup.prepare_database(FakeApi(), str(path), config.SCHEMA_VERSION)

    assert result.outcome == "upgrade_blocked"
    assert result.conn is None
    assert path.read_bytes() == before


def test_failed_ordinary_backup_warns_but_opens(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)
    monkeypatch.setattr(
        launch_backup, "create_launch_backup", lambda *args: launch_backup.LaunchBackup(False, "backup-folder", "full")
    )

    result = startup.prepare_database(FakeApi(), str(path), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    assert result.notices == ("Today's backup of your logbook couldn't be saved to backup-folder. Your logbook opened normally.",)
    result.conn.close()


def test_prune_failure_warns_but_opens(tmp_path, monkeypatch):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)
    monkeypatch.setattr(
        launch_backup,
        "create_launch_backup",
        lambda *args: launch_backup.LaunchBackup(True, "backup-folder", "denied", True),
    )

    result = startup.prepare_database(FakeApi(), str(path), config.SCHEMA_VERSION)

    assert result.outcome == "ready"
    assert result.notices == ("Older backups in backup-folder couldn't be cleaned up.",)
    result.conn.close()


def test_schema_would_change_for_each_trigger_and_not_for_prepared_database(tmp_path):
    path = tmp_path / config.DB_FILENAME
    _prepared(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    assert db.schema_would_change(conn, config.SCHEMA_VERSION) is False

    conn.execute("DROP TABLE attachments")
    assert db.schema_would_change(conn, config.SCHEMA_VERSION) is True
    conn.close()

    _prepared(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("DELETE FROM counters WHERE id=1")
    assert db.schema_would_change(conn, config.SCHEMA_VERSION) is True
    conn.close()

    _prepared(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA user_version = 0")
    assert db.schema_would_change(conn, config.SCHEMA_VERSION) is True
    conn.close()

    _prepared(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("ALTER TABLE firearms DROP COLUMN nfa_stamp_date")
    assert db.schema_would_change(conn, config.SCHEMA_VERSION) is True
    conn.close()
