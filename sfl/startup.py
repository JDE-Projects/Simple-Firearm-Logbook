"""Database preparation performed before the logbook window is created."""
import os
import sqlite3
from dataclasses import dataclass

from sfl import config, db
from sfl.services import launch_backup
from sfl.services import restore as restore_service


@dataclass(frozen=True)
class PreparedDatabase:
    """The ready connection or a reason launch must stop."""

    outcome: str
    conn: sqlite3.Connection | None
    detail: str
    notices: tuple[str, ...] = ()


def _readwrite_uri(path: str) -> str:
    """SQLite URI that opens an existing file read-write and never creates it.
    A network path (\\\\server\\share) gets an empty authority, file:////server,
    because SQLite drops a URI's authority and would lose the server name."""
    return launch_backup.database_uri(path, "rw")


def _readwrite_connection(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        _readwrite_uri(path),
        uri=True,
        timeout=5.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _result(api, outcome: str, conn: sqlite3.Connection | None, detail: str,
            notices: tuple[str, ...] = ()) -> PreparedDatabase:
    api.log(f"Launch database {outcome}: {detail}")
    return PreparedDatabase(outcome, conn, detail, notices)


def _close(conn: sqlite3.Connection | None) -> None:
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _database_error_outcome(error: sqlite3.DatabaseError) -> tuple[str, str]:
    code = getattr(error, "sqlite_errorcode", 0) or 0
    name = getattr(error, "sqlite_errorname", None) or type(error).__name__
    if code & 0xFF in (11, 26):
        return "damaged", f"database is corrupt or not SQLite ({name})"
    return "unavailable", f"database could not be opened ({name})"


def _initialize_database(api, db_path: str, schema_version: int, detail: str) -> PreparedDatabase:
    try:
        conn = db.open_db(db_path, schema_version)
        restore_service.sweep_stale_staging(api.log)
        return _result(api, "ready", conn, detail)
    except sqlite3.DatabaseError as error:
        outcome, error_detail = _database_error_outcome(error)
        return _result(api, outcome, None, error_detail)
    except Exception as error:
        return _result(api, "unavailable", None, f"database could not be prepared ({type(error).__name__})")


def prepare_database(api, db_path: str, schema_version: int) -> PreparedDatabase:
    """Recover, check, and prepare the live database for a launch."""
    if not os.path.exists(db_path):
        return _initialize_database(api, db_path, schema_version, "database created for first launch")

    try:
        with open(db_path, "rb") as file:
            header = file.read(8)
    except Exception as error:
        return _result(api, "unavailable", None, f"database header could not be read ({type(error).__name__})")
    if header == config.PRO_ENCRYPTED_MAGIC:
        return _result(api, "encrypted", None, "database is protected by Pro")
    if not header:
        return _initialize_database(
            api, db_path, schema_version, "zero-byte database initialized for first launch"
        )

    conn = None
    try:
        conn = _readwrite_connection(db_path)
        application_id = conn.execute("PRAGMA application_id").fetchone()[0]
        if application_id == config.PRO_APPLICATION_ID:
            _close(conn)
            return _result(api, "pro_stamped", None, "database carries the Pro application stamp")
        integrity = conn.execute("PRAGMA integrity_check").fetchall()
        if len(integrity) != 1 or integrity[0][0] != "ok":
            _close(conn)
            return _result(api, "damaged", None, "database integrity check failed")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > schema_version:
            _close(conn)
            return _result(api, "newer", None, f"database schema {version} is newer than {schema_version}")
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if not tables:
            _close(conn)
            conn = db.open_db(db_path, schema_version)
            restore_service.sweep_stale_staging(api.log)
            return _result(api, "ready", conn, "schema-empty database initialized for first launch")
        backup = launch_backup.create_launch_backup(conn, db_path, schema_version)
        if not backup.ok:
            api.log(f"Launch backup failed: {backup.detail}")
            if db.schema_would_change(conn, schema_version):
                _close(conn)
                return _result(api, "upgrade_blocked", None, backup.detail)
            notice = (
                f"Today's backup of your logbook couldn't be saved to {backup.folder}. "
                "Your logbook opened normally."
            )
            notices = (notice,)
        elif backup.prune_failed:
            api.log(f"Launch backup prune failed: {backup.detail}")
            notices = (f"Older backups in {backup.folder} couldn't be cleaned up.",)
        else:
            api.log(f"Launch backup saved: {backup.folder}")
            notices = ()
        conn.execute("PRAGMA foreign_keys = ON")
        db.ensure_schema(conn, schema_version)
        restore_service.sweep_stale_staging(api.log)
        return _result(api, "ready", conn, "database recovered, checked, and prepared", notices)
    except sqlite3.DatabaseError as error:
        _close(conn)
        outcome, detail = _database_error_outcome(error)
        return _result(api, outcome, None, detail)
    except Exception as error:
        _close(conn)
        return _result(api, "unavailable", None, f"database could not be prepared ({type(error).__name__})")
