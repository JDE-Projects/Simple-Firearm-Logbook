"""
Tests for open_db and NewerSchemaError in simple_firearm_logbook.py.

open_db is the only place the app's schema gets created or upgraded, and
its safety contract is that it must never touch a database stamped with a
schema version newer than this build understands (PRAGMA user_version):
it should raise NewerSchemaError and leave the file exactly as it found it,
rather than silently opening it and risking a downgrade-migration mismatch.
"""

import sqlite3

import pytest

import simple_firearm_logbook as app


# ─────────────────────────────────────────────────────────────
#  Fresh database: creation and schema setup
# ─────────────────────────────────────────────────────────────
def test_creates_file_and_all_expected_tables(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    conn = app.open_db(db_path)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"firearms", "photos", "counters"} <= tables
    finally:
        conn.close()


def test_sets_user_version_to_current_schema_version(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    conn = app.open_db(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == app.SCHEMA_VERSION
    finally:
        conn.close()


def test_seeds_counters_row_with_next_log_number_one(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    conn = app.open_db(db_path)
    try:
        row = conn.execute("SELECT next_log_number FROM counters WHERE id=1").fetchone()
        assert row["next_log_number"] == 1
    finally:
        conn.close()


def test_row_factory_allows_column_name_access(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    conn = app.open_db(db_path)
    try:
        row = conn.execute("SELECT * FROM counters WHERE id=1").fetchone()
        assert row["next_log_number"] == 1
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
#  Reopening an existing, same-version database
# ─────────────────────────────────────────────────────────────
def test_reopening_existing_database_does_not_raise_or_reset_counter(tmp_path):
    db_path = str(tmp_path / "existing.db")
    conn = app.open_db(db_path)
    conn.execute("UPDATE counters SET next_log_number=42 WHERE id=1")
    conn.commit()
    conn.close()

    conn2 = app.open_db(db_path)
    try:
        row = conn2.execute("SELECT next_log_number FROM counters WHERE id=1").fetchone()
        assert row["next_log_number"] == 42
    finally:
        conn2.close()


def test_older_schema_version_is_upgraded_without_raising(tmp_path):
    db_path = str(tmp_path / "older.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    conn2 = app.open_db(db_path)
    try:
        version = conn2.execute("PRAGMA user_version").fetchone()[0]
        assert version == app.SCHEMA_VERSION
    finally:
        conn2.close()


# ─────────────────────────────────────────────────────────────
#  Newer-than-supported schema: must raise, not silently open
# ─────────────────────────────────────────────────────────────
def test_newer_schema_version_raises_newer_schema_error(tmp_path):
    db_path = str(tmp_path / "newer.db")
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA user_version = {app.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    with pytest.raises(app.NewerSchemaError):
        app.open_db(db_path)


def test_newer_schema_database_is_left_untouched(tmp_path):
    db_path = str(tmp_path / "newer.db")
    newer_version = app.SCHEMA_VERSION + 1
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA user_version = {newer_version}")
    conn.commit()
    conn.close()

    with pytest.raises(app.NewerSchemaError):
        app.open_db(db_path)

    # No tables should have been created, and the version stamp must be
    # exactly what it was before open_db touched it.
    check = sqlite3.connect(db_path)
    try:
        version = check.execute("PRAGMA user_version").fetchone()[0]
        assert version == newer_version
        tables = {
            r[0]
            for r in check.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert tables == set()
    finally:
        check.close()
