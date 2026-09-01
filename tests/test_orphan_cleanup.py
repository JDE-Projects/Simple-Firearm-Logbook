"""
Tests for Phase 1 hardening (data safety) in simple_firearm_logbook.py:

  - delete_firearm: a photo or attachment file that can't be removed from
    disk no longer produces a silent "ok" result. The delete still goes
    through (the DB rows are already committed), but the result carries a
    "warning" naming how many files were left behind, and the failed paths
    are logged.
  - add_photos_from_data / add_attachments: a file is copied to disk before
    its DB row is inserted. If something after that (the commit, or an
    unexpected exception) fails, the just-copied file must not be left
    behind as an orphan with no row pointing at it.
"""

import base64
import io
import os
import sqlite3

from PIL import Image

import simple_firearm_logbook as app
from sfl import paths


def _api(tmp_path, monkeypatch):
    # Route photos\/attachments\ into tmp_path instead of the real app
    # folder (app_dir() defaults to this repo's own directory when unfrozen).
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    conn = app.open_db(str(tmp_path / "test.db"))
    api = app.Api()
    api.set_conn(conn)
    return api


def _make_file(path, content=b"hello world"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return str(path)


def _image_b64(size=(200, 150), color=(10, 20, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class _FailingCommitConn:
    """Wraps a real sqlite3.Connection so commit() raises while everything
    else (execute, cursor, rollback, ...) still hits the real connection.
    sqlite3.Connection is a C type and can't have its methods monkeypatched
    directly, so this stands in for it on the Api instance under test."""

    def __init__(self, real_conn):
        self._real = real_conn

    def commit(self):
        raise sqlite3.OperationalError("boom")

    def __getattr__(self, name):
        return getattr(self._real, name)


def _dir_files(path):
    if not os.path.isdir(path):
        return []
    return [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]


# ─────────────────────────────────────────────────────────────
#  add_photos_from_data: no orphan file on a post-write DB failure
# ─────────────────────────────────────────────────────────────
def test_add_photos_from_data_removes_written_file_when_commit_fails(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        monkeypatch.setattr(api, "_conn", _FailingCommitConn(api._conn))

        r = api.add_photos_from_data(fid, [{"name": "a.jpg", "data": _image_b64()}])

        assert not r["ok"]
        photos_dir = os.path.join(str(tmp_path), app.PHOTOS_DIRNAME)
        assert _dir_files(photos_dir) == []
        rows = api._conn.execute("SELECT * FROM photos WHERE firearm_id=?", (fid,)).fetchall()
        assert rows == []
    finally:
        api.close_conn()


def test_add_photos_from_data_per_source_failure_leaves_no_file(tmp_path, monkeypatch):
    # A source with a supported extension but undecodable image bytes fails
    # inside _import_photo_sources's own optimize step, which already
    # removes the file it wrote. Confirm that still holds (nothing orphaned).
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        bad_b64 = base64.b64encode(b"not actually an image").decode("ascii")

        r = api.add_photos_from_data(fid, [{"name": "bad.jpg", "data": bad_b64}])

        assert r["ok"], r
        assert r["added"] == 0
        photos_dir = os.path.join(str(tmp_path), app.PHOTOS_DIRNAME)
        assert _dir_files(photos_dir) == []
    finally:
        api.close_conn()


# ─────────────────────────────────────────────────────────────
#  add_attachments: no orphan file on a post-write DB failure
# ─────────────────────────────────────────────────────────────
def test_add_attachments_removes_written_file_when_commit_fails(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        src = _make_file(tmp_path / "src" / "receipt.pdf")
        monkeypatch.setattr(api, "_conn", _FailingCommitConn(api._conn))

        r = api.add_attachments(fid, [src])

        assert not r["ok"]
        attachments_dir = os.path.join(str(tmp_path), app.ATTACHMENTS_DIRNAME)
        assert _dir_files(attachments_dir) == []
        rows = api._conn.execute("SELECT * FROM attachments WHERE firearm_id=?", (fid,)).fetchall()
        assert rows == []
    finally:
        api.close_conn()


def test_add_attachments_per_source_failure_leaves_no_file(tmp_path, monkeypatch):
    # The copy step itself fails: the per-source cleanup already removes
    # the half-written file. Confirm that still holds (nothing orphaned).
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        src = _make_file(tmp_path / "src" / "receipt.pdf")

        def _raise_copy2(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(app.shutil, "copy2", _raise_copy2)

        r = api.add_attachments(fid, [src])

        assert r["ok"], r
        assert r["added"] == 0
        attachments_dir = os.path.join(str(tmp_path), app.ATTACHMENTS_DIRNAME)
        assert _dir_files(attachments_dir) == []
    finally:
        api.close_conn()


# ─────────────────────────────────────────────────────────────
#  delete_firearm: loud, not silent, when a file can't be removed
# ─────────────────────────────────────────────────────────────
def test_delete_firearm_reports_warning_when_file_removal_fails(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        photo_r = api.add_photos_from_data(fid, [{"name": "a.jpg", "data": _image_b64()}])
        assert photo_r["ok"], photo_r
        src = _make_file(tmp_path / "src" / "receipt.pdf")
        attach_r = api.add_attachments(fid, [src])
        assert attach_r["ok"], attach_r

        attempted = []

        def _raise_remove(path, *a, **kw):
            attempted.append(path)
            raise OSError("file is in use")

        monkeypatch.setattr(app.os, "remove", _raise_remove)

        r = api.delete_firearm(fid)

        assert r["ok"] is True
        assert "warning" in r
        assert len(attempted) == 2  # the photo and the attachment
    finally:
        api.close_conn()
