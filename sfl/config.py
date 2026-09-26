"""Top-level constants shared across the app: version/release info, on-disk
file and folder names, schema version, and the fixed vocabularies used by
firearms, photo/document validation, CSV export, and CSV import."""

APP_VERSION = "1.7.1"
GITHUB_OWNER = "JDE-Projects"
GITHUB_REPO = "Simple-Firearm-Logbook"

DB_FILENAME = "simple_firearm_logbook.db"
PHOTOS_DIRNAME = "photos"
ATTACHMENTS_DIRNAME = "attachments"
SCHEMA_VERSION = 1
# The SQLite application_id Simple Firearm Logbook Pro stamps on logbooks it opens.
PRO_APPLICATION_ID = 0x53464C50

DISPOSITION_STATUSES = ("Owned", "Sold", "Traded", "Lost", "Stolen", "Other")
STANDARD_FIREARM_TYPES = ("Pistol", "Revolver", "Rifle", "Shotgun", "Other")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff")

# Photo import optimization: scale the long edge down to this many pixels (only
# when the source is bigger) and re-encode as JPEG at this quality. A big phone
# photo drops from megabytes to a few hundred KB while a serial-number close-up
# stays readable. Applied on import only; the user's original file is untouched.
PHOTO_MAX_EDGE = 2400
PHOTO_JPEG_QUALITY = 85

# Decompression-bomb guard: refuse a source file bigger than this before ever
# opening it, so a hostile or corrupt file can't be handed to PIL at all.
MAX_IMAGE_BYTES = 50 * 1024 * 1024

# Documents attach verbatim, never opened or recompressed. ATTACHMENT_WARN_BYTES
# is a soft warning threshold only, not a hard cap.
ATTACHMENT_WARN_BYTES = 25 * 1024 * 1024
ATTACHMENT_LABEL_MAX = 100

CSV_FORMULA_LEAD_CHARS = ("=", "+", "-", "@", "\t", "\r")

# CSV import: field order matches _build_csv_text's header, minus "Log
# Number" (the app owns numbering, so an imported log number is read-only and
# never honored).
IMPORT_FIELDS = (
    ("make", "Make"),
    ("model", "Model"),
    ("serial_number", "Serial Number"),
    ("firearm_type", "Type"),
    ("sub_type", "Sub-Type"),
    ("caliber", "Caliber"),
    ("acquisition_date", "Acquisition Date"),
    ("acquired_from", "Acquired From"),
    ("purchase_price", "Purchase Price"),
    ("estimated_value", "Estimated Value"),
    ("insured_value", "Insured Value"),
    ("storage_location", "Storage Location"),
    ("held_in_trust", "Held In Trust"),
    ("trust_name", "Trust Name"),
    ("is_nfa", "NFA"),
    ("nfa_form_type", "NFA Form Type"),
    ("nfa_stamp_date", "NFA Stamp Date"),
    ("notes", "Notes"),
    ("disposition_status", "Disposition Status"),
    ("disposition_date", "Disposition Date"),
    ("disposition_to", "Disposition To"),
    ("disposition_address", "Disposition Address"),
    ("disposition_amount", "Disposition Amount"),
    ("disposition_notes", "Disposition Notes"),
)
