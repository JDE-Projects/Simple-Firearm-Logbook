import sqlite3

import simple_firearm_logbook as app
from sfl import paths, storage
from sfl.services import firearms

MARKER = "ZZMARK7341"


def _api(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    api = app.Api()
    api.set_conn(app.open_db(str(tmp_path / "logbook.db")))
    api._debug_log._redact_fn = lambda text: text
    assert api.set_debug(True) == {"ok": True}
    return api


def _log_text(tmp_path):
    return next(tmp_path.glob("Debug_Log_*.txt")).read_text(encoding="utf-8")


def _firearm_fields():
    return {
        "make": MARKER, "model": MARKER, "serial_number": MARKER,
        "firearm_type": MARKER, "caliber": MARKER, "acquisition_date": "2024-01-01",
        "acquired_from": MARKER, "purchase_price": "1.00", "estimated_value": "2.00",
        "insured_value": "3.00", "storage_location": MARKER, "notes": MARKER,
        "sub_type": MARKER, "held_in_trust": 1, "trust_name": MARKER,
        "is_nfa": 1, "nfa_form_type": MARKER, "nfa_stamp_date": "2024-01-02",
    }


def _create(api):
    return api.create_firearm(**_firearm_fields())


def _raise_marker(*_args, **_kwargs):
    raise RuntimeError(MARKER)


def test_create_firearm_success_does_not_log_typed_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert _create(api)["ok"]
    assert MARKER not in _log_text(tmp_path)


def test_create_firearm_failure_does_not_log_error_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(storage.current(), "commit", _raise_marker)
    result = _create(api)
    assert not result["ok"]
    text = _log_text(tmp_path)
    assert MARKER not in text
    assert "RuntimeError" in text


def test_update_firearm_success_does_not_log_typed_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    firearm_id = _create(api)["firearm_id"]
    assert api.update_firearm(firearm_id, **_firearm_fields())["ok"]
    assert MARKER not in _log_text(tmp_path)


def test_update_firearm_failure_does_not_log_error_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    firearm_id = _create(api)["firearm_id"]
    monkeypatch.setattr(storage.current(), "commit", _raise_marker)
    result = api.update_firearm(firearm_id, **_firearm_fields())
    assert not result["ok"]
    text = _log_text(tmp_path)
    assert MARKER not in text
    assert "RuntimeError" in text


def test_update_disposition_success_does_not_log_typed_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    firearm_id = _create(api)["firearm_id"]
    assert api.update_disposition(firearm_id, "Sold", "2024-01-03", MARKER, MARKER, "4.00", MARKER)["ok"]
    assert MARKER not in _log_text(tmp_path)


def test_update_disposition_failure_does_not_log_error_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    firearm_id = _create(api)["firearm_id"]
    monkeypatch.setattr(storage.current(), "commit", _raise_marker)
    result = api.update_disposition(firearm_id, "Sold", "2024-01-03", MARKER, MARKER, "4.00", MARKER)
    assert not result["ok"]
    text = _log_text(tmp_path)
    assert MARKER not in text
    assert "RuntimeError" in text


def test_delete_firearm_success_does_not_log_typed_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    firearm_id = _create(api)["firearm_id"]
    assert api.delete_firearm(firearm_id)["ok"]
    assert MARKER not in _log_text(tmp_path)


def test_delete_firearm_failure_does_not_log_error_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    firearm_id = _create(api)["firearm_id"]
    monkeypatch.setattr(storage.current(), "commit", _raise_marker)
    result = api.delete_firearm(firearm_id)
    assert not result["ok"]
    text = _log_text(tmp_path)
    assert MARKER not in text
    assert "RuntimeError" in text


def _attachment_id(api):
    firearm_id = _create(api)["firearm_id"]
    api._conn.execute(
        "INSERT INTO attachments (firearm_id, filename, label, seq, size_bytes) VALUES (?, ?, ?, 1, 1)",
        (firearm_id, "attachments/1.txt", "old"),
    )
    api._conn.commit()
    return api._conn.execute("SELECT id FROM attachments").fetchone()[0]


def test_rename_attachment_success_does_not_log_typed_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert api.rename_attachment(_attachment_id(api), MARKER)["ok"]
    assert MARKER not in _log_text(tmp_path)


def test_rename_attachment_failure_does_not_log_error_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    attachment_id = _attachment_id(api)
    monkeypatch.setattr(storage.current(), "commit", _raise_marker)
    result = api.rename_attachment(attachment_id, MARKER)
    assert not result["ok"]
    text = _log_text(tmp_path)
    assert MARKER not in text
    assert "RuntimeError" in text


def _import_record():
    record = {key: MARKER for key, _label in app.IMPORT_FIELDS}
    record.update({
        "acquisition_date": "2024-01-01", "purchase_price": "1.00",
        "estimated_value": "2.00", "insured_value": "3.00", "held_in_trust": 1,
        "is_nfa": 1, "nfa_stamp_date": "2024-01-02", "disposition_status": "Sold",
        "disposition_date": "2024-01-03", "disposition_amount": "4.00",
    })
    return record


def test_csv_import_commit_success_does_not_log_typed_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert api.import_csv_commit([_import_record()])["ok"]
    assert MARKER not in _log_text(tmp_path)


def test_csv_import_commit_failure_does_not_log_error_text(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(storage.current(), "commit", _raise_marker)
    result = api.import_csv_commit([_import_record()])
    assert not result["ok"]
    text = _log_text(tmp_path)
    assert MARKER not in text
    assert "RuntimeError" in text


def test_page_event_allowed_name_is_logged(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.log_page_event("extension_load_failed")
    assert "Page event: extension_load_failed" in _log_text(tmp_path)


def test_page_event_unknown_name_does_not_log_text_or_length(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    api.log_page_event(f"{MARKER} free text")
    for value in (None, 123, {"name": MARKER}):
        api.log_page_event(value)
    text = _log_text(tmp_path)
    assert "Page event: unknown" in text
    assert MARKER not in text
    assert "chars" not in text


def _public_names(api):
    return sorted(name for name in dir(api) if not name.startswith("_"))


def test_page_cannot_reach_a_free_text_log(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    assert not hasattr(api, "log")
    assert [name for name in _public_names(api) if name.startswith("log")] == ["log_page_event"]


def test_describe_error_in_real_log_line_omits_message(tmp_path, monkeypatch):
    api = _api(tmp_path, monkeypatch)
    monkeypatch.setattr(firearms, "_get_firearm_row", lambda *_: (_ for _ in ()).throw(sqlite3.OperationalError(MARKER)))
    result = api.get_firearm(1)
    assert not result["ok"]
    text = _log_text(tmp_path)
    assert "OperationalError" in text
    assert MARKER not in text
