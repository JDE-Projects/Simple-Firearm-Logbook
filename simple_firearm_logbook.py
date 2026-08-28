"""
Simple Firearm Logbook, a personal firearm inventory and record-keeping tool.

JDE-Projects "Simple X Tool": Python 3 + PySide6/pywebview, single-file UI.
Keeps a permanent, never-reused log number per firearm, tracks acquisition and
disposition details in a bound-book-forward shape (discrete columns, not
blobs, so a future ATF mode can reuse them), stores photos next to the exe,
and exports single-firearm or full-collection reports.
"""
import base64
import csv
import ctypes
import ctypes.wintypes as wintypes
import datetime
import errno
import io
import json
import os
import shutil
import socket
import sqlite3
import ssl
import sys
import urllib.error
import urllib.request
import zipfile
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import webview
from PIL import Image, ImageOps

# Refuse to decode an image with more than ~64 megapixels instead of letting a
# pixel-bomb file exhaust memory. Applies to every Image.open() call, so it
# covers photo import everywhere in the app.
Image.MAX_IMAGE_PIXELS = 64_000_000

APP_VERSION = "1.5.0"
GITHUB_OWNER = "JDE-Projects"
GITHUB_REPO = "Simple-Firearm-Logbook"

DB_FILENAME = "simple_firearm_logbook.db"
PHOTOS_DIRNAME = "photos"
ATTACHMENTS_DIRNAME = "attachments"
SCHEMA_VERSION = 1

DISPOSITION_STATUSES = ("Owned", "Sold", "Traded", "Lost", "Stolen", "Other")
STANDARD_FIREARM_TYPES = ("Pistol", "Revolver", "Rifle", "Shotgun", "Other")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff")

# Photo import optimization: scale the long edge down to this many pixels (only
# when the source is bigger) and re-encode as JPEG at this quality. A big phone
# photo drops from megabytes to a few hundred KB while a serial-number close-up
# stays readable. Applied on import only; the user's original file is untouched.
PHOTO_MAX_EDGE = 2400
PHOTO_JPEG_QUALITY = 85

# Decompression-bomb guard: refuse a source file bigger than this before ever
# opening it, so a hostile or corrupt file can't be handed to PIL at all.
MAX_IMAGE_BYTES = 50 * 1024 * 1024

# Documents attach verbatim, never opened or recompressed. ATTACHMENT_WARN_BYTES
# is a soft warning threshold only, not a hard cap.
ATTACHMENT_WARN_BYTES = 25 * 1024 * 1024
ATTACHMENT_LABEL_MAX = 100


def resource_path(rel: str) -> str:
    """Path to a bundled resource, working both from source and PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def app_dir() -> str:
    """Folder the app lives in: next to the .exe when frozen, else the script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


_INVALID_FILENAME_CHARS = '<>:"/\\|?*'


def sanitize_filename(name: str) -> str:
    """Strip characters that Windows doesn't allow in file names."""
    cleaned = "".join(c for c in name if c not in _INVALID_FILENAME_CHARS)
    return cleaned.strip()


def format_size(num_bytes) -> str:
    """Render a byte count as a short human string, binary units (1024).
    One decimal place at MB and up, none below that."""
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        n = 0.0
    if n < 1024:
        return f"{int(n)} B"
    kb = n / 1024
    if kb < 1024:
        return f"{int(kb)} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024
    return f"{gb:.1f} GB"


def _sanitize_attachment_label(raw: str) -> str:
    """Sanitize and cap a document's display label. Shared by the import
    default (built from the original filename) and rename_attachment."""
    return sanitize_filename(raw or "")[:ATTACHMENT_LABEL_MAX]


def _photo_source_too_large(path: str) -> bool:
    """Decompression-bomb guard: refuse a photo source bigger than
    MAX_IMAGE_BYTES before it is ever opened."""
    return os.path.getsize(path) > MAX_IMAGE_BYTES


def _photo_bytes_too_large(data: bytes) -> bool:
    """Decompression-bomb guard for raw bytes: refuse a photo source bigger
    than MAX_IMAGE_BYTES before it is ever opened. Same ceiling as
    _photo_source_too_large, for the drag-and-drop import path where the
    source is already-decoded bytes rather than a file on disk."""
    return len(data) > MAX_IMAGE_BYTES


# ---------------------------------------------------------------------------
# Field parsing helpers. Everything but make/model is optional, so blank
# input is always accepted and passed through as an empty string rather
# than treated as an error.
# ---------------------------------------------------------------------------
def parse_iso_date_optional(raw):
    """Validate an optional yyyy-mm-dd date. Returns ("", None) for blank
    input, (date_str, None) on success, or (None, error) on failure."""
    s = (raw or "").strip()
    if not s:
        return "", None
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return None, "Enter a valid date."
    return s, None


def parse_decimal_optional(raw):
    """Validate an optional non-negative decimal amount, normalized to a
    plain 2-decimal string (never a float, so it never drifts). Returns
    ("", None) for blank input, or (None, error) on failure."""
    s = (raw or "").strip()
    if not s:
        return "", None
    s = s.replace("$", "").replace(",", "").strip()
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None, "Enter a valid amount."
    if not value.is_finite():
        return None, "Enter a valid amount."
    try:
        quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        if value < 0:
            return None, "Amount must be zero or greater."
        return None, "Enter a valid amount."
    if quantized < 0:
        return None, "Amount must be zero or greater."
    if quantized.is_zero():
        # Normalizes both "-0" and small negatives that round to zero (e.g.
        # "-0.001") to a plain positive zero, rather than "-0.00".
        quantized = Decimal("0.00")
    return str(quantized), None


