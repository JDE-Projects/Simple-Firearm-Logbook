"""
Tests for document attachments in simple_firearm_logbook.py.

Documents mirror the photo plumbing (open_db table, Api methods, naming
scheme) but are copied in verbatim, never opened or recompressed, and carry
an editable display label separate from the stored filename. Covers:
  - open_db creates the attachments table on a fresh database, and adds it
    to an existing photos-only database on reopen (additive migration);
  - format_size formatting across bytes / KB / MB / GB;
  - the import default label (sanitized, capped basename) and
    rename_attachment's sanitize + cap + empty-rejection;
  - collision-free stored names across two attachments on the same firearm.
"""

import sqlite3

import simple_firearm_logbook as app


def _api(tmp_path, monkeypatch):
    # Route the attachments folder into tmp_path instead of the real app
    # folder (app_dir() defaults to this repo's own directory when unfrozen).
    monkeypatch.setattr(app, "app_dir", lambda: str(tmp_path))
    conn = app.open_db(str(tmp_path / "test.db"))
    api = app.Api()
    api.set_conn(conn)
    api.set_db_path(str(tmp_path / "test.db"))
    return api


def _make_file(path, content=b"hello world"):
    import os

    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return str(path)


# ─────────────────────────────────────────────────────────────
#  open_db: attachments table
# ─────────────────────────────────────────────────────────────
def test_fresh_database_gets_attachments_table(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    conn = app.open_db(db_path)
    try:
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "attachments" in tables
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(attachments)")}
        assert {"id", "firearm_id", "filename", "label", "seq", "size_bytes"} <= cols
    finally:
        conn.close()


def test_photos_only_database_gets_attachments_table_on_reopen(tmp_path):
    # A database created before attachments existed: only firearms/photos/counters.
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE firearms (id INTEGER PRIMARY KEY, log_number TEXT, make TEXT, model TEXT)"
    )
    conn.execute(
        "CREATE TABLE photos (id INTEGER PRIMARY KEY, firearm_id INTEGER, filename TEXT, "
        "seq INTEGER, is_primary INTEGER)"
    )
    conn.commit()
    conn.close()

    conn2 = app.open_db(db_path)
    try:
        tables = {
            r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "attachments" in tables
    finally:
        conn2.close()

    # SCHEMA_VERSION does not move for this additive change.
    assert app.SCHEMA_VERSION == 1


# ─────────────────────────────────────────────────────────────
#  format_size
# ─────────────────────────────────────────────────────────────
def test_format_size_bytes():
    assert app.format_size(0) == "0 B"
    assert app.format_size(512) == "512 B"


def test_format_size_kb():
    assert app.format_size(1024) == "1 KB"
    assert app.format_size(2048) == "2 KB"


def test_format_size_mb_has_one_decimal():
    assert app.format_size(1024 * 1024) == "1.0 MB"
    assert app.format_size(int(1.25 * 1024 * 1024)) == "1.2 MB"


def test_format_size_gb_has_one_decimal():
    assert app.format_size(3 * 1024 * 1024 * 1024) == "3.0 GB"
    assert app.format_size(int(3.4 * 1024 * 1024 * 1024)) == "3.4 GB"


# ─────────────────────────────────────────────────────────────
#  label handling
# ─────────────────────────────────────────────────────────────
def test_sanitize_attachment_label_strips_invalid_characters():
    assert app._sanitize_attachment_label('bad<name>.pdf') == "badname.pdf"


def test_sanitize_attachment_label_caps_length():
    long_name = "a" * (app.ATTACHMENT_LABEL_MAX + 50) + ".pdf"
    result = app._sanitize_attachment_label(long_name)
    assert len(result) == app.ATTACHMENT_LABEL_MAX


def test_sanitize_attachment_label_blank_input_returns_empty():
    assert app._sanitize_attachment_label("") == ""
    assert app._sanitize_attachment_label(None) == ""


def test_import_default_label_is_the_original_basename(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        src = _make_file(tmp_path / "src" / "receipt.pdf")
        r = api.add_attachments(fid, [src])
        assert r["ok"], r
        assert r["attachments"][0]["label"] == "receipt.pdf"
    finally:
        api.close_conn()


def test_import_default_label_is_capped_for_a_long_filename(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        # Windows itself won't allow the invalid-character set sanitize_filename
        # strips, but a very long filename is a real case the cap must handle.
        long_name = "a" * (app.ATTACHMENT_LABEL_MAX + 20) + ".pdf"
        src = _make_file(tmp_path / "src" / long_name)
        r = api.add_attachments(fid, [src])
        assert r["ok"], r
        assert len(r["attachments"][0]["label"]) == app.ATTACHMENT_LABEL_MAX
    finally:
        api.close_conn()


def test_rename_attachment_sanitizes_and_caps(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        src = _make_file(tmp_path / "src" / "receipt.pdf")
        added = api.add_attachments(fid, [src])
        attachment_id = added["attachments"][0]["id"]
        r = api.rename_attachment(attachment_id, 'new<name>*.pdf')
        assert r["ok"], r
        assert r["attachments"][0]["label"] == "newname.pdf"
    finally:
        api.close_conn()


def test_rename_attachment_rejects_empty_result(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        src = _make_file(tmp_path / "src" / "receipt.pdf")
        added = api.add_attachments(fid, [src])
        attachment_id = added["attachments"][0]["id"]
        r = api.rename_attachment(attachment_id, '<>:"/\\|?*')
        assert not r["ok"]
        assert r["error"] == "Enter a name."
    finally:
        api.close_conn()


# ─────────────────────────────────────────────────────────────
#  stored-name collision-freedom
# ─────────────────────────────────────────────────────────────
def test_two_attachments_get_distinct_stored_names_with_extension_preserved(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        src1 = _make_file(tmp_path / "src" / "one.pdf", b"aaa")
        src2 = _make_file(tmp_path / "src" / "two.PDF", b"bbb")
        r = api.add_attachments(fid, [src1, src2])
        assert r["ok"], r
        assert r["added"] == 2
        rows = r["attachments"]
        assert len(rows) == 2
        filenames = [row["filename"] for row in rows]
        assert len(set(filenames)) == 2
        for name in filenames:
            assert name.startswith("attachments/")
            assert name.lower().endswith(".pdf")
    finally:
        api.close_conn()
