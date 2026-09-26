"""Restore: unpack a backup zip (see sfl.services.backup, which writes the
manifest.json and inventory this reads back) and replace the live database,
photos, and attachments with its contents. Mirrors the CSV import wizard's
three-stage shape: restore_pick runs the native Open dialog then hands off
to inspect_backup for the actual unpack and validation (no window
dependency, so tests call it directly), and restore_commit does the actual
file swap, moving the current logbook aside rather than deleting it so a
mid-way failure can always be rolled back."""
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile

import webview

from sfl import config, paths
from sfl.utils import sha256_hex


def restore_pick(window, log, schema_version=config.SCHEMA_VERSION):
    """Stage 1: native Open dialog restricted to .zip. Extraction and
    validation happen in inspect_backup; this just picks the file and hands
    off a fresh staging folder. Returns inspect_backup's preview dict, which
    carries the staging path the caller must hold until commit or cancel."""
    try:
        result = window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("ZIP Files (*.zip)",)
        )
        if not result:
            return {"ok": True, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path:
            return {"ok": True, "cancelled": True}
        staging_dir = tempfile.mkdtemp(prefix="sfl_restore_")
        outcome = inspect_backup(path, staging_dir, log, schema_version)
        if not outcome.get("ok"):
            _cleanup_path(staging_dir, log)
        return outcome
    except Exception as e:
        log(f"restore_pick failed: {e}")
        return {"ok": False, "error": "Couldn't read that backup file."}


def inspect_backup(zip_path, staging_dir, log, schema_version=config.SCHEMA_VERSION):
    """Stage 2: extracts zip_path into staging_dir (guarding against zip
    path traversal by resolving every member's real path before trusting
    it), reads manifest.json, and checks every inventory entry's hash. The
    database is a hard block if it's missing, altered, or won't open;
    photos and documents are a soft warning only, since the rest of the
    logbook is still worth restoring. No window dependency, so tests call
    this directly."""
    try:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.infolist():
                    if paths._safe_resolve_path(staging_dir, member.filename) is None:
                        return {"ok": False, "error": "That backup file looks corrupted or unsafe."}
                zf.extractall(staging_dir)
        except zipfile.BadZipFile:
            return {"ok": False, "error": "That doesn't look like a valid backup file."}
        except Exception as e:
            log(f"inspect_backup extraction failed: {e}")
            return {"ok": False, "error": "Couldn't open that backup file."}

        manifest_path = os.path.join(staging_dir, "manifest.json")
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as e:
            log(f"inspect_backup manifest read failed: {e}")
            return {"ok": False, "error": "That backup file is missing its manifest."}

        format_version = manifest.get("format_version", 0)
        backup_schema_version = manifest.get("schema_version", 0)
        counts = manifest.get("counts", {})
        missing = manifest.get("missing", [])
        inventory = manifest.get("inventory", [])
        created_at = manifest.get("created_at", "")
        app_version = manifest.get("app_version", "")

        newer_msg = "This backup was made by a newer version of the app. Update the app to restore it."
        blocked = False
        block_reason = ""
        if format_version > 1 or backup_schema_version > schema_version:
            blocked = True
            block_reason = newer_msg

        integrity_warnings = []
        integrity_ok = True
        db_arcname = config.DB_FILENAME

        for entry in inventory:
            entry_path = entry.get("path", "")
            full = os.path.join(staging_dir, entry_path.replace("/", os.sep))
            is_db = entry_path == db_arcname
            entry_ok = os.path.isfile(full)
            if entry_ok:
                with open(full, "rb") as fh:
                    actual_hash = sha256_hex(fh.read())
                entry_ok = actual_hash == entry.get("sha256")
            if not entry_ok:
                if is_db:
                    integrity_ok = False
                    if not blocked:
                        blocked = True
                        block_reason = "The backup's database file is missing or damaged. It can't be restored."
                else:
                    integrity_warnings.append(
                        f"{entry_path} is missing or has changed since the backup was made."
                    )
                    # Flagged non-database files must be truly skipped, not
                    # just warned about: if the staged copy exists but failed
                    # its check (missing or hash mismatch), remove it here so
                    # restore_commit's wholesale folder move can't bring a
                    # tampered file into the live logbook. A file entirely
                    # absent from the zip is already fine as-is.
                    if os.path.isfile(full):
                        try:
                            os.remove(full)
                        except Exception as e:
                            log(f"inspect_backup: couldn't remove flagged staged file {entry_path}: {e}")

        # A backup with no database is unusable and must never restore: guard
        # the file's existence explicitly, because sqlite3.connect below would
        # otherwise CREATE an empty database at that path and let a tampered
        # backup (one whose manifest never listed the db) replace the live
        # logbook with nothing.
        db_full = os.path.join(staging_dir, db_arcname)
        if not blocked and not os.path.isfile(db_full):
            integrity_ok = False
            blocked = True
            block_reason = "The backup's database file is missing or damaged. It can't be restored."

        # The database hash matched (or there was no hash to check against);
        # confirm it actually opens as SQLite and isn't stamped with a schema
        # this build doesn't understand, same policy as db.open_db.
        if not blocked and integrity_ok:
            if _sqlite_opens_cleanly(db_full, log, "inspect_backup"):
                try:
                    test_conn = sqlite3.connect(db_full)
                    try:
                        db_version = test_conn.execute("PRAGMA user_version").fetchone()[0]
                    finally:
                        test_conn.close()
                    if db_version > schema_version:
                        blocked = True
                        block_reason = newer_msg
                except sqlite3.DatabaseError as e:
                    log(f"inspect_backup: staged database won't open: {e}")
                    integrity_ok = False
                    blocked = True
                    block_reason = "The backup's database file is missing or damaged. It can't be restored."
            else:
                integrity_ok = False
                blocked = True
                block_reason = "The backup's database file is missing or damaged. It can't be restored."

        log(
            f"Restore preview: {zip_path}, blocked={blocked}, "
            f"integrity_warnings={len(integrity_warnings)}"
        )
        return {
            "ok": True,
            "staging": staging_dir,
            "blocked": blocked,
            "block_reason": block_reason,
            "created_at": created_at,
            "app_version": app_version,
            "counts": counts,
            "incomplete": bool(missing),
            "missing": missing,
            "integrity_ok": integrity_ok,
            "integrity_warnings": integrity_warnings,
        }
    except Exception as e:
        log(f"inspect_backup failed: {e}")
        return {"ok": False, "error": "Couldn't read that backup file."}


def restore_commit(staging_dir, log):
    """Stage 3: the actual replace. Moves the live db, photos\\, and
    attachments\\ aside (never deletes them until the staged files are
    fully in place), then moves the staged files into their spot. Any
    failure during the swap rolls every moved-aside original back, so the
    live logbook is never left half-replaced. Files only: the caller closes
    the sqlite connection before calling this and reopens it after."""
    staged_db = os.path.join(staging_dir, config.DB_FILENAME)
    not_changed = " Your logbook was not changed."
    if not os.path.isfile(staged_db):
        return {"ok": False, "error": "The staged backup is missing its database." + not_changed}
    if not _sqlite_opens_cleanly(staged_db, log, "restore_commit"):
        return {"ok": False, "error": "The staged backup's database is damaged." + not_changed}

    base = paths.app_dir()
    live_db = os.path.join(base, config.DB_FILENAME)
    live_photos = os.path.join(base, config.PHOTOS_DIRNAME)
    live_attachments = os.path.join(base, config.ATTACHMENTS_DIRNAME)
    staged_photos = os.path.join(staging_dir, config.PHOTOS_DIRNAME)
    staged_attachments = os.path.join(staging_dir, config.ATTACHMENTS_DIRNAME)

    aside_dir = tempfile.mkdtemp(prefix="sfl_restore_aside_")
    moved_aside = []  # (aside_path, original_path), in the order they were moved

    try:
        for original, name in (
            (live_db, config.DB_FILENAME),
            (live_photos, config.PHOTOS_DIRNAME),
            (live_attachments, config.ATTACHMENTS_DIRNAME),
        ):
            if os.path.exists(original):
                aside_path = os.path.join(aside_dir, name)
                shutil.move(original, aside_path)
                moved_aside.append((aside_path, original))
    except Exception as e:
        log(f"restore_commit: couldn't set the current logbook aside: {e}")
        _rollback(moved_aside, log)
        _cleanup_path(aside_dir, log)
        return {"ok": False, "error": "Couldn't replace the current logbook." + not_changed}

    try:
        shutil.move(staged_db, live_db)
        if os.path.isdir(staged_photos):
            shutil.move(staged_photos, live_photos)
        if os.path.isdir(staged_attachments):
            shutil.move(staged_attachments, live_attachments)
    except Exception as e:
        log(f"restore_commit: failed moving the backup into place, rolling back: {e}")
        for path in (live_db, live_photos, live_attachments):
            _cleanup_path(path, log)
        _rollback(moved_aside, log)
        _cleanup_path(aside_dir, log)
        return {"ok": False, "error": "Couldn't restore the backup." + not_changed}

    _cleanup_path(aside_dir, log)
    _cleanup_path(staging_dir, log)

    counts = {}
    try:
        conn = sqlite3.connect(live_db)
        try:
            counts = {
                "firearms": conn.execute("SELECT COUNT(*) FROM firearms").fetchone()[0],
                "photos": conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0],
                "attachments": conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0],
            }
        finally:
            conn.close()
    except Exception as e:
        log(f"restore_commit: couldn't read counts from the restored database: {e}")

    log(f"Restore committed: {counts}")
    return {"ok": True, "counts": counts}


def _sqlite_opens_cleanly(db_path, log, context):
    """Connects to db_path, runs a harmless read-only query to confirm it's
    actually an openable SQLite database, then closes. Returns True if it
    opened cleanly, False if sqlite3.DatabaseError was raised (logged via
    log, tagged with context so the caller's log line is identifiable)."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master")
        finally:
            conn.close()
        return True
    except sqlite3.DatabaseError as e:
        log(f"{context}: staged database won't open: {e}")
        return False


def _rollback(moved_aside, log):
    """Puts every moved-aside original back where it came from, in reverse
    order. Best-effort: a failure here is logged, never raised, since this
    already runs from inside another failure's handling."""
    for aside_path, original in reversed(moved_aside):
        try:
            _cleanup_path(original, log)
            shutil.move(aside_path, original)
        except Exception as e:
            log(f"restore_commit: rollback failed for {original}: {e}")


def _cleanup_path(path, log):
    """Best-effort delete of a file or directory. Never raises."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
    except Exception as e:
        log(f"restore: cleanup failed for {path}: {e}")
