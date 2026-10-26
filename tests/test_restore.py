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
import sqlite3
import zipfile

import pytest

import sfl.services.restore as restore_mod
import simple_firearm_logbook as app
from sfl import config, paths, platform_win
from sfl.services.backup import _write_backup_zip
from sfl.services import zip_restore_recovery
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


def test_api_recovery_required_skips_reopen_and_survives_missing_or_broken_window(tmp_path, monkeypatch):
    api = app.Api()
    api._restore_staging = str(tmp_path / ".sfl_zip_restore_stage-a1b2c3d4")
    api._window = None
    shown = []
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(restore_mod, "restore_commit", lambda staging, log: {"ok": False, "recovery_required": True})
    monkeypatch.setattr(platform_win, "_show_zip_restore_recovery_required_error", lambda *args: shown.append(args))
    monkeypatch.setattr(restore_mod, "discard_staging", lambda *args: None)
    assert api.restore_commit()["recovery_required"] is True
    assert shown == [(str(tmp_path), "Simple Firearm Logbook")]

    class BrokenWindow:
        def destroy(self):
            raise RuntimeError("closed already")

    api._restore_staging = str(tmp_path / ".sfl_zip_restore_stage-a1b2c3d4")
    api._window = BrokenWindow()
    assert api.restore_commit()["recovery_required"] is True


def test_restore_commit_returns_success_when_swapped_cleanup_is_pending(tmp_path, monkeypatch):
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: str(live))
    staging = live / ".sfl_zip_restore_stage-a1b2c3d4"
    staging.mkdir()
    _database = app.open_db
    old = _database(str(live / config.DB_FILENAME))
    _insert_firearm(old, "00001", "Old", "One")
    old.commit()
    old.close()
    new = _database(str(staging / config.DB_FILENAME))
    _insert_firearm(new, "00001", "New", "One")
    new.commit()
    new.close()
    monkeypatch.setattr(zip_restore_recovery, "complete_swapped", lambda *args: "cleanup_pending")
    result = restore_commit(str(staging), lambda _: None)
    assert result["ok"] is True
    assert result["counts"]["firearms"] == 1
    assert os.path.exists(zip_restore_recovery.record_path(str(live)))


def _crash_restore_tree(folder, monkeypatch, *, live_parts=("photos", "attachments"), staged_parts=("photos", "attachments"), journal=False):
    monkeypatch.setattr(paths, "app_dir", lambda: str(folder))
    staging = folder / ".sfl_zip_restore_stage-a1b2c3d4"
    staging.mkdir()
    for root, make in ((folder, "old"), (staging, "new")):
        conn = app.open_db(str(root / config.DB_FILENAME))
        _insert_firearm(conn, "00001", make, "model")
        conn.commit()
        conn.close()
    for name, filename, contents in (("photos", "photo.jpg", b"photo"), ("attachments", "doc.pdf", b"document")):
        dirname = config.PHOTOS_DIRNAME if name == "photos" else config.ATTACHMENTS_DIRNAME
        if name in live_parts:
            _make_file(folder / dirname / filename, b"old-" + contents)
        if name in staged_parts:
            _make_file(staging / dirname / filename, b"new-" + contents)
    if journal:
        (folder / (config.DB_FILENAME + "-journal")).write_bytes(b"old-journal")
    return staging


def _crash_restore_contents(folder):
    conn = sqlite3.connect(folder / config.DB_FILENAME)
    try:
        make = conn.execute("SELECT make FROM firearms").fetchone()[0]
    finally:
        conn.close()
    return (
        make,
        (folder / config.PHOTOS_DIRNAME / "photo.jpg").read_bytes(),
        (folder / config.ATTACHMENTS_DIRNAME / "doc.pdf").read_bytes(),
    )


