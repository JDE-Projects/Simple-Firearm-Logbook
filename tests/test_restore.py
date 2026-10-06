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
from pathlib import Path

import pytest

import sfl.services.restore as restore_mod
import simple_firearm_logbook as app
from sfl import config, paths
from sfl.services.backup import _write_backup_zip
from sfl.services.restore import inspect_backup, restore_commit
from sfl.utils import sha256_hex


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


@pytest.mark.parametrize("manifest_schema_version", [config.SCHEMA_VERSION, config.SCHEMA_VERSION + 1])
def test_pro_backup_newer_schema_has_pro_block_reason(tmp_path, monkeypatch, manifest_schema_version):
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    def stamp_pro(entries):
        db_path = tmp_path / "staged.db"
        db_path.write_bytes(entries[config.DB_FILENAME])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA user_version = 2")
        conn.execute(f"PRAGMA application_id = {config.PRO_APPLICATION_ID}")
        conn.commit()
        conn.close()
        entries[config.DB_FILENAME] = db_path.read_bytes()
        manifest = json.loads(entries["manifest.json"].decode("utf-8"))
        manifest["schema_version"] = manifest_schema_version
        for item in manifest["inventory"]:
            if item["path"] == config.DB_FILENAME:
                item["size"] = len(entries[config.DB_FILENAME])
                item["sha256"] = sha256_hex(entries[config.DB_FILENAME])
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(dest_zip, stamp_pro)

    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(tmp_path / "staging"), log)

    assert preview["blocked"] is True
    assert preview["block_reason"] == (
        "This backup was made by Simple Firearm Logbook Pro. Restore it in "
        "Simple Firearm Logbook Pro."
    )


def test_plain_newer_backup_keeps_existing_block_reason(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    def bump_schema(entries):
        db_path = tmp_path / "staged.db"
        db_path.write_bytes(entries[config.DB_FILENAME])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        conn.close()
        entries[config.DB_FILENAME] = db_path.read_bytes()
        manifest = json.loads(entries["manifest.json"].decode("utf-8"))
        for item in manifest["inventory"]:
            if item["path"] == config.DB_FILENAME:
                item["size"] = len(entries[config.DB_FILENAME])
                item["sha256"] = sha256_hex(entries[config.DB_FILENAME])
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(dest_zip, bump_schema)

    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(tmp_path / "staging"), log)

    assert preview["blocked"] is True
    assert preview["block_reason"] == (
        "This backup was made by a newer version of the app. Update the app to restore it."
    )


def test_schema_version_is_checked_against_the_supplied_version(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    def bump_schema(entries):
        manifest = json.loads(entries["manifest.json"].decode("utf-8"))
        manifest["schema_version"] = 2
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(dest_zip, bump_schema)
    _, log = _log_collector()
    blocked = inspect_backup(str(dest_zip), str(tmp_path / "blocked"), log, schema_version=1)
    accepted = inspect_backup(str(dest_zip), str(tmp_path / "accepted"), log, schema_version=2)
    assert blocked["blocked"] is True
    assert accepted["blocked"] is False


def test_api_uses_description_schema_version_and_opener(tmp_path, monkeypatch):
    api = app.Api()
    calls = {}

    class Description:
        schema_version = 7

        @staticmethod
        def open_database(path, schema_version):
            calls["opened"] = (path, schema_version)
            return app.open_db(path, schema_version)

    api.set_app_description(Description())
    def fake_pick(window, log, schema_version):
        calls["picked"] = schema_version
        return {"ok": True}

    monkeypatch.setattr(restore_mod, "restore_pick", fake_pick)
    assert api.restore_pick()["ok"]
    assert calls["picked"] == 7
    api._restore_staging = str(tmp_path / "staging")
    monkeypatch.setattr(restore_mod, "restore_commit", lambda staging, log: {"ok": True})
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    assert api.restore_commit(extension_data={"ignored": True})["ok"]
    assert calls["opened"] == (os.path.join(str(tmp_path), config.DB_FILENAME), 7)
    api.close_conn()


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


def test_tampered_photo_is_truly_skipped_not_restored(tmp_path, monkeypatch):
    # A photo whose bytes no longer match its manifest hash must not make it
    # into the restored logbook, even though the surrounding photos/ folder
    # is moved into place wholesale.
    source_dir = tmp_path / "source"
    dest_zip = tmp_path / "backup.zip"
    source_dir.mkdir()
    good_photo, _ = _build_backup(tmp_path, monkeypatch, source_dir, dest_zip)

    import hashlib

    def add_second_photo(entries):
        bad_photo = f"{config.PHOTOS_DIRNAME}/00001_2.jpg"
        entries[bad_photo] = b"original-bytes"
        manifest = json.loads(entries["manifest.json"].decode("utf-8"))
        manifest["inventory"].append({
            "path": bad_photo,
            "size": len(b"original-bytes"),
            "sha256": hashlib.sha256(b"original-bytes").hexdigest(),
        })
        entries["manifest.json"] = json.dumps(manifest).encode("utf-8")

    _rewrite_zip(dest_zip, add_second_photo)

    def tamper_second_photo(entries):
        entries[f"{config.PHOTOS_DIRNAME}/00001_2.jpg"] = b"tampered-bytes"

    _rewrite_zip(dest_zip, tamper_second_photo)

    live_dir = tmp_path / "live_a"
    live_dir.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: str(live_dir))

    staging = tmp_path / "staging"
    _, log = _log_collector()
    preview = inspect_backup(str(dest_zip), str(staging), log)
    assert preview["ok"] and not preview["blocked"], preview
    assert preview["integrity_warnings"], preview

    result = restore_commit(str(staging), log)
    assert result["ok"], result

    assert (live_dir / good_photo).exists()
    assert not (live_dir / config.PHOTOS_DIRNAME / "00001_2.jpg").exists()


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


