"""Photo storage: add (from a file picker or raw drag-and-drop bytes),
delete, set primary, and read back as base64 for in-app display or export
embedding."""
import base64
import io
import os

import webview

from sfl import config, paths
from sfl.images import (
    _photo_b64_too_large,
    _photo_bytes_too_large,
    _photo_source_too_large,
    _sniff_image_mime,
    optimize_image_to_jpeg,
    photo_failure_warning,
)


def _get_photos(conn, firearm_id) -> list:
    rows = conn.execute(
        "SELECT id, filename, seq, is_primary FROM photos WHERE firearm_id=? ORDER BY seq",
        (firearm_id,),
    ).fetchall()
    return [
        {"id": r["id"], "filename": r["filename"], "seq": r["seq"], "is_primary": bool(r["is_primary"])}
        for r in rows
    ]


def _photo_with_data(p: dict) -> dict:
    full = paths._safe_photo_path(p["filename"])
    if not full or not os.path.isfile(full):
        return {**p, "data_uri": None}
    try:
        with open(full, "rb") as fh:
            data = fh.read()
        mime = _sniff_image_mime(data)
        b64 = base64.b64encode(data).decode("ascii")
        return {**p, "data_uri": f"data:{mime};base64,{b64}"}
    except Exception:
        return {**p, "data_uri": None}


def _import_photo_sources(firearm_id, row, cur, seq, has_primary, sources, written_paths, log):
    """Shared photo import loop, used by both add_photos (a file path per
    source) and add_photos_from_data (raw bytes per source), so the naming
    scheme, sequence numbering, and failure handling never drift between
    the two entry points.

    Each item in `sources` is a dict:
      - "opener": what optimize_image_to_jpeg can open (a path or a
        file-like object), or None if the source already failed before
        reaching that step (bad extension, undecodable data, etc.);
      - "too_large": True if the source has already been measured and is
        over MAX_IMAGE_BYTES, so it must be refused before it is opened;
      - "label": a name to use in log lines;
      - "skip_reason": only used when "opener" is None, a short phrase
        describing why for the log line.

    Creates photos\\ if needed, inserts one row per successfully imported
    source under the {lognum}_{seq}.jpg naming scheme, and makes the first
    photo added primary if the firearm has none yet. A source that fails
    never burns a sequence number. Does not commit: the caller commits
    once after this returns. Returns (added, failed, seq, has_primary,
    fail_counts), where fail_counts is a {"not_image": n, "damaged": n,
    "too_large": n} dict tallying why each refused source failed.

    `written_paths` is a list the caller owns: each file this call
    successfully writes to disk is appended to it, so the caller can
    remove them if a later step (the commit) fails.
    """
    photos_dir = os.path.join(paths.app_dir(), config.PHOTOS_DIRNAME)
    os.makedirs(photos_dir, exist_ok=True)

    added = 0
    failed = 0
    fail_counts = {"not_image": 0, "damaged": 0, "too_large": 0}
    for source in sources:
        label = source.get("label", "")
        if source.get("opener") is None:
            # Already failed before reaching the optimize step: never
            # burns a sequence number.
            failed += 1
            fail_counts[source.get("category", "damaged")] += 1
            reason = source.get("skip_reason", "unreadable")
            log(f"Photo skipped, {reason}: {os.path.basename(label)}")
            continue
        if source.get("too_large"):
            # Refuse to even open it: never burns a sequence number.
            failed += 1
            fail_counts["too_large"] += 1
            log(f"Photo skipped, over the size limit: {os.path.basename(label)}")
            continue
        seq += 1
        target_name = f"{row['log_number']}_{seq}.jpg"
        target_full = os.path.join(photos_dir, target_name)
        try:
            optimize_image_to_jpeg(source["opener"], target_full)
        except Exception as e:
            # Never store a broken or half-written photo, and don't burn
            # a sequence number on one that didn't make it in.
            seq -= 1
            failed += 1
            fail_counts["damaged"] += 1
            log(f"Photo optimize failed for {os.path.basename(label)}: {e}")
            try:
                if os.path.exists(target_full):
                    os.remove(target_full)
            except Exception:
                pass
            continue
        written_paths.append(target_full)
        rel_name = f"{config.PHOTOS_DIRNAME}/{target_name}"
        is_primary = 1 if not has_primary else 0
        cur.execute(
            "INSERT INTO photos (firearm_id, filename, seq, is_primary) VALUES (?, ?, ?, ?)",
            (firearm_id, rel_name, seq, is_primary),
        )
        if is_primary:
            has_primary = True
        added += 1
    return added, failed, seq, has_primary, fail_counts