@pytest.mark.parametrize("move_number", range(1, 7))
def test_commit_crash_after_each_move_leaves_old_or_new_set(tmp_path, monkeypatch, move_number):
    staging = _crash_restore_tree(tmp_path, monkeypatch)
    original_replace = os.replace
    moves = 0

    def crash_after_move(source, destination, *args, **kwargs):
        nonlocal moves
        result = original_replace(source, destination, *args, **kwargs)
        if os.path.basename(str(source)) in {config.DB_FILENAME, config.PHOTOS_DIRNAME, config.ATTACHMENTS_DIRNAME}:
            moves += 1
            if moves == move_number:
                raise KeyboardInterrupt
        return result

    monkeypatch.setattr(restore_mod.os, "replace", crash_after_move)
    with pytest.raises(KeyboardInterrupt):
        restore_commit(str(staging), lambda _: None)
    monkeypatch.setattr(restore_mod.os, "replace", original_replace)

    assert zip_restore_recovery.recover(str(tmp_path), lambda _: None) == "undone"
    assert _crash_restore_contents(tmp_path) == ("old", b"old-photo", b"old-document")
    assert not staging.exists()
    assert not list(tmp_path.glob(".sfl_zip_restore_aside-*"))
    assert not os.path.exists(zip_restore_recovery.record_path(str(tmp_path)))


def test_commit_crash_after_swapped_record_leaves_new_set(tmp_path, monkeypatch):
    staging = _crash_restore_tree(tmp_path, monkeypatch)
    original_write_record = zip_restore_recovery.write_record

    def crash_after_swapped(folder, record):
        original_write_record(folder, record)
        if record["stage"] == "swapped":
            raise KeyboardInterrupt

    monkeypatch.setattr(zip_restore_recovery, "write_record", crash_after_swapped)
    with pytest.raises(KeyboardInterrupt):
        restore_commit(str(staging), lambda _: None)
    monkeypatch.setattr(zip_restore_recovery, "write_record", original_write_record)

    assert zip_restore_recovery.recover(str(tmp_path), lambda _: None) == "finished"
    assert _crash_restore_contents(tmp_path) == ("new", b"new-photo", b"new-document")
    assert not staging.exists()
    assert not list(tmp_path.glob(".sfl_zip_restore_aside-*"))
    assert not os.path.exists(zip_restore_recovery.record_path(str(tmp_path)))


@pytest.mark.parametrize("after_write", [False, True])
def test_commit_crash_while_writing_prepared_record(tmp_path, monkeypatch, after_write):
    staging = _crash_restore_tree(tmp_path, monkeypatch)
    original_replace = os.replace
    record = zip_restore_recovery.record_path(str(tmp_path))

    def crash_writing_record(source, destination, *args, **kwargs):
        if str(destination) == record and not after_write:
            raise KeyboardInterrupt
        result = original_replace(source, destination, *args, **kwargs)
        if str(destination) == record and after_write:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(restore_mod.os, "replace", crash_writing_record)
    with pytest.raises(KeyboardInterrupt):
        restore_commit(str(staging), lambda _: None)
    monkeypatch.setattr(restore_mod.os, "replace", original_replace)

    expected = "undone" if after_write else "none"
    assert zip_restore_recovery.recover(str(tmp_path), lambda _: None) == expected
    assert _crash_restore_contents(tmp_path) == ("old", b"old-photo", b"old-document")