def test_restore_commit_reports_and_reconnects_when_aside_folder_fails(tmp_path, monkeypatch):
    live_dir = tmp_path / "live_a"
    live_dir.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: str(live_dir))
    conn_a = app.open_db(str(live_dir / config.DB_FILENAME))
    _insert_firearm(conn_a, "00001", "Ruger", "10/22")
    conn_a.commit()
    conn_a.close()

    staging = tmp_path / "staging"
    staging.mkdir()
    staged_conn = app.open_db(str(staging / config.DB_FILENAME))
    _insert_firearm(staged_conn, "00001", "Sig", "P320")
    staged_conn.commit()
    staged_conn.close()

    def failing_mkdtemp(*a, **kw):
        raise PermissionError("simulated: folder creation denied")

    monkeypatch.setattr(restore_mod.tempfile, "mkdtemp", failing_mkdtemp)

    api = app.Api()
    api.set_conn(app.open_db(str(live_dir / config.DB_FILENAME)))
    api._restore_staging = str(staging)
    try:
        result = api.restore_commit()

        assert result["ok"] is False
        assert "not changed" in result["error"]
        rows = api._conn.execute("SELECT make, model FROM firearms").fetchall()
        assert [tuple(r) for r in rows] == [("Ruger", "10/22")]
        assert not staging.exists()
        assert api._restore_staging is None
    finally:
        api.close_conn()


def test_restore_commit_keeps_originals_when_rollback_fails_setting_aside(tmp_path, monkeypatch):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: str(live_dir))
    conn = app.open_db(str(live_dir / config.DB_FILENAME))
    _insert_firearm(conn, "00001", "Ruger", "10/22")
    conn.commit()
    conn.close()
    (live_dir / config.PHOTOS_DIRNAME).mkdir()
    original_photo = live_dir / config.PHOTOS_DIRNAME / "original.jpg"
    original_photo.write_bytes(b"original")

    staging = tmp_path / "staging"
    staging.mkdir()
    staged_conn = app.open_db(str(staging / config.DB_FILENAME))
    staged_conn.close()

    original_move = shutil.move

    def fail_setting_aside_and_move_back(src, dst, *args, **kwargs):
        if os.path.abspath(str(src)) == os.path.abspath(str(live_dir / config.PHOTOS_DIRNAME)):
            raise OSError("simulated setting-aside failure")
        if os.path.basename(str(src)) == config.DB_FILENAME and "sfl_restore_aside_" in str(src):
            raise OSError("simulated move-back failure")
        return original_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_mod.shutil, "move", fail_setting_aside_and_move_back)
    logs, log = _log_collector()
    result = restore_commit(str(staging), log)

    kept = result["kept_folder"]
    try:
        assert result["ok"] is False
        assert Path(kept).parent == live_dir
        assert kept in result["error"]
        assert "not changed" not in result["error"].lower()
        assert "could not be fully put back" in result["error"].lower()
        assert (Path(kept) / config.DB_FILENAME).is_file()
        assert original_photo.is_file()
        assert any(kept in message for message in logs)
    finally:
        shutil.rmtree(kept, ignore_errors=True)


