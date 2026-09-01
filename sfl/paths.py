"""Filesystem path helpers: where the app and its resources live, and safe
resolution of the relative filenames stored in the database for photos and
attachments (guards against path traversal)."""
import os
import sys

from sfl.config import ATTACHMENT_LABEL_MAX

_INVALID_FILENAME_CHARS = '<>:"/\\|?*'


def resource_path(rel: str) -> str:
    """Path to a bundled resource, working both from source and PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, rel)


def app_dir() -> str:
    """Folder the app lives in: next to the .exe when frozen, else the script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sanitize_filename(name: str) -> str:
    """Strip characters that Windows doesn't allow in file names."""
    cleaned = "".join(c for c in name if c not in _INVALID_FILENAME_CHARS)
    return cleaned.strip()


def _sanitize_attachment_label(raw: str) -> str:
    """Sanitize and cap a document's display label. Shared by the import
    default (built from the original filename) and rename_attachment."""
    return sanitize_filename(raw or "")[:ATTACHMENT_LABEL_MAX]


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


def _pref_path() -> str:
    return os.path.join(app_dir(), "simple_firearm_logbook.pref")
