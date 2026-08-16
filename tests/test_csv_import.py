"""
Tests for CSV import in simple_firearm_logbook.py: read_import_csv_rows,
guess_column_mapping, build_import_record, build_import_preview,
_insert_imported_firearm, and the Api wrappers import_csv_pick /
import_csv_preview / import_csv_commit.

The pure parsing/mapping/validation functions take plain text/rows and
return data, with no db or pywebview dependency, so most of this is tested
directly. import_csv_commit is tested through a real open_db connection to
prove the single-transaction, all-or-nothing rollback guarantee.
"""

import sqlite3

import simple_firearm_logbook as app


def _api(tmp_path):
    conn = app.open_db(str(tmp_path / "test.db"))
    api = app.Api()
    api.set_conn(conn)
    api.set_db_path(str(tmp_path / "test.db"))
    return api


def _our_export_header():
    return app._build_csv_text([]).splitlines()[0].split(",")


# ─────────────────────────────────────────────────────────────
#  read_import_csv_rows: parsing, BOM handling, column mismatch
# ─────────────────────────────────────────────────────────────
def test_reads_header_and_rows():
    text = "Make,Model\nGlock,19\nSig,P320\n"
    header, rows = read_import_csv_rows_helper(text)
    assert header == ["Make", "Model"]
    assert len(rows) == 2
    assert rows[0]["cells"] == ["Glock", "19"]
    assert rows[0]["row_num"] == 1
    assert rows[1]["row_num"] == 2


def test_utf8_bom_is_tolerated():
    # Simulates reading a file opened with encoding="utf-8-sig": the BOM is
    # already stripped by the time the text reaches the parser.
    raw_bytes = "﻿Make,Model\nGlock,19\n".encode("utf-8")
    text = raw_bytes.decode("utf-8-sig")
    header, rows = read_import_csv_rows_helper(text)
    assert header == ["Make", "Model"]
    assert not header[0].startswith("﻿")
    assert rows[0]["cells"] == ["Glock", "19"]


def test_wrong_column_count_is_flagged():
    text = "Make,Model,Caliber\nGlock,19\nSig,P320,9mm\n"
    header, rows = read_import_csv_rows_helper(text)
    assert rows[0]["column_mismatch"] is True
    assert rows[1]["column_mismatch"] is False


def test_blank_lines_are_skipped_and_not_counted():
    text = "Make,Model\nGlock,19\n\nSig,P320\n"
    header, rows = read_import_csv_rows_helper(text)
    assert len(rows) == 2
    assert rows[1]["row_num"] == 2


def test_empty_text_returns_no_header_or_rows():
    header, rows = read_import_csv_rows_helper("")
    assert header == []
    assert rows == []


def read_import_csv_rows_helper(text):
    return app.read_import_csv_rows(text)


# ─────────────────────────────────────────────────────────────
#  guess_column_mapping: our own export round-trips perfectly
# ─────────────────────────────────────────────────────────────
def test_our_own_export_header_maps_every_field():
    header = _our_export_header()
    mapping = app.guess_column_mapping(header)
    for field_key, _label in app.IMPORT_FIELDS:
        assert mapping[field_key] is not None, f"{field_key} did not auto-map"


def test_mapping_is_case_insensitive():
    header = ["make", "MODEL", "Serial Number"]
    mapping = app.guess_column_mapping(header)
    assert mapping["make"] == 0
    assert mapping["model"] == 1
    assert mapping["serial_number"] == 2


def test_unrecognized_header_maps_to_none():
    header = ["Widget Name"]
    mapping = app.guess_column_mapping(header)
    assert mapping["make"] is None
    assert mapping["model"] is None


# ─────────────────────────────────────────────────────────────
#  build_import_record: validator rejections and their cell/reason
# ─────────────────────────────────────────────────────────────
def _mapping_for(*header_labels):
    header = list(header_labels)
    return header, app.guess_column_mapping(header)


def test_missing_make_is_rejected_with_cell():
    header, mapping = _mapping_for("Make", "Model")
    record, reason, cell = app.build_import_record(["", "19"], mapping)
    assert record is None
    assert cell == "Make"
    assert reason


