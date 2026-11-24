"""Database preparation performed before the logbook window is created."""
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from sfl import config, db
from sfl.debug_log import describe_error
from sfl.services import launch_backup, launch_restore
from sfl.services import restore as restore_service


@dataclass(frozen=True)
class PreparedDatabase:
    """The ready connection or a reason launch must stop."""

    outcome: str
    conn: sqlite3.Connection | None
    detail: str
    notices: tuple[str, ...] = ()
    restore_notice: str | None = None
    message: str | None = None


def _readwrite_uri(path: str) -> str:
    """SQLite URI that opens an existing file read-write and never creates it.
    A network path (\\\\server\\share) gets an empty authority, file:////server,
    because SQLite drops a URI's authority and would lose the server name."""
    return launch_backup.database_uri(path, "rw")


def _readwrite_connection(path: str) -> sqlite3.Connection:
    return launch_restore._readwrite_connection(path)


def _result(api, outcome: str, conn: sqlite3.Connection | None, detail: str,
            notices: tuple[str, ...] = (), restore_notice: str | None = None,
            failure_message: Callable[[str], str | None] | None = None) -> PreparedDatabase:
    api._log(f"Launch database {outcome}: {detail}")
    message = failure_message(outcome) if outcome != "ready" and failure_message is not None else None
    return PreparedDatabase(outcome, conn, detail, notices, restore_notice, message)


def _prepared_result(api, outcome: str, conn: sqlite3.Connection | None, detail: str,
                     notices: tuple[str, ...] = (), restore_notice: str | None = None,
                     finish: Callable[[sqlite3.Connection], None] | None = None,
                     failure_message: Callable[[str], str | None] | None = None) -> PreparedDatabase:
    """Finish a usable launch connection, then return its prepared result."""
    if conn is not None and finish is not None:
        try:
            finish(conn)
        except Exception as error:  # noqa: BLE001 - an edition finish step must never escape launch.
            _close(conn)
            return _result(
                api, "unavailable", None,
                f"database finishing step failed ({describe_error(error)})",
                failure_message=failure_message,
            )
    return _result(api, outcome, conn, detail, notices, restore_notice, failure_message)


def _close(conn: sqlite3.Connection | None) -> None:
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _database_error_outcome(error: sqlite3.DatabaseError) -> tuple[str, str]:
    code = getattr(error, "sqlite_errorcode", 0) or 0
    name = describe_error(error)
    if code & 0xFF in (11, 26):
        return "damaged", f"database is corrupt or not SQLite ({name})"
    return "unavailable", f"database could not be opened ({name})"


def _initialize_database(api, db_path: str, schema_version: int, detail: str,
                         finish: Callable[[sqlite3.Connection], None] | None,
                         failure_message: Callable[[str], str | None] | None) -> PreparedDatabase:
    try:
        conn = db.open_db(db_path, schema_version)
        restore_service.sweep_stale_staging(api._log)
        return _prepared_result(api, "ready", conn, detail, finish=finish, failure_message=failure_message)
    except sqlite3.DatabaseError as error:
        outcome, error_detail = _database_error_outcome(error)
        return _prepared_result(api, outcome, None, error_detail, failure_message=failure_message)
    except Exception as error:
        return _prepared_result(
            api, "unavailable", None, f"database could not be prepared ({describe_error(error)})",
            failure_message=failure_message,
        )


