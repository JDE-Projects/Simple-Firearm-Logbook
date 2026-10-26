"""Crash-safe automatic restoration from checked launch backups."""
import datetime
import json
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass

from sfl import config, db
from sfl.services import launch_backup
from sfl.services import restore as restore_service

STAGE_NAME_RE = re.compile(r"^\.sfl_launch_restore_stage-[0-9a-f]{8}\.db\Z", re.ASCII)
KEPT_NAME_RE = re.compile(r"^simple_firearm_logbook\.damaged-\d{8}-\d{6}-[0-9a-f]{8}\.db\Z", re.ASCII)
KEPT_TEMP_NAME_RE = re.compile(r"^\.sfl_launch_restore_keep-[0-9a-f]{8}\.db\.tmp\Z", re.ASCII)
RECORD_FILENAME = "simple_firearm_logbook.restore.json"


@dataclass(frozen=True)
class RestoreResult:
    outcome: str
    conn: sqlite3.Connection | None = None
    notice: str | None = None


def _path(folder: str, name: str) -> str:
    return os.path.join(folder, name)


def _readwrite_connection(path: str) -> sqlite3.Connection:
    """Open an existing database with the normal launch connection settings."""
    conn = sqlite3.connect(
        launch_backup.database_uri(path, "rw"),
        uri=True,
        timeout=5.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _log(api, message: str) -> None:
    api._log(f"Launch restore: {message}")


def _fsync_file(path: str) -> None:
    with open(path, "r+b") as file:
        os.fsync(file.fileno())


def _copy_durable(source: str, destination: str) -> None:
    shutil.copyfile(source, destination)
    _fsync_file(destination)


def _record_path(db_path: str) -> str:
    return _path(os.path.dirname(db_path), RECORD_FILENAME)


def _write_record(db_path: str, record: dict) -> None:
    path = _record_path(db_path)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as file:
        json.dump(record, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def clear_record(api, db_path: str) -> None:
    try:
        os.remove(_record_path(db_path))
        _log(api, "cleared completed restore record")
    except FileNotFoundError:
        _log(api, "completed restore record was already gone")
    except OSError as error:
        _log(api, f"couldn't clear completed restore record ({type(error).__name__})")


def _backup_timestamp(name: str) -> str:
    parts = name.removesuffix(".db").split("-")
    return f"{parts[1]}-{parts[2]}"


def _format_backup_date(timestamp: str) -> str:
    value = datetime.datetime.strptime(timestamp, "%Y%m%d-%H%M%S").astimezone()
    hour = value.strftime("%I").lstrip("0") or "0"
    return f"{value.strftime('%B')} {value.day}, {value.year} at {hour}:{value.strftime('%M %p')}"


def _notice(folder: str, record: dict) -> str:
    return (
        "Your logbook's data file was damaged, so it was restored from the automatic backup made on "
        f"{_format_backup_date(record['backup_timestamp'])}.\n\n"
        "Changes made after that time were lost.\n\n"
        "The damaged file was kept here in case it's needed:\n"
        f"{_path(folder, record['kept_name'])}\n\n"
        "Photos and documents added after the backup are still in the data folder but are no longer "
        "linked to a firearm. Ones deleted after the backup will show as missing."
    )


def _valid_record(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    required = {"stage", "backup_name", "backup_timestamp", "staged_name", "kept_name", "kept_journal_name", "kept_temporary_name"}
    if set(record) != required or not all(isinstance(record[key], str) for key in required):
        return False
    if record["stage"] not in {"prepared", "aborting", "swapped"}:
        return False
    if not launch_backup.BACKUP_FILENAME_RE.fullmatch(record["backup_name"]):
        return False
    if not re.fullmatch(r"\d{8}-\d{6}", record["backup_timestamp"], re.ASCII):
        return False
    if record["backup_timestamp"] != _backup_timestamp(record["backup_name"]):
        return False
    if not STAGE_NAME_RE.fullmatch(record["staged_name"]) or not KEPT_NAME_RE.fullmatch(record["kept_name"]):
        return False
    if record["kept_journal_name"] != record["kept_name"] + "-journal":
        return False
    return bool(KEPT_TEMP_NAME_RE.fullmatch(record["kept_temporary_name"]))


def _read_record(api, db_path: str) -> dict | None:
    try:
        with open(_record_path(db_path), encoding="utf-8") as file:
            record = json.load(file)
    except Exception as error:
        _log(api, f"couldn't read restore record ({type(error).__name__})")
        return None
    if not _valid_record(record):
        _log(api, "restore record has invalid names or stage")
        return None
    return record


def _undo(api, db_path: str, record: dict) -> bool:
    folder = os.path.dirname(db_path)
    live_journal = db_path + "-journal"
    kept_journal = _path(folder, record["kept_journal_name"])
    if os.path.exists(live_journal) and os.path.exists(kept_journal):
        _log(api, "undo stopped because both live and kept journals exist")
        return False
    try:
        if os.path.exists(kept_journal):
            os.replace(kept_journal, live_journal)
            _log(api, "undo moved the kept journal back")
        for name in (record["staged_name"], record["kept_name"], record["kept_temporary_name"]):
            path = _path(folder, name)
            if os.path.exists(path):
                os.remove(path)
                _log(api, "undo removed a staged restore file")
        os.remove(_record_path(db_path))
        _log(api, "undo cleared restore record")
        return True
    except OSError as error:
        _log(api, f"undo failed ({type(error).__name__})")
        return False


def _open_restored(api, db_path: str, schema_version: int, record: dict) -> RestoreResult:
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        _log(api, "restored live file is missing or zero-byte")
        return RestoreResult("restore_stopped")
    conn = None
    try:
        conn = _readwrite_connection(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        checked = launch_backup.check_backup(db_path, schema_version)
        if not checked.ok or checked.newer:
            _log(api, f"restored live file failed checks ({checked.detail})")
            conn.close()
            return RestoreResult("restore_stopped")
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            _log(api, "restored live file is schema-empty")
            conn.close()
            return RestoreResult("restore_stopped")
        db.ensure_schema(conn, schema_version)
        restore_service.sweep_stale_staging(api._log)
        return RestoreResult("ready", conn, _notice(os.path.dirname(db_path), record))
    except Exception as error:
        if conn is not None:
            conn.close()
        _log(api, f"restored live file could not be opened ({type(error).__name__})")
        return RestoreResult("restore_stopped")


def _recover(api, db_path: str, schema_version: int) -> RestoreResult | None:
    if not os.path.exists(_record_path(db_path)):
        return None
    record = _read_record(api, db_path)
    if record is None:
        return RestoreResult("restore_stopped")
    folder = os.path.dirname(db_path)
    if record["stage"] == "prepared":
        if os.path.exists(_path(folder, record["staged_name"])):
            _log(api, "recovering prepared restore by undoing it")
            _write_record(db_path, {**record, "stage": "aborting"})
            return None if _undo(api, db_path, record) else RestoreResult("restore_stopped")
        checked = launch_backup.check_backup(db_path, schema_version)
        if not checked.ok or checked.newer:
            _log(api, "prepared record has no stage and live file cannot prove swap")
            return RestoreResult("restore_stopped")
        _log(api, "prepared record has no stage; live file proves swap")
        _write_record(db_path, {**record, "stage": "swapped"})
        return _open_restored(api, db_path, schema_version, record)
    if record["stage"] == "aborting":
        _log(api, "recovering aborting restore")
        return None if _undo(api, db_path, record) else RestoreResult("restore_stopped")
    _log(api, "recovering swapped restore")
    return _open_restored(api, db_path, schema_version, record)


def recover(api, db_path: str, schema_version: int) -> RestoreResult | None:
    """Resume a recorded restore without allowing recovery errors to escape launch."""
    try:
        return _recover(api, db_path, schema_version)
    except Exception as error:
        _log(api, f"recovery stopped unexpectedly ({type(error).__name__})")
        return RestoreResult("restore_stopped")


def _pick_backup(api, db_path: str, schema_version: int) -> tuple[str, str] | None:
    folder = _path(os.path.dirname(db_path), config.BACKUPS_DIRNAME)
    try:
        names = sorted((name for name in os.listdir(folder) if launch_backup.BACKUP_FILENAME_RE.fullmatch(name)), reverse=True)
    except (FileNotFoundError, OSError) as error:
        _log(api, f"couldn't list backups ({type(error).__name__})")
        names = []
    for name in names:
        checked = launch_backup.check_backup(_path(folder, name), schema_version)
        if checked.ok and not checked.newer:
            _log(api, "selected backup")
            return name, _backup_timestamp(name)
        _log(api, f"skipped backup ({'newer schema' if checked.newer else checked.detail})")
    _log(api, "no usable launch backup was found")
    return None


def _remove_orphaned_staging(api, db_path: str) -> None:
    folder = os.path.dirname(db_path)
    try:
        names = os.listdir(folder)
    except OSError as error:
        _log(api, f"couldn't list orphaned staging files ({type(error).__name__})")
        return
    for name in names:
        if not (STAGE_NAME_RE.fullmatch(name) or KEPT_TEMP_NAME_RE.fullmatch(name)):
            continue
        path = _path(folder, name)
        if os.path.islink(path) or not os.path.isfile(path):
            _log(api, "left non-file orphaned staging path")
            continue
        try:
            os.remove(path)
            _log(api, "removed orphaned staging file")
        except OSError as error:
            _log(api, f"couldn't remove orphaned staging file ({type(error).__name__})")


def restore_damaged(api, db_path: str, schema_version: int) -> RestoreResult:
    if not os.path.exists(_record_path(db_path)):
        _remove_orphaned_staging(api, db_path)
    picked = _pick_backup(api, db_path, schema_version)
    if picked is None:
        return RestoreResult("damaged_no_backup")
    backup_name, backup_timestamp = picked
    folder = os.path.dirname(db_path)
    token = uuid.uuid4().hex[:8]
    kept_name = f"simple_firearm_logbook.damaged-{datetime.datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}-{token}.db"
    record = {"stage": "prepared", "backup_name": backup_name, "backup_timestamp": backup_timestamp,
              "staged_name": f".sfl_launch_restore_stage-{token}.db", "kept_name": kept_name,
              "kept_journal_name": kept_name + "-journal", "kept_temporary_name": f".sfl_launch_restore_keep-{token}.db.tmp"}
    staged = _path(folder, record["staged_name"])
    try:
        _copy_durable(_path(_path(folder, config.BACKUPS_DIRNAME), backup_name), staged)
        conn = sqlite3.connect(launch_backup.database_uri(staged, "rw"), uri=True, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            db.ensure_schema(conn, schema_version)
        finally:
            conn.close()
        checked = launch_backup.check_backup(staged, schema_version)
        if not checked.ok or checked.newer or os.path.exists(staged + "-journal"):
            raise RuntimeError("staged replacement did not pass checks")
        _fsync_file(staged)
    except Exception as error:
        try:
            os.remove(staged)
        except OSError:
            pass
        _log(api, f"staging failed ({type(error).__name__})")
        return RestoreResult("restore_failed")
    try:
        _write_record(db_path, record)
        _log(api, "prepared restore from backup")
        _copy_durable(db_path, _path(folder, record["kept_temporary_name"]))
        os.replace(_path(folder, record["kept_temporary_name"]), _path(folder, record["kept_name"]))
        _log(api, "kept damaged file")
        live_journal = db_path + "-journal"
        if os.path.exists(live_journal):
            os.replace(live_journal, _path(folder, record["kept_journal_name"]))
            _log(api, "moved live journal")
        if os.path.exists(live_journal):
            raise RuntimeError("live journal remains before swap")
    except Exception as error:
        _log(api, f"restore failed before swap ({type(error).__name__})")
        try:
            _write_record(db_path, {**record, "stage": "aborting"})
        except Exception as record_error:
            _log(api, f"couldn't mark restore aborting ({type(record_error).__name__})")
            return RestoreResult("restore_stopped")
        return RestoreResult("restore_failed") if _undo(api, db_path, record) else RestoreResult("restore_stopped")
    try:
        os.replace(staged, db_path)
        _log(api, "swapped staged replacement into place")
    except Exception as error:
        _log(api, f"swap failed ({type(error).__name__})")
        try:
            _write_record(db_path, {**record, "stage": "aborting"})
        except Exception as record_error:
            _log(api, f"couldn't mark restore aborting ({type(record_error).__name__})")
            return RestoreResult("restore_stopped")
        return RestoreResult("restore_failed") if _undo(api, db_path, record) else RestoreResult("restore_stopped")
    try:
        _write_record(db_path, {**record, "stage": "swapped"})
        _log(api, "recorded completed swap")
    except Exception as error:
        _log(api, f"couldn't record completed swap ({type(error).__name__})")
        return RestoreResult("restore_stopped")
    return _open_restored(api, db_path, schema_version, record)
