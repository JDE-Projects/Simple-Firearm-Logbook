"""Document attachments: verbatim file copies (never opened or recompressed)
with an editable display label, plus rename/open/delete/save-a-copy and the
global disk-use total."""
import os
import shutil
import subprocess

import webview

from sfl import config, paths


def _get_attachments(conn, firearm_id) -> list:
    rows = conn.execute(
        "SELECT id, filename, label, seq, size_bytes FROM attachments WHERE firearm_id=? ORDER BY seq",
        (firearm_id,),
    ).fetchall()
    result = []
    for r in rows:
        full = paths._safe_attachment_path(r["filename"])
        result.append({
            "id": r["id"],
            "filename": r["filename"],
            "label": r["label"],
            "seq": r["seq"],
            "size_bytes": r["size_bytes"],
            "missing": not (full and os.path.isfile(full)),
        })
    return result


def _attachment_stats(conn, firearm_id):
    row = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(SUM(size_bytes), 0) AS b FROM attachments WHERE firearm_id=?",
        (firearm_id,),
    ).fetchone()
    # Reuse the per-row on-disk check _get_attachments already does rather
    # than scanning the files a second time here.
    missing = sum(1 for a in _get_attachments(conn, firearm_id) if a["missing"])
    return row["c"], row["b"], missing


def choose_attachments(window, log):
    """Step one of the two-step add flow: opens a native multi-select file
    picker (any file type) and reports back name/size/too_large for each
    chosen file, without copying anything yet."""
    try:
        result = window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=("All files (*.*)",),
        )
        if not result:
            return {"ok": True, "cancelled": True}
        source_paths = list(result) if isinstance(result, (list, tuple)) else [result]
        files = []
        for p in source_paths:
            if not p or not os.path.isfile(p):
                continue
            size_bytes = os.path.getsize(p)
            files.append(
                {
                    "path": p,
                    "name": os.path.basename(p),
                    "size_bytes": size_bytes,
                    "too_large": size_bytes > config.ATTACHMENT_WARN_BYTES,
                }
            )
        return {"ok": True, "files": files}
    except Exception as e:
        log(f"choose_attachments failed: {e}")
        return {"ok": False, "error": "Couldn't open the file picker."}


def add_attachments(conn, log, firearm_id, source_paths):
    """Copies the given file paths into attachments\\ verbatim (never
    opened, recompressed, or validated as images) under the
    {lognum}_{seq} naming scheme, and inserts a row per file with a
    default label built from the original filename."""
    from sfl.services.firearms import _get_firearm_row

    try:
        row = _get_firearm_row(conn, firearm_id)
        if row is None:
            return {"ok": False, "error": "That firearm no longer exists."}

        attachments_dir = os.path.join(paths.app_dir(), config.ATTACHMENTS_DIRNAME)
        os.makedirs(attachments_dir, exist_ok=True)
        cur = conn.cursor()
        seq = cur.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM attachments WHERE firearm_id=?", (firearm_id,)
        ).fetchone()[0]

        added = 0
        failed = 0
        written_paths = []
        for src in source_paths or []:
            if not src or not os.path.isfile(src):
                # Re-validate here: the picker ran earlier and the file may
                # have vanished since.
                failed += 1
                continue
            ext = os.path.splitext(src)[1].lower()
            seq += 1
            target_name = f"{row['log_number']}_{seq}{ext}"
            target_full = os.path.join(attachments_dir, target_name)
            try:
                shutil.copy2(src, target_full)
                written_paths.append(target_full)
                size_bytes = os.path.getsize(target_full)
            except Exception as e:
                # Never store a half-written file, and don't burn a
                # sequence number on one that didn't make it in.
                seq -= 1
                failed += 1
                log(f"Attachment copy failed for {os.path.basename(src)}: {e}")
                try:
                    if os.path.exists(target_full):
                        os.remove(target_full)
                except Exception:
                    pass
                continue
            label = paths._sanitize_attachment_label(os.path.basename(src))
            rel_name = f"{config.ATTACHMENTS_DIRNAME}/{target_name}"
            cur.execute(
                "INSERT INTO attachments (firearm_id, filename, label, seq, size_bytes) VALUES (?, ?, ?, ?, ?)",
                (firearm_id, rel_name, label, seq, size_bytes),
            )
            added += 1
        conn.commit()
        log(f"Added {added} attachment(s) to firearm {row['log_number']}"
            + (f", {failed} failed" if failed else ""))
        result = {"ok": True, "attachments": _get_attachments(conn, firearm_id), "added": added}
        if failed:
            # No silent failures: tell the user some files didn't make it.
            result["warning"] = f"{failed} file(s) couldn't be added."
        return result
    except Exception as e:
        conn.rollback()
        for p in written_paths if "written_paths" in locals() else []:
            try:
                os.remove(p)
            except Exception:
                pass
        log(f"add_attachments failed: {e}")
        return {"ok": False, "error": "Couldn't add the file(s)."}


