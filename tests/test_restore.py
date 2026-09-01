"""
Tests for sfl.services.restore: inspect_backup (unpack + validate a backup
zip) and restore_commit (the actual file swap). Backups used here are
produced by sfl.services.backup._write_backup_zip itself, so these tests
exercise the real round trip: write a backup, then read it back.

Covers a clean backup passing inspection, the two hard blocks (a manifest
format/schema version newer than this build understands, and a missing or
corrupt database), a soft warning for an altered photo (allowed, not
blocked), a full restore_commit round trip replacing one logbook with
another, and a rollback when the swap fails partway through.
"""

import json
import os
import shutil
import sqlite3
import zipfile

import sfl.services.restore as restore_mod
import simple_firearm_logbook as app
from sfl import config, paths
from sfl.services.backup import _write_backup_zip
from sfl.services.restore import inspect_backup, restore_commit


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


def _log_collector():
    messages = []
    return messages, messages.append


def _build_backup(tmp_path, monkeypatch, source_dir, dest_zip, log_numbers=(("00001", "Glock", "19"),)):
    """Builds a small logbook under source_dir and backs it up to dest_zip
    using the real _write_backup_zip. Returns (photo_filename, doc_filename)."""
    monkeypatch.setattr(paths, "app_dir", lambda: str(source_dir))
    conn = app.open_db(str(source_dir / config.DB_FILENAME))
    fid = None
    for log_number, make, model in log_numbers:
        fid = _insert_firearm(conn, log_number, make, model)
    photo_filename = f"{config.PHOTOS_DIRNAME}/00001_1.jpg"
    _insert_photo(conn, fid, photo_filename)
    _make_file(source_dir / photo_filename, b"photo-bytes")
    doc_filename = f"{config.ATTACHMENTS_DIRNAME}/00001_1.pdf"
    _insert_attachment(conn, fid, doc_filename, "receipt.pdf", size_bytes=9)
    _make_file(source_dir / doc_filename, b"doc-bytes")
    conn.commit()

    _, log = _log_collector()
    result = _write_backup_zip(conn, str(dest_zip), log)
    assert result["ok"], result
    conn.close()
    return photo_filename, doc_filename


def _load_manifest(zip_path):
    with zipfile.ZipFile(str(zip_path)) as zf:
        return json.loads(zf.read("manifest.json").decode("utf-8"))


def _rewrite_zip(zip_path, mutator):
    """Reads every entry out of zip_path, lets mutator(entries dict of
    name -> bytes) edit them in place, and rewrites the zip with the result."""
    with zipfile.ZipFile(str(zip_path)) as zf:
        entries = {name: zf.read(name) for name in zf.namelist()}
    mutator(entries)
    with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_clean_backup_inspects_ok_not_blocked(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    staging = tmp_path / "staging"
    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(staging), log)

    assert preview["ok"], preview
    assert preview["blocked"] is False
    assert preview["integrity_ok"] is True
    assert preview["integrity_warnings"] == []
    assert preview["incomplete"] is False
    assert preview["counts"] == {"firearms": 1, "photos": 1, "attachments": 1}
    assert preview["app_version"] == app.APP_VERSION
    assert preview["created_at"]


def test_newer_schema_version_blocks(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    def bump_schema(entries):
        manifest = json.loads(entries["manifest.json"].decode("utf-8"))
        manifest["schema_version"] = config.SCHEMA_VERSION + 1
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(dest_zip, bump_schema)

    staging = tmp_path / "staging"
    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(staging), log)

    assert preview["ok"], preview
    assert preview["blocked"] is True
    assert preview["block_reason"]


def test_newer_format_version_blocks(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    def bump_format(entries):
        manifest = json.loads(entries["manifest.json"].decode("utf-8"))
        manifest["format_version"] = 2
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(dest_zip, bump_format)

    staging = tmp_path / "staging"
    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(staging), log)

    assert preview["ok"], preview
    assert preview["blocked"] is True
    assert preview["block_reason"]


def test_missing_database_in_zip_hard_blocks(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    def drop_db(entries):
        del entries[config.DB_FILENAME]

    _rewrite_zip(dest_zip, drop_db)

    staging = tmp_path / "staging"
    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(staging), log)

    assert preview["ok"], preview
    assert preview["blocked"] is True
    assert preview["integrity_ok"] is False


