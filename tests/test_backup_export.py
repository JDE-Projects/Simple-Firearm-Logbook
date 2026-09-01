"""
Tests for sfl.services.backup._write_backup_zip.

Backup is a complete, restorable archive: a safe SQLite-backup-API copy of
the database plus every photo and document file it references, with a
manifest.json inventory. Covers the clean case (everything present, sizes
and hashes correct, db copy openable), the missing-file case (recorded in
the manifest, zip still produced), and the manifest's format/schema version
fields.
"""

import hashlib
import json
import os
import sqlite3
import zipfile

import simple_firearm_logbook as app
from sfl import paths
from sfl.services.backup import _write_backup_zip


def _make_file(path, content=b"hello world"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return str(path)


def _insert_firearm(conn, log_number, make, model):
    conn.execute(
        "INSERT INTO firearms (log_number, make, model, created_at, updated_at) "
        "VALUES (?, ?, ?, datetime('now'), datetime('now'))",
        (log_number, make, model),
    )
    return conn.execute("SELECT id FROM firearms WHERE log_number=?", (log_number,)).fetchone()["id"]


def _insert_photo(conn, firearm_id, filename, seq=1, is_primary=1):
    conn.execute(
        "INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (?, ?, ?, ?)",
        (firearm_id, filename, seq, is_primary),
    )


def _insert_attachment(conn, firearm_id, filename, label, seq=1, size_bytes=0):
    conn.execute(
        "INSERT INTO attachments (firearm_id, filename, label, seq, size_bytes) VALUES (?, ?, ?, ?, ?)",
        (firearm_id, filename, label, seq, size_bytes),
    )


def _setup(tmp_path, monkeypatch):
    """Real db via open_db, app_dir routed into tmp_path, two firearms with
    a photo and an attachment created on disk at their stored paths."""
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    conn = app.open_db(str(tmp_path / "test.db"))

    fid1 = _insert_firearm(conn, "00001", "Glock", "19")
    fid2 = _insert_firearm(conn, "00002", "Sig", "P320")

    photo_filename = "photos/00001_1.jpg"
    _insert_photo(conn, fid1, photo_filename)
    photo_bytes = b"photo-bytes"
    _make_file(tmp_path / photo_filename, photo_bytes)

    doc_filename = "attachments/00002_1.pdf"
    _insert_attachment(conn, fid2, doc_filename, "receipt.pdf", size_bytes=len(b"doc-bytes"))
    doc_bytes = b"doc-bytes"
    _make_file(tmp_path / doc_filename, doc_bytes)

    conn.commit()
    return conn, photo_filename, doc_filename


def _log_collector():
    messages = []
    return messages, messages.append


def test_clean_backup_contains_manifest_db_and_files(tmp_path, monkeypatch):
    conn, photo_filename, doc_filename = _setup(tmp_path, monkeypatch)
    try:
        dest = tmp_path / "out" / "backup.zip"
        os.makedirs(dest.parent, exist_ok=True)
        _, log = _log_collector()

        result = _write_backup_zip(conn, str(dest), log)
        assert result["ok"], result
        assert result["missing"] == []
        assert result["counts"] == {"firearms": 2, "photos": 1, "attachments": 1}

        with zipfile.ZipFile(str(dest)) as zf:
            names = set(zf.namelist())
            assert "manifest.json" in names
            assert "simple_firearm_logbook.db" in names
            assert photo_filename in names
            assert doc_filename in names

            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert manifest["counts"] == {"firearms": 2, "photos": 1, "attachments": 1}
            assert manifest["missing"] == []

            inv_by_path = {e["path"]: e for e in manifest["inventory"]}
            db_bytes = zf.read("simple_firearm_logbook.db")
            assert inv_by_path["simple_firearm_logbook.db"]["size"] == len(db_bytes)
            assert inv_by_path["simple_firearm_logbook.db"]["sha256"] == hashlib.sha256(db_bytes).hexdigest()

            photo_bytes = zf.read(photo_filename)
            assert inv_by_path[photo_filename]["size"] == len(photo_bytes)
            assert inv_by_path[photo_filename]["sha256"] == hashlib.sha256(photo_bytes).hexdigest()

            # The db copy in the zip opens cleanly and has the same firearm rows.
            tmp_db = tmp_path / "restored.db"
            with open(tmp_db, "wb") as fh:
                fh.write(db_bytes)
            restored = sqlite3.connect(str(tmp_db))
            try:
                rows = restored.execute("SELECT make, model FROM firearms ORDER BY id").fetchall()
                assert rows == [("Glock", "19"), ("Sig", "P320")]
            finally:
                restored.close()
    finally:
        conn.close()


def test_missing_file_is_recorded_and_backup_still_produced(tmp_path, monkeypatch):
    conn, photo_filename, doc_filename = _setup(tmp_path, monkeypatch)
    try:
        os.remove(tmp_path / photo_filename)

        dest = tmp_path / "out" / "backup.zip"
        os.makedirs(dest.parent, exist_ok=True)
        messages, log = _log_collector()

        result = _write_backup_zip(conn, str(dest), log)
        assert result["ok"], result
        assert result["missing"] == [photo_filename]
        assert any(photo_filename in m for m in messages)

        with zipfile.ZipFile(str(dest)) as zf:
            names = set(zf.namelist())
            assert photo_filename not in names
            assert doc_filename in names
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            assert manifest["missing"] == [photo_filename]
    finally:
        conn.close()


def test_manifest_format_and_schema_version(tmp_path, monkeypatch):
    conn, photo_filename, doc_filename = _setup(tmp_path, monkeypatch)
    try:
        dest = tmp_path / "out" / "backup.zip"
        os.makedirs(dest.parent, exist_ok=True)
        _, log = _log_collector()

        result = _write_backup_zip(conn, str(dest), log)
        assert result["ok"], result

        with zipfile.ZipFile(str(dest)) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["format_version"] == 1
        assert manifest["schema_version"] == conn.execute("PRAGMA user_version").fetchone()[0]
        assert manifest["app_version"] == app.APP_VERSION
    finally:
        conn.close()
