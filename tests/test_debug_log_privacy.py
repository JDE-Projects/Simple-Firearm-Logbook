import io
import json
import zipfile
from pathlib import Path

import simple_firearm_logbook as app
from sfl import csv_import, paths, storage
from sfl.db import SaveRejected
from sfl.debug_log import DebugLog
from sfl.services import backup, extension, imports, photos, restore, settings


MARKER = "ZZMARKER42"


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    api = app.Api()
    api.set_conn(app.open_db(str(tmp_path / "logbook.db")))
    assert api.set_debug(True)["ok"]
    return api


def _contents(tmp_path):
    return next(tmp_path.glob("Debug_Log_*.txt")).read_text(encoding="utf-8")


def test_rejected_save_logs_class_not_message(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api._before_save = lambda *_: (_ for _ in ()).throw(SaveRejected(MARKER))
    assert not api.create_firearm("Make", "Model")["ok"]
    text = _contents(tmp_path)
    assert MARKER not in text and "create_firearm rejected (SaveRejected)" in text


def test_photo_skip_does_not_log_file_name(tmp_path, monkeypatch):
    logs = []
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    photos._import_photo_sources(1, {"log_number": "00001"}, None, 0, False,
        [{"opener": None, "category": "too_large", "skip_reason": "over the size limit", "label": MARKER}], [], logs.append)
    assert MARKER not in "\n".join(logs) and "Photo skipped, over the size limit" in logs


def test_attachment_copy_failure_does_not_log_file_name(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    firearm_id = api.create_firearm("Make", "Model")["firearm_id"]
    source = tmp_path / f"{MARKER}.pdf"
    source.write_bytes(b"x")
    monkeypatch.setattr(storage.current(), "import_file", lambda *_: (_ for _ in ()).throw(OSError("copy failed")))
    api.add_attachments(firearm_id, [str(source)])
    text = _contents(tmp_path)
    assert MARKER not in text and "Attachment copy failed (OSError)" in text


def test_backup_missing_file_does_not_log_stored_name(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    firearm_id = api.create_firearm("Make", "Model")["firearm_id"]
    api._conn.execute("INSERT INTO attachments (firearm_id, filename, label, seq, size_bytes) VALUES (?, ?, 'x', 1, 1)", (firearm_id, f"attachments/1.{MARKER.lower()}"))
    api._conn.commit()
    backup._write_backup_zip(api._conn, str(tmp_path / "backup.zip"), api._log)
    text = _contents(tmp_path)
    assert MARKER.lower() not in text and "Backup: referenced file missing on disk" in text


def test_restore_preview_does_not_log_zip_path(tmp_path):
    archive = tmp_path / f"{MARKER}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": 1, "inventory": [], "counts": {}}))
    logs = []
    restore.inspect_backup(str(archive), str(tmp_path / "stage"), logs.append)
    assert MARKER not in "\n".join(logs) and any("Restore preview: blocked=" in x for x in logs)


def test_extension_read_failure_logs_fixed_line(tmp_path):
    path = tmp_path / f"{MARKER}.js"
    logs = []
    result = extension.read_extension_files(str(path), None, logs.append)
    assert str(path) in result["errors"][0]
    assert MARKER not in "\n".join(logs) and "Couldn't read extension script file (FileNotFoundError)" in logs


def test_debug_log_scan_warning_keeps_path_out_of_log(tmp_path, monkeypatch):
    warnings = []
    dbg = DebugLog(str(tmp_path), "Test", on_warning=warnings.append)
    assert dbg.set_enabled(True)
    monkeypatch.setattr("sfl.debug_log.os.scandir", lambda _: (_ for _ in ()).throw(PermissionError(MARKER)))
    dbg.prune()
    text = Path(dbg._path).read_text(encoding="utf-8")
    assert warnings and MARKER not in text and "WARNING" in text


def test_update_check_ignores_odd_tag(monkeypatch):
    class Response(io.StringIO):
        def __enter__(self): return self
        def __exit__(self, *args): pass
    logs = []
    monkeypatch.setattr(settings.urllib.request, "urlopen", lambda *a, **k: Response(json.dumps({"tag_name": MARKER})))
    settings.check_update(logs.append)
    assert MARKER not in "\n".join(logs) and "check_update: found an unrecognized version tag" in logs
    logs.clear()
    monkeypatch.setattr(settings.urllib.request, "urlopen", lambda *a, **k: Response('{"tag_name":"v1.2.3"}'))
    settings.check_update(logs.append)
    assert any("check_update: found v1.2.3" in line for line in logs)


def test_disposition_status_logged_only_from_list(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    fid = api.create_firearm("Make", "Model")["firearm_id"]
    assert api.update_disposition(fid, "Sold")["ok"]
    assert not api.update_disposition(fid, MARKER)["ok"]
    text = _contents(tmp_path)
    assert MARKER not in text and "set to Sold" in text


def test_firearm_delete_file_failure_logs_count(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    fid = api.create_firearm("Make", "Model")["firearm_id"]
    api._conn.execute("INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (?, ?, 1, 1)", (fid, f"photos/{MARKER}.jpg"))
    api._conn.commit()
    monkeypatch.setattr(storage.current(), "remove", lambda _: (_ for _ in ()).throw(OSError()))
    api.delete_firearm(fid)
    text = _contents(tmp_path)
    assert MARKER not in text and "1 file(s) could not be removed" in text


def test_csv_import_rejection_does_not_log_cell_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    record, _, _ = csv_import.build_import_record([MARKER, "Model"], {"make": 0, "model": 1})
    result = imports.import_csv_commit(api._conn, api._log, [record], lambda *_: (_ for _ in ()).throw(SaveRejected(MARKER)))
    assert not result["ok"]
    text = _contents(tmp_path)
    assert MARKER not in text and "import_csv_commit rejected (SaveRejected)" in text