def _sniff_image_mime(data: bytes) -> str:
    """Guess an image's mime type from its file signature, independent of
    whatever extension it's stored under. Used for embedding photos as
    base64 data URIs and for in-app previews."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return "image/jpeg"


def optimize_image_to_jpeg(src: str, target: str, max_edge: int = PHOTO_MAX_EDGE,
                           quality: int = PHOTO_JPEG_QUALITY) -> None:
    """Read an image, apply its stored EXIF rotation, scale the long edge down
    to max_edge if it is larger, and write the result to target as a JPEG.

    src may be a path or a file-like object (e.g. io.BytesIO), since PIL's
    Image.open accepts either. target is always a path.

    Never reads or writes anything but src (read) and target (write), so the
    user's original is always safe. Raises on any failure (unreadable file,
    unsupported data, write error) so the caller can refuse to store a broken
    or oversized photo rather than pretend the import worked.
    """
    with Image.open(src) as opened:
        # Honor the orientation a phone camera records in EXIF, so a photo that
        # looks upright in the gallery isn't stored sideways.
        img = ImageOps.exif_transpose(opened)
        # JPEG has no transparency, so flatten any alpha channel onto white
        # instead of letting it turn black.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            flattened = Image.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.split()[-1])
            img = flattened
        elif img.mode != "RGB":
            img = img.convert("RGB")
        long_edge = max(img.size)
        if long_edge > max_edge:
            scale = max_edge / long_edge
            new_size = (max(1, round(img.size[0] * scale)),
                        max(1, round(img.size[1] * scale)))
            img = img.resize(new_size, Image.LANCZOS)
        img.save(target, "JPEG", quality=quality)


def photo_failure_warning(fail_counts):
    """Builds the user-facing warning for refused photos out of a
    {"not_image": n, "damaged": n, "too_large": n} count dict, so add_photos
    and add_photos_from_data always word it the same way. Returns None if
    nothing was refused."""
    not_image = fail_counts.get("not_image", 0)
    damaged = fail_counts.get("damaged", 0)
    too_large = fail_counts.get("too_large", 0)
    total = not_image + damaged + too_large
    if total == 0:
        return None

    buckets = [b for b in (("not_image", not_image), ("damaged", damaged), ("too_large", too_large)) if b[1]]
    if len(buckets) == 1:
        kind, n = buckets[0]
        if kind == "not_image":
            return ("1 file wasn't an image and wasn't added." if n == 1
                     else f"{n} files weren't images and weren't added.")
        if kind == "damaged":
            return ("1 image was damaged and wasn't added." if n == 1
                     else f"{n} images were damaged and weren't added.")
        return ("1 image was too large and wasn't added." if n == 1
                 else f"{n} images were too large and weren't added.")

    clauses = []
    for kind, n in buckets:
        if kind == "not_image":
            clauses.append("1 wasn't an image" if n == 1 else f"{n} weren't images")
        elif kind == "damaged":
            clauses.append("1 was damaged" if n == 1 else f"{n} were damaged")
        else:
            clauses.append("1 was too large" if n == 1 else f"{n} were too large")
    return f"{total} files weren't added: " + ", ".join(clauses) + "."


def _safe_photo_path(filename: str):
    """Resolve a photo's stored relative filename to a full path, refusing
    anything that would resolve outside the app folder (path traversal)."""
    try:
        base = os.path.realpath(app_dir())
        full = os.path.realpath(os.path.join(app_dir(), filename))
        if full != base and not full.startswith(base + os.sep):
            return None
        return full
    except Exception:
        return None


def _safe_attachment_path(filename: str):
    """Resolve a document's stored relative filename to a full path, refusing
    anything that would resolve outside the app folder (path traversal).
    Mirrors _safe_photo_path."""
    try:
        base = os.path.realpath(app_dir())
        full = os.path.realpath(os.path.join(app_dir(), filename))
        if full != base and not full.startswith(base + os.sep):
            return None
        return full
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Preferences: a small local file next to the app, not stored in the db.
# ---------------------------------------------------------------------------
def _pref_path() -> str:
    return os.path.join(app_dir(), "simple_firearm_logbook.pref")


def load_prefs() -> dict:
    """Load the full prefs dict. Tolerant of a missing or corrupt file."""
    try:
        with open(_pref_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_prefs(prefs: dict) -> bool:
    try:
        with open(_pref_path(), "w", encoding="utf-8") as f:
            json.dump(prefs, f)
        return True
    except Exception:
        return False


# Window geometry persistence. Windows-only: needs `import ctypes` and
# `from ctypes import wintypes` in the app.
#
# Save and restore the ABSOLUTE window frame rectangle via Win32, found by the
# window title but filtered to a window owned by this process (see
# `_own_window_handle` below). GetWindowRect (save) and SetWindowPos (restore)
# share one frame-based, physical-pixel coordinate space, so the rect
# round-trips exactly at any DPI or monitor layout. Do NOT pass x/y into
# create_window and do NOT use window.move: pywebview's Qt backend applies
# those pre-show and relative to the primary screen, so the window lands on
# the wrong monitor, drifts down by the title-bar height each launch, and
# slides sideways at non-100% scaling.


def _win32():
    u = ctypes.windll.user32
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return u


def _own_window_handle(title):
    """HWND of our own top-level window with this title.

    FindWindowW matches by title across the whole desktop, so with a second
    instance open it can return the other copy's window. Enumerate instead and
    keep only a window owned by this process.
    """
    try:
        u = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
        u.EnumWindows.restype = wintypes.BOOL
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowVisible.restype = wintypes.BOOL

        own_pid = os.getpid()
        found = {"hwnd": None}

        def _callback(hwnd, lparam):
            if not u.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != own_pid:
                return True
            length = u.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            u.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value != title:
                return True
            found["hwnd"] = hwnd
            return False   # stop enumerating, we found it

        proc = WNDENUMPROC(_callback)   # kept alive for the duration of the call below
        u.EnumWindows(proc, 0)
        return found["hwnd"]
    except Exception:
        return None


def _save_geometry(win) -> None:
    """Save the absolute frame rect (physical px) via Win32. Wire to `closing`.
    Wrapped end to end so a failure here can never block the window from closing."""
    try:
        u = _win32()
        hwnd = _own_window_handle(win.title)
        if not hwnd:
            return
        r = wintypes.RECT()
        if not u.GetWindowRect(hwnd, ctypes.byref(r)):
            return
        x, y, w, h = r.left, r.top, r.right - r.left, r.bottom - r.top
        if x <= -30000 or y <= -30000:   # minimized sentinel, not a real spot
            return
        if w <= 0 or h <= 0:
            return
        prefs = load_prefs()
        prefs["window"] = {"x": x, "y": y, "width": w, "height": h}
        save_prefs(prefs)
    except Exception:
        pass


def _restore_geometry(win) -> None:
    """Restore the saved frame rect via Win32. Wire to `shown` (after the OS
    window exists). Validate before applying; never raise."""
    try:
        geo = load_prefs().get("window")
        if not isinstance(geo, dict):
            return
        x, y, w, h = geo.get("x"), geo.get("y"), geo.get("width"), geo.get("height")
        for v in (x, y, w, h):
            if not isinstance(v, int) or isinstance(v, bool):
                return
        if w <= 0 or h <= 0:
            return
        # Is a point in the title bar still on a connected monitor?
        point = wintypes.POINT(x + 100, y + 30)
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        if not user32.MonitorFromPoint(point, 0):   # MONITOR_DEFAULTTONULL
            return
        u = _win32()
        hwnd = _own_window_handle(win.title)
        if not hwnd:
            return
        SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
        u.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
class NewerSchemaError(Exception):
    """Raised by open_db when the database's PRAGMA user_version is higher
    than this build's SCHEMA_VERSION. The database is never touched in this
    case; the caller should tell the user to update the app."""


def _ensure_column(conn, table: str, column: str, decl: str) -> None:
    """Add a column to an existing table only if it isn't already there.
    Additive-only: never rewrites or drops. Safe to call on every open."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def open_db(path: str) -> sqlite3.Connection:
    """Open (creating if missing) the SQLite database and ensure the schema.
    Refuses to touch a database stamped with a schema newer than this build
    understands; see NewerSchemaError."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    existing_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if existing_version > SCHEMA_VERSION:
        conn.close()
        raise NewerSchemaError(
            f"Database schema {existing_version} is newer than this app supports ({SCHEMA_VERSION})."
        )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS firearms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_number TEXT NOT NULL UNIQUE,
            make TEXT NOT NULL,
            model TEXT NOT NULL,
            serial_number TEXT NOT NULL DEFAULT '',
            firearm_type TEXT NOT NULL DEFAULT '',
            caliber TEXT NOT NULL DEFAULT '',
            acquisition_date TEXT NOT NULL DEFAULT '',
            acquired_from TEXT NOT NULL DEFAULT '',
            purchase_price TEXT NOT NULL DEFAULT '',
            estimated_value TEXT NOT NULL DEFAULT '',
            insured_value TEXT NOT NULL DEFAULT '',
            storage_location TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            sub_type TEXT NOT NULL DEFAULT '',
            held_in_trust INTEGER NOT NULL DEFAULT 0,
            trust_name TEXT NOT NULL DEFAULT '',
            is_nfa INTEGER NOT NULL DEFAULT 0,
            nfa_form_type TEXT NOT NULL DEFAULT '',
            nfa_stamp_date TEXT NOT NULL DEFAULT '',
            disposition_status TEXT NOT NULL DEFAULT 'Owned',
            disposition_date TEXT NOT NULL DEFAULT '',
            disposition_to TEXT NOT NULL DEFAULT '',
            disposition_address TEXT NOT NULL DEFAULT '',
            disposition_amount TEXT NOT NULL DEFAULT '',
            disposition_notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firearm_id INTEGER NOT NULL REFERENCES firearms(id),
            filename TEXT NOT NULL,
            seq INTEGER NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firearm_id INTEGER NOT NULL REFERENCES firearms(id),
            filename TEXT NOT NULL,
            label TEXT NOT NULL,
            seq INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS counters (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            next_log_number INTEGER NOT NULL
        );
        """
    )
    conn.execute("INSERT OR IGNORE INTO counters (id, next_log_number) VALUES (1, 1)")

    # Standing rule: migrations in this function must stay additive-only (new
    # tables/columns guarded by an existence check, never a destructive
    # rewrite), matching the rest of the JDE-Projects fleet. These columns are
    # backward compatible (an older build tolerates the extra columns), so they
    # do not bump SCHEMA_VERSION.
    _ensure_column(conn, "firearms", "insured_value", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "storage_location", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "sub_type", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "held_in_trust", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "firearms", "trust_name", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "is_nfa", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "firearms", "nfa_form_type", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "firearms", "nfa_stamp_date", "TEXT NOT NULL DEFAULT ''")

    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def _get_next_log_number(cur) -> str:
    """Claim the next permanent log number, zero-padded to 5 digits, and
    advance the counter. Never reused, even if the firearm this number was
    claimed for is later deleted or the insert that claimed it fails."""
    row = cur.execute("SELECT next_log_number FROM counters WHERE id=1").fetchone()
    n = row["next_log_number"]
    cur.execute("UPDATE counters SET next_log_number=? WHERE id=1", (n + 1,))
    return f"{n:05d}"


def _firearm_row_to_dict(r) -> dict:
    return {
        "id": r["id"],
        "log_number": r["log_number"],
        "make": r["make"],
        "model": r["model"],
        "serial_number": r["serial_number"],
        "firearm_type": r["firearm_type"],
        "caliber": r["caliber"],
        "acquisition_date": r["acquisition_date"],
        "acquired_from": r["acquired_from"],
        "purchase_price": r["purchase_price"],
        "estimated_value": r["estimated_value"],
        "insured_value": r["insured_value"],
        "storage_location": r["storage_location"],
        "notes": r["notes"],
        "sub_type": r["sub_type"],
        "held_in_trust": r["held_in_trust"],
        "trust_name": r["trust_name"],
        "is_nfa": r["is_nfa"],
        "nfa_form_type": r["nfa_form_type"],
        "nfa_stamp_date": r["nfa_stamp_date"],
        "disposition_status": r["disposition_status"],
        "disposition_date": r["disposition_date"],
        "disposition_to": r["disposition_to"],
        "disposition_address": r["disposition_address"],
        "disposition_amount": r["disposition_amount"],
        "disposition_notes": r["disposition_notes"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


# ---------------------------------------------------------------------------
# Export rendering: shared HTML/CSS builders used by both the single-firearm
# export and the full-collection report. Deliberately a plain, light,
# print-friendly look, independent of the app's own dark/light theme choice,
# since these are meant to be read or printed outside the app.
# ---------------------------------------------------------------------------
EXPORT_BASE_CSS = """
* { box-sizing: border-box; }
body { font-family: Georgia, 'Times New Roman', serif; background:#f5f5f2; color:#1a1a1a; margin:0; padding:24px; }
.report { max-width: 900px; margin: 0 auto; }
h1 { font-size: 22px; margin-bottom:4px; }
.report-meta { color:#555; font-size:12px; margin-bottom:24px; }
.firearm-card { background:#ffffff; border:1px solid #ddd; border-radius:8px; padding:20px 24px; margin-bottom:24px; }
.identity-block h2 { margin:0 0 2px; font-size:19px; }
.log-num { color:#666; font-size:12.5px; margin-bottom:12px; font-family: 'Courier New', monospace; }
table.id-table { border-collapse: collapse; width:100%; margin-bottom: 6px; }
table.id-table th { text-align:left; width:180px; padding:4px 10px 4px 0; color:#555; font-size:12.5px; font-weight:600; vertical-align:top; }
table.id-table td { padding:4px 0; font-size:13px; vertical-align:top; }
.disposition-block { margin-top:16px; padding-top:14px; border-top:1px solid #ddd; }
.disposition-block h3 { margin:0 0 8px; font-size:14px; color:#a33; }
.notes-block { margin-top:16px; padding-top:14px; border-top:1px solid #ddd; }
.notes-block h3 { margin:0 0 6px; font-size:14px; }
.notes-block p { font-size:13px; line-height:1.5; white-space:pre-wrap; margin:0; }
.photo-block { margin-top:16px; padding-top:14px; border-top:1px solid #ddd; }
.photo-block h3 { margin:0 0 10px; font-size:14px; }
.photo-grid { display:grid; grid-template-columns: repeat(3, 1fr); gap:10px; }
.photo-item { border:1px solid #ddd; border-radius:6px; overflow:hidden; background:#fafafa; text-align:center; font-size:11px; color:#777; padding:6px; }
.photo-item img { width:100%; height:auto; display:block; border-radius:4px; }
.photo-item.missing { padding:24px 8px; }
.photo-item.primary { border-color:#b8935a; }
.document-block { margin-top:16px; padding-top:14px; border-top:1px solid #ddd; }
.document-block h3 { margin:0 0 8px; font-size:14px; }
.document-list { margin:0; padding-left:18px; font-size:13px; line-height:1.7; }
.doc-size { color:#777; font-size:12px; }
"""

# Fresh page per firearm, keep-together blocks, capped 2-per-row photo grid,
# and a white background regardless of the viewer's own theme.
PRINT_CSS = """
@media print {
  body { background:#ffffff !important; color:#111 !important; }
  .firearm-card { page-break-before: always; border:none; box-shadow:none; }
  .firearm-card:first-child { page-break-before: avoid; }
  .identity-block, .notes-block, .disposition-block, .photo-block, .document-block { break-inside: avoid; page-break-inside: avoid; }
  .photo-grid { grid-template-columns: repeat(2, 1fr) !important; }
  .photo-grid img { max-width: 260px !important; max-height: 260px !important; }
}
"""


def _esc_html(s) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _fmt_price_html(s) -> str:
    if not s:
        return "Not set"
    try:
        value = Decimal(s)
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):,.2f}"
    except Exception:
        return _esc_html(s)