def test_database_absent_and_unlisted_hard_blocks(tmp_path, monkeypatch):
    # A tampered backup: the db is dropped from the zip AND from the manifest
    # inventory, so the inventory loop never flags it. The restore must still
    # refuse, and inspect_backup must not conjure an empty db by connecting.
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    def strip_db(entries):
        del entries[config.DB_FILENAME]
        manifest = json.loads(entries["manifest.json"].decode("utf-8"))
        manifest["inventory"] = [
            item for item in manifest["inventory"] if item["path"] != config.DB_FILENAME
        ]
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(dest_zip, strip_db)

    staging = tmp_path / "staging"
    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(staging), log)

    assert preview["ok"], preview
    assert preview["blocked"] is True
    assert preview["integrity_ok"] is False
    assert not os.path.isfile(str(staging / config.DB_FILENAME))


def test_corrupt_database_with_matching_hash_hard_blocks(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    import hashlib

    def corrupt_db(entries):
        garbage = b"not a real sqlite database"
        entries[config.DB_FILENAME] = garbage
        manifest = json.loads(entries["manifest.json"].decode("utf-8"))
        for item in manifest["inventory"]:
            if item["path"] == config.DB_FILENAME:
                item["size"] = len(garbage)
                item["sha256"] = hashlib.sha256(garbage).hexdigest()
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(dest_zip, corrupt_db)

    staging = tmp_path / "staging"
    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(staging), log)

    assert preview["ok"], preview
    assert preview["blocked"] is True
    assert preview["integrity_ok"] is False


def test_altered_photo_is_soft_warning_not_blocked(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    photo_filename, _ = _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    def corrupt_photo(entries):
        entries[photo_filename] = b"different-bytes"

    _rewrite_zip(dest_zip, corrupt_photo)

    staging = tmp_path / "staging"
    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(staging), log)

    assert preview["ok"], preview
    assert preview["blocked"] is False
    assert preview["integrity_warnings"], preview


def test_restore_commit_round_trip_replaces_logbook(tmp_path, monkeypatch):
    # Backup logbook B under its own folder.
    source_dir = tmp_path / "source_b"
    source_dir.mkdir()
    dest_zip = tmp_path / "backup_b.zip"
    _build_backup(
        tmp_path, monkeypatch, source_dir, dest_zip,
        log_numbers=(("00001", "Sig", "P320"),),
    )

    # Live logbook A, a completely separate folder/dataset.
    live_dir = tmp_path / "live_a"
    live_dir.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: str(live_dir))
    conn_a = app.open_db(str(live_dir / config.DB_FILENAME))
    _insert_firearm(conn_a, "00001", "Ruger", "10/22")
    conn_a.commit()
    conn_a.close()

    staging = tmp_path / "staging"
    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(staging), log)
    assert preview["ok"] and not preview["blocked"], preview

    result = restore_commit(str(staging), log)
    assert result["ok"], result
    assert result["counts"]["firearms"] == 1

    restored = sqlite3.connect(str(live_dir / config.DB_FILENAME))
    try:
        rows = restored.execute("SELECT make, model FROM firearms").fetchall()
        assert rows == [("Sig", "P320")]
    finally:
        restored.close()

    assert (live_dir / config.PHOTOS_DIRNAME / "00001_1.jpg").exists()
    assert not staging.exists()


def test_restore_commit_rolls_back_on_failure(tmp_path, monkeypatch):
    live_dir = tmp_path / "live_a"
    live_dir.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: str(live_dir))
    conn_a = app.open_db(str(live_dir / config.DB_FILENAME))
    _insert_firearm(conn_a, "00001", "Ruger", "10/22")
    conn_a.commit()
    conn_a.close()

    staging = tmp_path / "staging"
    staging.mkdir()
    staged_db_path = staging / config.DB_FILENAME
    staged_conn = app.open_db(str(staged_db_path))
    _insert_firearm(staged_conn, "00001", "Sig", "P320")
    staged_conn.commit()
    staged_conn.close()

    orig_move = shutil.move

    def failing_move(src, dst, *a, **kw):
        if os.path.abspath(str(src)) == os.path.abspath(str(staged_db_path)):
            raise OSError("simulated failure moving staged db into place")
        return orig_move(src, dst, *a, **kw)

    monkeypatch.setattr(restore_mod.shutil, "move", failing_move)

    _, log = _log_collector()
    result = restore_commit(str(staging), log)

    assert result["ok"] is False
    assert "not changed" in result["error"]

    restored = sqlite3.connect(str(live_dir / config.DB_FILENAME))
    try:
        rows = restored.execute("SELECT make, model FROM firearms").fetchall()
        assert rows == [("Ruger", "10/22")]
    finally:
        restored.close()
