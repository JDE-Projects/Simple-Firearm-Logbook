import simple_firearm_logbook as app
from sfl import paths
from sfl.services import firearms, photos


def _photo(conn, firearm_id, filename):
    conn.execute(
        "INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (?, ?, 1, 1)",
        (firearm_id, filename),
    )
    conn.commit()
    return conn.execute("SELECT id FROM photos").fetchone()["id"]


def test_delete_photo_warns_when_file_cannot_be_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    conn = app.open_db(str(tmp_path / "test.db"))
    try:
        firearm_id = firearms.create_firearm(conn, lambda message: None, "Glock", "19")["firearm_id"]
        filename = "photos/photo.jpg"
        full = tmp_path / filename
        full.parent.mkdir()
        full.write_bytes(b"photo")
        photo_id = _photo(conn, firearm_id, filename)
        logs = []
        monkeypatch.setattr(photos.os, "remove", lambda path: (_ for _ in ()).throw(OSError("locked")))

        result = photos.delete_photo(conn, logs.append, photo_id)

        assert result["ok"] is True
        assert result["photos"] == []
        assert "warning" in result
        assert any(str(full) in message for message in logs)
    finally:
        conn.close()


def test_delete_photo_has_no_warning_when_file_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    conn = app.open_db(str(tmp_path / "test.db"))
    try:
        firearm_id = firearms.create_firearm(conn, lambda message: None, "Glock", "19")["firearm_id"]
        filename = "photos/photo.jpg"
        full = tmp_path / filename
        full.parent.mkdir()
        full.write_bytes(b"photo")
        photo_id = _photo(conn, firearm_id, filename)

        result = photos.delete_photo(conn, lambda message: None, photo_id)

        assert result == {"ok": True, "photos": []}
        assert not full.exists()
    finally:
        conn.close()