def _render_identity_block(f: dict) -> str:
    return f"""
    <div class="identity-block">
      <h2>{_esc_html(f['make'])} {_esc_html(f['model'])}</h2>
      <div class="log-num">Log #{_esc_html(f['log_number'])}</div>
      <table class="id-table">
        <tr><th>Type</th><td>{_esc_html(f['firearm_type']) or 'Not set'}</td></tr>
        <tr><th>Sub-Type</th><td>{_esc_html(f['sub_type']) or 'Not set'}</td></tr>
        <tr><th>Caliber</th><td>{_esc_html(f['caliber']) or 'Not set'}</td></tr>
        <tr><th>Serial Number</th><td>{_esc_html(f['serial_number']) or 'Not set'}</td></tr>
        <tr><th>Acquisition Date</th><td>{_esc_html(f['acquisition_date']) or 'Not set'}</td></tr>
        <tr><th>Acquired From</th><td>{_esc_html(f['acquired_from']) or 'Not set'}</td></tr>
        <tr><th>Purchase Price</th><td>{_fmt_price_html(f['purchase_price'])}</td></tr>
        <tr><th>Estimated Value</th><td>{_fmt_price_html(f['estimated_value'])}</td></tr>
        <tr><th>Insured Value</th><td>{_fmt_price_html(f['insured_value'])}</td></tr>
        <tr><th>Storage Location</th><td>{_esc_html(f['storage_location']) or 'Not set'}</td></tr>
        {_render_trust_nfa_rows(f)}
      </table>
    </div>
    """


def _render_trust_nfa_rows(f: dict) -> str:
    """Trust and NFA rows are left out entirely for an ordinary firearm, so
    the identity table stays clean when neither applies."""
    rows = []
    if f["held_in_trust"]:
        rows.append("<tr><th>Held In Trust</th><td>Yes</td></tr>")
        rows.append(f"<tr><th>Trust Name</th><td>{_esc_html(f['trust_name']) or 'Not set'}</td></tr>")
    if f["is_nfa"]:
        rows.append("<tr><th>NFA</th><td>Yes</td></tr>")
        rows.append(f"<tr><th>NFA Form Type</th><td>{_esc_html(f['nfa_form_type']) or 'Not set'}</td></tr>")
        rows.append(f"<tr><th>NFA Stamp Date</th><td>{_esc_html(f['nfa_stamp_date']) or 'Not set'}</td></tr>")
    return "".join(rows)


def _render_disposition_block(f: dict) -> str:
    if f["disposition_status"] == "Owned":
        return ""
    return f"""
    <div class="disposition-block">
      <h3>Disposition: {_esc_html(f['disposition_status'])}</h3>
      <table class="id-table">
        <tr><th>Date</th><td>{_esc_html(f['disposition_date']) or 'Not set'}</td></tr>
        <tr><th>To</th><td>{_esc_html(f['disposition_to']) or 'Not set'}</td></tr>
        <tr><th>Address</th><td>{_esc_html(f['disposition_address']) or 'Not set'}</td></tr>
        <tr><th>Amount</th><td>{_fmt_price_html(f['disposition_amount'])}</td></tr>
        <tr><th>Notes</th><td>{_esc_html(f['disposition_notes']) or 'Not set'}</td></tr>
      </table>
    </div>
    """


def _render_notes_block(f: dict) -> str:
    if not f["notes"]:
        return ""
    return (
        '<div class="notes-block"><h3>Notes</h3><p>'
        + _esc_html(f["notes"]).replace("\n", "<br>")
        + "</p></div>"
    )


def _render_photo_block_embedded(photos_with_data: list) -> str:
    if not photos_with_data:
        return ""
    items = []
    for p in photos_with_data:
        cls = "photo-item primary" if p.get("is_primary") else "photo-item"
        if p.get("data_uri"):
            items.append(f'<div class="{cls}"><img src="{p["data_uri"]}" alt="Photo"></div>')
        else:
            name = _esc_html(os.path.basename(p["filename"]))
            items.append(f'<div class="{cls} missing">{name}<br><span>Photo file missing</span></div>')
    return f'<div class="photo-block"><h3>Photos</h3><div class="photo-grid">{"".join(items)}</div></div>'


def _render_photo_block_relative(photos: list) -> str:
    """Photo block for the full report, which references the relative
    photos\\ paths shipped alongside it in the export zip rather than
    embedding data. If the report is opened without extracting the zip
    first, each slot falls back to the filename and a note."""
    if not photos:
        return ""
    items = []
    for p in photos:
        rel = _esc_html(p["filename"].replace("\\", "/"))
        name = _esc_html(os.path.basename(p["filename"]))
        cls = "photo-item primary" if p.get("is_primary") else "photo-item"
        items.append(
            f'<div class="{cls}"><img src="{rel}" alt="Photo" '
            f"onerror=\"this.parentNode.className='{cls} missing';"
            f"this.parentNode.innerHTML='{name}&lt;br&gt;&lt;span&gt;Extract the zip first to see photos.&lt;/span&gt;';\">"
            f"</div>"
        )
    return f'<div class="photo-block"><h3>Photos</h3><div class="photo-grid">{"".join(items)}</div></div>'


def _render_document_block(attachments: list) -> str:
    """Documents are never embedded (arbitrary file types), so both the
    single export and the full report just list each label and size."""
    if not attachments:
        return ""
    items = "".join(
        f'<li>{_esc_html(a["label"])} <span class="doc-size">({format_size(a["size_bytes"])})</span></li>'
        for a in attachments
    )
    return f'<div class="document-block"><h3>Documents</h3><ul class="document-list">{items}</ul></div>'


def _build_single_export_html(f: dict, photos_with_data: list, attachments: list) -> str:
    title = f"{f['log_number']} - {f['make']} {f['model']}"
    body = (
        _render_identity_block(f)
        + _render_disposition_block(f)
        + _render_notes_block(f)
        + _render_photo_block_embedded(photos_with_data)
        + _render_document_block(attachments)
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc_html(title)}</title>
<style>{EXPORT_BASE_CSS}{PRINT_CSS}</style>
</head><body>
<div class="report">
<div class="firearm-card">{body}</div>
</div>
</body></html>"""


def _build_full_report_html(firearms: list, photos_by_firearm: dict, attachments_by_firearm: dict) -> str:
    cards = []
    for f in firearms:
        photos = photos_by_firearm.get(f["id"], [])
        attachments = attachments_by_firearm.get(f["id"], [])
        body = (
            _render_identity_block(f)
            + _render_disposition_block(f)
            + _render_notes_block(f)
            + _render_photo_block_relative(photos)
            + _render_document_block(attachments)
        )
        cards.append(f'<div class="firearm-card">{body}</div>')
    title = "Firearm Logbook Export"
    generated = datetime.date.today().isoformat()
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{_esc_html(title)}</title>
<style>{EXPORT_BASE_CSS}{PRINT_CSS}</style>
</head><body>
<div class="report">
<h1>{_esc_html(title)}</h1>
<div class="report-meta">Generated {generated}, {len(firearms)} firearm(s)</div>
{"".join(cards)}
</div>
</body></html>"""


