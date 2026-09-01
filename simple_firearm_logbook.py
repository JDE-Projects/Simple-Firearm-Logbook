"""
Simple Firearm Logbook, a personal firearm inventory and record-keeping tool.

JDE-Projects "Simple X Tool": Python 3 + PySide6/pywebview, single-file UI.
Keeps a permanent, never-reused log number per firearm, tracks acquisition and
disposition details in a bound-book-forward shape (discrete columns, not
blobs, so a future ATF mode can reuse them), stores photos next to the exe,
and exports single-firearm or full-collection reports.

The app's logic lives in the sfl/ package (config, paths, services, ...);
this file stays the PyInstaller/build entry point, holds main(), and
re-exports the names the app and its tests import from the top level.
"""
# ruff: noqa: F401  (this file's imports below are re-exports, not unused)
import ctypes
import os
import shutil
import subprocess
import sys

import webview

from sfl.api import Api
from sfl.config import (
    APP_VERSION,
    ATTACHMENT_LABEL_MAX,
    ATTACHMENT_WARN_BYTES,
    ATTACHMENTS_DIRNAME,
    CSV_FORMULA_LEAD_CHARS,
    DB_FILENAME,
    DISPOSITION_STATUSES,
    GITHUB_OWNER,
    GITHUB_REPO,
    IMAGE_EXTENSIONS,
    IMPORT_FIELDS,
    MAX_IMAGE_BYTES,
    PHOTO_JPEG_QUALITY,
    PHOTO_MAX_EDGE,
    PHOTOS_DIRNAME,
    SCHEMA_VERSION,
    STANDARD_FIREARM_TYPES,
)
from sfl.csv_export import _build_csv_text, _csv_safe
from sfl.csv_import import (
    _import_cell,
    _insert_imported_firearm,
    _yes_no_to_bool,
    build_import_preview,
    build_import_record,
    guess_column_mapping,
    read_import_csv_rows,
)
from sfl.db import (
    NewerSchemaError,
    _ensure_column,
    _firearm_row_to_dict,
    _get_next_log_number,
    open_db,
)
from sfl.errors import _update_error_reason
from sfl.images import (
    _photo_b64_too_large,
    _photo_bytes_too_large,
    _photo_source_too_large,
    _sniff_image_mime,
    optimize_image_to_jpeg,
    photo_failure_warning,
)
from sfl.paths import (
    _pref_path,
    _safe_attachment_path,
    _safe_photo_path,
    _sanitize_attachment_label,
    app_dir,
    resource_path,
    sanitize_filename,
)
from sfl.platform_win import (
    _acquire_single_instance,
    _focus_existing_window,
    _own_window_handle,
    _restore_geometry,
    _save_geometry,
    _show_newer_schema_error,
    _show_write_error,
    _win32,
    _writable_check,
)
from sfl.prefs import load_prefs, save_prefs
from sfl.render_html import (
    _build_full_report_html,
    _build_single_export_html,
    _esc_html,
    _fmt_price_html,
    _render_disposition_block,
    _render_document_block,
    _render_identity_block,
    _render_notes_block,
    _render_photo_block_embedded,
    _render_photo_block_relative,
    _render_trust_nfa_rows,
)
from sfl.utils import (
    _redact_username,
    format_size,
    parse_decimal_optional,
    parse_iso_date_optional,
)


def main():
    # Use the Windows certificate store for TLS instead of the bundled CA list,
    # so antivirus/network filters that inject their own root cert (common on
    # managed laptops) don't break the GitHub update check. Runs before the
    # Api object exists, so there's no logger yet to record a fallback; if
    # truststore is missing or fails, urllib silently keeps using its default
    # bundled CA list instead.
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass

    if not _acquire_single_instance("JDE_SimpleFirearmLogbook_SingleInstance"):
        if _focus_existing_window("Simple Firearm Logbook"):
            sys.exit(0)
        # Existing window not found: fail open and launch normally.

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "JDEProjects.SimpleFirearmLogbook"
            )
        except Exception:
            pass

    folder = app_dir()
    if not _writable_check(folder):
        _show_write_error(folder)
        sys.exit(1)

    db_path = os.path.join(folder, DB_FILENAME)

    api = Api()

    try:
        conn = open_db(db_path)
    except NewerSchemaError:
        _show_newer_schema_error()
        sys.exit(1)

    api.set_conn(conn)

    win = webview.create_window(
        "Simple Firearm Logbook",
        url=resource_path("simple_firearm_logbook-UI.html"),
        js_api=api,
        width=1280,
        height=820,
        min_size=(1000, 680),
        background_color="#0a0e14",
    )
    api.set_window(win)
    win.events.shown += lambda: _restore_geometry(win)

    def _on_window_closing():
        _save_geometry(win)
        return True

    win.events.closing += _on_window_closing
    try:
        webview.start(gui="qt", icon=resource_path("simple_firearm_logbook.png"))
    except TypeError:
        webview.start(gui="qt")

    api.close_conn()


if __name__ == "__main__":
    main()