def test_missing_model_is_rejected_with_cell():
    header, mapping = _mapping_for("Make", "Model")
    record, reason, cell = app.build_import_record(["Glock", ""], mapping)
    assert record is None
    assert cell == "Model"


def test_bad_acquisition_date_is_rejected_with_cell():
    header, mapping = _mapping_for("Make", "Model", "Acquisition Date")
    record, reason, cell = app.build_import_record(["Glock", "19", "not-a-date"], mapping)
    assert record is None
    assert cell == "Acquisition Date"


def test_bad_purchase_price_is_rejected_with_cell():
    header, mapping = _mapping_for("Make", "Model", "Purchase Price")
    record, reason, cell = app.build_import_record(["Glock", "19", "abc"], mapping)
    assert record is None
    assert cell == "Purchase Price"


def test_bad_disposition_status_is_rejected_with_cell():
    header, mapping = _mapping_for("Make", "Model", "Disposition Status")
    record, reason, cell = app.build_import_record(["Glock", "19", "Melted"], mapping)
    assert record is None
    assert cell == "Disposition Status"


def test_valid_row_builds_a_normalized_record():
    header, mapping = _mapping_for("Make", "Model", "Purchase Price")
    record, reason, cell = app.build_import_record(["Glock", "19", "500"], mapping)
    assert reason is None
    assert record["make"] == "Glock"
    assert record["model"] == "19"
    assert record["purchase_price"] == "500.00"


# ─────────────────────────────────────────────────────────────
#  Yes/No boolean parsing
# ─────────────────────────────────────────────────────────────
def test_yes_no_parsing_for_trust_and_nfa():
    header, mapping = _mapping_for("Make", "Model", "Held In Trust", "NFA")
    record, _reason, _cell = app.build_import_record(["Glock", "19", "Yes", "No"], mapping)
    assert record["held_in_trust"] == 1
    assert record["is_nfa"] == 0


def test_yes_no_parsing_is_case_insensitive_and_defaults_no():
    header, mapping = _mapping_for("Make", "Model", "Held In Trust", "NFA")
    record, _reason, _cell = app.build_import_record(["Glock", "19", "YES", "maybe"], mapping)
    assert record["held_in_trust"] == 1
    assert record["is_nfa"] == 0


def test_trust_name_cleared_when_not_held_in_trust():
    header, mapping = _mapping_for("Make", "Model", "Held In Trust", "Trust Name")
    record, _reason, _cell = app.build_import_record(["Glock", "19", "No", "The Smith Trust"], mapping)
    assert record["trust_name"] == ""


def test_nfa_fields_cleared_when_not_nfa():
    header, mapping = _mapping_for("Make", "Model", "NFA", "NFA Form Type", "NFA Stamp Date")
    record, _reason, _cell = app.build_import_record(
        ["Glock", "19", "No", "Form 4", "2024-01-15"], mapping
    )
    assert record["nfa_form_type"] == ""
    assert record["nfa_stamp_date"] == ""


# ─────────────────────────────────────────────────────────────
#  Disposition columns carried through (not forced to Owned)
# ─────────────────────────────────────────────────────────────
def test_disposition_fields_are_honored_not_forced_to_owned():
    header, mapping = _mapping_for(
        "Make", "Model", "Disposition Status", "Disposition To", "Disposition Amount"
    )
    record, _reason, _cell = app.build_import_record(
        ["Glock", "19", "Sold", "John Smith", "450"], mapping
    )
    assert record["disposition_status"] == "Sold"
    assert record["disposition_to"] == "John Smith"
    assert record["disposition_amount"] == "450.00"


def test_owned_disposition_clears_the_rest_of_the_disposition_fields():
    header, mapping = _mapping_for("Make", "Model", "Disposition Status", "Disposition To")
    record, _reason, _cell = app.build_import_record(["Glock", "19", "Owned", "John Smith"], mapping)
    assert record["disposition_status"] == "Owned"
    assert record["disposition_to"] == ""


def test_blank_disposition_status_defaults_to_owned():
    header, mapping = _mapping_for("Make", "Model")
    record, _reason, _cell = app.build_import_record(["Glock", "19"], mapping)
    assert record["disposition_status"] == "Owned"