def test_backup_adds_a_part_the_logbook_lacked(tmp_path, monkeypatch):
    staging = _crash_restore_tree(tmp_path, monkeypatch, live_parts=("photos",))
    original_replace = os.replace

    def crash_after_attachments_move(source, destination, *args, **kwargs):
        result = original_replace(source, destination, *args, **kwargs)
        if os.path.basename(str(source)) == config.ATTACHMENTS_DIRNAME and str(source).startswith(str(staging)):
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(restore_mod.os, "replace", crash_after_attachments_move)
    with pytest.raises(KeyboardInterrupt):
        restore_commit(str(staging), lambda _: None)
    monkeypatch.setattr(restore_mod.os, "replace", original_replace)

    original_delete = zip_restore_recovery._delete

    def check_rollback_before_staging_cleanup(path):
        if str(path) == str(staging) and (staging / config.ATTACHMENTS_DIRNAME).exists():
            assert zip_restore_recovery.read_record(str(tmp_path))["stage"] == "rolled_back"
        return original_delete(path)

    monkeypatch.setattr(zip_restore_recovery, "_delete", check_rollback_before_staging_cleanup)
    assert zip_restore_recovery.recover(str(tmp_path), lambda _: None) == "undone"
    assert not (tmp_path / config.ATTACHMENTS_DIRNAME).exists()
    assert not staging.exists()


def test_backup_lacks_a_part_the_logbook_had(tmp_path, monkeypatch):
    complete = tmp_path / "complete"
    complete.mkdir()
    staging = _crash_restore_tree(complete, monkeypatch, staged_parts=("attachments",))
    assert restore_commit(str(staging), lambda _: None)["ok"]
    assert not (complete / config.PHOTOS_DIRNAME).exists()

    interrupted = tmp_path / "interrupted"
    interrupted.mkdir()
    staging = _crash_restore_tree(interrupted, monkeypatch, staged_parts=("attachments",))
    original_replace = os.replace

    def crash_after_live_photos_aside(source, destination, *args, **kwargs):
        result = original_replace(source, destination, *args, **kwargs)
        if os.path.basename(str(source)) == config.PHOTOS_DIRNAME and "sfl_zip_restore_aside-" in str(destination):
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(restore_mod.os, "replace", crash_after_live_photos_aside)
    with pytest.raises(KeyboardInterrupt):
        restore_commit(str(staging), lambda _: None)
    monkeypatch.setattr(restore_mod.os, "replace", original_replace)
    assert zip_restore_recovery.recover(str(interrupted), lambda _: None) == "undone"
    assert (interrupted / config.PHOTOS_DIRNAME / "photo.jpg").read_bytes() == b"old-photo"


def test_leftover_journal_moves_with_the_database(tmp_path, monkeypatch):
    complete = tmp_path / "complete"
    complete.mkdir()
    staging = _crash_restore_tree(complete, monkeypatch, journal=True)
    assert restore_commit(str(staging), lambda _: None)["ok"]
    assert not (complete / (config.DB_FILENAME + "-journal")).exists()

    interrupted = tmp_path / "interrupted"
    interrupted.mkdir()
    staging = _crash_restore_tree(interrupted, monkeypatch, journal=True)
    original_replace = os.replace

    def crash_after_journal_aside(source, destination, *args, **kwargs):
        result = original_replace(source, destination, *args, **kwargs)
        if os.path.basename(str(source)) == config.DB_FILENAME + "-journal":
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(restore_mod.os, "replace", crash_after_journal_aside)
    with pytest.raises(KeyboardInterrupt):
        restore_commit(str(staging), lambda _: None)
    monkeypatch.setattr(restore_mod.os, "replace", original_replace)
    assert zip_restore_recovery.recover(str(interrupted), lambda _: None) == "undone"
    assert (interrupted / (config.DB_FILENAME + "-journal")).read_bytes() == b"old-journal"


def test_commit_undo_with_failed_cleanup_reports_unchanged(tmp_path, monkeypatch):
    staging = _crash_restore_tree(tmp_path, monkeypatch)
    original_replace = os.replace

    def fail_database_move_in(source, destination, *args, **kwargs):
        if str(source) == str(staging / config.DB_FILENAME):
            raise OSError("simulated move-in failure")
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(restore_mod.os, "replace", fail_database_move_in)
    monkeypatch.setattr(zip_restore_recovery, "_delete", lambda path: False)
    result = restore_commit(str(staging), lambda _: None)
    monkeypatch.undo()

    assert result.get("recovery_required") is not True
    assert "not changed" in result["error"]
    assert _crash_restore_contents(tmp_path) == ("old", b"old-photo", b"old-document")
    assert zip_restore_recovery.read_record(str(tmp_path))["stage"] == "rolled_back"
    assert zip_restore_recovery.recover(str(tmp_path), lambda _: None) == "undone"
    assert not staging.exists()


