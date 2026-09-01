"""CSV import: parsing, header auto-mapping, and per-row validation. Pure
functions with no db or pywebview dependency, so they are unit-testable
directly; the import service functions are thin wrappers that add the file
picker, the database, and the transaction. Field order matches
_build_csv_text's header, minus "Log Number": the app owns numbering, so
an imported log number is read-only and never honored."""
import csv
import datetime
import io

from sfl.config import CSV_FORMULA_LEAD_CHARS, DISPOSITION_STATUSES, IMPORT_FIELDS
from sfl.db import _get_next_log_number
from sfl.utils import parse_decimal_optional, parse_iso_date_optional


def read_import_csv_rows(text: str):
    """Parse raw CSV text into a header row and data rows. A row whose
    column count does not match the header is flagged (column_mismatch)
    rather than silently shifted into the wrong fields; its cells are still
    returned as-is for display, but nothing downstream treats them as
    mapped data. Fully blank lines are skipped and not counted as rows.

    Returns (header: list[str], rows: list[dict]), each row dict shaped
    {"row_num": int (1-based, data rows only), "cells": list[str],
    "column_mismatch": bool}."""
    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)
    if not all_rows:
        return [], []
    header = all_rows[0]
    ncols = len(header)
    rows = []
    row_num = 0
    for cells in all_rows[1:]:
        if not cells or all((c or "").strip() == "" for c in cells):
            continue
        row_num += 1
        rows.append(
            {
                "row_num": row_num,
                "cells": cells,
                "column_mismatch": len(cells) != ncols,
            }
        )
    return header, rows


def guess_column_mapping(header: list) -> dict:
    """Case-insensitive match of header names to our import field keys, so
    a round-trip of our own export maps perfectly. Returns
    {field_key: column_index_or_None}."""
    lower_header = [(h or "").strip().lower() for h in header]
    mapping = {}
    for field_key, label in IMPORT_FIELDS:
        idx = None
        for i, h in enumerate(lower_header):
            if h == label.lower():
                idx = i
                break
        mapping[field_key] = idx
    return mapping


def _yes_no_to_bool(raw) -> bool:
    """The CSV's Yes/No convention for boolean columns. Anything other than
    a case-insensitive 'yes' (including blank) is treated as No."""
    return (raw or "").strip().lower() == "yes"


def _import_cell(cells: list, mapping: dict, field_key: str) -> str:
    idx = mapping.get(field_key)
    if idx is None or idx < 0 or idx >= len(cells):
        return ""
    raw = cells[idx] or ""
    # Reverse _csv_safe's formula-injection guard so our own exports round
    # trip exactly: only drop the apostrophe when it is immediately followed
    # by a character we would have added it for, not a value the user
    # actually typed with a leading apostrophe.
    if len(raw) >= 2 and raw[0] == "'" and raw[1] in CSV_FORMULA_LEAD_CHARS:
        return raw[1:]
    return raw


