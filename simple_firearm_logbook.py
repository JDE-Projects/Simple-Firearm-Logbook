"""
Simple Firearm Logbook, a personal firearm inventory and record-keeping tool.

JDE-Projects "Simple X Tool": Python 3 + PySide6/pywebview, single-file UI.
Keeps a permanent, never-reused log number per firearm, tracks acquisition and
disposition details in a bound-book-forward shape (discrete columns, not
blobs, so a future ATF mode can reuse them), stores photos next to the exe,
and exports single-firearm or full-collection reports.

The app's logic and startup live in the sfl/ package; this file stays the
PyInstaller/build entry point and re-exports the names the app and its tests
import from the top level.
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
from sfl.launcher import run
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
    run()


if __name__ == "__main__":
    main()