def prepare_database(
    api, db_path: str, schema_version: int, *, accept_pro: bool = False,
    finish: Callable[[sqlite3.Connection], None] | None = None,
    needs_backup: Callable[[sqlite3.Connection], bool] | None = None,
    would_change: Callable[[sqlite3.Connection], bool] | None = None,
    failure_message: Callable[[str], str | None] | None = None,
) -> PreparedDatabase:
    """Recover, check, and prepare the live database for a launch."""
    recovered = launch_restore.recover(api, db_path, schema_version)
    if recovered is not None:
        return _prepared_result(
            api, recovered.outcome, recovered.conn, "automatic restore recovery",
            restore_notice=recovered.notice, finish=finish, failure_message=failure_message,
        )
    if not os.path.exists(db_path):
        return _initialize_database(
            api, db_path, schema_version, "database created for first launch", finish, failure_message
        )

    try:
        with open(db_path, "rb") as file:
            header = file.read(8)
    except Exception as error:
        return _prepared_result(
            api, "unavailable", None, f"database header could not be read ({describe_error(error)})",
            failure_message=failure_message,
        )
    if header == config.PRO_ENCRYPTED_MAGIC:
        return _prepared_result(
            api, "encrypted", None, "database is protected by Pro", failure_message=failure_message
        )
    if not header:
        return _initialize_database(
            api, db_path, schema_version, "zero-byte database initialized for first launch", finish, failure_message
        )

    conn = None
    try:
        conn = _readwrite_connection(db_path)
        application_id = conn.execute("PRAGMA application_id").fetchone()[0]
        if application_id == config.PRO_APPLICATION_ID and not accept_pro:
            _close(conn)
            return _prepared_result(
                api, "pro_stamped", None, "database carries the Pro application stamp",
                failure_message=failure_message,
            )
        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        if len(integrity) != 1 or integrity[0][0] != "ok":
            _close(conn)
            restored = launch_restore.restore_damaged(api, db_path, schema_version)
            return _prepared_result(
                api, restored.outcome, restored.conn, "database integrity check failed",
                restore_notice=restored.notice, finish=finish, failure_message=failure_message,
            )
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > schema_version:
            _close(conn)
            return _prepared_result(
                api, "newer", None, f"database schema {version} is newer than {schema_version}",
                failure_message=failure_message,
            )
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if not tables:
            _close(conn)
            conn = db.open_db(db_path, schema_version)
            restore_service.sweep_stale_staging(api._log)
            return _prepared_result(
                api, "ready", conn, "schema-empty database initialized for first launch",
                finish=finish, failure_message=failure_message,
            )
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            _close(conn)
            restored = launch_restore.restore_damaged(api, db_path, schema_version)
            return _prepared_result(
                api, restored.outcome, restored.conn, "database record link check failed",
                restore_notice=restored.notice, finish=finish, failure_message=failure_message,
            )
        try:
            core_needs_backup = db.has_user_records(conn)
        except Exception as error:
            # A failed check must not cost the user a backup.
            api._log(f"Launch backup record check failed ({describe_error(error)})")
            core_needs_backup = True
        if core_needs_backup is False and needs_backup is not None:
            try:
                core_needs_backup = needs_backup(conn)
            except Exception as error:  # noqa: BLE001 - a failed edition check must protect the backup.
                api._log(f"Launch backup edition record check failed ({describe_error(error)})")
                core_needs_backup = True
        if not core_needs_backup:
            # A backup of an empty logbook protects nothing and would push
            # real backups out of the rolling set. Not a failure: no notice,
            # and a schema upgrade goes ahead.
            api._log("Launch backup skipped: no records yet")
            notices = ()
        else:
            blocked, notices = _take_launch_backup(api, conn, db_path, schema_version, would_change)
            if blocked is not None:
                _close(conn)
                return _prepared_result(
                    api, "upgrade_blocked", None, blocked, failure_message=failure_message
                )
        conn.execute("PRAGMA foreign_keys = ON")
        db.ensure_schema(conn, schema_version)
        restore_service.sweep_stale_staging(api._log)
        return _prepared_result(
            api, "ready", conn, "database recovered, checked, and prepared", notices,
            finish=finish, failure_message=failure_message,
        )
    except sqlite3.DatabaseError as error:
        _close(conn)
        outcome, detail = _database_error_outcome(error)
        if outcome == "damaged":
            restored = launch_restore.restore_damaged(api, db_path, schema_version)
            return _prepared_result(
                api, restored.outcome, restored.conn, detail, restore_notice=restored.notice,
                finish=finish, failure_message=failure_message,
            )
        return _prepared_result(api, outcome, None, detail, failure_message=failure_message)
    except Exception as error:
        _close(conn)
        return _prepared_result(
            api, "unavailable", None, f"database could not be prepared ({describe_error(error)})",
            failure_message=failure_message,
        )


def _take_launch_backup(api, conn: sqlite3.Connection, db_path: str,
                       schema_version: int,
                       would_change: Callable[[sqlite3.Connection], bool] | None = None) -> tuple[str | None, tuple[str, ...]]:
    """Take the launch backup. Returns the failure detail when a failed
    backup must block the schema upgrade (else None), and the notices."""
    backup = launch_backup.create_launch_backup(conn, db_path, schema_version)
    if not backup.ok:
        api._log(f"Launch backup failed: {backup.detail}")
        schema_changes = db.schema_would_change(conn, schema_version)
        if schema_changes is False and would_change is not None:
            try:
                schema_changes = would_change(conn)
            except Exception as error:  # noqa: BLE001 - a failed edition check must block schema changes.
                api._log(f"Launch backup edition schema check failed ({describe_error(error)})")
                schema_changes = True
        if schema_changes:
            return backup.detail, ()
        notice = (
            f"Today's backup of your logbook couldn't be saved to {backup.folder}. "
            "Your logbook opened normally."
        )
        return None, (notice,)
    if backup.prune_failed:
        api._log(f"Launch backup prune failed: {backup.detail}")
        return None, (f"Older backups in {backup.folder} couldn't be cleaned up.",)
    api._log("Launch backup saved")
    return None, ()