def _build_csv_text(firearms: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Log Number", "Make", "Model", "Serial Number", "Type", "Sub-Type", "Caliber",
            "Acquisition Date", "Acquired From", "Purchase Price", "Estimated Value",
            "Insured Value", "Storage Location",
            "Held In Trust", "Trust Name", "NFA", "NFA Form Type", "NFA Stamp Date",
            "Notes",
            "Disposition Status", "Disposition Date", "Disposition To", "Disposition Address",
            "Disposition Amount", "Disposition Notes",
        ]
    )
    for f in firearms:
        writer.writerow(
            [
                f["log_number"], f["make"], f["model"], f["serial_number"], f["firearm_type"], f["sub_type"],
                f["caliber"],
                f["acquisition_date"], f["acquired_from"], f["purchase_price"], f["estimated_value"],
                f["insured_value"], f["storage_location"],
                "Yes" if f["held_in_trust"] else "No", f["trust_name"],
                "Yes" if f["is_nfa"] else "No", f["nfa_form_type"], f["nfa_stamp_date"],
                f["notes"],
                f["disposition_status"], f["disposition_date"], f["disposition_to"], f["disposition_address"],
                f["disposition_amount"], f["disposition_notes"],
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CSV import: parsing, header auto-mapping, and per-row validation. Pure
# functions with no db or pywebview dependency, so they are unit-testable
# directly; the Api methods below are thin wrappers that add the file
# picker, the database, and the transaction. Field order matches
# _build_csv_text's header, minus "Log Number": the app owns numbering, so
# an imported log number is read-only and never honored.
# ---------------------------------------------------------------------------
IMPORT_FIELDS = (
    ("make", "Make"),
    ("model", "Model"),
    ("serial_number", "Serial Number"),
    ("firearm_type", "Type"),
    ("sub_type", "Sub-Type"),
    ("caliber", "Caliber"),
    ("acquisition_date", "Acquisition Date"),
    ("acquired_from", "Acquired From"),
    ("purchase_price", "Purchase Price"),
    ("estimated_value", "Estimated Value"),
    ("insured_value", "Insured Value"),
    ("storage_location", "Storage Location"),
    ("held_in_trust", "Held In Trust"),
    ("trust_name", "Trust Name"),
    ("is_nfa", "NFA"),
    ("nfa_form_type", "NFA Form Type"),
    ("nfa_stamp_date", "NFA Stamp Date"),
    ("notes", "Notes"),
    ("disposition_status", "Disposition Status"),
    ("disposition_date", "Disposition Date"),
    ("disposition_to", "Disposition To"),
    ("disposition_address", "Disposition Address"),
    ("disposition_amount", "Disposition Amount"),
    ("disposition_notes", "Disposition Notes"),
)


def read_import_csv_rows(text: str):
    """Parse raw CSV text into a header row and data rows. A row whose
    column count does not match the header is flagged (column_mismatch)
    rather than silently shifted into the wrong fields; its cells are still
    returned as-is for display, but nothing downstream treats them as
    mapped data. Fully blank lines are skipped and not counted as rows.

    Returns (header: list[str], rows: list[dict]), each row dict shaped
    {"row_num": int (1-based, data rows only), "cells": list[str],
    "column_mismatch": bool}."""
    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)
    if not all_rows:
        return [], []
    header = all_rows[0]
    ncols = len(header)
    rows = []
    row_num = 0
    for cells in all_rows[1:]:
        if not cells or all((c or "").strip() == "" for c in cells):
            continue
        row_num += 1
        rows.append(
            {
                "row_num": row_num,
                "cells": cells,
                "column_mismatch": len(cells) != ncols,
            }
        )
    return header, rows


def guess_column_mapping(header: list) -> dict:
    """Case-insensitive match of header names to our import field keys, so
    a round-trip of our own export maps perfectly. Returns
    {field_key: column_index_or_None}."""
    lower_header = [(h or "").strip().lower() for h in header]
    mapping = {}
    for field_key, label in IMPORT_FIELDS:
        idx = None
        for i, h in enumerate(lower_header):
            if h == label.lower():
                idx = i
                break
        mapping[field_key] = idx
    return mapping


def _yes_no_to_bool(raw) -> bool:
    """The CSV's Yes/No convention for boolean columns. Anything other than
    a case-insensitive 'yes' (including blank) is treated as No."""
    return (raw or "").strip().lower() == "yes"


def _import_cell(cells: list, mapping: dict, field_key: str) -> str:
    idx = mapping.get(field_key)
    if idx is None or idx < 0 or idx >= len(cells):
        return ""
    return cells[idx] or ""


def build_import_record(cells: list, mapping: dict):
    """Validate and normalize one data row into a firearm record, routing
    every date and amount through the same validators the add screen uses.
    Returns (record_dict, None, None) on success, or (None, reason, cell)
    on the first failure, where cell is the offending field's label."""
    make_s = _import_cell(cells, mapping, "make").strip()
    if not make_s:
        return None, "Make is required.", "Make"
    model_s = _import_cell(cells, mapping, "model").strip()
    if not model_s:
        return None, "Model is required.", "Model"

    date_s, err = parse_iso_date_optional(_import_cell(cells, mapping, "acquisition_date"))
    if err:
        return None, err, "Acquisition Date"
    price_s, err = parse_decimal_optional(_import_cell(cells, mapping, "purchase_price"))
    if err:
        return None, err, "Purchase Price"
    value_s, err = parse_decimal_optional(_import_cell(cells, mapping, "estimated_value"))
    if err:
        return None, err, "Estimated Value"
    insured_s, err = parse_decimal_optional(_import_cell(cells, mapping, "insured_value"))
    if err:
        return None, err, "Insured Value"
    nfa_stamp_date_s, err = parse_iso_date_optional(_import_cell(cells, mapping, "nfa_stamp_date"))
    if err:
        return None, err, "NFA Stamp Date"
    disposition_date_s, err = parse_iso_date_optional(_import_cell(cells, mapping, "disposition_date"))
    if err:
        return None, err, "Disposition Date"
    disposition_amount_s, err = parse_decimal_optional(_import_cell(cells, mapping, "disposition_amount"))
    if err:
        return None, err, "Disposition Amount"

    disposition_status_s = _import_cell(cells, mapping, "disposition_status").strip() or "Owned"
    if disposition_status_s not in DISPOSITION_STATUSES:
        return None, "Enter a valid disposition status.", "Disposition Status"

    held_in_trust_i = 1 if _yes_no_to_bool(_import_cell(cells, mapping, "held_in_trust")) else 0
    trust_name_s = _import_cell(cells, mapping, "trust_name").strip()
    is_nfa_i = 1 if _yes_no_to_bool(_import_cell(cells, mapping, "is_nfa")) else 0
    nfa_form_type_s = _import_cell(cells, mapping, "nfa_form_type").strip()
    if not held_in_trust_i:
        trust_name_s = ""
    if not is_nfa_i:
        nfa_form_type_s = ""
        nfa_stamp_date_s = ""

    disposition_to_s = _import_cell(cells, mapping, "disposition_to").strip()
    disposition_address_s = _import_cell(cells, mapping, "disposition_address").strip()
    disposition_notes_s = _import_cell(cells, mapping, "disposition_notes").strip()
    if disposition_status_s == "Owned":
        disposition_date_s = disposition_to_s = disposition_address_s = ""
        disposition_amount_s = disposition_notes_s = ""

    record = {
        "make": make_s,
        "model": model_s,
        "serial_number": _import_cell(cells, mapping, "serial_number").strip(),
        "firearm_type": _import_cell(cells, mapping, "firearm_type").strip(),
        "sub_type": _import_cell(cells, mapping, "sub_type").strip(),
        "caliber": _import_cell(cells, mapping, "caliber").strip(),
        "acquisition_date": date_s,
        "acquired_from": _import_cell(cells, mapping, "acquired_from").strip(),
        "purchase_price": price_s,
        "estimated_value": value_s,
        "insured_value": insured_s,
        "storage_location": _import_cell(cells, mapping, "storage_location").strip(),
        "held_in_trust": held_in_trust_i,
        "trust_name": trust_name_s,
        "is_nfa": is_nfa_i,
        "nfa_form_type": nfa_form_type_s,
        "nfa_stamp_date": nfa_stamp_date_s,
        "notes": _import_cell(cells, mapping, "notes").strip(),
        "disposition_status": disposition_status_s,
        "disposition_date": disposition_date_s,
        "disposition_to": disposition_to_s,
        "disposition_address": disposition_address_s,
        "disposition_amount": disposition_amount_s,
        "disposition_notes": disposition_notes_s,
    }
    return record, None, None


def build_import_preview(rows: list, mapping: dict, existing_serials) -> list:
    """Runs every row through build_import_record and classifies it for the
    preview: "error" (wrong column count or a failed validator, never
    importable), "duplicate" (serial already in the collection, a soft
    warning the user decides on per row), or "ok"."""
    existing = {s for s in (existing_serials or []) if s}
    preview = []
    for row in rows:
        row_num = row["row_num"]
        if row["column_mismatch"]:
            preview.append(
                {
                    "row_num": row_num,
                    "status": "error",
                    "reason": "Wrong number of columns for this row.",
                    "cell": None,
                    "record": None,
                }
            )
            continue
        record, reason, cell = build_import_record(row["cells"], mapping)
        if record is None:
            preview.append(
                {"row_num": row_num, "status": "error", "reason": reason, "cell": cell, "record": None}
            )
            continue
        if record["serial_number"] and record["serial_number"] in existing:
            preview.append(
                {
                    "row_num": row_num,
                    "status": "duplicate",
                    "reason": "This serial number already exists in your collection.",
                    "cell": "Serial Number",
                    "record": record,
                }
            )
            continue
        preview.append({"row_num": row_num, "status": "ok", "reason": None, "cell": None, "record": record})
    return preview


def _insert_imported_firearm(cur, record: dict) -> str:
    """Insert one already-validated import record, honoring its disposition
    fields (unlike create_firearm, which always forces new firearms to
    'Owned'). Returns the freshly claimed log number."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    log_number = _get_next_log_number(cur)
    cur.execute(
        "INSERT INTO firearms (log_number, make, model, serial_number, firearm_type, caliber, "
        "acquisition_date, acquired_from, purchase_price, estimated_value, "
        "insured_value, storage_location, notes, "
        "sub_type, held_in_trust, trust_name, is_nfa, nfa_form_type, nfa_stamp_date, "
        "disposition_status, disposition_date, disposition_to, disposition_address, "
        "disposition_amount, disposition_notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            log_number, record["make"], record["model"], record["serial_number"], record["firearm_type"],
            record["caliber"], record["acquisition_date"], record["acquired_from"], record["purchase_price"],
            record["estimated_value"], record["insured_value"], record["storage_location"], record["notes"],
            record["sub_type"], record["held_in_trust"], record["trust_name"], record["is_nfa"],
            record["nfa_form_type"], record["nfa_stamp_date"],
            record["disposition_status"], record["disposition_date"], record["disposition_to"],
            record["disposition_address"], record["disposition_amount"], record["disposition_notes"],
            now, now,
        ),
    )
    return log_number


def _update_error_reason(exc: BaseException) -> str:
    """Turn a check_update exception into a short, plain-language reason to
    show in the UI. Pure and network-free: takes the already-raised exception,
    never touches the network itself.

    Each branch is specific to a failure that can actually cause it, and
    names a next step where there is a sensible one. Subclasses are checked
    before their parents: SSLCertVerificationError and SSLEOFError/
    SSLZeroReturnError before the generic ssl.SSLError, and the specific
    ConnectionError subclasses and socket.gaierror before the generic OSError
    branch (socket.timeout is an alias of TimeoutError, and both are OSError
    subclasses)."""
    # HTTPError is a URLError subclass but carries its own .code, so classify
    # it before unwrapping anything.
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403:
            return (
                "GitHub is rate-limiting update checks from this network. "
                "Try again later."
            )
        if exc.code == 404:
            return "No published release was found."
        if 500 <= exc.code < 600:
            return f"GitHub is having trouble on its end (HTTP {exc.code})."
        return f"GitHub returned an error (HTTP {exc.code})."

    if isinstance(exc, json.JSONDecodeError):
        return (
            "GitHub returned something unexpected. This often means a proxy "
            "or a guest wifi sign-in page answered instead."
        )

    # A plain URLError wraps the underlying cause (ssl.SSLError, socket.timeout,
    # a DNS/socket OSError, ...) in its .reason; unwrap it to classify the
    # actual cause, but remember it came from a URLError for the fallback below.
    is_url_error = isinstance(exc, urllib.error.URLError)
    cause = exc.reason if is_url_error and exc.reason is not None else exc

    if isinstance(cause, ssl.SSLCertVerificationError):
        return (
            "GitHub's certificate could not be verified. This usually means "
            "antivirus or a network filter is inspecting HTTPS traffic."
        )
    if isinstance(cause, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        return "The secure connection was cut off during the handshake with GitHub."
    if isinstance(cause, ssl.SSLError):
        return "The secure connection to GitHub failed."
    if isinstance(cause, socket.gaierror):
        return (
            "The address for api.github.com could not be looked up. Check "
            "DNS or the internet connection."
        )
    if isinstance(cause, (socket.timeout, TimeoutError)):
        return "GitHub didn't respond in time."
    if isinstance(cause, (ConnectionRefusedError, ConnectionResetError)):
        return (
            "The connection was refused or reset. A firewall or proxy may "
            "be blocking it."
        )
    if isinstance(cause, OSError) and getattr(cause, "errno", None) == errno.ENETUNREACH:
        return "No network connection."
    if is_url_error:
        return "Couldn't reach GitHub. Check the internet connection."

    text = f"{type(exc).__name__}: {exc}"
    if len(text) > 120:
        text = text[:117] + "..."
    return text


class Api:
    """Bridge exposed to the UI. Methods return JSON-able dicts; the UI awaits."""

    def __init__(self):
        self._window = None
        self._conn = None
        self._db_path = None
        self._debug = False
        self._debug_path = None

    def set_window(self, w):
        self._window = w

    def set_conn(self, conn: sqlite3.Connection):
        self._conn = conn

    def set_db_path(self, path: str):
        self._db_path = path

    def close_conn(self):
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass

    # --- config -------------------------------------------------------------
    def get_config(self):
        try:
            return {"ok": True, "version": APP_VERSION, "theme": self._load_theme()}
        except Exception as e:
            self.log(f"get_config failed: {e}")
            return {"ok": False, "error": "Couldn't load the app's configuration."}

    def get_autocomplete(self):
        """Make / caliber / type suggestions drawn from existing entries.
        Type suggestions always include the standard categories first."""
        try:
            cur = self._conn.cursor()
            makes = [
                r[0] for r in cur.execute(
                    "SELECT DISTINCT make FROM firearms WHERE make<>'' ORDER BY make COLLATE NOCASE"
                ).fetchall()
            ]
            calibers = [
                r[0] for r in cur.execute(
                    "SELECT DISTINCT caliber FROM firearms WHERE caliber<>'' ORDER BY caliber COLLATE NOCASE"
                ).fetchall()
            ]
            existing_types = [
                r[0] for r in cur.execute(
                    "SELECT DISTINCT firearm_type FROM firearms WHERE firearm_type<>'' ORDER BY firearm_type COLLATE NOCASE"
                ).fetchall()
            ]
            types = list(STANDARD_FIREARM_TYPES)
            for t in existing_types:
                if t not in types:
                    types.append(t)
            return {"ok": True, "makes": makes, "calibers": calibers, "types": types}
        except Exception as e:
            self.log(f"get_autocomplete failed: {e}")
            return {"ok": False, "error": "Couldn't load suggestions."}

    # --- firearms -------------------------------------------------------------
    def _get_firearm_row(self, firearm_id):
        return self._conn.execute("SELECT * FROM firearms WHERE id=?", (firearm_id,)).fetchone()

    def _get_photos(self, firearm_id) -> list:
        rows = self._conn.execute(
            "SELECT id, filename, seq, is_primary FROM photos WHERE firearm_id=? ORDER BY seq",
            (firearm_id,),
        ).fetchall()
        return [
            {"id": r["id"], "filename": r["filename"], "seq": r["seq"], "is_primary": bool(r["is_primary"])}
            for r in rows
        ]

    def _attachment_stats(self, firearm_id):
        row = self._conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(size_bytes), 0) AS b FROM attachments WHERE firearm_id=?",
            (firearm_id,),
        ).fetchone()
        return row["c"], row["b"]

    def list_firearms(self):
        try:
            rows = self._conn.execute("SELECT * FROM firearms ORDER BY log_number").fetchall()
            firearms = []
            for r in rows:
                d = _firearm_row_to_dict(r)
                photos = self._get_photos(r["id"])
                primary = next((p for p in photos if p["is_primary"]), photos[0] if photos else None)
                d["photo_count"] = len(photos)
                d["primary_photo_filename"] = primary["filename"] if primary else None
                attachment_count, attachment_bytes = self._attachment_stats(r["id"])
                d["attachment_count"] = attachment_count
                d["attachment_bytes"] = attachment_bytes
                firearms.append(d)
            return {"ok": True, "firearms": firearms}
        except Exception as e:
            self.log(f"list_firearms failed: {e}")
            return {"ok": False, "error": "Couldn't load the firearms list."}

    def get_firearm(self, firearm_id):
        try:
            row = self._get_firearm_row(firearm_id)
            if row is None:
                return {"ok": False, "error": "That firearm no longer exists."}
            return {
                "ok": True,
                "firearm": _firearm_row_to_dict(row),
                "photos": self._get_photos(firearm_id),
                "attachments": self._get_attachments(firearm_id),
            }
        except Exception as e:
            self.log(f"get_firearm failed: {e}")
            return {"ok": False, "error": "Couldn't load that firearm."}

    def create_firearm(self, make, model, serial_number="", firearm_type="", caliber="",
                        acquisition_date="", acquired_from="", purchase_price="",
                        estimated_value="", insured_value="", storage_location="", notes="",
                        sub_type="", held_in_trust=0, trust_name="", is_nfa=0,
                        nfa_form_type="", nfa_stamp_date=""):
        try:
            make_s = (make or "").strip()
            model_s = (model or "").strip()
            if not make_s or not model_s:
                return {"ok": False, "error": "Make and model are required."}
            date_s, err = parse_iso_date_optional(acquisition_date)
            if err:
                return {"ok": False, "error": err}
            price_s, err = parse_decimal_optional(purchase_price)
            if err:
                return {"ok": False, "error": err}
            value_s, err = parse_decimal_optional(estimated_value)
            if err:
                return {"ok": False, "error": err}
            insured_s, err = parse_decimal_optional(insured_value)
            if err:
                return {"ok": False, "error": err}
            nfa_stamp_date_s, err = parse_iso_date_optional(nfa_stamp_date)
            if err:
                return {"ok": False, "error": err}
            serial_s = (serial_number or "").strip()
            type_s = (firearm_type or "").strip()
            caliber_s = (caliber or "").strip()
            acquired_from_s = (acquired_from or "").strip()
            storage_s = (storage_location or "").strip()
            notes_s = (notes or "").strip()
            sub_type_s = (sub_type or "").strip()
            held_in_trust_i = 1 if held_in_trust else 0
            trust_name_s = (trust_name or "").strip()
            is_nfa_i = 1 if is_nfa else 0
            nfa_form_type_s = (nfa_form_type or "").strip()
            if not held_in_trust_i:
                trust_name_s = ""
            if not is_nfa_i:
                nfa_form_type_s = ""
                nfa_stamp_date_s = ""
            now = datetime.datetime.now().isoformat(timespec="seconds")
            cur = self._conn.cursor()
            log_number = _get_next_log_number(cur)
            cur.execute(
                "INSERT INTO firearms (log_number, make, model, serial_number, firearm_type, caliber, "
                "acquisition_date, acquired_from, purchase_price, estimated_value, "
                "insured_value, storage_location, notes, "
                "sub_type, held_in_trust, trust_name, is_nfa, nfa_form_type, nfa_stamp_date, "
                "disposition_status, disposition_date, disposition_to, disposition_address, "
                "disposition_amount, disposition_notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'Owned', '', '', '', '', '', ?, ?)",
                (log_number, make_s, model_s, serial_s, type_s, caliber_s, date_s, acquired_from_s,
                 price_s, value_s, insured_s, storage_s, notes_s,
                 sub_type_s, held_in_trust_i, trust_name_s, is_nfa_i, nfa_form_type_s, nfa_stamp_date_s,
                 now, now),
            )
            new_id = cur.lastrowid
            self._conn.commit()
            self.log(f"Firearm {log_number} created")
            return {"ok": True, "firearm_id": new_id, "log_number": log_number}
        except Exception as e:
            self._conn.rollback()
            self.log(f"create_firearm failed: {e}")
            return {"ok": False, "error": "Couldn't save the firearm."}

    def update_firearm(self, firearm_id, make, model, serial_number="", firearm_type="", caliber="",
                        acquisition_date="", acquired_from="", purchase_price="",
                        estimated_value="", insured_value="", storage_location="", notes="",
                        sub_type="", held_in_trust=0, trust_name="", is_nfa=0,
                        nfa_form_type="", nfa_stamp_date=""):
        """Edits identity/notes fields only; log number and disposition are
        untouched (disposition has its own editing action)."""
        try:
            row = self._get_firearm_row(firearm_id)
            if row is None:
                return {"ok": False, "error": "That firearm no longer exists."}
            make_s = (make or "").strip()
            model_s = (model or "").strip()
            if not make_s or not model_s:
                return {"ok": False, "error": "Make and model are required."}
            date_s, err = parse_iso_date_optional(acquisition_date)
            if err:
                return {"ok": False, "error": err}
            price_s, err = parse_decimal_optional(purchase_price)
            if err:
                return {"ok": False, "error": err}
            value_s, err = parse_decimal_optional(estimated_value)
            if err:
                return {"ok": False, "error": err}
            insured_s, err = parse_decimal_optional(insured_value)
            if err:
                return {"ok": False, "error": err}
            nfa_stamp_date_s, err = parse_iso_date_optional(nfa_stamp_date)
            if err:
                return {"ok": False, "error": err}
            serial_s = (serial_number or "").strip()
            type_s = (firearm_type or "").strip()
            caliber_s = (caliber or "").strip()
            acquired_from_s = (acquired_from or "").strip()
            storage_s = (storage_location or "").strip()
            notes_s = (notes or "").strip()
            sub_type_s = (sub_type or "").strip()
            held_in_trust_i = 1 if held_in_trust else 0
            trust_name_s = (trust_name or "").strip()
            is_nfa_i = 1 if is_nfa else 0
            nfa_form_type_s = (nfa_form_type or "").strip()
            if not held_in_trust_i:
                trust_name_s = ""
            if not is_nfa_i:
                nfa_form_type_s = ""
                nfa_stamp_date_s = ""
            now = datetime.datetime.now().isoformat(timespec="seconds")
            self._conn.execute(
                "UPDATE firearms SET make=?, model=?, serial_number=?, firearm_type=?, caliber=?, "
                "acquisition_date=?, acquired_from=?, purchase_price=?, estimated_value=?, "
                "insured_value=?, storage_location=?, notes=?, "
                "sub_type=?, held_in_trust=?, trust_name=?, is_nfa=?, nfa_form_type=?, nfa_stamp_date=?, "
                "updated_at=? WHERE id=?",
                (make_s, model_s, serial_s, type_s, caliber_s, date_s, acquired_from_s,
                 price_s, value_s, insured_s, storage_s, notes_s,
                 sub_type_s, held_in_trust_i, trust_name_s, is_nfa_i, nfa_form_type_s, nfa_stamp_date_s,
                 now, firearm_id),
            )
            self._conn.commit()
            self.log(f"Firearm {row['log_number']} updated")
            return {"ok": True}
        except Exception as e:
            self._conn.rollback()
            self.log(f"update_firearm failed: {e}")
            return {"ok": False, "error": "Couldn't save the firearm."}

    def update_disposition(self, firearm_id, status, date="", to="", address="", amount="", notes=""):
        """Record or clear a disposition. Setting the status back to Owned
        clears the rest of the disposition fields, since the UI hides them
        once a firearm is owned again."""
        try:
            row = self._get_firearm_row(firearm_id)
            if row is None:
                return {"ok": False, "error": "That firearm no longer exists."}
            status_s = (status or "Owned").strip()
            if status_s not in DISPOSITION_STATUSES:
                return {"ok": False, "error": "Choose a valid disposition status."}
            if status_s == "Owned":
                date_s = to_s = address_s = amount_s = notes_s = ""
            else:
                date_s, err = parse_iso_date_optional(date)
                if err:
                    return {"ok": False, "error": err}
                amount_s, err = parse_decimal_optional(amount)
                if err:
                    return {"ok": False, "error": err}
                to_s = (to or "").strip()
                address_s = (address or "").strip()
                notes_s = (notes or "").strip()
            now = datetime.datetime.now().isoformat(timespec="seconds")
            self._conn.execute(
                "UPDATE firearms SET disposition_status=?, disposition_date=?, disposition_to=?, "
                "disposition_address=?, disposition_amount=?, disposition_notes=?, updated_at=? WHERE id=?",
                (status_s, date_s, to_s, address_s, amount_s, notes_s, now, firearm_id),
            )
            self._conn.commit()
            self.log(f"Disposition for firearm {row['log_number']} set to {status_s}")
            return {"ok": True}
        except Exception as e:
            self._conn.rollback()
            self.log(f"update_disposition failed: {e}")
            return {"ok": False, "error": "Couldn't save the disposition."}

    def delete_firearm(self, firearm_id, export_backup_first=False):
        """Delete a firearm and its photo and document files. The log number
        is never reissued. If export_backup_first is set, a backup zip is
        produced (with its own save dialog) before anything is deleted;
        cancelling that save dialog cancels the whole delete."""
        try:
            row = self._get_firearm_row(firearm_id)
            if row is None:
                return {"ok": False, "error": "That firearm no longer exists."}
            if export_backup_first:
                backup_result = self._export_single_backup_zip_internal(firearm_id)
                if not backup_result.get("ok") or backup_result.get("cancelled"):
                    return backup_result
            photos = self._conn.execute(
                "SELECT filename FROM photos WHERE firearm_id=?", (firearm_id,)
            ).fetchall()
            attachments = self._conn.execute(
                "SELECT filename FROM attachments WHERE firearm_id=?", (firearm_id,)
            ).fetchall()
            cur = self._conn.cursor()
            cur.execute("DELETE FROM photos WHERE firearm_id=?", (firearm_id,))
            # Foreign keys are enforced (PRAGMA foreign_keys = ON), so attachment
            # rows must go before the firearm row, same as photos.
            cur.execute("DELETE FROM attachments WHERE firearm_id=?", (firearm_id,))
            cur.execute("DELETE FROM firearms WHERE id=?", (firearm_id,))
            self._conn.commit()
            for p in photos:
                full = _safe_photo_path(p["filename"])
                if full:
                    try:
                        os.remove(full)
                    except Exception:
                        pass
            for a in attachments:
                full = _safe_attachment_path(a["filename"])
                if full:
                    try:
                        os.remove(full)
                    except Exception:
                        pass
            self.log(f"Firearm {row['log_number']} deleted")
            return {"ok": True}
        except Exception as e:
            self._conn.rollback()
            self.log(f"delete_firearm failed: {e}")
            return {"ok": False, "error": "Couldn't delete the firearm."}

    # --- photos -----------------------------------------------------------------
    def _import_photo_sources(self, firearm_id, row, cur, seq, has_primary, sources):
        """Shared photo import loop, used by both add_photos (a file path per
        source) and add_photos_from_data (raw bytes per source), so the naming
        scheme, sequence numbering, and failure handling never drift between
        the two entry points.

        Each item in `sources` is a dict:
          - "opener": what optimize_image_to_jpeg can open (a path or a
            file-like object), or None if the source already failed before
            reaching that step (bad extension, undecodable data, etc.);
          - "too_large": True if the source has already been measured and is
            over MAX_IMAGE_BYTES, so it must be refused before it is opened;
          - "label": a name to use in log lines;
          - "skip_reason": only used when "opener" is None, a short phrase
            describing why for the log line.

        Creates photos\\ if needed, inserts one row per successfully imported
        source under the {lognum}_{seq}.jpg naming scheme, and makes the first
        photo added primary if the firearm has none yet. A source that fails
        never burns a sequence number. Does not commit: the caller commits
        once after this returns. Returns (added, failed, seq, has_primary,
        fail_counts), where fail_counts is a {"not_image": n, "damaged": n,
        "too_large": n} dict tallying why each refused source failed.
        """
        photos_dir = os.path.join(app_dir(), PHOTOS_DIRNAME)
        os.makedirs(photos_dir, exist_ok=True)

        added = 0
        failed = 0
        fail_counts = {"not_image": 0, "damaged": 0, "too_large": 0}
        for source in sources:
            label = source.get("label", "")
            if source.get("opener") is None:
                # Already failed before reaching the optimize step: never
                # burns a sequence number.
                failed += 1
                fail_counts[source.get("category", "damaged")] += 1
                reason = source.get("skip_reason", "unreadable")
                self.log(f"Photo skipped, {reason}: {label}")
                continue
            if source.get("too_large"):
                # Refuse to even open it: never burns a sequence number.
                failed += 1
                fail_counts["too_large"] += 1
                self.log(f"Photo skipped, over the size limit: {label}")
                continue
            seq += 1
            target_name = f"{row['log_number']}_{seq}.jpg"
            target_full = os.path.join(photos_dir, target_name)
            try:
                optimize_image_to_jpeg(source["opener"], target_full)
            except Exception as e:
                # Never store a broken or half-written photo, and don't burn
                # a sequence number on one that didn't make it in.
                seq -= 1
                failed += 1
                fail_counts["damaged"] += 1
                self.log(f"Photo optimize failed for {label}: {e}")
                try:
                    if os.path.exists(target_full):
                        os.remove(target_full)
                except Exception:
                    pass
                continue
            rel_name = f"{PHOTOS_DIRNAME}/{target_name}"
            is_primary = 1 if not has_primary else 0
            cur.execute(
                "INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (?, ?, ?, ?)",
                (firearm_id, rel_name, seq, is_primary),
            )
            if is_primary:
                has_primary = True
            added += 1
        return added, failed, seq, has_primary, fail_counts

    def add_photos(self, firearm_id):
        """Opens a native multi-select file picker, copies each chosen image
        into photos\\ under the {lognum}_{seq} naming scheme, and inserts a
        row per photo. The first photo added becomes primary if none is set."""
        try:
            row = self._get_firearm_row(firearm_id)
            if row is None:
                return {"ok": False, "error": "That firearm no longer exists."}
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=True,
                file_types=("Image Files (*.jpg;*.jpeg;*.png;*.bmp;*.gif;*.webp;*.tif;*.tiff)",),
            )
            if not result:
                return {"ok": True, "cancelled": True}
            paths = list(result) if isinstance(result, (list, tuple)) else [result]

            cur = self._conn.cursor()
            seq = cur.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM photos WHERE firearm_id=?", (firearm_id,)
            ).fetchone()[0]
            has_primary = cur.execute(
                "SELECT COUNT(*) FROM photos WHERE firearm_id=? AND is_primary=1", (firearm_id,)
            ).fetchone()[0] > 0

            # A source with an unsupported extension is filtered out here,
            # silently, same as before the refactor: the file picker's own
            # filter already keeps this from happening in normal use.
            sources = []
            for src in paths:
                if not src or not os.path.isfile(src):
                    continue
                ext = os.path.splitext(src)[1].lower()
                if ext not in IMAGE_EXTENSIONS:
                    continue
                sources.append({
                    "opener": src,
                    "too_large": _photo_source_too_large(src),
                    "label": src,
                })

            added, failed, seq, has_primary, fail_counts = self._import_photo_sources(
                firearm_id, row, cur, seq, has_primary, sources
            )
            self._conn.commit()
            self.log(f"Added {added} photo(s) to firearm {row['log_number']}"
                     + (f", {failed} failed" if failed else ""))
            result = {"ok": True, "photos": self._get_photos(firearm_id), "added": added}
            # No silent failures: tell the user some photos didn't make it.
            warning = photo_failure_warning(fail_counts)
            if warning:
                result["warning"] = warning
            return result
        except Exception as e:
            self._conn.rollback()
            self.log(f"add_photos failed: {e}")
            return {"ok": False, "error": "Couldn't add the photo(s)."}

    def add_photos_from_data(self, firearm_id, files):
        """Imports photos from raw bytes instead of a file path, for a
        browser drag-and-drop drop of image data. `files` is a list of
        {"name": <original filename>, "data": <base64-encoded contents>}
        dicts. Shares the naming scheme, sequence numbering, and
        first-photo-becomes-primary rule with add_photos via
        _import_photo_sources, so nothing can drift between the two."""
        try:
            row = self._get_firearm_row(firearm_id)
            if row is None:
                return {"ok": False, "error": "That firearm no longer exists."}

            cur = self._conn.cursor()
            seq = cur.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM photos WHERE firearm_id=?", (firearm_id,)
            ).fetchone()[0]
            has_primary = cur.execute(
                "SELECT COUNT(*) FROM photos WHERE firearm_id=? AND is_primary=1", (firearm_id,)
            ).fetchone()[0] > 0

            sources = []
            for f in files or []:
                f = f or {}
                name = f.get("name") or ""
                label = name or "(unnamed)"
                ext = os.path.splitext(name)[1].lower()
                if ext not in IMAGE_EXTENSIONS:
                    sources.append({
                        "opener": None, "label": label, "category": "not_image",
                        "skip_reason": "not a supported image type",
                    })
                    continue
                raw = f.get("data")
                if not raw:
                    sources.append({
                        "opener": None, "label": label, "category": "damaged",
                        "skip_reason": "no data received",
                    })
                    continue
                try:
                    data = base64.b64decode(raw, validate=True)
                except Exception:
                    sources.append({
                        "opener": None, "label": label, "category": "damaged",
                        "skip_reason": "couldn't decode",
                    })
                    continue
                sources.append({
                    "opener": io.BytesIO(data),
                    "too_large": _photo_bytes_too_large(data),
                    "label": label,
                })

            added, failed, seq, has_primary, fail_counts = self._import_photo_sources(
                firearm_id, row, cur, seq, has_primary, sources
            )
            self._conn.commit()
            self.log(f"Added {added} photo(s) to firearm {row['log_number']}"
                     + (f", {failed} failed" if failed else ""))
            result = {"ok": True, "photos": self._get_photos(firearm_id), "added": added}
            # No silent failures: tell the user some photos didn't make it.
            warning = photo_failure_warning(fail_counts)
            if warning:
                result["warning"] = warning
            return result
        except Exception as e:
            self._conn.rollback()
            self.log(f"add_photos_from_data failed: {e}")
            return {"ok": False, "error": "Couldn't add the photo(s)."}

    def delete_photo(self, photo_id):
        try:
            row = self._conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "That photo no longer exists."}
            firearm_id = row["firearm_id"]
            was_primary = bool(row["is_primary"])
            full = _safe_photo_path(row["filename"])
            cur = self._conn.cursor()
            cur.execute("DELETE FROM photos WHERE id=?", (photo_id,))
            if was_primary:
                nxt = cur.execute(
                    "SELECT id FROM photos WHERE firearm_id=? ORDER BY seq LIMIT 1", (firearm_id,)
                ).fetchone()
                if nxt:
                    cur.execute("UPDATE photos SET is_primary=1 WHERE id=?", (nxt["id"],))
            self._conn.commit()
            if full:
                try:
                    os.remove(full)
                except Exception:
                    pass
            self.log(f"Deleted photo {photo_id}")
            return {"ok": True, "photos": self._get_photos(firearm_id)}
        except Exception as e:
            self._conn.rollback()
            self.log(f"delete_photo failed: {e}")
            return {"ok": False, "error": "Couldn't delete the photo."}

    def set_primary_photo(self, photo_id):
        try:
            row = self._conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "That photo no longer exists."}
            firearm_id = row["firearm_id"]
            cur = self._conn.cursor()
            cur.execute("UPDATE photos SET is_primary=0 WHERE firearm_id=?", (firearm_id,))
            cur.execute("UPDATE photos SET is_primary=1 WHERE id=?", (photo_id,))
            self._conn.commit()
            self.log(f"Primary photo set for firearm {firearm_id}")
            return {"ok": True, "photos": self._get_photos(firearm_id)}
        except Exception as e:
            self._conn.rollback()
            self.log(f"set_primary_photo failed: {e}")
            return {"ok": False, "error": "Couldn't set the primary photo."}

    def get_photo_data(self, filename):
        """Returns a photo's bytes as a base64 data URI for in-app display.
        Images are stored next to the exe (not bundled), so this is the one
        reliable way to show them from a UI file that may itself be running
        out of a PyInstaller temp folder."""
        try:
            full = _safe_photo_path(filename)
            if not full or not os.path.isfile(full):
                return {"ok": False, "error": "That photo file is missing."}
            with open(full, "rb") as f:
                data = f.read()
            mime = _sniff_image_mime(data)
            b64 = base64.b64encode(data).decode("ascii")
            return {"ok": True, "data_uri": f"data:{mime};base64,{b64}"}
        except Exception as e:
            self.log(f"get_photo_data failed: {e}")
            return {"ok": False, "error": "Couldn't load that photo."}

    def _photo_with_data(self, p: dict) -> dict:
        full = _safe_photo_path(p["filename"])
        if not full or not os.path.isfile(full):
            return {**p, "data_uri": None}
        try:
            with open(full, "rb") as fh:
                data = fh.read()
            mime = _sniff_image_mime(data)
            b64 = base64.b64encode(data).decode("ascii")
            return {**p, "data_uri": f"data:{mime};base64,{b64}"}
        except Exception:
            return {**p, "data_uri": None}

    # --- attachments --------------------------------------------------------
    def _get_attachments(self, firearm_id) -> list:
        rows = self._conn.execute(
            "SELECT id, filename, label, seq, size_bytes FROM attachments WHERE firearm_id=? ORDER BY seq",
            (firearm_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "filename": r["filename"],
                "label": r["label"],
                "seq": r["seq"],
                "size_bytes": r["size_bytes"],
            }
            for r in rows
        ]

    def choose_attachments(self):
        """Step one of the two-step add flow: opens a native multi-select file
        picker (any file type) and reports back name/size/too_large for each
        chosen file, without copying anything yet."""
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=True,
                file_types=("All files (*.*)",),
            )
            if not result:
                return {"ok": True, "cancelled": True}
            paths = list(result) if isinstance(result, (list, tuple)) else [result]
            files = []
            for p in paths:
                if not p or not os.path.isfile(p):
                    continue
                size_bytes = os.path.getsize(p)
                files.append(
                    {
                        "path": p,
                        "name": os.path.basename(p),
                        "size_bytes": size_bytes,
                        "too_large": size_bytes > ATTACHMENT_WARN_BYTES,
                    }
                )
            return {"ok": True, "files": files}
        except Exception as e:
            self.log(f"choose_attachments failed: {e}")
            return {"ok": False, "error": "Couldn't open the file picker."}

    def add_attachments(self, firearm_id, paths):
        """Copies the given file paths into attachments\\ verbatim (never
        opened, recompressed, or validated as images) under the
        {lognum}_{seq} naming scheme, and inserts a row per file with a
        default label built from the original filename."""
        try:
            row = self._get_firearm_row(firearm_id)
            if row is None:
                return {"ok": False, "error": "That firearm no longer exists."}

            attachments_dir = os.path.join(app_dir(), ATTACHMENTS_DIRNAME)
            os.makedirs(attachments_dir, exist_ok=True)
            cur = self._conn.cursor()
            seq = cur.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM attachments WHERE firearm_id=?", (firearm_id,)
            ).fetchone()[0]

            added = 0
            failed = 0
            for src in paths or []:
                if not src or not os.path.isfile(src):
                    # Re-validate here: the picker ran earlier and the file may
                    # have vanished since.
                    failed += 1
                    continue
                ext = os.path.splitext(src)[1].lower()
                seq += 1
                target_name = f"{row['log_number']}_{seq}{ext}"
                target_full = os.path.join(attachments_dir, target_name)
                try:
                    shutil.copy2(src, target_full)
                    size_bytes = os.path.getsize(target_full)
                except Exception as e:
                    # Never store a half-written file, and don't burn a
                    # sequence number on one that didn't make it in.
                    seq -= 1
                    failed += 1
                    self.log(f"Attachment copy failed for {src}: {e}")
                    try:
                        if os.path.exists(target_full):
                            os.remove(target_full)
                    except Exception:
                        pass
                    continue
                label = _sanitize_attachment_label(os.path.basename(src))
                rel_name = f"{ATTACHMENTS_DIRNAME}/{target_name}"
                cur.execute(
                    "INSERT INTO attachments (firearm_id, filename, label, seq, size_bytes) VALUES (?, ?, ?, ?, ?)",
                    (firearm_id, rel_name, label, seq, size_bytes),
                )
                added += 1
            self._conn.commit()
            self.log(f"Added {added} attachment(s) to firearm {row['log_number']}"
                     + (f", {failed} failed" if failed else ""))
            result = {"ok": True, "attachments": self._get_attachments(firearm_id), "added": added}
            if failed:
                # No silent failures: tell the user some files didn't make it.
                result["warning"] = f"{failed} file(s) couldn't be added."
            return result
        except Exception as e:
            self._conn.rollback()
            self.log(f"add_attachments failed: {e}")
            return {"ok": False, "error": "Couldn't add the file(s)."}

    def rename_attachment(self, attachment_id, new_label):
        try:
            row = self._conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "That document no longer exists."}
            label = _sanitize_attachment_label(new_label)
            if not label:
                return {"ok": False, "error": "Enter a name."}
            self._conn.execute("UPDATE attachments SET label=? WHERE id=?", (label, attachment_id))
            self._conn.commit()
            self.log(f"Renamed attachment {attachment_id}")
            return {"ok": True, "attachments": self._get_attachments(row["firearm_id"])}
        except Exception as e:
            self._conn.rollback()
            self.log(f"rename_attachment failed: {e}")
            return {"ok": False, "error": "Couldn't rename the document."}

    def delete_attachment(self, attachment_id):
        try:
            row = self._conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            if row is None:
                return {"ok": False, "error": "That document no longer exists."}
            firearm_id = row["firearm_id"]
            full = _safe_attachment_path(row["filename"])
            self._conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
            self._conn.commit()
            if full:
                try:
                    os.remove(full)
                except Exception:
                    pass
            self.log(f"Deleted attachment {attachment_id}")
            return {"ok": True, "attachments": self._get_attachments(firearm_id)}
        except Exception as e:
            self._conn.rollback()
            self.log(f"delete_attachment failed: {e}")
            return {"ok": False, "error": "Couldn't delete the document."}

    def get_attachment_totals(self):
        """Global disk-use total across every firearm's documents, summed
        from the stored size_bytes column (no filesystem scan)."""
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(size_bytes), 0) AS b FROM attachments"
            ).fetchone()
            return {"ok": True, "total_bytes": row["b"], "count": row["c"]}
        except Exception as e:
            self.log(f"get_attachment_totals failed: {e}")
            return {"ok": False, "error": "Couldn't load the document totals."}

    # --- exports ------------------------------------------------------------
    def export_single_html(self, firearm_id):
        """Self-contained single-firearm export: one HTML file with photos
        embedded as base64 data URIs, so it can be opened or shared standalone."""
        try:
            row = self._get_firearm_row(firearm_id)
            if row is None:
                return {"ok": False, "error": "That firearm no longer exists."}
            f = _firearm_row_to_dict(row)
            photos = [self._photo_with_data(p) for p in self._get_photos(firearm_id)]
            attachments = self._get_attachments(firearm_id)
            html = _build_single_export_html(f, photos, attachments)
            default_name = sanitize_filename(f"{f['log_number']} {f['make']} {f['model']}.html")
            result = self._window.create_file_dialog(
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
            self.log(f"Exported single firearm HTML for {f['log_number']}")
            return {"ok": True, "path": path}
        except Exception as e:
            self.log(f"export_single_html failed: {e}")
            return {"ok": False, "error": "Couldn't export the HTML file."}

    def export_single_backup_zip(self, firearm_id):
        return self._export_single_backup_zip_internal(firearm_id)

    def _export_single_backup_zip_internal(self, firearm_id):
        """Zip containing the self-contained HTML plus the original photo
        files. Shared by the standalone export action and the delete flow's
        'export backup first' option."""
        try:
            row = self._get_firearm_row(firearm_id)
            if row is None:
                return {"ok": False, "error": "That firearm no longer exists."}
            f = _firearm_row_to_dict(row)
            photos = self._get_photos(firearm_id)
            attachments = self._get_attachments(firearm_id)
            html = _build_single_export_html(f, [self._photo_with_data(p) for p in photos], attachments)
            default_name = sanitize_filename(f"{f['log_number']} {f['make']} {f['model']} backup.zip")
            result = self._window.create_file_dialog(
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
                    full = _safe_photo_path(p["filename"])
                    if full and os.path.isfile(full):
                        zf.write(full, arcname=os.path.basename(p["filename"]))
                # Attachments are always fully included, unlike photos: they're
                # the point of a backup. Kept in their own folder in the zip
                # rather than flattened, since two files can share a basename.
                for a in attachments:
                    full = _safe_attachment_path(a["filename"])
                    if full and os.path.isfile(full):
                        zf.write(full, arcname=f"attachments/{os.path.basename(a['filename'])}")
            self.log(f"Exported backup zip for firearm {f['log_number']}")
            return {"ok": True, "path": path}
        except Exception as e:
            self.log(f"export_single_backup_zip failed: {e}")
            return {"ok": False, "error": "Couldn't create the backup zip."}

    def export_full(self, photo_depth="primary"):
        """Full collection export: one zip with an all-firearms HTML report,
        a CSV of all fields, a photos\\ folder, and an attachments\\ folder.
        photo_depth controls how many photos per firearm are included:
        'primary', 'all', or 'none'. Documents have no depth selector and are
        always fully included; they're the point of the backup."""
        try:
            if photo_depth not in ("primary", "all", "none"):
                photo_depth = "primary"
            rows = self._conn.execute("SELECT * FROM firearms ORDER BY log_number").fetchall()
            firearms = [_firearm_row_to_dict(r) for r in rows]

            photos_by_firearm = {}
            attachments_by_firearm = {}
            files_to_include = []
            for fd in firearms:
                photos = self._get_photos(fd["id"])
                if photo_depth == "none":
                    chosen = []
                elif photo_depth == "primary":
                    primary = [p for p in photos if p["is_primary"]]
                    chosen = primary[:1] if primary else (photos[:1] if photos else [])
                else:
                    chosen = photos
                photos_by_firearm[fd["id"]] = chosen
                files_to_include.extend(p["filename"] for p in chosen)

                attachments = self._get_attachments(fd["id"])
                attachments_by_firearm[fd["id"]] = attachments
                files_to_include.extend(a["filename"] for a in attachments)

            html = _build_full_report_html(firearms, photos_by_firearm, attachments_by_firearm)
            csv_text = _build_csv_text(firearms)

            stamp = datetime.date.today().strftime("%Y%m%d")
            default_name = f"Firearm_Logbook_Export_{stamp}.zip"
            result = self._window.create_file_dialog(
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
                zf.writestr("data.csv", "\ufeff" + csv_text)
                for filename in files_to_include:
                    full = _safe_photo_path(filename) if filename.startswith(PHOTOS_DIRNAME + "/") \
                        else _safe_attachment_path(filename)
                    if full and os.path.isfile(full):
                        zf.write(full, arcname=filename.replace("\\", "/"))
            self.log(f"Full export created, photo depth={photo_depth}, {len(firearms)} firearm(s)")
            return {"ok": True, "path": path}
        except Exception as e:
            self.log(f"export_full failed: {e}")
            return {"ok": False, "error": "Couldn't create the export."}

    # --- CSV import -----------------------------------------------------------
    def import_csv_pick(self):
        """Stage 1: native picker restricted to .csv, read as UTF-8 with a
        BOM tolerated (our own export and a plain spreadsheet "Save As CSV"
        both open cleanly), and parse into a header plus raw rows. Nothing
        touches the database here; the page shows a mapping step next."""
        try:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, file_types=("CSV Files (*.csv)",)
            )
            if not result:
                return {"ok": True, "cancelled": True}
            path = result[0] if isinstance(result, (list, tuple)) else result
            if not path:
                return {"ok": True, "cancelled": True}
            try:
                with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                    text = fh.read()
            except UnicodeDecodeError:
                return {"ok": False, "error": "That file isn't valid UTF-8 text."}
            header, rows = read_import_csv_rows(text)
            if not header:
                return {"ok": False, "error": "That CSV file is empty."}
            mapping = guess_column_mapping(header)
            self.log(f"CSV import file picked, {len(rows)} row(s)")
            return {
                "ok": True,
                "header": header,
                "rows": rows,
                "mapping": mapping,
                "fields": [{"key": k, "label": v} for k, v in IMPORT_FIELDS],
            }
        except Exception as e:
            self.log(f"import_csv_pick failed: {e}")
            return {"ok": False, "error": "Couldn't read that CSV file."}

    def import_csv_preview(self, rows, mapping):
        """Stage 2: run every row through the shared validators and classify
        it as ok / duplicate-serial (soft warning) / error (specific reason
        and offending cell). Nothing touches the database except a read of
        existing serials, used only to flag duplicates."""
        try:
            existing_serials = {
                r[0] for r in self._conn.execute(
                    "SELECT serial_number FROM firearms WHERE serial_number<>''"
                ).fetchall()
            }
            preview = build_import_preview(rows or [], mapping or {}, existing_serials)
            return {"ok": True, "preview": preview}
        except Exception as e:
            self.log(f"import_csv_preview failed: {e}")
            return {"ok": False, "error": "Couldn't validate that file."}

    def import_csv_commit(self, records):
        """Stage 3: import only the rows the user accepted, in a single
        transaction: all succeed or the whole import rolls back, so a
        half-import is impossible. Each row gets a fresh app-assigned log
        number; an imported Log Number column is never honored."""
        try:
            records = records or []
            if not records:
                return {"ok": False, "error": "No rows were selected to import."}
            cur = self._conn.cursor()
            imported = []
            for record in records:
                log_number = _insert_imported_firearm(cur, record)
                imported.append(log_number)
            self._conn.commit()
            self.log(f"CSV import committed, {len(imported)} firearm(s)")
            return {"ok": True, "imported": len(imported), "log_numbers": imported}
        except Exception as e:
            self._conn.rollback()
            self.log(f"import_csv_commit failed: {e}")
            return {"ok": False, "error": "Couldn't import the file. Nothing was saved."}

    # --- preferences (local file, not stored in the db) ----------------------
    def _load_theme(self) -> str:
        theme = load_prefs().get("theme")
        return theme if theme in ("dark", "light") else "dark"

    def get_theme(self):
        return self._load_theme()

    def save_theme(self, theme: str):
        if theme not in ("dark", "light"):
            return {"ok": False}
        prefs = load_prefs()
        prefs["theme"] = theme
        if save_prefs(prefs):
            self.log(f"Theme set to {theme}")
            return {"ok": True}
        self.log("Could not save theme pref")
        return {"ok": False}

    # --- misc bridge helpers --------------------------------------------------
    def open_url(self, url: str):
        """Open a link in the system browser, never by navigating the app window."""
        import webbrowser

        webbrowser.open(url)
        return {"ok": True}

    def check_update(self):
        """Compare the latest published release to APP_VERSION. Quiet in the UI on
        failure (see _update_error_reason), but always logged when debug is on."""
        result = {"current": APP_VERSION, "version": None, "update": False, "offline": False}
        try:
            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.load(r)
            latest = (data.get("tag_name") or "").lstrip("v")
            result["version"] = latest
            if latest and self._is_newer(latest, APP_VERSION):
                result["update"] = True
            self.log(f"check_update: found v{latest}, current v{APP_VERSION}")
        except Exception as e:
            result["offline"] = True
            result["reason"] = _update_error_reason(e)
            self.log(f"check_update failed: {type(e).__name__}: {e}")
        return result

    @staticmethod
    def _is_newer(latest: str, current: str) -> bool:
        def parts(v):
            out = []
            for p in v.split("."):
                try:
                    out.append(int(p))
                except ValueError:
                    out.append(0)
            return out

        return parts(latest) > parts(current)

    # --- debug log --------------------------------------------------------------
    def set_debug(self, on: bool):
        self._debug = bool(on)
        if self._debug and not self._debug_path:
            stamp = datetime.datetime.now().strftime("%m%d%Y_%H%M%S")
            self._debug_path = os.path.join(app_dir(), f"Debug_Log_{stamp}.txt")
            self.log("Debug log started")
        return {"ok": True}

    def log(self, msg: str):
        # Privacy rule for every call site: this app has no credentials, but
        # keep entries to ids, counts, and status words, not free-text notes
        # or personal details the user typed in.
        if not self._debug or not self._debug_path:
            return
        try:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._debug_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
def _writable_check(folder: str) -> bool:
    """Try creating and deleting a temp file next to the exe."""
    try:
        test_path = os.path.join(folder, f".wtest_{os.getpid()}.tmp")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(test_path)
        return True
    except Exception:
        return False


def _show_write_error(folder: str):
    msg = (
        "Simple Firearm Logbook keeps its data in a file next to the app, "
        f"but this folder isn't writable:\n\n{folder}\n\n"
        "This often happens when the app is placed in Program Files. Move it "
        "to a writable folder (like your Desktop or Documents) and try again."
    )
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, "Simple Firearm Logbook", 0x10)  # MB_ICONERROR
    except Exception:
        pass