def test_restore_commit_keeps_originals_when_rollback_fails_moving_backup_in(tmp_path, monkeypatch):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: str(live_dir))
    conn = app.open_db(str(live_dir / config.DB_FILENAME))
    _insert_firearm(conn, "00001", "Ruger", "10/22")
    conn.commit()
    conn.close()

    staging = tmp_path / "staging"
    staging.mkdir()
    staged_db = staging / config.DB_FILENAME
    staged_conn = app.open_db(str(staged_db))
    staged_conn.close()

    original_move = shutil.move

    def fail_backup_and_move_back(src, dst, *args, **kwargs):
        if os.path.abspath(str(src)) == os.path.abspath(str(staged_db)):
            raise OSError("simulated backup move failure")
        if os.path.basename(str(src)) == config.DB_FILENAME and "sfl_restore_aside_" in str(src):
            raise OSError("simulated move-back failure")
        return original_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_mod.shutil, "move", fail_backup_and_move_back)
    logs, log = _log_collector()
    result = restore_commit(str(staging), log)

    kept = result["kept_folder"]
    try:
        assert result["ok"] is False
        assert kept in result["error"]
        assert "not changed" not in result["error"].lower()
        assert "could not be fully put back" in result["error"].lower()
        assert (Path(kept) / config.DB_FILENAME).is_file()
        assert any(kept in message for message in logs)
    finally:
        shutil.rmtree(kept, ignore_errors=True)


def test_restore_commit_keeps_originals_when_a_partial_restore_cannot_be_cleared(tmp_path, monkeypatch):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: str(live_dir))
    conn = app.open_db(str(live_dir / config.DB_FILENAME))
    _insert_firearm(conn, "00001", "Ruger", "10/22")
    conn.commit()
    conn.close()
    (live_dir / config.PHOTOS_DIRNAME).mkdir()
    (live_dir / config.PHOTOS_DIRNAME / "original.jpg").write_bytes(b"original")

    staging = tmp_path / "staging"
    staging.mkdir()
    staged_conn = app.open_db(str(staging / config.DB_FILENAME))
    staged_conn.close()
    (staging / config.PHOTOS_DIRNAME).mkdir()
    (staging / config.PHOTOS_DIRNAME / "backup.jpg").write_bytes(b"backup")
    (staging / config.ATTACHMENTS_DIRNAME).mkdir()

    live_photos = os.path.abspath(str(live_dir / config.PHOTOS_DIRNAME))
    original_move = shutil.move
    original_cleanup = restore_mod._cleanup_path

    def fail_attachments_move(src, dst, *args, **kwargs):
        if os.path.basename(str(src)) == config.ATTACHMENTS_DIRNAME and "staging" in str(src):
            raise OSError("simulated backup move failure")
        return original_move(src, dst, *args, **kwargs)

    def keep_restored_photos(path, log):
        if os.path.abspath(str(path)) == live_photos:
            return  # simulates a locked folder that can't be removed
        original_cleanup(path, log)

    monkeypatch.setattr(restore_mod.shutil, "move", fail_attachments_move)
    monkeypatch.setattr(restore_mod, "_cleanup_path", keep_restored_photos)
    logs, log = _log_collector()
    result = restore_commit(str(staging), log)

    kept = result["kept_folder"]
    try:
        assert result["ok"] is False
        assert "not changed" not in result["error"].lower()
        assert (Path(kept) / config.PHOTOS_DIRNAME / "original.jpg").is_file()
        assert not (live_dir / config.PHOTOS_DIRNAME / config.PHOTOS_DIRNAME).exists()
    finally:
        shutil.rmtree(kept, ignore_errors=True)


def _make_staging(temp_dir, name, age_seconds, now):
    folder = temp_dir / name
    _make_file(folder / "logbook.db", b"private")
    os.utime(folder, (now - age_seconds, now - age_seconds))
    return folder


