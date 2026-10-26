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
import uuid
import time
import zipfile

import webview

from sfl import config, paths
from sfl import storage
from sfl.services import zip_restore_recovery
from sfl.utils import sha256_hex

STAGING_PREFIX = "sfl_restore_"
# A staging folder touched more recently than this is left alone by the
# launch sweep: the free and Pro editions share the prefix, so another
# running copy of the app may be showing a restore preview from it.
STALE_STAGING_SECONDS = 60 * 60


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
        staging_dir = os.path.join(paths.app_dir(), f".sfl_zip_restore_stage-{uuid.uuid4().hex[:8]}")
        os.mkdir(staging_dir)
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
            with storage.current().open_backup_reader(zip_path) as reader, zipfile.ZipFile(reader) as zf:
                members = []
                for member in zf.infolist():
                    full = paths._safe_resolve_path(staging_dir, member.filename)
                    if full is None:
                        return {"ok": False, "error": "That backup file looks corrupted or unsafe."}
                    members.append((member, full))
                for member, full in members:
                    if member.is_dir():
                        os.makedirs(full, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(full), exist_ok=True)
                        storage.current().write_bytes(full, zf.read(member))
        except zipfile.BadZipFile:
            return {"ok": False, "error": "That doesn't look like a valid backup file."}
        except Exception as e:
            log(f"inspect_backup extraction failed: {e}")
            return {"ok": False, "error": "Couldn't open that backup file."}

        manifest_path = os.path.join(staging_dir, "manifest.json")
        try:
            manifest = json.loads(storage.current().read_bytes(manifest_path).decode("utf-8"))
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
        newer_schema_blocked = backup_schema_version > schema_version
        blocked = False
        block_reason = ""
        if format_version > 1 or newer_schema_blocked:
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
                actual_hash = sha256_hex(storage.current().read_bytes(full))
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
                            storage.current().remove(full)
                        except Exception as e:
                            log(f"inspect_backup: couldn't remove flagged staged file {entry_path}: {e}")

        # A backup with no database is unusable and must never restore: guard
        # the file's existence explicitly, because opening it below would
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
        pro_database = False
        if integrity_ok and os.path.isfile(db_full):
            if _sqlite_opens_cleanly(db_full, log, "inspect_backup"):
                try:
                    test_conn = storage.current().connect_file(db_full)
                    try:
                        db_version = test_conn.execute("PRAGMA user_version").fetchone()[0]
                        pro_database = (
                            test_conn.execute("PRAGMA application_id").fetchone()[0]
                            == config.PRO_APPLICATION_ID
                        )
                    finally:
                        test_conn.close()
                    if db_version > schema_version:
                        newer_schema_blocked = True
                        blocked = True
                        block_reason = newer_msg
                except sqlite3.DatabaseError as e:
                    log(f"inspect_backup: staged database won't open: {e}")
                    integrity_ok = False
                    if not blocked:
                        blocked = True
                        block_reason = "The backup's database file is missing or damaged. It can't be restored."
            else:
                integrity_ok = False
                if not blocked:
                    blocked = True
                    block_reason = "The backup's database file is missing or damaged. It can't be restored."

        if newer_schema_blocked and pro_database:
            block_reason = (
                "This backup was made by Simple Firearm Logbook Pro. Restore it in "
                "Simple Firearm Logbook Pro."
            )

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
    try:
        record = zip_restore_recovery.make_record(base, staging_dir)
        for _, filename, kind in zip_restore_recovery.PARTS:
            staged = os.path.join(staging_dir, filename)
            if kind == "file" and os.path.isfile(staged):
                zip_restore_recovery._fsync_file(staged)
        aside_dir = os.path.join(base, record["aside_name"])
        os.mkdir(aside_dir)
        zip_restore_recovery.write_record(base, record)
        for name, filename, _kind in zip_restore_recovery.PARTS:
            live = os.path.join(base, filename)
            if record["parts"][name]["live"]:
                os.replace(live, os.path.join(aside_dir, filename))
        for name, filename, _kind in zip_restore_recovery.PARTS:
            staged = os.path.join(staging_dir, filename)
            if record["parts"][name]["staged"]:
                os.replace(staged, os.path.join(base, filename))
        record = {**record, "stage": "swapped"}
        zip_restore_recovery.write_record(base, record)
        completion = zip_restore_recovery.complete_swapped(base, record)
        if completion == "mismatch":
            return {"ok": False, "recovery_required": True, "error": f"Restore needs recovery. Files were kept in {base}."}
        if completion == "cleanup_pending":
            log("restore_commit: swap completed; cleanup will retry at next launch")
    except Exception as e:
        log(f"restore_commit: swap failed: {e}")
        if not os.path.exists(zip_restore_recovery.record_path(base)):
            return {"ok": False, "error": "Couldn't start the restore." + not_changed}
        try:
            record = zip_restore_recovery.read_record(base)
            if record is None:
                raise OSError("restore record is unavailable")
            zip_restore_recovery.write_record(base, {**record, "stage": "aborting"})
            if not zip_restore_recovery.undo(base, {**record, "stage": "aborting"}):
                # The originals are back but the leftovers couldn't be
                # deleted: the record says rolled_back, so the next launch
                # finishes the cleanup and the logbook is unchanged now.
                rolled_back = zip_restore_recovery.read_record(base)
                if not (rolled_back and rolled_back["stage"] == "rolled_back"
                        and zip_restore_recovery._live_matches(base, rolled_back, "live")):
                    raise OSError("undo did not complete")
                log("restore_commit: undo completed; cleanup will retry at next launch")
        except Exception as undo_error:
            log(f"restore_commit: recovery required: {undo_error}")
            return {"ok": False, "recovery_required": True, "error": f"Restore needs recovery. Files were kept in {base}."}
        return {"ok": False, "error": "Couldn't restore the backup." + not_changed}

    counts = {}
    try:
        conn = storage.current().connect_file(live_db)
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
        conn = storage.current().connect_file(db_path)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master")
        finally:
            conn.close()
        return True
    except sqlite3.DatabaseError as e:
        log(f"{context}: staged database won't open: {e}")
        return False


