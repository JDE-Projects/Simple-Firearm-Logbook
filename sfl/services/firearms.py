"""Firearm CRUD, disposition, listing, autocomplete, and app config. Every
function takes its dependencies (conn, and window/log where needed)
explicitly rather than reading them off an object, so there is no hidden
state."""
import datetime
import os

from sfl import config, paths
from sfl.db import _firearm_row_to_dict, _get_next_log_number
from sfl.services import attachments as attachments_service
from sfl.services import photos as photos_service
from sfl.services import settings as settings_service
from sfl.utils import parse_decimal_optional, parse_iso_date_optional


def _get_firearm_row(conn, firearm_id):
    return conn.execute("SELECT * FROM firearms WHERE id=?", (firearm_id,)).fetchone()


def get_config(log):
    try:
        return {"ok": True, "version": config.APP_VERSION, "theme": settings_service._load_theme()}
    except Exception as e:
        log(f"get_config failed: {e}")
        return {"ok": False, "error": "Couldn't load the app's configuration."}


def get_autocomplete(conn, log):
    """Make / caliber / type suggestions drawn from existing entries.
    Type suggestions always include the standard categories first."""
    try:
        cur = conn.cursor()
        makes = [
            r[0] for r in cur.execute(
                "SELECT DISTINCT make FROM firearms WHERE make<>'' ORDER BY make COLLATE NOCASE"
            ).fetchall()
        ]
        calibers = [
            r[0] for r in cur.execute(
                "SELECT DISTINCT caliber FROM firearms WHERE caliber<>'' ORDER BY caliber COLLATE NOCASE"
            ).fetchall()
        ]
        existing_types = [
            r[0] for r in cur.execute(
                "SELECT DISTINCT firearm_type FROM firearms WHERE firearm_type<>'' ORDER BY firearm_type COLLATE NOCASE"
            ).fetchall()
        ]
        types = list(config.STANDARD_FIREARM_TYPES)
        for t in existing_types:
            if t not in types:
                types.append(t)
        return {"ok": True, "makes": makes, "calibers": calibers, "types": types}
    except Exception as e:
        log(f"get_autocomplete failed: {e}")
        return {"ok": False, "error": "Couldn't load suggestions."}


def list_firearms(conn, log):
    try:
        rows = conn.execute("SELECT * FROM firearms ORDER BY log_number").fetchall()
        firearms = []
        for r in rows:
            d = _firearm_row_to_dict(r)
            photos = photos_service._get_photos(conn, r["id"])
            primary = next((p for p in photos if p["is_primary"]), photos[0] if photos else None)
            d["photo_count"] = len(photos)
            d["primary_photo_filename"] = primary["filename"] if primary else None
            attachment_count, attachment_bytes, attachment_missing = attachments_service._attachment_stats(
                conn, r["id"]
            )
            d["attachment_count"] = attachment_count
            d["attachment_bytes"] = attachment_bytes
            d["attachment_missing"] = attachment_missing
            firearms.append(d)
        return {"ok": True, "firearms": firearms}
    except Exception as e:
        log(f"list_firearms failed: {e}")
        return {"ok": False, "error": "Couldn't load the firearms list."}


def get_firearm(conn, firearm_id, log):
    try:
        row = _get_firearm_row(conn, firearm_id)
        if row is None:
            return {"ok": False, "error": "That firearm no longer exists."}
        return {
            "ok": True,
            "firearm": _firearm_row_to_dict(row),
            "photos": photos_service._get_photos(conn, firearm_id),
            "attachments": attachments_service._get_attachments(conn, firearm_id),
        }
    except Exception as e:
        log(f"get_firearm failed: {e}")
        return {"ok": False, "error": "Couldn't load that firearm."}


