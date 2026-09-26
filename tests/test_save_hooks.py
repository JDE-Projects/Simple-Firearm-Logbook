"""Tests for the embedding app firearm-save hook."""
import os

import pytest

import simple_firearm_logbook as app
from sfl import config, paths
from sfl.db import SaveRejected


def _record(make="Glock", model="19"):
    return {
        "make": make, "model": model, "serial_number": "", "firearm_type": "",
        "sub_type": "", "caliber": "", "acquisition_date": "", "acquired_from": "",
        "purchase_price": "", "estimated_value": "", "insured_value": "",
        "storage_location": "", "held_in_trust": 0, "trust_name": "", "is_nfa": 0,
        "nfa_form_type": "", "nfa_stamp_date": "", "notes": "",
        "disposition_status": "Owned", "disposition_date": "", "disposition_to": "",
        "disposition_address": "", "disposition_amount": "", "disposition_notes": "",
    }


class RecordingApi(app.Api):
    def __init__(self):
        super().__init__()
        self.events = []

    def _before_save(self, cur, event):
        cur.execute("CREATE TABLE IF NOT EXISTS hook_rows (action TEXT)")
        cur.execute("INSERT INTO hook_rows VALUES (?)", (event["action"],))
        self.events.append(event)


def _api(tmp_path, cls=RecordingApi):
    api = cls()
    api.set_conn(app.open_db(str(tmp_path / "test.db")))
    return api


def _add(api):
    result = api.create_firearm("Glock", "19", extension_data={"source": "test"})
    assert result["ok"], result
    return result["firearm_id"]


@pytest.mark.parametrize("action", ["created", "updated", "disposition", "deleted", "imported"])
def test_each_write_path_calls_hook_in_its_transaction(tmp_path, action):
    api = _api(tmp_path)
    try:
        if action == "created":
            firearm_id = _add(api)
        else:
            firearm_id = _add(api)
            api.events.clear()
            if action == "updated":
                result = api.update_firearm(firearm_id, "Sig", "P320", extension_data="edit")
            elif action == "disposition":
                result = api.update_disposition(firearm_id, "Sold", extension_data="status")
            elif action == "deleted":
                result = api.delete_firearm(firearm_id, extension_data="remove")
            else:
                result = api.import_csv_commit([_record("Ruger", "LCP")])
            assert result["ok"], result
        event = api.events[-1]
        assert event["action"] == action
        assert event["firearm_id"]
        assert event["old"] is None if action in {"created", "imported"} else event["old"] is not None
        assert event["new"] is None if action == "deleted" else event["new"] is not None
        assert event["extension_data"] == ({"source": "test"} if action == "created" else None if action == "imported" else {"updated": "edit", "disposition": "status", "deleted": "remove"}[action])
        assert api._conn.execute("SELECT action FROM hook_rows").fetchall()[-1][0] == action
    finally:
        api.close_conn()


def test_import_calls_the_hook_for_each_row(tmp_path):
    api = _api(tmp_path)
    try:
        result = api.import_csv_commit([_record("Glock", "19"), _record("Sig", "P320")])
        assert result["ok"], result
        assert [event["action"] for event in api.events] == ["imported", "imported"]
        assert api._conn.execute("SELECT COUNT(*) FROM hook_rows").fetchone()[0] == 2
    finally:
        api.close_conn()


class RejectingApi(app.Api):
    def _before_save(self, cur, event):
        raise SaveRejected("Not allowed")


class LaterImportRejectingApi(app.Api):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def _before_save(self, cur, event):
        self.calls += 1
        if self.calls == 2:
            raise SaveRejected("Not allowed")


@pytest.mark.parametrize("action", ["created", "updated", "disposition", "deleted", "imported"])
def test_rejected_write_leaves_database_unchanged(tmp_path, action):
    api = _api(tmp_path, RejectingApi)
    try:
        if action == "created":
            result = api.create_firearm("Glock", "19")
        else:
            api._conn.execute(
                "INSERT INTO firearms (log_number, make, model, created_at, updated_at) VALUES ('00001', 'Glock', '19', 'x', 'x')"
            )
            api._conn.execute("UPDATE counters SET next_log_number=2 WHERE id=1")
            api._conn.commit()
            if action == "updated":
                result = api.update_firearm(1, "Sig", "P320")
            elif action == "disposition":
                result = api.update_disposition(1, "Sold")
            elif action == "deleted":
                result = api.delete_firearm(1)
            else:
                result = api.import_csv_commit([_record()])
        assert result == {"ok": False, "error": "Not allowed"}
        if action == "created":
            assert api._conn.execute("SELECT COUNT(*) FROM firearms").fetchone()[0] == 0
            assert api._conn.execute("SELECT next_log_number FROM counters").fetchone()[0] == 1
        elif action == "imported":
            assert api._conn.execute("SELECT COUNT(*) FROM firearms").fetchone()[0] == 1
            assert api._conn.execute("SELECT next_log_number FROM counters").fetchone()[0] == 2
        else:
            assert api._conn.execute("SELECT make FROM firearms WHERE id=1").fetchone()[0] == "Glock"
            if action == "disposition":
                assert api._conn.execute(
                    "SELECT disposition_status FROM firearms WHERE id=1"
                ).fetchone()[0] == "Owned"
    finally:
        api.close_conn()


def test_later_import_rejection_rolls_back_every_row_and_counter(tmp_path):
    api = _api(tmp_path, LaterImportRejectingApi)
    try:
        result = api.import_csv_commit([_record("Glock", "19"), _record("Sig", "P320")])
        assert result == {"ok": False, "error": "Not allowed"}
        assert api._conn.execute("SELECT COUNT(*) FROM firearms").fetchone()[0] == 0
        assert api._conn.execute("SELECT next_log_number FROM counters").fetchone()[0] == 1
    finally:
        api.close_conn()


class BrokenApi(app.Api):
    def _before_save(self, cur, event):
        raise RuntimeError("broken hook")


@pytest.mark.parametrize("action,error", [
    ("created", "Couldn't save the firearm."),
    ("updated", "Couldn't save the firearm."),
    ("disposition", "Couldn't save the disposition."),
    ("deleted", "Couldn't delete the firearm."),
    ("imported", "Couldn't import the file. Nothing was saved."),
])
def test_hook_error_uses_existing_generic_error(tmp_path, action, error):
    api = _api(tmp_path, BrokenApi)
    try:
        if action == "created":
            result = api.create_firearm("Glock", "19")
        elif action == "imported":
            result = api.import_csv_commit([_record()])
        else:
            api._conn.execute(
                "INSERT INTO firearms (log_number, make, model, created_at, updated_at) VALUES ('00001', 'Glock', '19', 'x', 'x')"
            )
            api._conn.commit()
            if action == "updated":
                result = api.update_firearm(1, "Sig", "P320")
            elif action == "disposition":
                result = api.update_disposition(1, "Sold")
            else:
                result = api.delete_firearm(1)
        assert result["error"] == error
        if action in {"created", "imported"}:
            assert api._conn.execute("SELECT COUNT(*) FROM firearms").fetchone()[0] == 0
            assert api._conn.execute("SELECT next_log_number FROM counters").fetchone()[0] == 1
        else:
            assert api._conn.execute("SELECT make FROM firearms WHERE id=1").fetchone()[0] == "Glock"
            if action == "disposition":
                assert api._conn.execute(
                    "SELECT disposition_status FROM firearms WHERE id=1"
                ).fetchone()[0] == "Owned"
    finally:
        api.close_conn()


def test_failed_delete_leaves_files(tmp_path, monkeypatch):
    api = _api(tmp_path, RejectingApi)
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    try:
        api._conn.execute(
            "INSERT INTO firearms (log_number, make, model, created_at, updated_at) VALUES ('00001', 'Glock', '19', 'x', 'x')"
        )
        api._conn.execute("INSERT INTO photos (firearm_id, filename, seq) VALUES (1, 'one.jpg', 1)")
        api._conn.execute("INSERT INTO attachments (firearm_id, filename, label, seq) VALUES (1, 'one.pdf', 'one', 1)")
        api._conn.commit()
        for folder, name in ((config.PHOTOS_DIRNAME, "one.jpg"), (config.ATTACHMENTS_DIRNAME, "one.pdf")):
            os.makedirs(tmp_path / folder, exist_ok=True)
            (tmp_path / folder / name).write_bytes(b"x")
        assert api.delete_firearm(1)["error"] == "Not allowed"
        assert (tmp_path / config.PHOTOS_DIRNAME / "one.jpg").is_file()
        assert (tmp_path / config.ATTACHMENTS_DIRNAME / "one.pdf").is_file()
    finally:
        api.close_conn()
