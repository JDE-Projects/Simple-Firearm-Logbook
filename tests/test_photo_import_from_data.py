"""
Tests for add_photos_from_data in simple_firearm_logbook.py.

add_photos_from_data is the bytes-based twin of add_photos, for a browser
drag-and-drop drop of image data instead of a file picker. It shares the
_import_photo_sources helper with add_photos, so the naming scheme, sequence
numbering, and first-photo-becomes-primary rule are identical. Covers:
  - a good image is stored under the {lognum}_{seq}.jpg naming scheme as a
    valid, readable JPEG;
  - an oversize source is refused before it burns a sequence number, so the
    next good file still gets seq 1;
  - a non-image (bad extension, or bytes that aren't a decodable image) is
    rejected and counted as failed, never stored;
  - the first photo added to a firearm with no primary becomes primary.
"""

import base64
import io

from PIL import Image

import simple_firearm_logbook as app


def _api(tmp_path, monkeypatch):
    # Route the photos folder into tmp_path instead of the real app folder
    # (app_dir() defaults to this repo's own directory when unfrozen).
    monkeypatch.setattr(app, "app_dir", lambda: str(tmp_path))
    conn = app.open_db(str(tmp_path / "test.db"))
    api = app.Api()
    api.set_conn(conn)
    api.set_db_path(str(tmp_path / "test.db"))
    return api


def _b64_image(size=(300, 200), color=(120, 60, 30)):
    """A small real PNG, base64-encoded, for a "good" upload."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_good_image_is_stored_as_a_valid_jpeg(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        log_number = api._get_firearm_row(fid)["log_number"]
        r = api.add_photos_from_data(fid, [{"name": "photo.png", "data": _b64_image()}])
        assert r["ok"], r
        assert r["added"] == 1
        assert "warning" not in r
        photo = r["photos"][0]
        assert photo["filename"] == f"photos/{log_number}_1.jpg"
        assert photo["is_primary"] is True
        full = tmp_path / photo["filename"]
        assert full.exists()
        with Image.open(full) as result:
            assert result.format == "JPEG"
    finally:
        api.close_conn()


def test_oversize_source_is_refused_without_burning_a_sequence_number(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        log_number = api._get_firearm_row(fid)["log_number"]
        good_data = _b64_image()

        # Make the ceiling small enough that the real encoded image trips it,
        # scoped to just this call so the next call sees the real ceiling.
        with monkeypatch.context() as m:
            m.setattr(app, "MAX_IMAGE_BYTES", 10)
            r = api.add_photos_from_data(fid, [{"name": "toobig.png", "data": good_data}])
        assert r["ok"], r
        assert r["added"] == 0
        assert r["warning"]
        # Refused before it was ever opened, so it never burned a sequence
        # number and no photo row exists.
        assert r["photos"] == []

        # A genuinely good file next gets seq 1, not seq 2, proving the
        # refused source above never burned a number.
        r2 = api.add_photos_from_data(fid, [{"name": "ok.png", "data": good_data}])
        assert r2["ok"], r2
        assert r2["added"] == 1
        assert r2["photos"][0]["filename"] == f"photos/{log_number}_1.jpg"
    finally:
        api.close_conn()


def test_non_image_extension_is_rejected_and_counted_as_failed(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        data = base64.b64encode(b"just some notes").decode("ascii")
        r = api.add_photos_from_data(fid, [{"name": "notes.txt", "data": data}])
        assert r["ok"], r
        assert r["added"] == 0
        assert r["warning"]
        assert r["photos"] == []
    finally:
        api.close_conn()


def test_undecodable_image_bytes_are_rejected_and_counted_as_failed(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        # A supported extension, but the bytes underneath aren't a real image.
        data = base64.b64encode(b"this is not image data").decode("ascii")
        r = api.add_photos_from_data(fid, [{"name": "fake.jpg", "data": data}])
        assert r["ok"], r
        assert r["added"] == 0
        assert r["warning"]
        assert r["photos"] == []
    finally:
        api.close_conn()


def test_first_photo_added_becomes_primary(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        r = api.add_photos_from_data(fid, [{"name": "photo.png", "data": _b64_image()}])
        assert r["ok"], r
        assert r["photos"][0]["is_primary"] is True
    finally:
        api.close_conn()