def _photo_import_result(conn, firearm_id, added, fail_counts):
    result = {"ok": True, "photos": _get_photos(conn, firearm_id), "added": added}
    # No silent failures: tell the user some photos didn't make it.
    warning = photo_failure_warning(fail_counts)
    if warning:
        result["warning"] = warning
    return result


def add_photos(conn, window, log, firearm_id):
    """Opens a native multi-select file picker, copies each chosen image
    into photos\\ under the {lognum}_{seq} naming scheme, and inserts a
    row per photo. The first photo added becomes primary if none is set."""
    from sfl.services.firearms import _get_firearm_row

    written_paths = []
    try:
        row = _get_firearm_row(conn, firearm_id)
        if row is None:
            return {"ok": False, "error": "That firearm no longer exists."}
        result = window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=True,
            file_types=("Image Files (*.jpg;*.jpeg;*.png;*.bmp;*.gif;*.webp;*.tif;*.tiff)",),
        )
        if not result:
            return {"ok": True, "cancelled": True}
        source_paths = list(result) if isinstance(result, (list, tuple)) else [result]

        cur = conn.cursor()
        seq = cur.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM photos WHERE firearm_id=?", (firearm_id,)
        ).fetchone()[0]
        has_primary = cur.execute(
            "SELECT COUNT(*) FROM photos WHERE firearm_id=? AND is_primary=1", (firearm_id,)
        ).fetchone()[0] > 0

        # A source with an unsupported extension is filtered out here,
        # silently, same as before the refactor: the file picker's own
        # filter already keeps this from happening in normal use.
        sources = []
        for src in source_paths:
            if not src or not os.path.isfile(src):
                continue
            ext = os.path.splitext(src)[1].lower()
            if ext not in config.IMAGE_EXTENSIONS:
                continue
            sources.append({
                "opener": src,
                "too_large": _photo_source_too_large(src),
                "label": src,
            })

        added, failed, seq, has_primary, fail_counts = _import_photo_sources(
            firearm_id, row, cur, seq, has_primary, sources, written_paths, log
        )
        conn.commit()
        log(f"Added {added} photo(s) to firearm {row['log_number']}"
            + (f", {failed} failed" if failed else ""))
        return _photo_import_result(conn, firearm_id, added, fail_counts)
    except Exception as e:
        conn.rollback()
        for p in written_paths:
            try:
                os.remove(p)
            except Exception:
                pass
        log(f"add_photos failed: {e}")
        return {"ok": False, "error": "Couldn't add the photo(s)."}