def create_firearm(conn, log, make, model, serial_number="", firearm_type="", caliber="",
                    acquisition_date="", acquired_from="", purchase_price="",
                    estimated_value="", insured_value="", storage_location="", notes="",
                    sub_type="", held_in_trust=0, trust_name="", is_nfa=0,
                    nfa_form_type="", nfa_stamp_date=""):
    try:
        make_s = (make or "").strip()
        model_s = (model or "").strip()
        if not make_s or not model_s:
            return {"ok": False, "error": "Make and model are required."}
        date_s, err = parse_iso_date_optional(acquisition_date)
        if err:
            return {"ok": False, "error": err}
        price_s, err = parse_decimal_optional(purchase_price)
        if err:
            return {"ok": False, "error": err}
        value_s, err = parse_decimal_optional(estimated_value)
        if err:
            return {"ok": False, "error": err}
        insured_s, err = parse_decimal_optional(insured_value)
        if err:
            return {"ok": False, "error": err}
        nfa_stamp_date_s, err = parse_iso_date_optional(nfa_stamp_date)
        if err:
            return {"ok": False, "error": err}
        serial_s = (serial_number or "").strip()
        type_s = (firearm_type or "").strip()
        caliber_s = (caliber or "").strip()
        acquired_from_s = (acquired_from or "").strip()
        storage_s = (storage_location or "").strip()
        notes_s = (notes or "").strip()
        sub_type_s = (sub_type or "").strip()
        held_in_trust_i = 1 if held_in_trust else 0
        trust_name_s = (trust_name or "").strip()
        is_nfa_i = 1 if is_nfa else 0
        nfa_form_type_s = (nfa_form_type or "").strip()
        if not held_in_trust_i:
            trust_name_s = ""
        if not is_nfa_i:
            nfa_form_type_s = ""
            nfa_stamp_date_s = ""
        now = datetime.datetime.now().isoformat(timespec="seconds")
        cur = conn.cursor()
        log_number = _get_next_log_number(cur)
        cur.execute(
            "INSERT INTO firearms (log_number, make, model, serial_number, firearm_type, caliber, "
            "acquisition_date, acquired_from, purchase_price, estimated_value, "
            "insured_value, storage_location, notes, "
            "sub_type, held_in_trust, trust_name, is_nfa, nfa_form_type, nfa_stamp_date, "
            "disposition_status, disposition_date, disposition_to, disposition_address, "
            "disposition_amount, disposition_notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "'Owned', '', '', '', '', '', ?, ?)",
            (log_number, make_s, model_s, serial_s, type_s, caliber_s, date_s, acquired_from_s,
             price_s, value_s, insured_s, storage_s, notes_s,
             sub_type_s, held_in_trust_i, trust_name_s, is_nfa_i, nfa_form_type_s, nfa_stamp_date_s,
             now, now),
        )
        new_id = cur.lastrowid
        conn.commit()
        log(f"Firearm {log_number} created")
        return {"ok": True, "firearm_id": new_id, "log_number": log_number}
    except Exception as e:
        conn.rollback()
        log(f"create_firearm failed: {e}")
        return {"ok": False, "error": "Couldn't save the firearm."}


def update_firearm(conn, log, firearm_id, make, model, serial_number="", firearm_type="", caliber="",
                    acquisition_date="", acquired_from="", purchase_price="",
                    estimated_value="", insured_value="", storage_location="", notes="",
                    sub_type="", held_in_trust=0, trust_name="", is_nfa=0,
                    nfa_form_type="", nfa_stamp_date=""):
    """Edits identity/notes fields only; log number and disposition are
    untouched (disposition has its own editing action)."""
    try:
        row = _get_firearm_row(conn, firearm_id)
        if row is None:
            return {"ok": False, "error": "That firearm no longer exists."}
        make_s = (make or "").strip()
        model_s = (model or "").strip()
        if not make_s or not model_s:
            return {"ok": False, "error": "Make and model are required."}
        date_s, err = parse_iso_date_optional(acquisition_date)
        if err:
            return {"ok": False, "error": err}
        price_s, err = parse_decimal_optional(purchase_price)
        if err:
            return {"ok": False, "error": err}
        value_s, err = parse_decimal_optional(estimated_value)
        if err:
            return {"ok": False, "error": err}
        insured_s, err = parse_decimal_optional(insured_value)
        if err:
            return {"ok": False, "error": err}
        nfa_stamp_date_s, err = parse_iso_date_optional(nfa_stamp_date)
        if err:
            return {"ok": False, "error": err}
        serial_s = (serial_number or "").strip()
        type_s = (firearm_type or "").strip()
        caliber_s = (caliber or "").strip()
        acquired_from_s = (acquired_from or "").strip()
        storage_s = (storage_location or "").strip()
        notes_s = (notes or "").strip()
        sub_type_s = (sub_type or "").strip()
        held_in_trust_i = 1 if held_in_trust else 0
        trust_name_s = (trust_name or "").strip()
        is_nfa_i = 1 if is_nfa else 0
        nfa_form_type_s = (nfa_form_type or "").strip()
        if not held_in_trust_i:
            trust_name_s = ""
        if not is_nfa_i:
            nfa_form_type_s = ""
            nfa_stamp_date_s = ""
        now = datetime.datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE firearms SET make=?, model=?, serial_number=?, firearm_type=?, caliber=?, "
            "acquisition_date=?, acquired_from=?, purchase_price=?, estimated_value=?, "
            "insured_value=?, storage_location=?, notes=?, "
            "sub_type=?, held_in_trust=?, trust_name=?, is_nfa=?, nfa_form_type=?, nfa_stamp_date=?, "
            "updated_at=? WHERE id=?",
            (make_s, model_s, serial_s, type_s, caliber_s, date_s, acquired_from_s,
             price_s, value_s, insured_s, storage_s, notes_s,
             sub_type_s, held_in_trust_i, trust_name_s, is_nfa_i, nfa_form_type_s, nfa_stamp_date_s,
             now, firearm_id),
        )
        conn.commit()
        log(f"Firearm {row['log_number']} updated")
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        log(f"update_firearm failed: {e}")
        return {"ok": False, "error": "Couldn't save the firearm."}