# ─────────────────────────────────────────────────────────────
#  build_import_preview: row classification, log number ignored,
#  wrong column count flagged loudly, duplicate serial soft-warned
# ─────────────────────────────────────────────────────────────
def test_wrong_column_count_row_is_an_error_never_shifted():
    rows = [{"row_num": 1, "cells": ["Glock"], "column_mismatch": True}]
    mapping = app.guess_column_mapping(["Make", "Model"])
    preview = app.build_import_preview(rows, mapping, existing_serials=set())
    assert preview[0]["status"] == "error"
    assert preview[0]["record"] is None


def test_valid_rows_are_ok():
    header, mapping = _mapping_for("Make", "Model")
    rows = [{"row_num": 1, "cells": ["Glock", "19"], "column_mismatch": False}]
    preview = app.build_import_preview(rows, mapping, existing_serials=set())
    assert preview[0]["status"] == "ok"
    assert preview[0]["record"]["make"] == "Glock"


def test_duplicate_serial_is_a_soft_warning_not_a_hard_block():
    header, mapping = _mapping_for("Make", "Model", "Serial Number")
    rows = [{"row_num": 1, "cells": ["Glock", "19", "ABC123"], "column_mismatch": False}]
    preview = app.build_import_preview(rows, mapping, existing_serials={"ABC123"})
    assert preview[0]["status"] == "duplicate"
    # A record is still built, so the user can choose to import it anyway.
    assert preview[0]["record"] is not None


def test_log_number_column_is_never_used_for_mapping():
    header = ["Log Number", "Make", "Model"]
    mapping = app.guess_column_mapping(header)
    assert "log_number" not in mapping


# ─────────────────────────────────────────────────────────────
#  Commit: fresh log number assigned, single-transaction rollback
# ─────────────────────────────────────────────────────────────
def _record(**overrides):
    base = {
        "make": "Glock", "model": "19", "serial_number": "", "firearm_type": "",
        "sub_type": "", "caliber": "", "acquisition_date": "", "acquired_from": "",
        "purchase_price": "", "estimated_value": "", "insured_value": "",
        "storage_location": "", "held_in_trust": 0, "trust_name": "", "is_nfa": 0,
        "nfa_form_type": "", "nfa_stamp_date": "", "notes": "",
        "disposition_status": "Owned", "disposition_date": "", "disposition_to": "",
        "disposition_address": "", "disposition_amount": "", "disposition_notes": "",
    }
    base.update(overrides)
    return base


def test_commit_assigns_a_fresh_log_number_ignoring_any_imported_one(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.import_csv_commit([_record(make="Glock", model="19")])
        assert r["ok"], r
        assert r["imported"] == 1
        assert r["log_numbers"] == ["00001"]
        got = api.get_firearm(1)["firearm"]
        assert got["log_number"] == "00001"
    finally:
        api.close_conn()


def test_commit_honors_disposition_fields_via_api(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.import_csv_commit([
            _record(make="Colt", model="1911", disposition_status="Sold", disposition_to="Jane Doe")
        ])
        assert r["ok"], r
        got = api.get_firearm(1)["firearm"]
        assert got["disposition_status"] == "Sold"
        assert got["disposition_to"] == "Jane Doe"
    finally:
        api.close_conn()


def test_commit_is_a_single_transaction_all_or_nothing(tmp_path, monkeypatch):
    api = _api(tmp_path)
    try:
        real_insert = app._insert_imported_firearm
        calls = {"n": 0}

        def flaky_insert(cur, record):
            calls["n"] += 1
            if calls["n"] == 2:
                raise sqlite3.OperationalError("simulated failure")
            return real_insert(cur, record)

        monkeypatch.setattr(app, "_insert_imported_firearm", flaky_insert)
        r = api.import_csv_commit([
            _record(make="Glock", model="19"),
            _record(make="Sig", model="P320"),
            _record(make="Ruger", model="LCP"),
        ])
        assert r["ok"] is False
        count = api._conn.execute("SELECT COUNT(*) FROM firearms").fetchone()[0]
        assert count == 0, "a failed row must roll back the whole batch, not just itself"
    finally:
        api.close_conn()


def test_commit_with_no_records_is_an_error(tmp_path):
    api = _api(tmp_path)
    try:
        r = api.import_csv_commit([])
        assert r["ok"] is False
    finally:
        api.close_conn()
