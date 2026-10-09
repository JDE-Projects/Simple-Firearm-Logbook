"""Replaceable persistence operations for an embedding app."""
import os
import shutil
import sqlite3
import tempfile


class Storage:
    """Plain-file storage used when an embedding app does not supply its own."""

    def commit(self, conn) -> None:
        """Make pending database changes durable, or raise with conn at its last durable state."""
        conn.commit()

    def connect_file(self, path):
        """Open a database file that is not the live application connection."""
        return sqlite3.connect(path)

    def snapshot_db_bytes(self, conn) -> bytes:
        """Return complete, consistent bytes for the live database's current state."""
        fd, path = tempfile.mkstemp(suffix=".db", dir=tempfile.gettempdir())
        os.close(fd)
        try:
            target = sqlite3.connect(path)
            try:
                conn.backup(target)
            finally:
                target.close()
            with open(path, "rb") as file:
                return file.read()
        finally:
            if os.path.exists(path):
                os.remove(path)

    def read_bytes(self, full_path) -> bytes:
        """Return the plain bytes stored at full_path."""
        with open(full_path, "rb") as file:
            return file.read()

    def write_bytes(self, full_path, data: bytes) -> None:
        """Atomically replace full_path with data using a temporary file beside it."""
        folder = os.path.dirname(full_path) or "."
        fd, temporary_path = tempfile.mkstemp(dir=folder)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            os.replace(temporary_path, full_path)
        except Exception:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
            raise

    def remove(self, full_path) -> None:
        """Remove the stored file at full_path."""
        os.remove(full_path)

    def import_file(self, src_path, target_full_path) -> int:
        """Copy outside plain content into storage and return the stored byte count."""
        shutil.copy2(src_path, target_full_path)
        return os.path.getsize(target_full_path)

    def export_file(self, full_path, dest_path) -> None:
        """Copy stored plain content to a user-chosen destination."""
        shutil.copy2(full_path, dest_path)

    def open_external(self, full_path) -> None:
        """Open stored content in the Windows default viewer."""
        os.startfile(full_path)

    def open_backup_writer(self, dest_path):
        """Return a writable binary stream for a backup archive."""
        return open(dest_path, "wb")

    def open_backup_reader(self, path):
        """Return a readable, seekable binary stream for a backup archive."""
        return open(path, "rb")


_installed = None


def install(storage) -> None:
    """Install the storage object used by the core."""
    global _installed
    _installed = storage


def current():
    """Return the installed storage object, or the default plain-file storage."""
    global _installed
    if _installed is None:
        _installed = Storage()
    return _installed