def test_api_finally_keeps_a_recorded_staging_folder(tmp_path, monkeypatch):
    staging = tmp_path / ".sfl_zip_restore_stage-a1b2c3d4"
    staging.mkdir()
    aside = tmp_path / ".sfl_zip_restore_aside-a1b2c3d4"
    aside.mkdir()
    record = zip_restore_recovery.make_record(str(tmp_path), str(staging))
    zip_restore_recovery.write_record(str(tmp_path), record)
    api = app.Api()
    api._restore_staging = str(staging)
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    monkeypatch.setattr(restore_mod, "restore_commit", lambda staging, log: {"ok": False, "recovery_required": True})
    monkeypatch.setattr(platform_win, "_show_zip_restore_recovery_required_error", lambda *args: None)
    assert api.restore_commit()["recovery_required"] is True
    assert staging.exists()

    preview = tmp_path / ".sfl_zip_restore_stage-b1c2d3e4"
    preview.mkdir()
    api._restore_staging = str(preview)
    assert api.restore_cancel() == {"ok": True}
    assert not preview.exists()


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

    staging = live_dir / ".sfl_zip_restore_stage-a1b2c3d4"
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

    staging = live_dir / ".sfl_zip_restore_stage-a1b2c3d4"
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

    staging = live_dir / ".sfl_zip_restore_stage-a1b2c3d4"
    staging.mkdir()
    staged_db_path = staging / config.DB_FILENAME
    staged_conn = app.open_db(str(staged_db_path))
    _insert_firearm(staged_conn, "00001", "Sig", "P320")
    staged_conn.commit()
    staged_conn.close()

    orig_replace = os.replace

    def failing_replace(src, dst, *a, **kw):
        if os.path.abspath(str(src)) == os.path.abspath(str(staged_db_path)):
            raise OSError("simulated failure moving staged db into place")
        return orig_replace(src, dst, *a, **kw)

    monkeypatch.setattr(restore_mod.os, "replace", failing_replace)

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

    staging = live_dir / ".sfl_zip_restore_stage-a1b2c3d4"
    staging.mkdir()
    staged_conn = app.open_db(str(staging / config.DB_FILENAME))
    _insert_firearm(staged_conn, "00001", "Sig", "P320")
    staged_conn.commit()
    staged_conn.close()

    original_mkdir = os.mkdir

    def failing_mkdir(path, *args, **kwargs):
        if "sfl_zip_restore_aside-" in str(path):
            raise PermissionError("simulated: folder creation denied")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(restore_mod.os, "mkdir", failing_mkdir)

    result = restore_commit(str(staging), lambda _: None)

    assert result["ok"] is False
    assert "not changed" in result["error"]
    assert (live_dir / config.DB_FILENAME).is_file()


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

    staging = live_dir / ".sfl_zip_restore_stage-a1b2c3d4"
    staging.mkdir()
    staged_conn = app.open_db(str(staging / config.DB_FILENAME))
    staged_conn.close()

    original_replace = os.replace

    def fail_setting_aside_and_move_back(src, dst, *args, **kwargs):
        if os.path.abspath(str(src)) == os.path.abspath(str(live_dir / config.PHOTOS_DIRNAME)):
            raise OSError("simulated setting-aside failure")
        if os.path.basename(str(src)) == config.DB_FILENAME and "sfl_zip_restore_aside-" in str(src):
            raise OSError("simulated move-back failure")
        return original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_mod.os, "replace", fail_setting_aside_and_move_back)
    logs, log = _log_collector()
    result = restore_commit(str(staging), log)

    assert result["ok"] is False
    assert result["recovery_required"] is True
    assert original_photo.read_bytes() == b"original"
    assert (staging / config.DB_FILENAME).is_file()
    assert (live_dir / ".sfl_zip_restore_aside-a1b2c3d4" / config.DB_FILENAME).is_file()
    assert os.path.exists(live_dir / "simple_firearm_logbook.zip_restore.json")


