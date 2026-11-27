"""Tests for saving a stored photo copy through the API bridge."""

import base64
import builtins
import io
import os

from PIL import Image

import simple_firearm_logbook as app
from sfl import paths, storage


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    conn = app.open_db(str(tmp_path / "test.db"))
    api = app.Api()
    api.set_conn(conn)
    return api


def _b64_image(color=(120, 60, 30)):
    image = Image.new("RGB", (30, 20), color)
    data = io.BytesIO()
    image.save(data, "PNG")
    return base64.b64encode(data.getvalue()).decode("ascii")


def _add_photos(api, firearm_id, count=1):
    return api.add_photos_from_data(
        firearm_id,
        [{"name": f"photo-{number}.png", "data": _b64_image((number, 60, 30))} for number in range(count)],
    )["photos"]


class _StubWindow:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    def create_file_dialog(self, *args, **kwargs):
        self.kwargs = kwargs
        return self.result


def test_save_photo_copy_writes_the_stored_bytes_to_the_chosen_path(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        photo = _add_photos(api, api.create_firearm("Glock", "19")["firearm_id"])[0]
        target = tmp_path / "saved" / "copy.jpg"
        target.parent.mkdir()
        api._window = _StubWindow(str(target))

        result = api.save_photo_copy(photo["id"])

        assert result == {"ok": True, "path": str(target)}
        assert target.read_bytes() == (tmp_path / photo["filename"]).read_bytes()
    finally:
        api.close_conn()


def test_save_photo_copy_suggests_log_number_and_position_name(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        firearm_id = api.create_firearm("Glock", "19")["firearm_id"]
        log_number = api._get_firearm_row(firearm_id)["log_number"]
        third_photo = _add_photos(api, firearm_id, 3)[2]
        api._window = _StubWindow(None)

        api.save_photo_copy(third_photo["id"])

        assert api._window.kwargs["save_filename"] == f"SFL-{log_number}_03.jpg"
    finally:
        api.close_conn()


def test_save_photo_copy_position_ignores_deleted_photos(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        firearm_id = api.create_firearm("Glock", "19")["firearm_id"]
        photos = _add_photos(api, firearm_id, 3)
        api.delete_photo(photos[0]["id"])
        api._window = _StubWindow(None)

        api.save_photo_copy(photos[2]["id"])

        assert api._window.kwargs["save_filename"].endswith("_02.jpg")
    finally:
        api.close_conn()


def test_save_photo_copy_passes_downloads_folder_as_directory(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        photo = _add_photos(api, api.create_firearm("Glock", "19")["firearm_id"])[0]
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(paths, "downloads_dir", lambda: str(downloads))
        api._window = _StubWindow(None)

        api.save_photo_copy(photo["id"])

        assert api._window.kwargs["directory"] == str(downloads)
    finally:
        api.close_conn()


def test_save_photo_copy_omits_directory_when_no_downloads_folder(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        photo = _add_photos(api, api.create_firearm("Glock", "19")["firearm_id"])[0]
        monkeypatch.setattr(paths, "downloads_dir", lambda: "")
        api._window = _StubWindow(None)

        api.save_photo_copy(photo["id"])

        assert "directory" not in api._window.kwargs
    finally:
        api.close_conn()


def test_save_photo_copy_cancel_returns_cancelled(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        photo = _add_photos(api, api.create_firearm("Glock", "19")["firearm_id"])[0]
        api._window = _StubWindow(None)

        assert api.save_photo_copy(photo["id"]) == {"ok": True, "cancelled": True}
    finally:
        api.close_conn()


def test_save_photo_copy_reports_missing_without_opening_dialog(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        photo = _add_photos(api, api.create_firearm("Glock", "19")["firearm_id"])[0]
        os.remove(tmp_path / photo["filename"])

        class _NoDialogWindow:
            def create_file_dialog(self, *args, **kwargs):
                raise AssertionError("dialog should not be opened when the file is missing")

        api._window = _NoDialogWindow()
        result = api.save_photo_copy(photo["id"])

        assert result["ok"] is False
        assert result["missing"] is True
        assert result["error"] == "That photo file is missing."
    finally:
        api.close_conn()


def test_save_photo_copy_unknown_id_returns_error(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        assert api.save_photo_copy(999999) == {"ok": False, "error": "That photo no longer exists."}
    finally:
        api.close_conn()


def test_save_photo_copy_adds_extension_when_user_left_it_off(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        photo = _add_photos(api, api.create_firearm("Glock", "19")["firearm_id"])[0]
        target = tmp_path / "saved" / "copy"
        target.parent.mkdir()
        api._window = _StubWindow(str(target))

        result = api.save_photo_copy(photo["id"])

        assert result == {"ok": True, "path": f"{target}.jpg"}
        assert (tmp_path / "saved" / "copy.jpg").exists()
    finally:
        api.close_conn()


def test_save_photo_copy_respects_a_user_chosen_extension(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        photo = _add_photos(api, api.create_firearm("Glock", "19")["firearm_id"])[0]
        target = tmp_path / "saved" / "copy.png"
        target.parent.mkdir()
        api._window = _StubWindow(str(target))

        result = api.save_photo_copy(photo["id"])

        assert result == {"ok": True, "path": str(target)}
        assert target.exists()
    finally:
        api.close_conn()


def test_save_photo_copy_write_failure_returns_error_and_logs(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        photo = _add_photos(api, api.create_firearm("Glock", "19")["firearm_id"])[0]
        target = tmp_path / "saved" / "copy.jpg"
        target.parent.mkdir()
        api._window = _StubWindow(str(target))
        logs = []
        api._log = logs.append
        monkeypatch.setattr(storage.current(), "export_file", lambda *args: (_ for _ in ()).throw(OSError("blocked")))

        result = api.save_photo_copy(photo["id"])

        assert result == {"ok": False, "error": "Couldn't save a copy of the photo."}
        assert logs[0].startswith("save_photo_copy failed")
    finally:
        api.close_conn()


def test_downloads_dir_falls_back_when_qt_lookup_fails(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    real_import = builtins.__import__

    def _raising_import(name, *args, **kwargs):
        if name == "PySide6.QtCore":
            raise ImportError("Qt unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _raising_import)
    monkeypatch.setattr(paths.os.path, "expanduser", lambda value: str(downloads))

    assert paths.downloads_dir() == str(downloads)
    downloads.rmdir()
    assert paths.downloads_dir() == ""