def test_sweep_removes_only_old_restore_folders(tmp_path):
    now = 1_000_000.0
    old = _make_staging(tmp_path, "sfl_restore_old", restore_mod.STALE_STAGING_SECONDS + 60, now)
    recent = _make_staging(tmp_path, "sfl_restore_recent", 60, now)
    other = _make_staging(tmp_path, "other_app_old", restore_mod.STALE_STAGING_SECONDS + 60, now)
    stray_file = tmp_path / "sfl_restore_file"
    stray_file.write_bytes(b"x")
    os.utime(stray_file, (0, 0))

    restore_mod.sweep_stale_staging(lambda msg: None, temp_dir=str(tmp_path), now=now)

    assert not old.exists()
    assert recent.exists()
    assert other.exists()
    assert stray_file.exists()


def test_sweep_logs_a_folder_it_cannot_delete(tmp_path, monkeypatch):
    now = 1_000_000.0
    _make_staging(tmp_path, "sfl_restore_locked", restore_mod.STALE_STAGING_SECONDS + 60, now)

    def fail(path):
        raise PermissionError("in use")

    monkeypatch.setattr(restore_mod.shutil, "rmtree", fail)
    logged = []

    restore_mod.sweep_stale_staging(logged.append, temp_dir=str(tmp_path), now=now)

    assert len(logged) == 1
    assert "sfl_restore_locked" in logged[0] and "in use" in logged[0]


def test_sweep_logs_when_temp_folder_cannot_be_read(tmp_path):
    logged = []

    restore_mod.sweep_stale_staging(logged.append, temp_dir=str(tmp_path / "missing"))

    assert len(logged) == 1
    assert "couldn't scan" in logged[0]


def test_close_restore_discards_an_open_preview(tmp_path):
    api = app.Api()
    staging = tmp_path / "sfl_restore_preview"
    _make_file(staging / "logbook.db", b"private")
    api._restore_staging = str(staging)

    api.close_restore()

    assert not staging.exists()
    assert api._restore_staging is None


def test_close_restore_leaves_a_running_restore_alone(tmp_path):
    api = app.Api()
    staging = tmp_path / "sfl_restore_running"
    _make_file(staging / "logbook.db", b"private")
    api._restore_staging = str(staging)
    api._restore_running = True

    api.close_restore()

    assert staging.exists()
    assert api._restore_staging == str(staging)


def test_restore_commit_clears_running_flag_even_when_it_raises(tmp_path, monkeypatch):
    api = app.Api()
    api._restore_staging = str(tmp_path / "staging")
    seen = []

    def boom(staging, log):
        seen.append(api._restore_running)
        raise RuntimeError("swap failed")

    monkeypatch.setattr(restore_mod, "restore_commit", boom)

    with pytest.raises(RuntimeError):
        api.restore_commit()

    assert seen == [True]
    assert api._restore_running is False


def test_restore_failing_after_window_closed_still_discards_staging(tmp_path, monkeypatch):
    api = app.Api()
    staging = tmp_path / "sfl_restore_closing"
    _make_file(staging / "logbook.db", b"private")
    api._restore_staging = str(staging)

    def close_then_fail(staging_dir, log):
        api.close_restore()  # the window closes while the restore runs
        assert os.path.isdir(staging_dir)
        return {"ok": False, "error": "Couldn't restore the backup."}

    monkeypatch.setattr(restore_mod, "restore_commit", close_then_fail)
    monkeypatch.setattr("sfl.db.open_db", lambda *a, **kw: None)

    assert api.restore_commit()["ok"] is False

    assert not staging.exists()
    assert api._restore_staging is None


def test_restore_cancel_logs_a_staging_folder_it_cannot_delete(tmp_path, monkeypatch):
    api = app.Api()
    staging = tmp_path / "sfl_restore_locked"
    _make_file(staging / "logbook.db", b"private")
    api._restore_staging = str(staging)
    logged = []
    monkeypatch.setattr(api, "log", logged.append)
    monkeypatch.setattr(restore_mod.shutil, "rmtree", lambda path: (_ for _ in ()).throw(PermissionError("in use")))

    assert api.restore_cancel() == {"ok": True}

    assert len(logged) == 1 and "in use" in logged[0]
    assert api._restore_staging is None
