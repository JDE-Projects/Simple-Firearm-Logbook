"""
Tests for add_photos, the file-picker twin of add_photos_from_data.

A file the user chose must either be stored or be named in the returned
warning, never dropped silently. Covers:
  - a good image is stored with no warning;
  - a chosen file that no longer exists (moved or deleted after picking) is
    refused and counted as missing;
  - a chosen file with an unsupported extension is refused and counted as
    not an image;
  - a refused file alongside a good one still lets the good one in.
"""

import io

from PIL import Image

import simple_firearm_logbook as app
from sfl import paths


def _api(tmp_path, monkeypatch, picked):
    # Route the photos folder into tmp_path instead of the real app folder
    # (app_dir() defaults to this repo's own directory when unfrozen).
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    conn = app.open_db(str(tmp_path / "test.db"))
    api = app.Api()
    api.set_conn(conn)

    class Window:
        def create_file_dialog(self, *args, **kwargs):
            return [str(p) for p in picked]

    api.set_window(Window())
    return api


def _png(path):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (120, 60, 30)).save(buf, "PNG")
    path.write_bytes(buf.getvalue())
    return path


def _photo_rows(api, fid):
    return api._conn.execute("SELECT COUNT(*) FROM photos WHERE firearm_id=?", (fid,)).fetchone()[0]


def test_good_image_is_stored_without_warning(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, [_png(tmp_path / "a.png")])
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        r = api.add_photos(fid)
        assert r["ok"], r
        assert r["added"] == 1
        assert "warning" not in r
        assert _photo_rows(api, fid) == 1
    finally:
        api.close_conn()


def test_missing_file_is_named_in_warning(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, [tmp_path / "gone.png"])
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        r = api.add_photos(fid)
        assert r["ok"], r
        assert r["added"] == 0
        assert r["warning"] == "1 file couldn't be found and wasn't added."
        assert _photo_rows(api, fid) == 0
    finally:
        api.close_conn()


def test_unsupported_extension_is_named_in_warning(tmp_path, monkeypatch):
    notes = tmp_path / "notes.txt"
    notes.write_text("not a photo")
    api = _api(tmp_path, monkeypatch, [notes])
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        r = api.add_photos(fid)
        assert r["ok"], r
        assert r["added"] == 0
        assert r["warning"] == "1 file wasn't an image and wasn't added."
        assert _photo_rows(api, fid) == 0
    finally:
        api.close_conn()


def test_refused_file_does_not_block_good_one(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch, [tmp_path / "gone.png", _png(tmp_path / "a.png")])
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        r = api.add_photos(fid)
        assert r["ok"], r
        assert r["added"] == 1
        assert r["warning"] == "1 file couldn't be found and wasn't added."
        assert _photo_rows(api, fid) == 1
        # The refused file never burns a sequence number.
        assert r["photos"][0]["seq"] == 1
    finally:
        api.close_conn()