def discard_staging(staging_dir, log):
    """Deletes a staged backup that is no longer needed: a cancelled
    preview, or a restore that finished or failed. A folder still named
    in a zip-restore record is left for launch recovery. A failure is
    logged, and launch removes the unrecorded folder on a later start."""
    base = os.path.dirname(staging_dir)
    if os.path.basename(staging_dir) in zip_restore_recovery.record_names(base):
        return
    _cleanup_path(staging_dir, log)


def sweep_stale_staging(log, temp_dir=None, now=None):
    """Deletes restore staging folders left in the temp folder by an older
    build that never cleared them (a crash, or Windows ending the process).
    Only folders named with STAGING_PREFIX are touched, never links, and
    never one modified within STALE_STAGING_SECONDS."""
    temp_dir = temp_dir or tempfile.gettempdir()
    now = time.time() if now is None else now
    try:
        entries = list(os.scandir(temp_dir))
    except OSError as e:
        log(f"restore: couldn't scan {temp_dir} for leftover restore folders: {e}")
        return
    for entry in entries:
        if not entry.name.startswith(STAGING_PREFIX):
            continue
        try:
            if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            if now - entry.stat(follow_symlinks=False).st_mtime < STALE_STAGING_SECONDS:
                continue
        except OSError as e:
            log(f"restore: couldn't check leftover restore folder {entry.path}: {e}")
            continue
        _cleanup_path(entry.path, log)


def _cleanup_path(path, log):
    """Best-effort delete of a file or directory. Never raises."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
    except Exception as e:
        log(f"restore: cleanup failed for {path}: {e}")