def update_disposition(conn, log, firearm_id, status, date="", to="", address="", amount="", notes=""):
    """Record or clear a disposition. Setting the status back to Owned
    clears the rest of the disposition fields, since the UI hides them
    once a firearm is owned again."""
    try:
        row = _get_firearm_row(conn, firearm_id)
        if row is None:
            return {"ok": False, "error": "That firearm no longer exists."}
        status_s = (status or "Owned").strip()
        if status_s not in config.DISPOSITION_STATUSES:
            return {"ok": False, "error": "Choose a valid disposition status."}
        if status_s == "Owned":
            date_s = to_s = address_s = amount_s = notes_s = ""
        else:
            date_s, err = parse_iso_date_optional(date)
            if err:
                return {"ok": False, "error": err}
            amount_s, err = parse_decimal_optional(amount)
            if err:
                return {"ok": False, "error": err}
            to_s = (to or "").strip()
            address_s = (address or "").strip()
            notes_s = (notes or "").strip()
        now = datetime.datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE firearms SET disposition_status=?, disposition_date=?, disposition_to=?, "
            "disposition_address=?, disposition_amount=?, disposition_notes=?, updated_at=? WHERE id=?",
            (status_s, date_s, to_s, address_s, amount_s, notes_s, now, firearm_id),
        )
        conn.commit()
        log(f"Disposition for firearm {row['log_number']} set to {status_s}")
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        log(f"update_disposition failed: {e}")
        return {"ok": False, "error": "Couldn't save the disposition."}


def delete_firearm(conn, window, log, firearm_id, export_backup_first=False):
    """Delete a firearm and its photo and document files. The log number
    is never reissued. If export_backup_first is set, a backup zip is
    produced (with its own save dialog) before anything is deleted;
    cancelling that save dialog cancels the whole delete."""
    from sfl.services import export as export_service

    try:
        row = _get_firearm_row(conn, firearm_id)
        if row is None:
            return {"ok": False, "error": "That firearm no longer exists."}
        if export_backup_first:
            backup_result = export_service._export_single_backup_zip_internal(conn, window, log, firearm_id)
            if not backup_result.get("ok") or backup_result.get("cancelled"):
                return backup_result
        photos = conn.execute(
            "SELECT filename FROM photos WHERE firearm_id=?", (firearm_id,)
        ).fetchall()
        attachments = conn.execute(
            "SELECT filename FROM attachments WHERE firearm_id=?", (firearm_id,)
        ).fetchall()
        cur = conn.cursor()
        cur.execute("DELETE FROM photos WHERE firearm_id=?", (firearm_id,))
        # Foreign keys are enforced (PRAGMA foreign_keys = ON), so attachment
        # rows must go before the firearm row, same as photos.
        cur.execute("DELETE FROM attachments WHERE firearm_id=?", (firearm_id,))
        cur.execute("DELETE FROM firearms WHERE id=?", (firearm_id,))
        conn.commit()
        failed_paths = []
        for p in photos:
            full = paths._safe_photo_path(p["filename"])
            if full:
                try:
                    os.remove(full)
                except Exception:
                    failed_paths.append(full)
        for a in attachments:
            full = paths._safe_attachment_path(a["filename"])
            if full:
                try:
                    os.remove(full)
                except Exception:
                    failed_paths.append(full)
        log(f"Firearm {row['log_number']} deleted")
        if failed_paths:
            failed_names = [os.path.basename(p) for p in failed_paths]
            log(f"Firearm {row['log_number']} deleted, but these files could not be removed from disk: {failed_names}")
            return {
                "ok": True,
                "warning": f"The record was deleted, but {len(failed_paths)} file(s) could not be removed from disk. See the log for details.",
            }
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        log(f"delete_firearm failed: {e}")
        return {"ok": False, "error": "Couldn't delete the firearm."}
