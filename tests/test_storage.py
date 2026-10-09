"""Tests for the storage object supplied by an embedding app."""
import base64
import io
import sqlite3
import zipfile

import pytest
from PIL import Image

from sfl import config, paths, storage
from sfl.db import NewerSchemaError, ensure_schema, open_db
from sfl.services import attachments, backup, export, firearms, photos, restore


@pytest.fixture(autouse=True)
def reset_storage():
    storage.install(None)
    yield
    storage.install(None)


class RecordingStorage(storage.Storage):
    def __init__(self):
        self.calls = []

    def commit(self, conn):
        self.calls.append("commit")
        super().commit(conn)

    def connect_file(self, path):
        self.calls.append("connect_file")
        return super().connect_file(path)

    def snapshot_db_bytes(self, conn):
        self.calls.append("snapshot_db_bytes")
        return super().snapshot_db_bytes(conn)

    def read_bytes(self, full_path):
        self.calls.append("read_bytes")
        return super().read_bytes(full_path)[::-1] if not full_path.endswith(".db") else super().read_bytes(full_path)

    def write_bytes(self, full_path, data):
        self.calls.append("write_bytes")
        super().write_bytes(full_path, data[::-1] if not full_path.endswith(".db") else data)

    def remove(self, full_path):
        self.calls.append("remove")
        super().remove(full_path)

    def import_file(self, src_path, target_full_path):
        self.calls.append("import_file")
        self.write_bytes(target_full_path, open(src_path, "rb").read())
        return len(self.read_bytes(target_full_path))

    def export_file(self, full_path, dest_path):
        self.calls.append("export_file")
        with open(dest_path, "wb") as file:
            file.write(self.read_bytes(full_path))

    def open_external(self, full_path):
        self.calls.append("open_external")

    def open_backup_writer(self, dest_path):
        self.calls.append("open_backup_writer")
        return super().open_backup_writer(dest_path)

    def open_backup_reader(self, path):
        self.calls.append("open_backup_reader")
        return super().open_backup_reader(path)


