"""Bridge exposed to the UI. A thin facade: holds the live state (db
connection, window, debug flag/path) and delegates every method to a plain
service function in sfl.services.*, passing that state through as explicit
arguments. Methods return JSON-able dicts; the UI awaits."""
import sqlite3

from sfl.services import attachments as attachments_service
from sfl.services import backup as backup_service
from sfl.services import export as export_service
from sfl.services import firearms as firearms_service
from sfl.services import imports as imports_service
from sfl.services import photos as photos_service
from sfl.services import settings as settings_service


class Api:
    """Bridge exposed to the UI. Methods return JSON-able dicts; the UI awaits."""

    def __init__(self):
        self._window = None
        self._conn = None
        self._debug = False
        self._debug_path = None

    def set_window(self, w):
        self._window = w

    def set_conn(self, conn: sqlite3.Connection):
        self._conn = conn

    def close_conn(self):
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass

    # --- config -------------------------------------------------------------
    def get_config(self):
        return firearms_service.get_config(self.log)

    def get_autocomplete(self):
        return firearms_service.get_autocomplete(self._conn, self.log)

    # --- firearms -------------------------------------------------------------
    def _get_firearm_row(self, firearm_id):
        return firearms_service._get_firearm_row(self._conn, firearm_id)

    def _get_photos(self, firearm_id) -> list:
        return photos_service._get_photos(self._conn, firearm_id)

    def _attachment_stats(self, firearm_id):
        return attachments_service._attachment_stats(self._conn, firearm_id)

    def list_firearms(self):
        return firearms_service.list_firearms(self._conn, self.log)

    def get_firearm(self, firearm_id):
        return firearms_service.get_firearm(self._conn, firearm_id, self.log)

    def create_firearm(self, make, model, serial_number="", firearm_type="", caliber="",
                        acquisition_date="", acquired_from="", purchase_price="",
                        estimated_value="", insured_value="", storage_location="", notes="",
                        sub_type="", held_in_trust=0, trust_name="", is_nfa=0,
                        nfa_form_type="", nfa_stamp_date=""):
        return firearms_service.create_firearm(
            self._conn, self.log, make, model, serial_number, firearm_type, caliber,
            acquisition_date, acquired_from, purchase_price,
            estimated_value, insured_value, storage_location, notes,
            sub_type, held_in_trust, trust_name, is_nfa,
            nfa_form_type, nfa_stamp_date,
        )

    def update_firearm(self, firearm_id, make, model, serial_number="", firearm_type="", caliber="",
                        acquisition_date="", acquired_from="", purchase_price="",
                        estimated_value="", insured_value="", storage_location="", notes="",
                        sub_type="", held_in_trust=0, trust_name="", is_nfa=0,
                        nfa_form_type="", nfa_stamp_date=""):
        """Edits identity/notes fields only; log number and disposition are
        untouched (disposition has its own editing action)."""
        return firearms_service.update_firearm(
            self._conn, self.log, firearm_id, make, model, serial_number, firearm_type, caliber,
            acquisition_date, acquired_from, purchase_price,
            estimated_value, insured_value, storage_location, notes,
            sub_type, held_in_trust, trust_name, is_nfa,
            nfa_form_type, nfa_stamp_date,
        )

    def update_disposition(self, firearm_id, status, date="", to="", address="", amount="", notes=""):
        """Record or clear a disposition. Setting the status back to Owned
        clears the rest of the disposition fields, since the UI hides them
        once a firearm is owned again."""
        return firearms_service.update_disposition(
            self._conn, self.log, firearm_id, status, date, to, address, amount, notes
        )

    def delete_firearm(self, firearm_id, export_backup_first=False):
        """Delete a firearm and its photo and document files. The log number
        is never reissued. If export_backup_first is set, a backup zip is
        produced (with its own save dialog) before anything is deleted;
        cancelling that save dialog cancels the whole delete."""
        return firearms_service.delete_firearm(
            self._conn, self._window, self.log, firearm_id, export_backup_first
        )

    # --- photos -----------------------------------------------------------------
    def _import_photo_sources(self, firearm_id, row, cur, seq, has_primary, sources, written_paths):
        return photos_service._import_photo_sources(
            firearm_id, row, cur, seq, has_primary, sources, written_paths, self.log
        )

    def _photo_import_result(self, firearm_id, added, fail_counts):
        return photos_service._photo_import_result(self._conn, firearm_id, added, fail_counts)

    def add_photos(self, firearm_id):
        """Opens a native multi-select file picker, copies each chosen image
        into photos\\ under the {lognum}_{seq} naming scheme, and inserts a
        row per photo. The first photo added becomes primary if none is set."""
        return photos_service.add_photos(self._conn, self._window, self.log, firearm_id)

    def add_photos_from_data(self, firearm_id, files):
        """Imports photos from raw bytes instead of a file path, for a
        browser drag-and-drop drop of image data. `files` is a list of
        {"name": <original filename>, "data": <base64-encoded contents>}
        dicts. Shares the naming scheme, sequence numbering, and
        first-photo-becomes-primary rule with add_photos via
        _import_photo_sources, so nothing can drift between the two."""
        return photos_service.add_photos_from_data(self._conn, self.log, firearm_id, files)

    def delete_photo(self, photo_id):
        return photos_service.delete_photo(self._conn, self.log, photo_id)

    def set_primary_photo(self, photo_id):
        return photos_service.set_primary_photo(self._conn, self.log, photo_id)

    def get_photo_data(self, filename):
        """Returns a photo's bytes as a base64 data URI for in-app display.
        Images are stored next to the exe (not bundled), so this is the one
        reliable way to show them from a UI file that may itself be running
        out of a PyInstaller temp folder."""
        return photos_service.get_photo_data(self.log, filename)

    def _photo_with_data(self, p: dict) -> dict:
        return photos_service._photo_with_data(p)

    # --- attachments --------------------------------------------------------
    def _get_attachments(self, firearm_id) -> list:
        return attachments_service._get_attachments(self._conn, firearm_id)

    def choose_attachments(self):
        """Step one of the two-step add flow: opens a native multi-select file
        picker (any file type) and reports back name/size/too_large for each
        chosen file, without copying anything yet."""
        return attachments_service.choose_attachments(self._window, self.log)

    def add_attachments(self, firearm_id, paths):
        """Copies the given file paths into attachments\\ verbatim (never
        opened, recompressed, or validated as images) under the
        {lognum}_{seq} naming scheme, and inserts a row per file with a
        default label built from the original filename."""
        return attachments_service.add_attachments(self._conn, self.log, firearm_id, paths)

    def rename_attachment(self, attachment_id, new_label):
        return attachments_service.rename_attachment(self._conn, self.log, attachment_id, new_label)

    def open_attachment(self, attachment_id):
        """Opens a document with the OS default handler. Falls back to
        revealing it in File Explorer if there's no associated program."""
        return attachments_service.open_attachment(self._conn, self.log, attachment_id)

    def delete_attachment(self, attachment_id):
        return attachments_service.delete_attachment(self._conn, self.log, attachment_id)

    def save_attachment_copy(self, attachment_id):
        """Copies a document's original file out to a location the user
        picks, for use from the delete-document confirmation ('save a copy
        first') so a deletion is never the only chance to keep the file."""
        return attachments_service.save_attachment_copy(self._conn, self._window, self.log, attachment_id)

    def get_attachment_totals(self):
        """Global disk-use total across every firearm's documents, summed
        from the stored size_bytes column, plus a count of documents whose
        file is missing from disk (a light per-file existence check)."""
        return attachments_service.get_attachment_totals(self._conn, self.log)

    # --- exports ------------------------------------------------------------
    def export_single_html(self, firearm_id):
        """Self-contained single-firearm export: one HTML file with photos
        embedded as base64 data URIs, so it can be opened or shared standalone."""
        return export_service.export_single_html(self._conn, self._window, self.log, firearm_id)

    def export_single_backup_zip(self, firearm_id):
        return self._export_single_backup_zip_internal(firearm_id)

    def _export_single_backup_zip_internal(self, firearm_id):
        """Zip containing the self-contained HTML plus the original photo
        files. Shared by the standalone export action and the delete flow's
        'export backup first' option."""
        return export_service._export_single_backup_zip_internal(self._conn, self._window, self.log, firearm_id)

    def export_full(self, photo_depth="primary"):
        """Full collection export: one zip with an all-firearms HTML report,
        a CSV of all fields, a photos\\ folder, and an attachments\\ folder.
        photo_depth controls how many photos per firearm are included:
        'primary', 'all', or 'none'. Documents have no depth selector and are
        always fully included; they're the point of the backup."""
        return export_service.export_full(self._conn, self._window, self.log, photo_depth)

    # --- backup ---------------------------------------------------------------
    def create_backup(self):
        """Complete, restorable archive: a safe SQLite copy of the database
        plus every photo and document file it references, with a manifest.
        Separate from the exports above, which are share/print output."""
        return backup_service.create_backup(self._conn, self._window, self.log)

    # --- CSV import -----------------------------------------------------------
    def import_csv_pick(self):
        """Stage 1: native picker restricted to .csv, read as UTF-8 with a
        BOM tolerated (our own export and a plain spreadsheet "Save As CSV"
        both open cleanly), and parse into a header plus raw rows. Nothing
        touches the database here; the page shows a mapping step next."""
        return imports_service.import_csv_pick(self._window, self.log)

    def import_csv_preview(self, rows, mapping):
        """Stage 2: run every row through the shared validators and classify
        it as ok / duplicate-serial (soft warning) / error (specific reason
        and offending cell). Nothing touches the database except a read of
        existing serials, used only to flag duplicates."""
        return imports_service.import_csv_preview(self._conn, self.log, rows, mapping)

    def import_csv_commit(self, records):
        """Stage 3: import only the rows the user accepted, in a single
        transaction: all succeed or the whole import rolls back, so a
        half-import is impossible. Each row gets a fresh app-assigned log
        number; an imported Log Number column is never honored."""
        return imports_service.import_csv_commit(self._conn, self.log, records)

    # --- preferences (local file, not stored in the db) ----------------------
    def _load_theme(self) -> str:
        return settings_service._load_theme()

    def get_theme(self):
        return settings_service.get_theme()

    def save_theme(self, theme: str):
        return settings_service.save_theme(self.log, theme)

    # --- misc bridge helpers --------------------------------------------------
    def open_url(self, url: str):
        """Open a link in the system browser, never by navigating the app window."""
        return settings_service.open_url(url)

    def check_update(self):
        """Compare the latest published release to APP_VERSION. Quiet in the UI on
        failure (see _update_error_reason), but always logged when debug is on."""
        return settings_service.check_update(self.log)

    @staticmethod
    def _is_newer(latest: str, current: str) -> bool:
        return settings_service._is_newer(latest, current)

    # --- debug log --------------------------------------------------------------
    def set_debug(self, on: bool):
        self._debug, self._debug_path, started = settings_service.set_debug(on, self._debug_path)
        if started:
            self.log("Debug log started")
        return {"ok": True}

    def log(self, msg: str):
        settings_service.log(self._debug, self._debug_path, msg)