def test_restore_commit_keeps_originals_when_rollback_fails_moving_backup_in(tmp_path, monkeypatch):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    monkeypatch.setattr(paths, "app_dir", lambda: str(live_dir))
    conn = app.open_db(str(live_dir / config.DB_FILENAME))
    _insert_firearm(conn, "00001", "Ruger", "10/22")
    conn.commit()
    conn.close()

    staging = live_dir / ".sfl_zip_restore_stage-a1b2c3d4"
    staging.mkdir()
    staged_db = staging / config.DB_FILENAME
    staged_conn = app.open_db(str(staged_db))
    staged_conn.close()

    original_replace = os.replace

    def fail_backup_and_move_back(src, dst, *args, **kwargs):
        if os.path.abspath(str(src)) == os.path.abspath(str(staged_db)):
            raise OSError("simulated backup move failure")
        if os.path.basename(str(src)) == config.DB_FILENAME and "sfl_zip_restore_aside-" in str(src):
            raise OSError("simulated move-back failure")
        return original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_mod.os, "replace", fail_backup_and_move_back)
    logs, log = _log_collector()
    result = restore_commit(str(staging), log)

    assert result["ok"] is False
    assert result["recovery_required"] is True
    assert staged_db.is_file()
    assert (live_dir / ".sfl_zip_restore_aside-a1b2c3d4" / config.DB_FILENAME).is_file()
    assert os.path.exists(live_dir / "simple_firearm_logbook.zip_restore.json")


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

    staging = live_dir / ".sfl_zip_restore_stage-a1b2c3d4"
    staging.mkdir()
    staged_conn = app.open_db(str(staging / config.DB_FILENAME))
    staged_conn.close()
    (staging / config.PHOTOS_DIRNAME).mkdir()
    (staging / config.PHOTOS_DIRNAME / "backup.jpg").write_bytes(b"backup")
    (staging / config.ATTACHMENTS_DIRNAME).mkdir()

    original_replace = os.replace

    def fail_attachments_move(src, dst, *args, **kwargs):
        if os.path.basename(str(src)) == config.ATTACHMENTS_DIRNAME and "sfl_zip_restore_stage-" in str(src):
            raise OSError("simulated backup move failure")
        if os.path.basename(str(src)) == config.PHOTOS_DIRNAME and "sfl_zip_restore_aside-" in str(src):
            raise OSError("simulated partial restore rollback failure")
        return original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(restore_mod.os, "replace", fail_attachments_move)
    logs, log = _log_collector()
    result = restore_commit(str(staging), log)

    assert result["ok"] is False
    assert result["recovery_required"] is True
    assert (staging / config.PHOTOS_DIRNAME / "backup.jpg").read_bytes() == b"backup"
    assert (live_dir / ".sfl_zip_restore_aside-a1b2c3d4" / config.PHOTOS_DIRNAME / "original.jpg").read_bytes() == b"original"
    assert (staging / config.ATTACHMENTS_DIRNAME).is_dir()


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
    assert "cleanup failed (PermissionError)" in logged[0]


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
    monkeypatch.setattr(api, "_log", logged.append)
    monkeypatch.setattr(restore_mod.shutil, "rmtree", lambda path: (_ for _ in ()).throw(PermissionError("in use")))

    assert api.restore_cancel() == {"ok": True}

    assert len(logged) == 1 and "cleanup failed (PermissionError)" in logged[0]
    assert api._restore_staging is None