def _show_newer_schema_error():
    msg = (
        "This data file was created by a newer version of Simple Firearm "
        "Logbook than this one.\n\n"
        "Update to the latest version of the app to open it."
    )
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, "Simple Firearm Logbook", 0x10)  # MB_ICONERROR
    except Exception:
        pass


_mutex_handle = None   # module-level: must live for the process lifetime


def _acquire_single_instance(mutex_name: str) -> bool:
    # Name convention: "JDE_Simple{Thing}Tool_SingleInstance"
    # Session-local (no "Global\" prefix): each Windows session (e.g. RDP,
    # fast user switching) gets its own instance instead of colliding across users.
    global _mutex_handle
    try:
        # use_last_error=True: ctypes.windll's GetLastError() can be clobbered
        # by ctypes-internal calls, so read the error via ctypes.get_last_error() instead.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _mutex_handle = kernel32.CreateMutexW(None, False, mutex_name)
        return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return True   # fail open: never block launch over a mutex error


def _focus_existing_window(app_title: str) -> bool:
    # Best-effort only: any failure here must not stop the caller from deciding what to do next.
    try:
        user32 = ctypes.windll.user32
        found = {"hwnd": None}

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _enum_proc(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            # Exact match only: a prefix match could hit an unrelated window
            # (e.g. a browser tab starting with the app name). A miss falls
            # through to a normal launch anyway.
            if buf.value == app_title:
                found["hwnd"] = hwnd
                return False   # stop enumerating, match found
            return True

        user32.EnumWindows(WNDENUMPROC(_enum_proc), 0)

        hwnd = found["hwnd"]
        if not hwnd:
            return False

        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


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
    api.set_db_path(db_path)

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
