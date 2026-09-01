"""
Tests for the Feature A categorize/filter fields on create_firearm /
update_firearm in simple_firearm_logbook.py: sub_type, held_in_trust,
trust_name, is_nfa, nfa_form_type, nfa_stamp_date.

held_in_trust and is_nfa are boolean flags coerced to 0/1. trust_name and the
NFA text fields are free text, trimmed. nfa_stamp_date goes through the same
optional-date validation as the other date fields. Turning a flag off must
clear its dependent text fields, mirroring how update_disposition clears its
fields when status goes back to Owned.
"""

import simple_firearm_logbook as app


def _api(tmp_path):
    conn = app.open_db(str(tmp_path / "test.db"))
    api = app.Api()
    api.set_conn(conn)
    return api


def test_create_stores_all_six_new_fields(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.create_firearm(
            "Colt", "AR-15", sub_type="Semi-auto",
            held_in_trust=1, trust_name="  The Smith Trust  ",
            is_nfa=1, nfa_form_type="Form 4", nfa_stamp_date="2024-01-15",
        )
        assert r["ok"], r
        got = api.get_firearm(r["firearm_id"])["firearm"]
        assert got["sub_type"] == "Semi-auto"
        assert got["held_in_trust"] == 1
        assert got["trust_name"] == "The Smith Trust"
        assert got["is_nfa"] == 1
        assert got["nfa_form_type"] == "Form 4"
        assert got["nfa_stamp_date"] == "2024-01-15"
    finally:
        api.close_conn()


def test_create_coerces_flags_to_zero_or_one(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.create_firearm("Glock", "19", held_in_trust=True, is_nfa=False)
        assert r["ok"], r
        got = api.get_firearm(r["firearm_id"])["firearm"]
        assert got["held_in_trust"] == 1
        assert got["is_nfa"] == 0
    finally:
        api.close_conn()


def test_create_rejects_a_bad_nfa_stamp_date(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.create_firearm("Glock", "19", is_nfa=1, nfa_stamp_date="not a date")
        assert not r["ok"]
    finally:
        api.close_conn()


def test_create_clears_trust_name_when_not_held_in_trust(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.create_firearm("Glock", "19", held_in_trust=0, trust_name="Ignored Trust")
        assert r["ok"], r
        got = api.get_firearm(r["firearm_id"])["firearm"]
        assert got["held_in_trust"] == 0
        assert got["trust_name"] == ""
    finally:
        api.close_conn()


def test_create_clears_nfa_fields_when_not_nfa(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.create_firearm(
            "Glock", "19", is_nfa=0, nfa_form_type="Form 4", nfa_stamp_date="2024-01-15"
        )
        assert r["ok"], r
        got = api.get_firearm(r["firearm_id"])["firearm"]
        assert got["is_nfa"] == 0
        assert got["nfa_form_type"] == ""
        assert got["nfa_stamp_date"] == ""
    finally:
        api.close_conn()


def test_blank_new_fields_round_trip_as_empty(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.create_firearm("Glock", "19")
        got = api.get_firearm(r["firearm_id"])["firearm"]
        assert got["sub_type"] == ""
        assert got["held_in_trust"] == 0
        assert got["trust_name"] == ""
        assert got["is_nfa"] == 0
        assert got["nfa_form_type"] == ""
        assert got["nfa_stamp_date"] == ""
    finally:
        api.close_conn()


def test_update_changes_the_new_fields(tmp_path):
    api = _api(tmp_path)
    try:
        fid = api.create_firearm(
            "Colt", "AR-15", sub_type="Semi-auto",
            held_in_trust=1, trust_name="Old Trust",
            is_nfa=1, nfa_form_type="Form 4", nfa_stamp_date="2024-01-15",
        )["firearm_id"]
        r = api.update_firearm(
            fid, "Colt", "AR-15", sub_type="Bolt",
            held_in_trust=1, trust_name="New Trust",
            is_nfa=1, nfa_form_type="Form 1", nfa_stamp_date="2025-06-01",
        )
        assert r["ok"], r
        got = api.get_firearm(fid)["firearm"]
        assert got["sub_type"] == "Bolt"
        assert got["trust_name"] == "New Trust"
        assert got["nfa_form_type"] == "Form 1"
        assert got["nfa_stamp_date"] == "2025-06-01"
    finally:
        api.close_conn()


def test_update_clears_trust_name_when_trust_turned_off(tmp_path):
    api = _api(tmp_path)
    try:
        fid = api.create_firearm(
            "Glock", "19", held_in_trust=1, trust_name="Old Trust"
        )["firearm_id"]
        r = api.update_firearm(fid, "Glock", "19", held_in_trust=0, trust_name="Old Trust")
        assert r["ok"], r
        got = api.get_firearm(fid)["firearm"]
        assert got["held_in_trust"] == 0
        assert got["trust_name"] == ""
    finally:
        api.close_conn()


def test_update_clears_nfa_fields_when_nfa_turned_off(tmp_path):
    api = _api(tmp_path)
    try:
        fid = api.create_firearm(
            "Colt", "AR-15", is_nfa=1, nfa_form_type="Form 4", nfa_stamp_date="2024-01-15"
        )["firearm_id"]
        r = api.update_firearm(
            fid, "Colt", "AR-15", is_nfa=0, nfa_form_type="Form 4", nfa_stamp_date="2024-01-15"
        )
        assert r["ok"], r
        got = api.get_firearm(fid)["firearm"]
        assert got["is_nfa"] == 0
        assert got["nfa_form_type"] == ""
        assert got["nfa_stamp_date"] == ""
    finally:
        api.close_conn()


def test_update_rejects_a_bad_nfa_stamp_date(tmp_path):
    api = _api(tmp_path)
    try:
        fid = api.create_firearm("Glock", "19")["firearm_id"]
        r = api.update_firearm(fid, "Glock", "19", is_nfa=1, nfa_stamp_date="not a date")
        assert not r["ok"]
    finally:
        api.close_conn()
