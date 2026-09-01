"""Backup: a complete, restorable archive of the database plus every photo
and document file it references. Separate from Export (sfl.services.export),
which produces share/print output, not a restorable copy. create_backup runs
the native Save dialog; _write_backup_zip does the actual work and has no
window dependency, so it can be tested directly."""
import datetime
import json
import os
import sqlite3
import tempfile
import zipfile

import webview

from sfl import config, paths
from sfl.utils import sha256_hex


def create_backup(conn, window, log):
    """Native Save dialog, default filename SFL_Backup_MMDDYYYY_HHMM.zip,
    then hands off to _write_backup_zip for the actual archive."""
    try:
        stamp = datetime.datetime.now().strftime("%m%d%Y_%H%M")
        default_name = f"SFL_Backup_{stamp}.zip"
        result = window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=default_name, file_types=("ZIP Files (*.zip)",)
        )
        if not result:
            return {"ok": True, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path:
            return {"ok": True, "cancelled": True}
        if not path.lower().endswith(".zip"):
            path += ".zip"
        outcome = _write_backup_zip(conn, path, log)
        outcome["path"] = path
        return outcome
    except Exception as e:
        log(f"create_backup failed: {e}")
        return {"ok": False, "error": "Couldn't create the backup."}


def _write_backup_zip(conn, dest_path, log):
    """Writes the actual backup zip to dest_path: a safe SQLite backup-API
    copy of the database, every photo/attachment file on disk, and a
    manifest.json inventory. No window dependency, so this is what tests
    call directly. Missing referenced files are recorded and logged, never
    silently dropped."""
    tmp_db_path = None
    try:
        firearm_count = conn.execute("SELECT COUNT(*) FROM firearms").fetchone()[0]
        photo_rows = conn.execute("SELECT filename FROM photos").fetchall()
        attachment_rows = conn.execute("SELECT filename FROM attachments").fetchall()

        # Safe DB copy: use SQLite's own backup API rather than a raw file
        # copy, so a concurrently-open connection can't yield a corrupt copy.
        tmp_fd, tmp_db_path = tempfile.mkstemp(suffix=".db", dir=tempfile.gettempdir())
        os.close(tmp_fd)
        dest_conn = None
        try:
            dest_conn = sqlite3.connect(tmp_db_path)
            conn.backup(dest_conn)
        finally:
            if dest_conn is not None:
                dest_conn.close()
        with open(tmp_db_path, "rb") as fh:
            db_bytes = fh.read()

        inventory = []
        missing = []

        db_arcname = config.DB_FILENAME
        db_sha256 = sha256_hex(db_bytes)
        inventory.append({"path": db_arcname, "size": len(db_bytes), "sha256": db_sha256})

        files_to_include = []
        for row in photo_rows:
            files_to_include.append((row["filename"], paths._safe_photo_path(row["filename"])))
        for row in attachment_rows:
            files_to_include.append((row["filename"], paths._safe_attachment_path(row["filename"])))

        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(db_arcname, db_bytes)
            for stored_filename, full in files_to_include:
                if not full or not os.path.isfile(full):
                    missing.append(stored_filename)
                    log(f"Backup: referenced file missing on disk: {stored_filename}")
                    continue
                arcname = stored_filename.replace("\\", "/")
                with open(full, "rb") as fh:
                    file_bytes = fh.read()
                zf.write(full, arcname=arcname)
                inventory.append({
                    "path": arcname,
                    "size": len(file_bytes),
                    "sha256": sha256_hex(file_bytes),
                })

            counts = {
                "firearms": firearm_count,
                "photos": len(photo_rows),
                "attachments": len(attachment_rows),
            }
            manifest = {
                "format_version": 1,
                "app_version": config.APP_VERSION,
                "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "counts": counts,
                "inventory": inventory,
                "missing": missing,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        log(f"Backup created: {counts['firearms']} firearm(s), {len(missing)} missing file(s)")
        return {"ok": True, "missing": missing, "counts": counts}
    except Exception as e:
        log(f"_write_backup_zip failed: {e}")
        return {"ok": False, "error": "Couldn't create the backup."}
    finally:
        if tmp_db_path and os.path.exists(tmp_db_path):
            try:
                os.remove(tmp_db_path)
            except Exception:
                pass
