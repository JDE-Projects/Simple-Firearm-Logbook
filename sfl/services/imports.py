"""CSV import: file pick, per-row preview/validation, and the committing
transaction. Thin wrappers around sfl.csv_import's pure parsing/validation
functions that add the file picker, the database, and the transaction."""
from sfl import csv_import
from sfl.config import IMPORT_FIELDS
from sfl.db import SaveRejected, _firearm_row_to_dict


def import_csv_pick(window, log):
    """Stage 1: native picker restricted to .csv, read as UTF-8 with a
    BOM tolerated (our own export and a plain spreadsheet "Save As CSV"
    both open cleanly), and parse into a header plus raw rows. Nothing
    touches the database here; the page shows a mapping step next."""
    import webview

    try:
        result = window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("CSV Files (*.csv)",)
        )
        if not result:
            return {"ok": True, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path:
            return {"ok": True, "cancelled": True}
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                text = fh.read()
        except UnicodeDecodeError:
            return {"ok": False, "error": "That file isn't valid UTF-8 text."}
        header, rows = csv_import.read_import_csv_rows(text)
        if not header:
            return {"ok": False, "error": "That CSV file is empty."}
        mapping = csv_import.guess_column_mapping(header)
        log(f"CSV import file picked, {len(rows)} row(s)")
        return {
            "ok": True,
            "header": header,
            "rows": rows,
            "mapping": mapping,
            "fields": [{"key": k, "label": v} for k, v in IMPORT_FIELDS],
        }
    except Exception as e:
        log(f"import_csv_pick failed: {e}")
        return {"ok": False, "error": "Couldn't read that CSV file."}


def import_csv_preview(conn, log, rows, mapping):
    """Stage 2: run every row through the shared validators and classify
    it as ok / duplicate-serial (soft warning) / error (specific reason
    and offending cell). Nothing touches the database except a read of
    existing serials, used only to flag duplicates."""
    try:
        existing_serials = {
            r[0] for r in conn.execute(
                "SELECT serial_number FROM firearms WHERE serial_number<>''"
            ).fetchall()
        }
        preview = csv_import.build_import_preview(rows or [], mapping or {}, existing_serials)
        return {"ok": True, "preview": preview}
    except Exception as e:
        log(f"import_csv_preview failed: {e}")
        return {"ok": False, "error": "Couldn't validate that file."}


def import_csv_commit(conn, log, records, before_save=None):
    """Stage 3: import only the rows the user accepted, in a single
    transaction: all succeed or the whole import rolls back, so a
    half-import is impossible. Each row gets a fresh app-assigned log
    number; an imported Log Number column is never honored."""
    try:
        records = records or []
        if not records:
            return {"ok": False, "error": "No rows were selected to import."}
        cur = conn.cursor()
        imported = []
        for record in records:
            log_number = csv_import._insert_imported_firearm(cur, record)
            imported.append(log_number)
            if before_save:
                firearm_id = cur.lastrowid
                new = _firearm_row_to_dict(
                    cur.execute("SELECT * FROM firearms WHERE id=?", (firearm_id,)).fetchone()
                )
                before_save(cur, {"action": "imported", "firearm_id": firearm_id, "old": None,
                                  "new": new, "extension_data": None})
        conn.commit()
        log(f"CSV import committed, {len(imported)} firearm(s)")
        return {"ok": True, "imported": len(imported), "log_numbers": imported}
    except SaveRejected as e:
        conn.rollback()
        log(f"import_csv_commit rejected: {e}")
        return {"ok": False, "error": str(e)}
    except Exception as e:
        conn.rollback()
        log(f"import_csv_commit failed: {e}")
        return {"ok": False, "error": "Couldn't import the file. Nothing was saved."}