def rename_attachment(conn, log, attachment_id, new_label):
    try:
        row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "That document no longer exists."}
        label = paths._sanitize_attachment_label(new_label)
        if not label:
            return {"ok": False, "error": "Enter a name."}
        conn.execute("UPDATE attachments SET label=? WHERE id=?", (label, attachment_id))
        conn.commit()
        log(f"Renamed attachment {attachment_id}")
        return {"ok": True, "attachments": _get_attachments(conn, row["firearm_id"])}
    except Exception as e:
        conn.rollback()
        log(f"rename_attachment failed: {e}")
        return {"ok": False, "error": "Couldn't rename the document."}


def open_attachment(conn, log, attachment_id):
    """Opens a document with the OS default handler. Falls back to
    revealing it in File Explorer if there's no associated program."""
    try:
        row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "That document no longer exists."}
        full = paths._safe_attachment_path(row["filename"])
        if full is None:
            log(f"open_attachment failed: unsafe path for attachment {attachment_id}")
            return {"ok": False, "error": "Couldn't open that document."}
        if not os.path.isfile(full):
            log(f"open_attachment: file missing for attachment {attachment_id}")
            return {
                "ok": False,
                "missing": True,
                "label": row["label"],
                "error": f'"{row["label"]}" is missing from the app\'s attachments folder.',
            }
        try:
            os.startfile(full)
            log(f"Opened document {attachment_id}")
            return {"ok": True}
        except OSError:
            # No associated program, or the OS refused: reveal it in
            # Explorer instead so the user can still get to the file.
            subprocess.run(["explorer", "/select,", full])
            log(f"open_attachment: no default handler for attachment {attachment_id}, revealed in Explorer")
            return {"ok": True, "revealed": True}
    except Exception as e:
        log(f"open_attachment failed: {e}")
        return {"ok": False, "error": "Couldn't open that document."}


def delete_attachment(conn, log, attachment_id):
    try:
        row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "That document no longer exists."}
        firearm_id = row["firearm_id"]
        full = paths._safe_attachment_path(row["filename"])
        conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
        conn.commit()
        if full:
            try:
                os.remove(full)
            except Exception:
                pass
        log(f"Deleted attachment {attachment_id}")
        return {"ok": True, "attachments": _get_attachments(conn, firearm_id)}
    except Exception as e:
        conn.rollback()
        log(f"delete_attachment failed: {e}")
        return {"ok": False, "error": "Couldn't delete the document."}


def save_attachment_copy(conn, window, log, attachment_id):
    """Copies a document's original file out to a location the user
    picks, for use from the delete-document confirmation ('save a copy
    first') so a deletion is never the only chance to keep the file."""
    try:
        row = conn.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "That document no longer exists."}
        full = paths._safe_attachment_path(row["filename"])
        if full is None or not os.path.isfile(full):
            return {
                "ok": False,
                "missing": True,
                "error": f'"{row["label"]}" is missing from the app\'s attachments folder.',
            }
        ext = os.path.splitext(row["filename"])[1]
        # The label usually already carries the original extension (it's
        # built from the source filename), so only add the extension when
        # it's actually absent, to avoid a doubled "name.md.md".
        default_name = paths.sanitize_filename(row["label"])
        if ext and not default_name.lower().endswith(ext.lower()):
            default_name += ext
        result = window.create_file_dialog(webview.FileDialog.SAVE, save_filename=default_name)
        if not result:
            return {"ok": True, "cancelled": True}
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path:
            return {"ok": True, "cancelled": True}
        # Only supply the original extension when the user left one off
        # entirely; if they typed a different one, respect their choice
        # rather than forcing a doubled "notes.txt.pdf".
        if ext and not os.path.splitext(path)[1]:
            path += ext
        shutil.copy2(full, path)
        log(f"Saved a copy of attachment {attachment_id}")
        return {"ok": True, "path": path}
    except Exception as e:
        log(f"save_attachment_copy failed: {e}")
        return {"ok": False, "error": "Couldn't save a copy."}


def get_attachment_totals(conn, log):
    """Global disk-use total across every firearm's documents, summed
    from the stored size_bytes column, plus a count of documents whose
    file is missing from disk (a light per-file existence check)."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(size_bytes), 0) AS b FROM attachments"
        ).fetchone()
        filenames = conn.execute("SELECT filename FROM attachments").fetchall()
        missing = 0
        for fn in filenames:
            full = paths._safe_attachment_path(fn["filename"])
            if not (full and os.path.isfile(full)):
                missing += 1
        return {"ok": True, "total_bytes": row["b"], "count": row["c"], "missing": missing}
    except Exception as e:
        log(f"get_attachment_totals failed: {e}")
        return {"ok": False, "error": "Couldn't load the document totals."}
