"""CSV export: the full-collection CSV built for export_full, with a
formula-injection guard on every string cell."""
import csv
import io

from sfl.config import CSV_FORMULA_LEAD_CHARS


def _csv_safe(value):
    """Neutralize CSV formula injection: if value is a non-empty string
    starting with a character a spreadsheet would treat as a formula
    trigger, prefix it with an apostrophe so the app opens it as plain
    text. Non-string values pass through untouched."""
    if isinstance(value, str) and value and value[0] in CSV_FORMULA_LEAD_CHARS:
        return "'" + value
    return value


def _build_csv_text(firearms: list) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Log Number", "Make", "Model", "Serial Number", "Type", "Sub-Type", "Caliber",
            "Acquisition Date", "Acquired From", "Purchase Price", "Estimated Value",
            "Insured Value", "Storage Location",
            "Held In Trust", "Trust Name", "NFA", "NFA Form Type", "NFA Stamp Date",
            "Notes",
            "Disposition Status", "Disposition Date", "Disposition To", "Disposition Address",
            "Disposition Amount", "Disposition Notes",
        ]
    )
    for f in firearms:
        writer.writerow(
            [
                _csv_safe(f["log_number"]), _csv_safe(f["make"]), _csv_safe(f["model"]),
                _csv_safe(f["serial_number"]), _csv_safe(f["firearm_type"]), _csv_safe(f["sub_type"]),
                _csv_safe(f["caliber"]),
                _csv_safe(f["acquisition_date"]), _csv_safe(f["acquired_from"]), _csv_safe(f["purchase_price"]),
                _csv_safe(f["estimated_value"]),
                _csv_safe(f["insured_value"]), _csv_safe(f["storage_location"]),
                "Yes" if f["held_in_trust"] else "No", _csv_safe(f["trust_name"]),
                "Yes" if f["is_nfa"] else "No", _csv_safe(f["nfa_form_type"]), _csv_safe(f["nfa_stamp_date"]),
                _csv_safe(f["notes"]),
                _csv_safe(f["disposition_status"]), _csv_safe(f["disposition_date"]), _csv_safe(f["disposition_to"]),
                _csv_safe(f["disposition_address"]),
                _csv_safe(f["disposition_amount"]), _csv_safe(f["disposition_notes"]),
            ]
        )
    return buf.getvalue()
