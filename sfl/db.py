"""SQLite schema creation, additive migrations, and the small row/dict
helpers shared by every service that reads a firearms row."""
import sqlite3

from sfl.config import SCHEMA_VERSION


class NewerSchemaError(Exception):
    """Raised by open_db when the database's PRAGMA user_version is higher
    than this build's SCHEMA_VERSION. The database is never touched in this
    case; the caller should tell the user to update the app."""


class SaveRejected(Exception):
    """Raised by an embedding app to refuse a pending firearm save."""


def _ensure_column(conn, table: str, column: str, decl: str) -> None:
    """Add a column to an existing table only if it isn't already there.
    Additive-only: never rewrites or drops. Safe to call on every open."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def open_db(path: str, schema_version=SCHEMA_VERSION) -> sqlite3.Connection:
    """Open (creating if missing) the SQLite database and ensure the schema.
    Refuses to touch a database stamped with a schema newer than the caller
    supports; see NewerSchemaError."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    existing_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if existing_version > schema_version:
        conn.close()
        raise NewerSchemaError(
            f"Database schema {existing_version} is newer than this app supports ({schema_version})."
        )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS firearms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_number TEXT NOT NULL UNIQUE,
            make TEXT NOT NULL,
            model TEXT NOT NULL,
            serial_number TEXT NOT NULL DEFAULT '',
            firearm_type TEXT NOT NULL DEFAULT '',
            caliber TEXT NOT NULL DEFAULT '',
            acquisition_date TEXT NOT NULL DEFAULT '',
            acquired_from TEXT NOT NULL DEFAULT '',
            purchase_price TEXT NOT NULL DEFAULT '',
            estimated_value TEXT NOT NULL DEFAULT '',
            insured_value TEXT NOT NULL DEFAULT '',
            storage_location TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            sub_type TEXT NOT NULL DEFAULT '',
            held_in_trust INTEGER NOT NULL DEFAULT 0,
            trust_name TEXT NOT NULL DEFAULT '',
            is_nfa INTEGER NOT NULL DEFAULT 0,
            nfa_form_type TEXT NOT NULL DEFAULT '',
            nfa_stamp_date TEXT NOT NULL DEFAULT '',
            disposition_status TEXT NOT NULL DEFAULT 'Owned',
            disposition_date TEXT NOT NULL DEFAULT '',
            disposition_to TEXT NOT NULL DEFAULT '',
            disposition_address TEXT NOT NULL DEFAULT '',
            disposition_amount TEXT NOT NULL DEFAULT '',
            disposition_notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firearm_id INTEGER NOT NULL REFERENCES firearms(id),
            filename TEXT NOT NULL,
            seq INTEGER NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firearm_id INTEGER NOT NULL REFERENCES firearms(id),
            filename TEXT NOT NULL,
            label TEXT NOT NULL,
            seq INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS counters (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            next_log_number INTEGER NOT NULL
        );
        """
    )
    conn.execute("INSERT OR IGNORE INTO counters (id, next_log_number) VALUES (1, 1)")

    # Standing rule: migrations in this function must stay additive-only (new
    # tables/columns guarded by an existence check, never a destructive
    # rewrite), matching the rest of the JDE-Projects fleet. These columns are
    # backward compatible (an older build tolerates the extra columns), so they
    # do not bump SCHEMA_VERSION.
    _ensure_column(conn, "firearms", "insured_value", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "storage_location", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "sub_type", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "held_in_trust", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "firearms", "trust_name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "is_nfa", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "firearms", "nfa_form_type", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "nfa_stamp_date", "TEXT NOT NULL DEFAULT ''")

    if existing_version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def _get_next_log_number(cur) -> str:
    """Return the next log number, zero-padded to 5 digits, and advance the
    counter. Numbers are simple sequential. The counter bump and the insert
    share one transaction, so a rolled-back insert reuses the number."""
    row = cur.execute("SELECT next_log_number FROM counters WHERE id=1").fetchone()
    n = row["next_log_number"]
    cur.execute("UPDATE counters SET next_log_number=? WHERE id=1", (n + 1,))
    return f"{n:05d}"


def _firearm_row_to_dict(r) -> dict:
    return {
        "id": r["id"],
        "log_number": r["log_number"],
        "make": r["make"],
        "model": r["model"],
        "serial_number": r["serial_number"],
        "firearm_type": r["firearm_type"],
        "caliber": r["caliber"],
        "acquisition_date": r["acquisition_date"],
        "acquired_from": r["acquired_from"],
        "purchase_price": r["purchase_price"],
        "estimated_value": r["estimated_value"],
        "insured_value": r["insured_value"],
        "storage_location": r["storage_location"],
        "notes": r["notes"],
        "sub_type": r["sub_type"],
        "held_in_trust": r["held_in_trust"],
        "trust_name": r["trust_name"],
        "is_nfa": r["is_nfa"],
        "nfa_form_type": r["nfa_form_type"],
        "nfa_stamp_date": r["nfa_stamp_date"],
        "disposition_status": r["disposition_status"],
        "disposition_date": r["disposition_date"],
        "disposition_to": r["disposition_to"],
        "disposition_address": r["disposition_address"],
        "disposition_amount": r["disposition_amount"],
        "disposition_notes": r["disposition_notes"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }
