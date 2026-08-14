"""
Tests for the insured_value and storage_location fields on create_firearm /
update_firearm in simple_firearm_logbook.py.

insured_value is a money field: it goes through the same optional-decimal
validation as purchase_price / estimated_value, so a bad amount must be
rejected and a good one normalized to a plain 2-decimal string. storage_location
is free text: it is only trimmed, never validated. Both must round-trip through
a save and reload.
"""

import simple_firearm_logbook as app


def _api(tmp_path):
    conn = app.open_db(str(tmp_path / "test.db"))
    api = app.Api()
    api.set_conn(conn)
    api.set_db_path(str(tmp_path / "test.db"))
    return api


def test_create_stores_insured_value_and_storage_location(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.create_firearm(
            "Glock", "19", insured_value="1500", storage_location="  Safe A  "
        )
        assert r["ok"], r
        got = api.get_firearm(r["firearm_id"])["firearm"]
        # insured_value normalized to two decimals; storage_location trimmed.
        assert got["insured_value"] == "1500.00"
        assert got["storage_location"] == "Safe A"
    finally:
        api.close_conn()


def test_create_rejects_a_bad_insured_value(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.create_firearm("Glock", "19", insured_value="not a number")
        assert not r["ok"]
    finally:
        api.close_conn()


def test_blank_new_fields_round_trip_as_empty(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.create_firearm("Glock", "19")
        got = api.get_firearm(r["firearm_id"])["firearm"]
        assert got["insured_value"] == ""
        assert got["storage_location"] == ""
    finally:
        api.close_conn()


def test_update_changes_the_new_fields(tmp_path):
    api = _api(tmp_path)
    try:
        fid = api.create_firearm(
            "Glock", "19", insured_value="1000", storage_location="Safe A"
        )["firearm_id"]
        r = api.update_firearm(
            fid, "Glock", "19", insured_value="2000.5", storage_location="Safe B"
        )
        assert r["ok"], r
        got = api.get_firearm(fid)["firearm"]
        assert got["insured_value"] == "2000.50"
        assert got["storage_location"] == "Safe B"
    finally:
        api.close_conn()