def add_photos_from_data(conn, log, firearm_id, files):
    """Imports photos from raw bytes instead of a file path, for a
    browser drag-and-drop drop of image data. `files` is a list of
    {"name": <original filename>, "data": <base64-encoded contents>}
    dicts. Shares the naming scheme, sequence numbering, and
    first-photo-becomes-primary rule with add_photos via
    _import_photo_sources, so nothing can drift between the two."""
    from sfl.services.firearms import _get_firearm_row

    written_paths = []
    try:
        row = _get_firearm_row(conn, firearm_id)
        if row is None:
            return {"ok": False, "error": "That firearm no longer exists."}

        cur = conn.cursor()
        seq = cur.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM photos WHERE firearm_id=?", (firearm_id,)
        ).fetchone()[0]
        has_primary = cur.execute(
            "SELECT COUNT(*) FROM photos WHERE firearm_id=? AND is_primary=1", (firearm_id,)
        ).fetchone()[0] > 0

        sources = []
        for f in files or []:
            f = f or {}
            name = f.get("name") or ""
            label = name or "(unnamed)"
            ext = os.path.splitext(name)[1].lower()
            if ext not in config.IMAGE_EXTENSIONS:
                sources.append({
                    "opener": None, "label": label, "category": "not_image",
                    "skip_reason": "not a supported image type",
                })
                continue
            raw = f.get("data")
            if not raw:
                sources.append({
                    "opener": None, "label": label, "category": "damaged",
                    "skip_reason": "no data received",
                })
                continue
            if _photo_b64_too_large(raw):
                # Refuse from the encoded length alone, before spending
                # the memory to base64-decode an oversized payload.
                sources.append({
                    "opener": label, "too_large": True, "label": label,
                })
                continue
            try:
                data = base64.b64decode(raw, validate=True)
            except Exception:
                sources.append({
                    "opener": None, "label": label, "category": "damaged",
                    "skip_reason": "couldn't decode",
                })
                continue
            sources.append({
                "opener": io.BytesIO(data),
                "too_large": _photo_bytes_too_large(data),
                "label": label,
            })

        added, failed, seq, has_primary, fail_counts = _import_photo_sources(
            firearm_id, row, cur, seq, has_primary, sources, written_paths, log
        )
        conn.commit()
        log(f"Added {added} photo(s) to firearm {row['log_number']}"
            + (f", {failed} failed" if failed else ""))
        return _photo_import_result(conn, firearm_id, added, fail_counts)
    except Exception as e:
        conn.rollback()
        for p in written_paths:
            try:
                os.remove(p)
            except Exception:
                pass
        log(f"add_photos_from_data failed: {e}")
        return {"ok": False, "error": "Couldn't add the photo(s)."}


def delete_photo(conn, log, photo_id):
    try:
        row = conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "That photo no longer exists."}
        firearm_id = row["firearm_id"]
        was_primary = bool(row["is_primary"])
        full = paths._safe_photo_path(row["filename"])
        cur = conn.cursor()
        cur.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        if was_primary:
            nxt = cur.execute(
                "SELECT id FROM photos WHERE firearm_id=? ORDER BY seq LIMIT 1", (firearm_id,)
            ).fetchone()
            if nxt:
                cur.execute("UPDATE photos SET is_primary=1 WHERE id=?", (nxt["id"],))
        conn.commit()
        if full:
            try:
                os.remove(full)
            except Exception:
                pass
        log(f"Deleted photo {photo_id}")
        return {"ok": True, "photos": _get_photos(conn, firearm_id)}
    except Exception as e:
        conn.rollback()
        log(f"delete_photo failed: {e}")
        return {"ok": False, "error": "Couldn't delete the photo."}


def set_primary_photo(conn, log, photo_id):
    try:
        row = conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            return {"ok": False, "error": "That photo no longer exists."}
        firearm_id = row["firearm_id"]
        cur = conn.cursor()
        cur.execute("UPDATE photos SET is_primary=0 WHERE firearm_id=?", (firearm_id,))
        cur.execute("UPDATE photos SET is_primary=1 WHERE id=?", (photo_id,))
        conn.commit()
        log(f"Primary photo set for firearm {firearm_id}")
        return {"ok": True, "photos": _get_photos(conn, firearm_id)}
    except Exception as e:
        conn.rollback()
        log(f"set_primary_photo failed: {e}")
        return {"ok": False, "error": "Couldn't set the primary photo."}


def get_photo_data(log, filename):
    """Returns a photo's bytes as a base64 data URI for in-app display.
    Images are stored next to the exe (not bundled), so this is the one
    reliable way to show them from a UI file that may itself be running
    out of a PyInstaller temp folder."""
    try:
        full = paths._safe_photo_path(filename)
        if not full or not os.path.isfile(full):
            return {"ok": False, "error": "That photo file is missing."}
        with open(full, "rb") as f:
            data = f.read()
        mime = _sniff_image_mime(data)
        b64 = base64.b64encode(data).decode("ascii")
        return {"ok": True, "data_uri": f"data:{mime};base64,{b64}"}
    except Exception as e:
        log(f"get_photo_data failed: {e}")
        return {"ok": False, "error": "Couldn't load that photo."}