def _image_data():
    image = Image.new("RGB", (20, 10), (1, 2, 3))
    output = io.BytesIO()
    image.save(output, "PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    conn = open_db(str(tmp_path / config.DB_FILENAME))
    created = firearms.create_firearm(conn, lambda _: None, "Glock", "19")
    return conn, created["firearm_id"]


def test_default_storage_methods(tmp_path, monkeypatch):
    default = storage.Storage()
    source = tmp_path / "source.bin"
    target = tmp_path / "target.bin"
    exported = tmp_path / "exported.bin"
    source.write_bytes(b"source")
    assert default.import_file(source, target) == 6
    assert default.read_bytes(target) == b"source"
    default.write_bytes(target, b"changed")
    assert target.read_bytes() == b"changed"
    default.export_file(target, exported)
    assert exported.read_bytes() == b"changed"
    opened_connection = default.connect_file(tmp_path / "database.db")
    opened_connection.close()
    opened = []
    monkeypatch.setattr(storage.os, "startfile", opened.append, raising=False)
    default.open_external(str(target))
    assert opened == [str(target)]
    with default.open_backup_writer(tmp_path / "backup.bin") as writer:
        writer.write(b"backup")
    with default.open_backup_reader(tmp_path / "backup.bin") as reader:
        assert reader.read() == b"backup"
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE item (value TEXT)")
    conn.execute("INSERT INTO item VALUES ('saved')")
    default.commit(conn)
    assert b"SQLite format 3" in default.snapshot_db_bytes(conn)
    conn.close()
    default.remove(target)
    assert not target.exists()


def test_storage_routes_photo_document_and_firearm_files(tmp_path, monkeypatch):
    recorded = RecordingStorage()
    storage.install(recorded)
    conn, firearm_id = _setup(tmp_path, monkeypatch)
    try:
        added_photo = photos.add_photos_from_data(
            conn, lambda _: None, firearm_id, [{"name": "photo.png", "data": _image_data()}]
        )
        assert added_photo["ok"]
        assert photos.get_photo_data(lambda _: None, added_photo["photos"][0]["filename"])["ok"]
        source = tmp_path / "receipt.pdf"
        source.write_bytes(b"receipt")
        added_document = attachments.add_attachments(conn, lambda _: None, firearm_id, [str(source)])
        attachment_id = added_document["attachments"][0]["id"]
        assert attachments.open_attachment(conn, lambda _: None, attachment_id)["ok"]

        class Window:
            def create_file_dialog(self, *args, **kwargs):
                return [str(tmp_path / "copy.pdf")]

        assert attachments.save_attachment_copy(conn, Window(), lambda _: None, attachment_id)["ok"]
        assert attachments.delete_attachment(conn, lambda _: None, attachment_id)["ok"]
        assert firearms.delete_firearm(conn, None, lambda _: None, firearm_id)["ok"]
        assert {"write_bytes", "read_bytes", "remove", "import_file", "open_external", "export_file", "commit"} <= set(recorded.calls)
    finally:
        conn.close()


def test_storage_routes_export_backup_and_restore(tmp_path, monkeypatch):
    recorded = RecordingStorage()
    storage.install(recorded)
    conn, firearm_id = _setup(tmp_path, monkeypatch)
    try:
        photos.add_photos_from_data(conn, lambda _: None, firearm_id, [{"name": "photo.png", "data": _image_data()}])
        source = tmp_path / "receipt.pdf"
        source.write_bytes(b"receipt")
        attachments.add_attachments(conn, lambda _: None, firearm_id, [str(source)])
        archive = tmp_path / "backup.zip"
        assert backup._write_backup_zip(conn, str(archive), lambda _: None)["ok"]
        with zipfile.ZipFile(archive) as zipped:
            assert zipped.read("photos/00001_1.jpg")
            assert zipped.read("attachments/00001_1.pdf") == b"receipt"
        staging = tmp_path / "staging"
        preview = restore.inspect_backup(str(archive), str(staging), lambda _: None)
        assert preview["ok"] and preview["integrity_ok"]
        conn.close()
        conn = None
        assert restore.restore_commit(str(staging), lambda _: None)["ok"]
        restored_document = tmp_path / config.ATTACHMENTS_DIRNAME / "00001_1.pdf"
        assert restored_document.read_bytes() == b"receipt"[::-1]
        assert recorded.read_bytes(str(restored_document)) == b"receipt"
        assert {"snapshot_db_bytes", "open_backup_writer", "open_backup_reader", "connect_file"} <= set(recorded.calls)
    finally:
        if conn is not None:
            conn.close()


def test_storage_routes_export_files(tmp_path, monkeypatch):
    recorded = RecordingStorage()
    storage.install(recorded)
    conn, firearm_id = _setup(tmp_path, monkeypatch)
    try:
        photos.add_photos_from_data(conn, lambda _: None, firearm_id, [{"name": "photo.png", "data": _image_data()}])

        class Window:
            def create_file_dialog(self, *args, **kwargs):
                return [str(tmp_path / "export.zip")]

        assert export._export_single_backup_zip_internal(conn, Window(), lambda _: None, firearm_id)["ok"]
        assert "read_bytes" in recorded.calls
    finally:
        conn.close()


def test_commit_failure_returns_existing_service_failure(tmp_path, monkeypatch):
    class FailingStorage(storage.Storage):
        def commit(self, conn):
            raise OSError("no save")

    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    conn = open_db(str(tmp_path / config.DB_FILENAME))
    conn.execute("INSERT INTO firearms (log_number, make, model, created_at, updated_at) VALUES ('00001', 'A', 'B', '', '')")
    storage.install(FailingStorage())
    result = firearms.update_firearm(conn, lambda _: None, 1, "A", "B")
    assert result == {"ok": False, "error": "Couldn't save the firearm."}
    conn.close()


def test_ensure_schema_handles_memory_and_newer_version():
    recorded = RecordingStorage()
    storage.install(recorded)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn, config.SCHEMA_VERSION)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='firearms'").fetchone()["name"] == "firearms"
    assert "commit" in recorded.calls
    conn.execute(f"PRAGMA user_version = {config.SCHEMA_VERSION + 1}")
    with pytest.raises(NewerSchemaError):
        ensure_schema(conn, config.SCHEMA_VERSION)
    conn.close()
