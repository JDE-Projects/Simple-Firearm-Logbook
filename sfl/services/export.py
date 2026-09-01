"""Exports: a self-contained single-firearm HTML file, a single-firearm
backup zip (HTML plus original photos and documents), and the full
collection export (report.html + data.csv + photos\\ + attachments\\)."""
import datetime
import os
import zipfile

import webview

from sfl import config, paths
from sfl.csv_export import _build_csv_text
from sfl.db import _firearm_row_to_dict
from sfl.render_html import _build_full_report_html, _build_single_export_html
from sfl.services.attachments import _get_attachments
from sfl.services.firearms import _get_firearm_row
from sfl.services.photos import _get_photos, _photo_with_data


def export_single_html(conn, window, log, firearm_id):
    """Self-contained single-firearm export: one HTML file with photos
    embedded as base64 data URIs, so it can be opened or shared standalone."""
    try:
        row = _get_firearm_row(conn, firearm_id)
        if row is None:
            return {"ok": False, "error": "That firearm no longer exists."}
        f = _firearm_row_to_dict(row)
        photos = [_photo_with_data(p) for p in _get_photos(conn, firearm_id)]
        attachments = _get_attachments(conn, firearm_id)
        html = _build_single_export_html(f, photos, attachments)
        default_name = paths.sanitize_filename(f"{f['log_number']} {f['make']} {f['model']}.html")
        result = window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=default_name, file_types=("HTML Files (*.html)",)
        )
        if not result:
            return {"ok": True, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path:
            return {"ok": True, "cancelled": True}
        if not path.lower().endswith(".html"):
            path += ".html"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        log(f"Exported single firearm HTML for {f['log_number']}")
        return {"ok": True, "path": path}
    except Exception as e:
        log(f"export_single_html failed: {e}")
        return {"ok": False, "error": "Couldn't export the HTML file."}


def export_single_backup_zip(conn, window, log, firearm_id):
    return _export_single_backup_zip_internal(conn, window, log, firearm_id)


def _export_single_backup_zip_internal(conn, window, log, firearm_id):
    """Zip containing the self-contained HTML plus the original photo
    files. Shared by the standalone export action and the delete flow's
    'export backup first' option."""
    try:
        row = _get_firearm_row(conn, firearm_id)
        if row is None:
            return {"ok": False, "error": "That firearm no longer exists."}
        f = _firearm_row_to_dict(row)
        photos = _get_photos(conn, firearm_id)
        attachments = _get_attachments(conn, firearm_id)
        html = _build_single_export_html(f, [_photo_with_data(p) for p in photos], attachments)
        default_name = paths.sanitize_filename(f"{f['log_number']} {f['make']} {f['model']} backup.zip")
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
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{f['log_number']}.html", html)
            for p in photos:
                full = paths._safe_photo_path(p["filename"])
                if full and os.path.isfile(full):
                    zf.write(full, arcname=os.path.basename(p["filename"]))
            # Attachments are always fully included, unlike photos: they're
            # the point of a backup. Kept in their own folder in the zip
            # rather than flattened, since two files can share a basename.
            for a in attachments:
                full = paths._safe_attachment_path(a["filename"])
                if full and os.path.isfile(full):
                    zf.write(full, arcname=f"attachments/{os.path.basename(a['filename'])}")
        log(f"Exported backup zip for firearm {f['log_number']}")
        return {"ok": True, "path": path}
    except Exception as e:
        log(f"export_single_backup_zip failed: {e}")
        return {"ok": False, "error": "Couldn't create the backup zip."}


def export_full(conn, window, log, photo_depth="primary"):
    """Full collection export: one zip with an all-firearms HTML report,
    a CSV of all fields, a photos\\ folder, and an attachments\\ folder.
    photo_depth controls how many photos per firearm are included:
    'primary', 'all', or 'none'. Documents have no depth selector and are
    always fully included; they're the point of the backup."""
    try:
        if photo_depth not in ("primary", "all", "none"):
            photo_depth = "primary"
        rows = conn.execute("SELECT * FROM firearms ORDER BY log_number").fetchall()
        firearms = [_firearm_row_to_dict(r) for r in rows]

        photos_by_firearm = {}
        attachments_by_firearm = {}
        files_to_include = []
        for fd in firearms:
            photos = _get_photos(conn, fd["id"])
            if photo_depth == "none":
                chosen = []
            elif photo_depth == "primary":
                primary = [p for p in photos if p["is_primary"]]
                chosen = primary[:1] if primary else (photos[:1] if photos else [])
            else:
                chosen = photos
            photos_by_firearm[fd["id"]] = chosen
            files_to_include.extend(p["filename"] for p in chosen)

            attachments = _get_attachments(conn, fd["id"])
            attachments_by_firearm[fd["id"]] = attachments
            files_to_include.extend(a["filename"] for a in attachments)

        html = _build_full_report_html(firearms, photos_by_firearm, attachments_by_firearm)
        csv_text = _build_csv_text(firearms)

        stamp = datetime.date.today().strftime("%Y%m%d")
        default_name = f"Firearm_Logbook_Export_{stamp}.zip"
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
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("report.html", html)
            # BOM so Excel opens the CSV as UTF-8
            zf.writestr("data.csv", "﻿" + csv_text)
            for filename in files_to_include:
                full = paths._safe_photo_path(filename) if filename.startswith(config.PHOTOS_DIRNAME + "/") \
                    else paths._safe_attachment_path(filename)
                if full and os.path.isfile(full):
                    zf.write(full, arcname=filename.replace("\\", "/"))
        log(f"Full export created, photo depth={photo_depth}, {len(firearms)} firearm(s)")
        return {"ok": True, "path": path}
    except Exception as e:
        log(f"export_full failed: {e}")
        return {"ok": False, "error": "Couldn't create the export."}