def build_import_record(cells: list, mapping: dict):
    """Validate and normalize one data row into a firearm record, routing
    every date and amount through the same validators the add screen uses.
    Returns (record_dict, None, None) on success, or (None, reason, cell)
    on the first failure, where cell is the offending field's label."""
    make_s = _import_cell(cells, mapping, "make").strip()
    if not make_s:
        return None, "Make is required.", "Make"
    model_s = _import_cell(cells, mapping, "model").strip()
    if not model_s:
        return None, "Model is required.", "Model"

    date_s, err = parse_iso_date_optional(_import_cell(cells, mapping, "acquisition_date"))
    if err:
        return None, err, "Acquisition Date"
    price_s, err = parse_decimal_optional(_import_cell(cells, mapping, "purchase_price"))
    if err:
        return None, err, "Purchase Price"
    value_s, err = parse_decimal_optional(_import_cell(cells, mapping, "estimated_value"))
    if err:
        return None, err, "Estimated Value"
    insured_s, err = parse_decimal_optional(_import_cell(cells, mapping, "insured_value"))
    if err:
        return None, err, "Insured Value"
    nfa_stamp_date_s, err = parse_iso_date_optional(_import_cell(cells, mapping, "nfa_stamp_date"))
    if err:
        return None, err, "NFA Stamp Date"
    disposition_date_s, err = parse_iso_date_optional(_import_cell(cells, mapping, "disposition_date"))
    if err:
        return None, err, "Disposition Date"
    disposition_amount_s, err = parse_decimal_optional(_import_cell(cells, mapping, "disposition_amount"))
    if err:
        return None, err, "Disposition Amount"

    disposition_status_s = _import_cell(cells, mapping, "disposition_status").strip() or "Owned"
    if disposition_status_s not in DISPOSITION_STATUSES:
        return None, "Enter a valid disposition status.", "Disposition Status"

    held_in_trust_i = 1 if _yes_no_to_bool(_import_cell(cells, mapping, "held_in_trust")) else 0
    trust_name_s = _import_cell(cells, mapping, "trust_name").strip()
    is_nfa_i = 1 if _yes_no_to_bool(_import_cell(cells, mapping, "is_nfa")) else 0
    nfa_form_type_s = _import_cell(cells, mapping, "nfa_form_type").strip()
    if not held_in_trust_i:
        trust_name_s = ""
    if not is_nfa_i:
        nfa_form_type_s = ""
        nfa_stamp_date_s = ""

    disposition_to_s = _import_cell(cells, mapping, "disposition_to").strip()
    disposition_address_s = _import_cell(cells, mapping, "disposition_address").strip()
    disposition_notes_s = _import_cell(cells, mapping, "disposition_notes").strip()
    if disposition_status_s == "Owned":
        disposition_date_s = disposition_to_s = disposition_address_s = ""
        disposition_amount_s = disposition_notes_s = ""

    record = {
        "make": make_s,
        "model": model_s,
        "serial_number": _import_cell(cells, mapping, "serial_number").strip(),
        "firearm_type": _import_cell(cells, mapping, "firearm_type").strip(),
        "sub_type": _import_cell(cells, mapping, "sub_type").strip(),
        "caliber": _import_cell(cells, mapping, "caliber").strip(),
        "acquisition_date": date_s,
        "acquired_from": _import_cell(cells, mapping, "acquired_from").strip(),
        "purchase_price": price_s,
        "estimated_value": value_s,
        "insured_value": insured_s,
        "storage_location": _import_cell(cells, mapping, "storage_location").strip(),
        "held_in_trust": held_in_trust_i,
        "trust_name": trust_name_s,
        "is_nfa": is_nfa_i,
        "nfa_form_type": nfa_form_type_s,
        "nfa_stamp_date": nfa_stamp_date_s,
        "notes": _import_cell(cells, mapping, "notes").strip(),
        "disposition_status": disposition_status_s,
        "disposition_date": disposition_date_s,
        "disposition_to": disposition_to_s,
        "disposition_address": disposition_address_s,
        "disposition_amount": disposition_amount_s,
        "disposition_notes": disposition_notes_s,
    }
    return record, None, None


def build_import_preview(rows: list, mapping: dict, existing_serials) -> list:
    """Runs every row through build_import_record and classifies it for the
    preview: "error" (wrong column count or a failed validator, never
    importable), "duplicate" (serial already in the collection, a soft
    warning the user decides on per row), or "ok"."""
    existing = {s for s in (existing_serials or []) if s}
    preview = []
    for row in rows:
        row_num = row["row_num"]
        if row["column_mismatch"]:
            preview.append(
                {
                    "row_num": row_num,
                    "status": "error",
                    "reason": "Wrong number of columns for this row.",
                    "cell": None,
                    "record": None,
                }
            )
            continue
        record, reason, cell = build_import_record(row["cells"], mapping)
        if record is None:
            preview.append(
                {"row_num": row_num, "status": "error", "reason": reason, "cell": cell, "record": None}
            )
            continue
        if record["serial_number"] and record["serial_number"] in existing:
            preview.append(
                {
                    "row_num": row_num,
                    "status": "duplicate",
                    "reason": "This serial number already exists in your collection.",
                    "cell": "Serial Number",
                    "record": record,
                }
            )
            continue
        preview.append({"row_num": row_num, "status": "ok", "reason": None, "cell": None, "record": record})
    return preview


def _insert_imported_firearm(cur, record: dict) -> str:
    """Insert one already-validated import record, honoring its disposition
    fields (unlike create_firearm, which always forces new firearms to
    'Owned'). Returns the freshly claimed log number."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    log_number = _get_next_log_number(cur)
    cur.execute(
        "INSERT INTO firearms (log_number, make, model, serial_number, firearm_type, caliber, "
        "acquisition_date, acquired_from, purchase_price, estimated_value, "
        "insured_value, storage_location, notes, "
        "sub_type, held_in_trust, trust_name, is_nfa, nfa_form_type, nfa_stamp_date, "
        "disposition_status, disposition_date, disposition_to, disposition_address, "
        "disposition_amount, disposition_notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            log_number, record["make"], record["model"], record["serial_number"], record["firearm_type"],
            record["caliber"], record["acquisition_date"], record["acquired_from"], record["purchase_price"],
            record["estimated_value"], record["insured_value"], record["storage_location"], record["notes"],
            record["sub_type"], record["held_in_trust"], record["trust_name"], record["is_nfa"],
            record["nfa_form_type"], record["nfa_stamp_date"],
            record["disposition_status"], record["disposition_date"], record["disposition_to"],
            record["disposition_address"], record["disposition_amount"], record["disposition_notes"],
            now, now,
        ),
    )
    return log_number
